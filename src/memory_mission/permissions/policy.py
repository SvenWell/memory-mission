"""Per-firm access-control policy — scopes + employee assignments.

Native to the architecture (Emile: "if a person cannot see it, their
agent cannot"). Not a bolt-on security feature.

**Shape of the policy:**

- A firm declares a set of named **scopes**. Each scope has a description
  and optionally default-applied to pages via glob patterns. Example
  scopes: ``public``, ``partner-only``, ``client-confidential``.
- Each **employee** has a set of allowed scopes. An employee can read
  a firm-plane page iff the page's scope is in their allowed set.
- **Personal plane** is always private to its employee — no cross-
  employee reads, regardless of policy.
- **``can_propose``** enforces no-escalation: an employee can only
  propose a firm-plane promotion into scopes they themselves have read
  access to. This prevents permission uplift via the promotion path.

Pages carry their scope in frontmatter (``scope: partner-only``). When
absent, the policy's ``default_scope`` applies (typically ``public``).

The policy is a typed object (``Policy``); source format is a markdown
file (``protocols/permissions.md``) that a firm administrator edits.
Markdown parsing lives in ``parse_policy_markdown`` so the typed object
is test-constructible without touching the filesystem.

This module is pure library — no engine integration. Host-agent skills
call ``can_read`` / ``can_propose`` as utility functions before
returning results or staging proposals. Keeping it separate matches the
"host agent owns orchestration" principle and lets the same policy
check land in both the retrieval path and the proposal path without a
tight coupling to ``BrainEngine``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.pages import Page

PUBLIC_SCOPE = "public"


class Scope(BaseModel):
    """One named access scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""


class EmployeeEntry(BaseModel):
    """An employee plus the scopes they can read (and propose into)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: str
    scopes: frozenset[str] = Field(default_factory=frozenset)


class Policy(BaseModel):
    """Per-firm access-control policy.

    ``constitutional_mode`` is the opt-in strict-coherence flag
    (Maciek frame, Step 15). When True, coherence warnings from
    ``promote()`` BLOCK the promotion instead of surfacing advisory.
    Firms that want legal-style governance enable it; firms that
    want the lighter advisory model (default) leave it off. See
    ``knowledge_graph.check_coherence`` for the detection logic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    firm_id: str
    scopes: dict[str, Scope] = Field(default_factory=dict)
    employees: dict[str, EmployeeEntry] = Field(default_factory=dict)
    default_scope: str = PUBLIC_SCOPE
    constitutional_mode: bool = False

    def employee(self, employee_id: str) -> EmployeeEntry | None:
        return self.employees.get(employee_id)

    def has_scope(self, scope_name: str) -> bool:
        return scope_name in self.scopes


def page_scope(page: Page, *, default: str = PUBLIC_SCOPE) -> str:
    """Read the ``scope:`` field from a page's frontmatter, or return the default.

    Pages opt into a stricter scope by adding ``scope: <name>`` to their
    YAML frontmatter. Absence falls back to ``default`` (typically
    ``public``).
    """
    extras = page.frontmatter.model_extra or {}
    raw = extras.get("scope")
    return str(raw) if raw else default


def can_read(policy: Policy, employee_id: str, page: Page) -> bool:
    """Return True if ``employee_id`` is allowed to read ``page`` on the
    firm plane under ``policy``.

    Rules:
    - Unknown employee → False (default deny).
    - Page's scope is ``public`` → always True (public is the baseline
      everyone at the firm sees).
    - Otherwise the scope must be in the employee's allowed scopes.
    - A page tagged with a scope that doesn't exist in the policy →
      False (fail-closed on misconfiguration).

    Note: this function is for firm-plane pages. Personal-plane pages
    are private to their owning employee; callers should short-circuit
    that case without consulting the policy.
    """
    entry = policy.employee(employee_id)
    if entry is None:
        return False
    scope_name = page_scope(page, default=policy.default_scope)
    if scope_name == PUBLIC_SCOPE:
        return True
    if scope_name not in policy.scopes:
        return False
    return scope_name in entry.scopes


def viewer_scopes(policy: Policy, employee_id: str) -> frozenset[str]:
    """Return the effective scope set this employee reads under.

    Includes ``PUBLIC_SCOPE`` implicitly — known employees always see
    public pages. Returns the empty frozenset for unknown employees, so
    KG query filters fail closed when the viewer isn't in the policy.

    Use this at read time whenever you have a policy + employee_id and
    need to filter KG results (``query_entity``, ``query_relationship``,
    ``timeline``) by their ``viewer_scopes`` kwarg.
    """
    entry = policy.employee(employee_id)
    if entry is None:
        return frozenset()
    return frozenset(entry.scopes) | {PUBLIC_SCOPE}


def can_propose(policy: Policy, employee_id: str, proposed_scope: str) -> bool:
    """Return True if ``employee_id`` may propose a firm-plane promotion
    targeting ``proposed_scope`` under ``policy``.

    The no-escalation rule: you can only propose facts into scopes you
    yourself have read access to. Bob, who only has ``public``, cannot
    propose a ``partner-only`` promotion — even if the source he's
    extracting from was somehow shown to him. This blocks the
    classic permission-uplift-via-promotion path.

    Public is a baseline — everyone can propose ``public`` items (all
    employees have it implicitly).
    """
    entry = policy.employee(employee_id)
    if entry is None:
        return False
    if proposed_scope == PUBLIC_SCOPE:
        return True
    if proposed_scope not in policy.scopes:
        return False
    return proposed_scope in entry.scopes


# ---------- Markdown parsing ----------

_SCOPE_HEADING = re.compile(r"^###?\s+scope:\s*(\S+)\s*$", re.MULTILINE)
_EMPLOYEE_HEADING = re.compile(r"^###?\s+employee:\s*(\S+)\s*$", re.MULTILINE)
_FIRM_HEADING = re.compile(r"^##\s+firm:\s*(\S+)\s*$", re.MULTILINE)
_DEFAULT_SCOPE_HEADING = re.compile(r"^##\s+default[_ -]scope:\s*(\S+)\s*$", re.MULTILINE)


def parse_policy_markdown(text: str) -> Policy:
    """Parse a policy file in the minimal markdown form.

    Expected shape (headings are case-insensitive, underscores or hyphens
    accepted between words):

        ## firm: <firm_id>
        ## default_scope: <name>            # optional, defaults to "public"

        ### scope: <name>
        <free-text description until next heading>

        ### employee: <employee_id>
        scopes: [scope-a, scope-b, ...]

    Whitespace is tolerant; missing sections default (no scopes, no
    employees). Duplicate scope or employee ids raise ``ValueError``.
    """
    firm_match = _FIRM_HEADING.search(text)
    if firm_match is None:
        raise ValueError("policy is missing '## firm: <firm_id>' heading")
    firm_id = firm_match.group(1)

    default_match = _DEFAULT_SCOPE_HEADING.search(text)
    default_scope = default_match.group(1) if default_match is not None else PUBLIC_SCOPE

    scopes: dict[str, Scope] = {}
    for name, body in _iter_sections(text, _SCOPE_HEADING):
        if name in scopes:
            raise ValueError(f"duplicate scope {name!r}")
        scopes[name] = Scope(name=name, description=body.strip())

    employees: dict[str, EmployeeEntry] = {}
    for emp_id, body in _iter_sections(text, _EMPLOYEE_HEADING):
        if emp_id in employees:
            raise ValueError(f"duplicate employee {emp_id!r}")
        employees[emp_id] = EmployeeEntry(
            employee_id=emp_id,
            scopes=frozenset(_parse_scope_list(body)),
        )

    return Policy(
        firm_id=firm_id,
        scopes=scopes,
        employees=employees,
        default_scope=default_scope,
    )


def load_policy(path: Path) -> Policy:
    """Read + parse a ``protocols/permissions.md`` file."""
    return parse_policy_markdown(path.read_text(encoding="utf-8"))


# ---------- Internals ----------


def _iter_sections(text: str, heading_re: re.Pattern[str]) -> Iterable[tuple[str, str]]:
    """Yield ``(name, section_body)`` for each heading matched by ``heading_re``."""
    matches = list(heading_re.finditer(text))
    for idx, match in enumerate(matches):
        name = match.group(1)
        body_start = match.end()
        # Stop at the next heading (any level ending a block) or end of text.
        if idx + 1 < len(matches):
            body_end = matches[idx + 1].start()
        else:
            body_end = len(text)
        # Don't eat into sibling top-level sections.
        # Safe approach: read until any line starting with '## ' or '### ' that
        # isn't the *current* heading text.
        yield name, _trim_at_next_top_level(text[body_start:body_end])


_NEXT_TOP_LEVEL = re.compile(r"^(?:##|###)\s", re.MULTILINE)


def _trim_at_next_top_level(block: str) -> str:
    """Stop the section body at the next ``##`` / ``###`` heading if one appears."""
    m = _NEXT_TOP_LEVEL.search(block)
    if m is None:
        return block
    return block[: m.start()]


_SCOPES_LINE = re.compile(r"scopes?\s*:\s*\[([^\]]*)\]", re.IGNORECASE)


def _parse_scope_list(body: str) -> list[str]:
    """Extract ``scopes: [a, b, c]`` from an employee body block."""
    match = _SCOPES_LINE.search(body)
    if match is None:
        return []
    inner = match.group(1)
    return [s.strip() for s in inner.split(",") if s.strip()]


__all__ = [
    "PUBLIC_SCOPE",
    "EmployeeEntry",
    "Policy",
    "Scope",
    "can_propose",
    "can_read",
    "load_policy",
    "page_scope",
    "parse_policy_markdown",
]


# Silence "unused Any" for mypy on the model_extra dict typing.
_: Any = None
