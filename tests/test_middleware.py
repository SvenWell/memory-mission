"""Tests for component 0.7 — Middleware Layer."""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from memory_mission.middleware import (
    MiddlewareChain,
    ModelCall,
    ModelFn,
    ModelResponse,
    PIIRedactionMiddleware,
)

# ---------- Helpers ----------


def _call(messages: list[dict[str, Any]] | None = None, **kwargs: Any) -> ModelCall:
    defaults: dict[str, Any] = {
        "messages": messages or [{"role": "user", "content": "hi"}],
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
    }
    defaults.update(kwargs)
    return ModelCall(**defaults)


def _echo_model(call: ModelCall) -> ModelResponse:
    """A deterministic 'model' used for tests — echoes the last message."""
    last = call.messages[-1]["content"] if call.messages else ""
    return ModelResponse(
        content=f"echo: {last}",
        provider=call.provider,
        model=call.model,
    )


# ---------- MiddlewareChain ----------


def test_empty_chain_passes_through() -> None:
    chain = MiddlewareChain([])
    call = _call()
    response = chain.execute(call, _echo_model)
    assert response.content == "echo: hi"


def test_chain_runs_before_model_in_order() -> None:
    events: list[str] = []

    class BeforeA:
        def before_model(self, call: ModelCall) -> ModelCall:
            events.append("A-before")
            return call

    class BeforeB:
        def before_model(self, call: ModelCall) -> ModelCall:
            events.append("B-before")
            return call

    chain = MiddlewareChain([BeforeA(), BeforeB()])
    chain.execute(_call(), _echo_model)
    assert events == ["A-before", "B-before"]


def test_chain_runs_after_model_in_reverse_order() -> None:
    events: list[str] = []

    class AfterA:
        def after_model(self, call: ModelCall, response: ModelResponse) -> ModelResponse:
            events.append("A-after")
            return response

    class AfterB:
        def after_model(self, call: ModelCall, response: ModelResponse) -> ModelResponse:
            events.append("B-after")
            return response

    chain = MiddlewareChain([AfterA(), AfterB()])
    chain.execute(_call(), _echo_model)
    # B registered last, should run first on the way out.
    assert events == ["B-after", "A-after"]


def test_chain_wraps_model_call_onion_style() -> None:
    events: list[str] = []

    class Outer:
        def wrap_model_call(self, call: ModelCall, next_: ModelFn) -> ModelResponse:
            events.append("outer-in")
            response = next_(call)
            events.append("outer-out")
            return response

    class Inner:
        def wrap_model_call(self, call: ModelCall, next_: ModelFn) -> ModelResponse:
            events.append("inner-in")
            response = next_(call)
            events.append("inner-out")
            return response

    chain = MiddlewareChain([Outer(), Inner()])
    chain.execute(_call(), _echo_model)

    assert events == ["outer-in", "inner-in", "inner-out", "outer-out"]


def test_chain_allows_subset_of_hooks() -> None:
    """Middleware implementing only one hook doesn't break the chain."""
    events: list[str] = []

    class OnlyBefore:
        def before_model(self, call: ModelCall) -> ModelCall:
            events.append("before")
            return call

    class OnlyAfter:
        def after_model(self, call: ModelCall, response: ModelResponse) -> ModelResponse:
            events.append("after")
            return response

    chain = MiddlewareChain([OnlyBefore(), OnlyAfter()])
    chain.execute(_call(), _echo_model)
    assert events == ["before", "after"]


def test_before_model_can_mutate_call() -> None:
    class AppendGreeting:
        def before_model(self, call: ModelCall) -> ModelCall:
            new_msgs = [*call.messages, {"role": "system", "content": "hello!"}]
            return call.model_copy(update={"messages": new_msgs})

    chain = MiddlewareChain([AppendGreeting()])
    response = chain.execute(_call(), _echo_model)
    assert response.content == "echo: hello!"


def test_after_model_can_mutate_response() -> None:
    class Uppercase:
        def after_model(self, call: ModelCall, response: ModelResponse) -> ModelResponse:
            return response.model_copy(update={"content": response.content.upper()})

    chain = MiddlewareChain([Uppercase()])
    response = chain.execute(_call(), _echo_model)
    assert response.content == "ECHO: HI"


def test_exception_in_hook_propagates() -> None:
    class Broken:
        def before_model(self, call: ModelCall) -> ModelCall:
            raise RuntimeError("fail")

    chain = MiddlewareChain([Broken()])
    with pytest.raises(RuntimeError, match="fail"):
        chain.execute(_call(), _echo_model)


def test_append_adds_to_chain() -> None:
    class M:
        def before_model(self, call: ModelCall) -> ModelCall:
            return call

    chain = MiddlewareChain([])
    assert len(chain) == 0
    chain.append(M())
    assert len(chain) == 1


# ---------- PIIRedactionMiddleware ----------


def test_pii_redacts_ssn() -> None:
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": "My SSN is 123-45-6789."}])
    redacted = mw.before_model(call)
    assert redacted.messages[0]["content"] == "My SSN is [SSN]."
    assert redacted.metadata["pii_redactions_input"]["ssn"] == 1


def test_pii_redacts_email_and_phone() -> None:
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": "Contact me at sarah@example.com or 555-123-4567."}])
    redacted = mw.before_model(call)
    content = redacted.messages[0]["content"]
    assert "[EMAIL]" in content
    assert "[PHONE]" in content
    assert "sarah@example.com" not in content


def test_pii_redacts_api_key_patterns() -> None:
    mw = PIIRedactionMiddleware()
    call = _call(
        [
            {
                "role": "user",
                "content": "Use sk-proj_abcdefghijklmnopqrstuvwx as your key.",
            }
        ]
    )
    redacted = mw.before_model(call)
    assert "[APIKEY]" in redacted.messages[0]["content"]
    assert "sk-proj" not in redacted.messages[0]["content"]


def test_pii_handles_clean_text() -> None:
    """Text with no PII passes through unchanged; redaction count is empty."""
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": "How's the weather today?"}])
    redacted = mw.before_model(call)
    assert redacted.messages[0]["content"] == "How's the weather today?"
    assert redacted.metadata["pii_redactions_input"] == {}


@pytest.mark.parametrize(
    "raw,redacted_marker",
    [
        ("12345678", "[ACCOUNT]"),  # 8-digit (REGRESSION GUARD — review finding #3)
        ("1234-5678", "[ACCOUNT]"),  # 8-digit with hyphen
        ("123456789012", "[ACCOUNT]"),  # 12-digit
        ("12345678901234567", "[ACCOUNT]"),  # 17-digit (upper bound)
    ],
)
def test_pii_redacts_8_to_17_digit_accounts(raw: str, redacted_marker: str) -> None:
    """MEDIUM-SEVERITY REGRESSION GUARD (review finding #3).

    The docstring + audit compliance requirement says 8-17 digit account
    numbers get redacted. Previously the regex required 9+ digits, leaking
    8-digit account numbers to model/provider logs.
    """
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": f"Account {raw} balance check."}])
    redacted = mw.before_model(call)
    content = redacted.messages[0]["content"]
    assert raw not in content
    assert redacted_marker in content


def test_pii_does_not_redact_short_numerics() -> None:
    """7 digits is not an account per our docstring; stays unredacted."""
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": "Order 1234567 shipped."}])
    redacted = mw.before_model(call)
    # "1234567" (7 digits) should pass through.
    assert "1234567" in redacted.messages[0]["content"]


def test_pii_is_idempotent() -> None:
    """Redacting already-redacted text doesn't modify placeholders."""
    mw = PIIRedactionMiddleware()
    call1 = _call([{"role": "user", "content": "SSN: 123-45-6789"}])
    once = mw.before_model(call1)
    assert once.messages[0]["content"] == "SSN: [SSN]"
    call2 = _call([{"role": "user", "content": once.messages[0]["content"]}])
    twice = mw.before_model(call2)
    assert twice.messages[0]["content"] == once.messages[0]["content"]


def test_pii_extra_patterns() -> None:
    mw = PIIRedactionMiddleware(
        extra_patterns=[(re.compile(r"DEAL-\d{4}"), "[DEAL]")],
    )
    call = _call([{"role": "user", "content": "See DEAL-1234 for details."}])
    redacted = mw.before_model(call)
    assert redacted.messages[0]["content"] == "See [DEAL] for details."
    assert "custom_0" in redacted.metadata["pii_redactions_input"]


def test_pii_literal_redactions() -> None:
    """Firm-supplied literal strings (e.g., client names) get redacted verbatim."""
    mw = PIIRedactionMiddleware(literal_redactions=["Acme Corp", "Project Phoenix"])
    call = _call(
        [
            {
                "role": "user",
                "content": "Brief on Acme Corp + Project Phoenix rollout.",
            }
        ]
    )
    redacted = mw.before_model(call)
    content = redacted.messages[0]["content"]
    assert "Acme Corp" not in content
    assert "Project Phoenix" not in content
    assert redacted.metadata["pii_redactions_input"]["literal"] == 2


def test_pii_redact_input_false_skips() -> None:
    mw = PIIRedactionMiddleware(redact_input=False)
    original = _call([{"role": "user", "content": "SSN: 123-45-6789"}])
    redacted = mw.before_model(original)
    assert redacted.messages[0]["content"] == "SSN: 123-45-6789"


def test_pii_redact_output_scrubs_response() -> None:
    mw = PIIRedactionMiddleware()
    call = _call([{"role": "user", "content": "hi"}])
    response = ModelResponse(
        content="Your SSN 123-45-6789 is on file.",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    redacted = mw.after_model(call, response)
    assert "[SSN]" in redacted.content
    assert "123-45-6789" not in redacted.content
    assert redacted.metadata["pii_redactions_output"]["ssn"] == 1


def test_pii_redact_output_false_skips() -> None:
    mw = PIIRedactionMiddleware(redact_output=False)
    response = ModelResponse(
        content="Your SSN 123-45-6789",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    same = mw.after_model(_call(), response)
    assert same.content == "Your SSN 123-45-6789"


def test_pii_end_to_end_through_chain() -> None:
    """PII middleware integrated with a full chain end-to-end."""
    mw = PIIRedactionMiddleware()
    chain = MiddlewareChain([mw])

    def leaky_model(call: ModelCall) -> ModelResponse:
        # Simulate a model that echoes the input — including PII.
        return ModelResponse(
            content=f"You said: {call.messages[-1]['content']}",
            provider=call.provider,
            model=call.model,
        )

    call = _call(
        [
            {
                "role": "user",
                "content": "My account is 12345678-90 and email is s@example.com.",
            }
        ]
    )
    response = chain.execute(call, leaky_model)
    # Input was redacted before the model, so echo contains placeholders, not raw PII.
    assert "12345678-90" not in response.content
    assert "s@example.com" not in response.content
    assert "[ACCOUNT]" in response.content
    assert "[EMAIL]" in response.content


def test_pii_preserves_non_content_message_fields() -> None:
    """Redaction must not drop other fields on messages (e.g. tool_call_id)."""
    mw = PIIRedactionMiddleware()
    call = _call(
        [
            {
                "role": "tool",
                "content": "SSN 123-45-6789",
                "tool_call_id": "tc_abc",
                "name": "lookup",
            }
        ]
    )
    redacted = mw.before_model(call)
    msg = redacted.messages[0]
    assert msg["tool_call_id"] == "tc_abc"
    assert msg["name"] == "lookup"
    assert msg["role"] == "tool"


# ---------- Type immutability ----------


def test_model_call_is_frozen() -> None:
    """ModelCall is frozen — middleware must use ``.model_copy(update=...)``."""
    call = _call()
    with pytest.raises(ValidationError):  # ValidationError from Pydantic
        call.model = "other-model"  # type: ignore[misc]


def test_model_response_is_frozen() -> None:
    response = ModelResponse(content="x", provider="anthropic", model="m")
    with pytest.raises(ValidationError):
        response.content = "y"  # type: ignore[misc]
