"""Contract tests for overlays/venture/ — the venture overlay (P7-A).

Persistent test coverage for what inline smoke checks verified during
Week 1 of the Joyful Prism plan
(/Users/svenwellmann/.claude/plans/okay-lets-envision-a-joyful-prism.md).

These tests catch drift if anyone edits the overlay files without
re-running the inline checks. The overlay's value depends on its files
being machine-loadable + round-trip-clean — "looks right" markdown is
not enough.

Per the overlays/README.md contract, every overlay ships:

  firm_template.yaml       — load_systems_manifest must accept it
  constitution_seed.md      — parse_page must accept it; tier=constitution
  prompt_examples.md       — pure prose; verified by token presence
  permissions_preset.md    — pure prose template; verified by token presence
  page_templates/*.md       — parse_page must accept each; valid domains + tiers

Tests assert the venture overlay satisfies this contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from memory_mission.ingestion.roles import ConnectorRole
from memory_mission.ingestion.systems_manifest import load_systems_manifest
from memory_mission.memory.pages import parse_page, render_page
from memory_mission.memory.schema import CORE_DOMAINS
from memory_mission.memory.tiers import ALL_TIERS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENTURE = _REPO_ROOT / "overlays" / "venture"

_SAFE_VOCAB_VALUE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------- firm_template.yaml ----------


def test_firm_template_parses_via_load_systems_manifest() -> None:
    manifest = load_systems_manifest(_VENTURE / "firm_template.yaml")
    assert manifest.firm_id, "firm_id must be set"


def test_firm_template_binds_all_six_roles() -> None:
    manifest = load_systems_manifest(_VENTURE / "firm_template.yaml")
    expected = {
        ConnectorRole.EMAIL,
        ConnectorRole.CALENDAR,
        ConnectorRole.TRANSCRIPT,
        ConnectorRole.DOCUMENT,
        ConnectorRole.WORKSPACE,
        ConnectorRole.CHAT,
    }
    assert set(manifest.bindings.keys()) == expected


def test_firm_template_workspace_bound_to_affinity() -> None:
    """Venture default is Affinity; swap to attio is per-firm override."""
    manifest = load_systems_manifest(_VENTURE / "firm_template.yaml")
    assert manifest.binding(ConnectorRole.WORKSPACE).app == "affinity"


def test_firm_template_chat_bound_to_slack_with_firm_default_plane() -> None:
    """Slack DMs/MPDMs override to personal at the helper layer (ADR-0011)."""
    manifest = load_systems_manifest(_VENTURE / "firm_template.yaml")
    chat = manifest.binding(ConnectorRole.CHAT)
    assert chat.app == "slack"
    assert chat.target_plane == "firm"


# ---------- constitution_seed.md ----------


def test_constitution_round_trips() -> None:
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    rerendered = render_page(p)
    p2 = parse_page(rerendered)
    extras2 = p2.frontmatter.model_extra or {}
    assert extras2.keys() == extras.keys(), (
        f"extras drift: lost {set(extras) - set(extras2)}; "
        f"gained {set(extras2) - set(extras)}"
    )


def test_constitution_is_constitution_tier() -> None:
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    assert p.frontmatter.tier == "constitution"
    assert p.frontmatter.domain == "concepts"


def test_constitution_declares_canonical_lifecycle_stages() -> None:
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    stages = extras.get("lifecycle_stages")
    assert isinstance(stages, list) and len(stages) > 0, (
        "lifecycle_stages must be a non-empty list"
    )
    for stage in stages:
        assert isinstance(stage, str) and _SAFE_VOCAB_VALUE.match(stage), (
            f"lifecycle_stages value {stage!r} must be a kebab-or-snake-case identifier"
        )
    # The canonical 9-stage venture lifecycle must include these critical waypoints.
    required = {"sourced", "diligence", "ic", "decision", "portfolio", "passed"}
    assert required.issubset(set(stages)), (
        f"lifecycle_stages missing required waypoints: {required - set(stages)}"
    )


@pytest.mark.parametrize(
    "extras_key,minimum_values",
    [
        ("ddq_statuses", {"not_sent", "sent", "complete"}),
        ("memo_statuses", {"not_started", "draft", "ready_for_ic"}),
        ("ic_statuses", {"scheduled", "decided"}),
        ("closing_statuses", {"not_started", "closed"}),
        ("portfolio_statuses", {"active", "exited"}),
    ],
)
def test_constitution_declares_parallel_sub_state_vocabularies(
    extras_key: str, minimum_values: set[str]
) -> None:
    """Each sub-state machine has its own vocabulary (Hermes-feedback #3)."""
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    vocab = extras.get(extras_key)
    assert isinstance(vocab, list) and len(vocab) > 0, (
        f"{extras_key} must be a non-empty list"
    )
    for value in vocab:
        assert isinstance(value, str) and _SAFE_VOCAB_VALUE.match(value), (
            f"{extras_key} value {value!r} must be a kebab-or-snake-case identifier"
        )
    assert minimum_values.issubset(set(vocab)), (
        f"{extras_key} missing required values: {minimum_values - set(vocab)}"
    )


def test_constitution_declares_ic_quorum() -> None:
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    quorum = extras.get("ic_quorum")
    assert isinstance(quorum, int) and quorum > 0, (
        "ic_quorum must be a positive int"
    )


def test_constitution_declares_decision_rights() -> None:
    text = (_VENTURE / "constitution_seed.md").read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    rights = extras.get("decision_rights")
    assert isinstance(rights, dict) and len(rights) > 0, (
        "decision_rights must be a non-empty mapping"
    )
    # The three authority tiers must be declared.
    assert {"partner_solo", "partner_pair", "ic_full"} <= rights.keys()


# ---------- page templates ----------


_PAGE_TEMPLATES = [
    "deal.md",
    "portfolio_company.md",
    "ic_decision.md",
    "ddq_response.md",
]


@pytest.mark.parametrize("template", _PAGE_TEMPLATES)
def test_page_template_round_trips(template: str) -> None:
    text = (_VENTURE / "page_templates" / template).read_text()
    p = parse_page(text)
    extras = p.frontmatter.model_extra or {}
    rerendered = render_page(p)
    p2 = parse_page(rerendered)
    extras2 = p2.frontmatter.model_extra or {}
    assert extras2.keys() == extras.keys(), (
        f"{template} extras drift: lost {set(extras) - set(extras2)}; "
        f"gained {set(extras2) - set(extras)}"
    )


@pytest.mark.parametrize("template", _PAGE_TEMPLATES)
def test_page_template_uses_core_domain(template: str) -> None:
    text = (_VENTURE / "page_templates" / template).read_text()
    p = parse_page(text)
    assert p.frontmatter.domain in CORE_DOMAINS, (
        f"{template} domain {p.frontmatter.domain!r} not in CORE_DOMAINS"
    )


@pytest.mark.parametrize("template", _PAGE_TEMPLATES)
def test_page_template_uses_valid_tier(template: str) -> None:
    text = (_VENTURE / "page_templates" / template).read_text()
    p = parse_page(text)
    assert p.frontmatter.tier in ALL_TIERS, (
        f"{template} tier {p.frontmatter.tier!r} not in ALL_TIERS"
    )


# ---------- permissions_preset.md ----------


def test_permissions_preset_exists_and_is_non_empty() -> None:
    """Template file is documentation-heavy; operators copy + customize.
    Strict Policy-parsing happens in firm/protocols/permissions.md after
    the operator replaces placeholders; we verify token presence here."""
    text = (_VENTURE / "permissions_preset.md").read_text()
    assert len(text) > 500, "permissions_preset.md should have substantive content"


def test_permissions_preset_contains_role_presets() -> None:
    text = (_VENTURE / "permissions_preset.md").read_text().lower()
    for role in ("partner", "principal", "associate", "ic member", "lp relations"):
        assert role.lower() in text, f"permissions_preset.md missing role preset for {role!r}"


def test_permissions_preset_contains_scope_vocabulary() -> None:
    text = (_VENTURE / "permissions_preset.md").read_text()
    for scope in ("public", "external-shared", "firm-internal", "partner-only", "lp-only"):
        assert scope in text, f"permissions_preset.md missing scope {scope!r}"


# ---------- prompt_examples.md ----------


def test_prompt_examples_exists_and_is_non_empty() -> None:
    text = (_VENTURE / "prompt_examples.md").read_text()
    assert len(text) > 1000, "prompt_examples.md should have substantive content"


def test_prompt_examples_contains_venture_predicate_vocabulary() -> None:
    """The host agent merges prompt_examples.md into EXTRACTION_PROMPT;
    the LLM must see the venture predicate names."""
    text = (_VENTURE / "prompt_examples.md").read_text()
    required_predicates = (
        "lifecycle_status",
        "ic_decision",
        "ddq_status",
        "next_step",
        "co_investor",
        "lead_negotiator",
        "valuation_at_entry",
    )
    for predicate in required_predicates:
        assert predicate in text, (
            f"prompt_examples.md missing predicate {predicate!r} from the venture vocabulary"
        )


def test_prompt_examples_contains_worked_extraction_examples() -> None:
    """Worked examples are how the LLM learns; markers it should always have."""
    text = (_VENTURE / "prompt_examples.md").read_text().lower()
    assert "worked example" in text, "prompt_examples.md missing 'Worked example' headings"
    assert "support_quote" in text, "prompt_examples.md missing 'support_quote' guidance"
