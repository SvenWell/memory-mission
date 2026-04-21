"""``PIIRedactionMiddleware`` — scrubs sensitive data before model/after model.

Wealth management compliance (SEC / FINRA / MiFID / GDPR) generally requires:

1. **Regulated identifiers must not end up in third-party logs.** SSNs, bank
   account numbers, routing numbers, full card numbers should never appear in
   traces, observability exports, or LLM provider logs. Redact these always.

2. **Client names and dollar amounts are context-sensitive.** The model OFTEN
   needs them to be useful (you can't draft a personalized email to a redacted
   client). Don't redact these by default — they're scoped to the firm's own
   workspace and stay within our system. Opt-in via ``redact_names`` for
   specific workflows.

3. **Responses should also be scanned.** A model can accidentally leak PII it
   was given (e.g., by including a full account number in a summary). The
   ``after_model`` hook scrubs model output too.

Design:
- Regex-based — fast, deterministic, auditable. No ML required for V1.
- Configurable per-middleware-instance (different agents may have different
  policies).
- Stamps ``metadata["pii_redactions"]`` on call/response so observability can
  log exactly what was scrubbed without re-scanning the text.
- Idempotent: redacting already-redacted text is a no-op (placeholder tokens
  are left alone).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from memory_mission.middleware.types import ModelCall, ModelResponse

# --- Patterns ---------------------------------------------------------------

# US Social Security Number, conservative format: NNN-NN-NNNN
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Bank/brokerage account numbers: runs of 8-17 digits, optionally with hyphens
# or internal spaces. The leading ``{7,16}`` + trailing ``\d`` means 8-17
# digits total (7 repetitions + 1 trailing = 8 minimum; 16 + 1 = 17 max).
# Word boundaries + length bound reduce false positives on order numbers and
# timestamps. Skews toward over-redaction on purpose — missing a real account
# number in a trace is worse than occasionally redacting a non-account numeric.
ACCOUNT_PATTERN = re.compile(r"\b(?:\d[\s-]?){7,16}\d\b")
# Credit cards (16 digits, optionally spaced/hyphenated).
CARD_PATTERN = re.compile(r"\b(?:\d[\s-]?){12,15}\d\b")
# Email addresses.
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone numbers. Tightened to require the canonical NXX-NXX-NNNN shape with
# explicit separators so it doesn't swallow long account numbers.
#   - Optional country code: "+N" .. "+NNN"
#   - 3-digit area code (plain or parenthesized)
#   - REQUIRED separator
#   - 3-digit exchange
#   - REQUIRED separator
#   - 4-digit subscriber
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}")
# API keys / bearer tokens (heuristic: "sk-..." or "Bearer ..." or 32+ hex).
APIKEY_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|[A-Fa-f0-9]{32,})\b"
)

# Placeholder tokens used by the middleware when redacting. Having stable
# tokens lets downstream code (including the model itself) reason about
# "this field was scrubbed" rather than seeing random garbage.
REDACTION_TOKENS = {
    "ssn": "[SSN]",
    "account": "[ACCOUNT]",
    "card": "[CARD]",
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "apikey": "[APIKEY]",
}


@dataclass(frozen=True)
class _Rule:
    """A named redaction pattern applied to text."""

    name: str
    pattern: re.Pattern[str]
    placeholder: str


_DEFAULT_RULES: tuple[_Rule, ...] = (
    # Most specific first so greedier patterns don't swallow them.
    _Rule("ssn", SSN_PATTERN, REDACTION_TOKENS["ssn"]),
    _Rule("email", EMAIL_PATTERN, REDACTION_TOKENS["email"]),
    _Rule("apikey", APIKEY_PATTERN, REDACTION_TOKENS["apikey"]),
    # Phone before account: phones are 10-11 digits which otherwise match
    # the generic account regex first.
    _Rule("phone", PHONE_PATTERN, REDACTION_TOKENS["phone"]),
    _Rule("card", CARD_PATTERN, REDACTION_TOKENS["card"]),
    _Rule("account", ACCOUNT_PATTERN, REDACTION_TOKENS["account"]),
)


class PIIRedactionMiddleware:
    """Redact sensitive data from model input and output.

    Parameters
    ----------
    redact_input:
        If True (default), scrub PII from messages BEFORE the model sees them.
        Set False for workflows where the model legitimately needs raw data
        (e.g., a compliance review agent) — but think twice.
    redact_output:
        If True (default), scrub PII from model output AFTER the model returns.
        Belt-and-suspenders defense against leakage in summaries/drafts.
    extra_patterns:
        Additional ``(pattern, placeholder)`` tuples applied after defaults.
        Use for firm-specific policies (e.g. internal deal codes).
    literal_redactions:
        Substrings redacted verbatim. Useful for client name redaction when
        the firm provides a list — safer than trying to NER at runtime.
    """

    def __init__(
        self,
        *,
        redact_input: bool = True,
        redact_output: bool = True,
        extra_patterns: Sequence[tuple[re.Pattern[str], str]] | None = None,
        literal_redactions: Iterable[str] | None = None,
    ) -> None:
        self._redact_input = redact_input
        self._redact_output = redact_output
        self._rules: tuple[_Rule, ...] = (
            *_DEFAULT_RULES,
            *(
                _Rule(f"custom_{i}", p, placeholder)
                for i, (p, placeholder) in enumerate(extra_patterns or [])
            ),
        )
        self._literals: tuple[str, ...] = tuple(literal_redactions or ())

    # --- Middleware hooks ---------------------------------------------------

    def before_model(self, call: ModelCall) -> ModelCall:
        if not self._redact_input:
            return call
        new_messages: list[dict[str, Any]] = []
        total: dict[str, int] = {}
        for msg in call.messages:
            redacted_content, found = self._redact_text(msg.get("content", ""))
            new_messages.append({**msg, "content": redacted_content})
            for name, n in found.items():
                total[name] = total.get(name, 0) + n

        new_metadata = {
            **call.metadata,
            "pii_redactions_input": total,
        }
        return call.model_copy(update={"messages": new_messages, "metadata": new_metadata})

    def after_model(self, call: ModelCall, response: ModelResponse) -> ModelResponse:
        if not self._redact_output:
            return response
        redacted, found = self._redact_text(response.content)
        new_metadata = {
            **response.metadata,
            "pii_redactions_output": found,
        }
        return response.model_copy(update={"content": redacted, "metadata": new_metadata})

    # --- Redaction -----------------------------------------------------------

    def _redact_text(self, text: str) -> tuple[str, dict[str, int]]:
        """Apply all rules + literals, return redacted text and a count per rule."""
        found: dict[str, int] = {}
        result = text
        for rule in self._rules:
            result, n = rule.pattern.subn(rule.placeholder, result)
            if n:
                found[rule.name] = n
        for literal in self._literals:
            if not literal:
                continue
            before = result
            result = result.replace(literal, "[REDACTED]")
            n = before.count(literal)
            if n:
                found["literal"] = found.get("literal", 0) + n
        return result, found
