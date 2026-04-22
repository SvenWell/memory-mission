"""Tests for the per-firm access-control policy (step 8b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.memory import PageFrontmatter, new_page
from memory_mission.permissions import (
    PUBLIC_SCOPE,
    EmployeeEntry,
    Policy,
    Scope,
    can_propose,
    can_read,
    load_policy,
    page_scope,
    parse_policy_markdown,
)

# ---------- Helpers ----------


def _policy_with_scopes(**employees: list[str]) -> Policy:
    """Build a policy with a partner-only scope + one employee per kwarg."""
    return Policy(
        firm_id="acme",
        scopes={
            "public": Scope(name="public"),
            "partner-only": Scope(name="partner-only"),
            "client-confidential": Scope(name="client-confidential"),
        },
        employees={
            emp: EmployeeEntry(employee_id=emp, scopes=frozenset(scopes))
            for emp, scopes in employees.items()
        },
    )


def _firm_page(slug: str = "acme-corp", scope: str | None = None):
    """A firm-plane page with an optional ``scope`` in frontmatter extras."""
    extras = {"scope": scope} if scope else {}
    return new_page(
        slug=slug,
        title=slug.replace("-", " ").title(),
        domain="companies",
        compiled_truth="body",
    ).model_copy(
        update={
            "frontmatter": PageFrontmatter(
                slug=slug,
                title=slug.replace("-", " ").title(),
                domain="companies",
                **extras,
            )
        }
    )


# ---------- page_scope ----------


def test_page_scope_returns_public_when_missing() -> None:
    assert page_scope(_firm_page("p")) == PUBLIC_SCOPE


def test_page_scope_reads_frontmatter_extra() -> None:
    page = _firm_page("p", scope="partner-only")
    assert page_scope(page) == "partner-only"


def test_page_scope_respects_custom_default() -> None:
    """Default_scope is a per-policy choice, passed through."""
    assert page_scope(_firm_page("p"), default="restricted") == "restricted"


# ---------- can_read ----------


def test_can_read_unknown_employee_denies() -> None:
    policy = _policy_with_scopes(alice=["public", "partner-only"])
    assert can_read(policy, "nobody", _firm_page("p")) is False


def test_can_read_public_scope_always_allowed() -> None:
    """Even carol without partner-only access can see public firm info."""
    policy = _policy_with_scopes(
        carol=["public"],
        alice=["public", "partner-only"],
    )
    public_page = _firm_page("p", scope="public")
    assert can_read(policy, "carol", public_page) is True
    assert can_read(policy, "alice", public_page) is True


def test_can_read_restricted_scope_blocked_for_employee_without_it() -> None:
    policy = _policy_with_scopes(bob=["public"], alice=["public", "partner-only"])
    page = _firm_page("deal-memo", scope="partner-only")
    assert can_read(policy, "bob", page) is False
    assert can_read(policy, "alice", page) is True


def test_can_read_unknown_scope_denies() -> None:
    """Fail-closed on pages tagged with a scope the policy doesn't know."""
    policy = _policy_with_scopes(alice=["public", "partner-only"])
    misconfigured = _firm_page("p", scope="made-up-scope")
    assert can_read(policy, "alice", misconfigured) is False


def test_can_read_falls_back_to_default_scope_when_page_unscoped() -> None:
    """An unscoped page on a 'partner-only default' firm must require partner access."""
    policy = Policy(
        firm_id="acme",
        scopes={
            "public": Scope(name="public"),
            "partner-only": Scope(name="partner-only"),
        },
        employees={
            "alice": EmployeeEntry(
                employee_id="alice", scopes=frozenset({"public", "partner-only"})
            ),
            "bob": EmployeeEntry(employee_id="bob", scopes=frozenset({"public"})),
        },
        default_scope="partner-only",
    )
    unscoped = _firm_page("p")  # no scope in frontmatter
    assert can_read(policy, "alice", unscoped) is True
    assert can_read(policy, "bob", unscoped) is False


# ---------- can_propose ----------


def test_can_propose_unknown_employee_denies() -> None:
    policy = _policy_with_scopes(alice=["public"])
    assert can_propose(policy, "nobody", "public") is False


def test_can_propose_public_always_allowed_for_known_employees() -> None:
    policy = _policy_with_scopes(bob=["public"])
    assert can_propose(policy, "bob", "public") is True


def test_can_propose_blocks_escalation() -> None:
    """Bob (public only) cannot propose a partner-only promotion."""
    policy = _policy_with_scopes(bob=["public"], alice=["public", "partner-only"])
    assert can_propose(policy, "bob", "partner-only") is False
    assert can_propose(policy, "alice", "partner-only") is True


def test_can_propose_unknown_scope_denies() -> None:
    policy = _policy_with_scopes(alice=["public", "partner-only"])
    assert can_propose(policy, "alice", "made-up") is False


def test_can_propose_allows_exactly_their_scopes() -> None:
    policy = _policy_with_scopes(alice=["public", "partner-only", "client-confidential"])
    for scope in ("public", "partner-only", "client-confidential"):
        assert can_propose(policy, "alice", scope) is True


# ---------- Markdown parsing ----------


VALID_POLICY_MD = """\
# Firm Permissions

## firm: acme-capital

## default_scope: public

### scope: public

Baseline scope everyone has.

### scope: partner-only

Partner-level strategy.

### scope: deal-team

Deal-specific working notes.

### employee: alice

scopes: [public, partner-only, deal-team]

### employee: bob

scopes: [public, deal-team]

### employee: carol

scopes: [public]
"""


def test_parse_policy_markdown_firm_and_default_scope() -> None:
    policy = parse_policy_markdown(VALID_POLICY_MD)
    assert policy.firm_id == "acme-capital"
    assert policy.default_scope == "public"


def test_parse_policy_markdown_all_scopes_parsed() -> None:
    policy = parse_policy_markdown(VALID_POLICY_MD)
    assert set(policy.scopes) == {"public", "partner-only", "deal-team"}
    assert policy.scopes["partner-only"].description == "Partner-level strategy."


def test_parse_policy_markdown_employees_and_scope_lists() -> None:
    policy = parse_policy_markdown(VALID_POLICY_MD)
    assert set(policy.employees) == {"alice", "bob", "carol"}
    assert policy.employees["alice"].scopes == frozenset({"public", "partner-only", "deal-team"})
    assert policy.employees["bob"].scopes == frozenset({"public", "deal-team"})
    assert policy.employees["carol"].scopes == frozenset({"public"})


def test_parse_policy_rejects_missing_firm_heading() -> None:
    with pytest.raises(ValueError, match="firm"):
        parse_policy_markdown("## default_scope: public\n")


def test_parse_policy_rejects_duplicate_scope() -> None:
    md = """
## firm: acme

### scope: partner-only
first

### scope: partner-only
second
"""
    with pytest.raises(ValueError, match="duplicate scope"):
        parse_policy_markdown(md)


def test_parse_policy_rejects_duplicate_employee() -> None:
    md = """
## firm: acme

### employee: alice
scopes: [public]

### employee: alice
scopes: [public, partner-only]
"""
    with pytest.raises(ValueError, match="duplicate employee"):
        parse_policy_markdown(md)


def test_parse_policy_employees_without_scopes_line_default_empty() -> None:
    md = """
## firm: acme

### employee: alice

(no scope list yet)
"""
    policy = parse_policy_markdown(md)
    assert policy.employees["alice"].scopes == frozenset()


def test_parse_policy_default_scope_defaults_to_public() -> None:
    md = """
## firm: acme
"""
    policy = parse_policy_markdown(md)
    assert policy.default_scope == PUBLIC_SCOPE


# ---------- Template + load_policy ----------


def test_template_file_exists_at_repo_root() -> None:
    template = Path(__file__).resolve().parent.parent / "protocols" / "permissions.md.template"
    assert template.is_file()


def test_template_parses_as_valid_policy() -> None:
    template = Path(__file__).resolve().parent.parent / "protocols" / "permissions.md.template"
    policy = load_policy(template)
    assert policy.firm_id == "acme-capital"
    assert "public" in policy.scopes
    assert "partner-only" in policy.scopes
    # Template employees demonstrate all three access tiers.
    assert "alice" in policy.employees
    assert "carol" in policy.employees


def test_load_policy_from_written_file(tmp_path: Path) -> None:
    path = tmp_path / "permissions.md"
    path.write_text(VALID_POLICY_MD)
    policy = load_policy(path)
    assert policy.firm_id == "acme-capital"


# ---------- Integration: template-driven reads ----------


def test_template_scope_enforcement_end_to_end(tmp_path: Path) -> None:
    """The shipped template enforces partner-only on partner pages, public on public."""
    template = Path(__file__).resolve().parent.parent / "protocols" / "permissions.md.template"
    policy = load_policy(template)

    partner_page = _firm_page("thesis", scope="partner-only")
    public_page = _firm_page("industry-report", scope="public")

    # alice = partner; bob = associate (no partner-only); carol = public only
    assert can_read(policy, "alice", partner_page) is True
    assert can_read(policy, "bob", partner_page) is False
    assert can_read(policy, "carol", partner_page) is False

    assert can_read(policy, "alice", public_page) is True
    assert can_read(policy, "bob", public_page) is True
    assert can_read(policy, "carol", public_page) is True

    # No-escalation: carol can only propose public
    assert can_propose(policy, "carol", "public") is True
    assert can_propose(policy, "carol", "partner-only") is False
