"""Step 17 tests — ``compile_agent_context`` and ``AgentContext`` rendering.

Covers the binary criteria in ``docs/EVALS.md`` section 2.8:

1. Attendees correctly identified
2. Most recent relevant interaction surfaced
3. No superseded facts (invalidated triples omitted)
4. At least one load-bearing fact per attendee when data exists
5. Doctrine context respects tier_floor

Plus the plumbing tests: empty attendees, no-engine path, canonical
name via IdentityResolver, render shape, round-trip JSON.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.identity import LocalIdentityResolver
from memory_mission.memory import InMemoryEngine, KnowledgeGraph, new_page
from memory_mission.observability import observability_scope
from memory_mission.synthesis import (
    AgentContext,
    AttendeeContext,
    DoctrineContext,
    compile_agent_context,
)

# ---------- Fixtures ----------


@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph(tmp_path / "kg.sqlite3")


@pytest.fixture
def engine() -> InMemoryEngine:
    return InMemoryEngine()


# ---------- Attendee resolution ----------


def test_compile_returns_empty_attendee_when_kg_has_no_data(
    kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    assert len(ctx.attendees) == 1
    attendee = ctx.attendees[0]
    assert attendee.attendee_id == "sarah-chen"
    assert attendee.canonical_name is None
    assert attendee.fact_count == 0
    assert attendee.display_name == "sarah-chen"


def test_compile_classifies_triples_by_predicate(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Outgoing relationships go to outgoing_triples; ``event`` predicate
    goes to events; ``prefers`` goes to preferences."""
    kg.add_triple("sarah-chen", "works_at", "acme-corp")
    kg.add_triple("sarah-chen", "prefers", "morning-meetings")
    kg.add_triple(
        "sarah-chen",
        "event",
        "board meeting Q3",
        valid_from=date(2026, 3, 15),
    )
    # Incoming: bob reports_to sarah
    kg.add_triple("bob", "reports_to", "sarah-chen")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    [attendee] = ctx.attendees
    assert len(attendee.outgoing_triples) == 1
    assert attendee.outgoing_triples[0].predicate == "works_at"
    assert len(attendee.preferences) == 1
    assert attendee.preferences[0].object == "morning-meetings"
    assert len(attendee.events) == 1
    assert attendee.events[0].object == "board meeting Q3"
    assert len(attendee.incoming_triples) == 1
    assert attendee.incoming_triples[0].subject == "bob"
    assert attendee.fact_count == 4


def test_compile_omits_superseded_facts(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Invalidated triples MUST NOT appear in the attendee context.

    Per EVALS.md 2.8 criterion 3 — this is a hard requirement.
    """
    kg.add_triple("sarah-chen", "works_at", "acme-corp", valid_from=date(2020, 1, 1))
    # She moved to beta
    kg.invalidate("sarah-chen", "works_at", "acme-corp", ended=date(2026, 3, 15))
    kg.add_triple("sarah-chen", "works_at", "beta-fund", valid_from=date(2026, 3, 16))

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    [attendee] = ctx.attendees
    # Only the current job; the old one is invalidated
    assert len(attendee.outgoing_triples) == 1
    assert attendee.outgoing_triples[0].object == "beta-fund"


def test_compile_events_sorted_newest_first(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Recent interactions surface before older ones (EVALS.md 2.8 crit 2)."""
    today = date(2026, 4, 22)
    kg.add_triple(
        "sarah-chen",
        "event",
        "last week's call",
        valid_from=today - timedelta(days=7),
    )
    kg.add_triple(
        "sarah-chen",
        "event",
        "yesterday's email",
        valid_from=today - timedelta(days=1),
    )
    kg.add_triple(
        "sarah-chen",
        "event",
        "old onboarding note",
        valid_from=today - timedelta(days=365),
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    [attendee] = ctx.attendees
    descriptions = [e.object for e in attendee.events]
    assert descriptions == [
        "yesterday's email",
        "last week's call",
        "old onboarding note",
    ]


def test_compile_resolves_canonical_name_from_resolver(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """When an IdentityResolver is wired, display name uses canonical_name."""
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    alice_id = resolver.resolve(
        {"email:sarah@acme.com"},
        canonical_name="Sarah Chen",
    )
    kg.add_triple(alice_id, "works_at", "acme-corp")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[alice_id],
            kg=kg,
            identity_resolver=resolver,
        )
    [attendee] = ctx.attendees
    assert attendee.canonical_name == "Sarah Chen"
    assert attendee.display_name == "Sarah Chen"


def test_compile_handles_multiple_attendees_independently(
    kg: KnowledgeGraph, tmp_path: Path
) -> None:
    kg.add_triple("sarah", "works_at", "acme")
    kg.add_triple("bob", "reports_to", "sarah")
    kg.add_triple("bob", "prefers", "async-review")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah", "bob"],
            kg=kg,
        )
    assert ctx.attendee_ids == ["sarah", "bob"]
    sarah, bob = ctx.attendees
    # Each attendee's facts are scoped to them
    assert len(sarah.outgoing_triples) == 1
    assert len(sarah.incoming_triples) == 1
    assert len(bob.preferences) == 1
    assert len(bob.outgoing_triples) == 1  # reports_to


def test_compile_time_travels_via_as_of(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """``as_of`` restricts triples to those valid on the given date."""
    kg.add_triple(
        "sarah-chen",
        "works_at",
        "acme-corp",
        valid_from=date(2020, 1, 1),
        valid_to=date(2025, 1, 1),
    )
    kg.add_triple("sarah-chen", "works_at", "beta-fund", valid_from=date(2025, 1, 2))

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        # In 2023, she was at acme
        past = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
            as_of=date(2023, 6, 1),
        )
        # In 2026, she's at beta
        now = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
            as_of=date(2026, 4, 22),
        )
    assert past.attendees[0].outgoing_triples[0].object == "acme-corp"
    assert now.attendees[0].outgoing_triples[0].object == "beta-fund"


# ---------- Doctrine filtering ----------


def test_doctrine_empty_without_engine(kg: KnowledgeGraph, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[],
            kg=kg,
            tier_floor="doctrine",
        )
    assert ctx.doctrine.pages == []
    assert ctx.doctrine.page_count == 0


def test_doctrine_empty_without_tier_floor(
    kg: KnowledgeGraph, engine: InMemoryEngine, tmp_path: Path
) -> None:
    """No tier_floor = caller opted out of doctrine."""
    engine.put_page(
        new_page(
            slug="mission",
            title="Mission",
            domain="concepts",
            tier="constitution",
        ),
        plane="firm",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[],
            kg=kg,
            engine=engine,
        )
    assert ctx.doctrine.pages == []


def test_doctrine_filters_by_tier_floor(
    kg: KnowledgeGraph, engine: InMemoryEngine, tmp_path: Path
) -> None:
    engine.put_page(
        new_page(
            slug="mission",
            title="Firm Mission",
            domain="concepts",
            tier="constitution",
        ),
        plane="firm",
    )
    engine.put_page(
        new_page(
            slug="thesis",
            title="Investment Thesis",
            domain="concepts",
            tier="doctrine",
        ),
        plane="firm",
    )
    engine.put_page(
        new_page(
            slug="review-cadence",
            title="Allocation Review Cadence",
            domain="concepts",
            tier="policy",
        ),
        plane="firm",
    )
    engine.put_page(
        new_page(
            slug="sarah",
            title="Sarah Chen",
            domain="people",
            tier="decision",
        ),
        plane="firm",
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[],
            kg=kg,
            engine=engine,
            tier_floor="doctrine",
        )
    slugs = [p.frontmatter.slug for p in ctx.doctrine.pages]
    # Constitution + doctrine, sorted highest-tier first
    assert slugs == ["mission", "thesis"]


def test_doctrine_highest_tier_first(
    kg: KnowledgeGraph, engine: InMemoryEngine, tmp_path: Path
) -> None:
    engine.put_page(
        new_page(slug="policy-one", title="P1", domain="concepts", tier="policy"),
        plane="firm",
    )
    engine.put_page(
        new_page(slug="doctrine-one", title="D1", domain="concepts", tier="doctrine"),
        plane="firm",
    )
    engine.put_page(
        new_page(slug="mission", title="M", domain="concepts", tier="constitution"),
        plane="firm",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[],
            kg=kg,
            engine=engine,
            tier_floor="policy",
        )
    slugs = [p.frontmatter.slug for p in ctx.doctrine.pages]
    assert slugs == ["mission", "doctrine-one", "policy-one"]


# ---------- Related pages ----------


def test_related_page_fetched_when_slug_matches_attendee_id(
    kg: KnowledgeGraph, engine: InMemoryEngine, tmp_path: Path
) -> None:
    """V1: if a curated page exists at slug == attendee_id, attach it."""
    engine.put_page(
        new_page(slug="sarah-chen", title="Sarah Chen", domain="people"),
        plane="firm",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
            engine=engine,
        )
    [attendee] = ctx.attendees
    assert len(attendee.related_pages) == 1
    assert attendee.related_pages[0].frontmatter.slug == "sarah-chen"


# ---------- Rendering ----------


def test_render_produces_markdown_with_role_and_task(kg: KnowledgeGraph, tmp_path: Path) -> None:
    kg.add_triple("sarah-chen", "works_at", "acme-corp")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep for the Q3 review with Acme",
            attendees=["sarah-chen"],
            kg=kg,
        )
    rendered = ctx.render()
    assert "# Context for meeting-prep" in rendered
    assert "prep for the Q3 review with Acme" in rendered
    assert "sarah-chen" in rendered
    assert "works_at" in rendered


def test_render_shows_empty_groups_explicitly(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Empty groups render as '(none on file)' so the LLM sees absence."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    rendered = ctx.render()
    assert "(none on file)" in rendered
    assert "sarah-chen" in rendered


def test_render_includes_doctrine_when_populated(
    kg: KnowledgeGraph, engine: InMemoryEngine, tmp_path: Path
) -> None:
    engine.put_page(
        new_page(
            slug="mission",
            title="Firm Mission",
            domain="concepts",
            compiled_truth="Preserve client capital over generations.",
            tier="constitution",
        ),
        plane="firm",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=[],
            kg=kg,
            engine=engine,
            tier_floor="doctrine",
        )
    rendered = ctx.render()
    assert "## Firm doctrine" in rendered
    assert "Firm Mission" in rendered
    assert "Preserve client capital" in rendered


def test_render_no_attendees_renders_placeholder(kg: KnowledgeGraph, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="no attendees",
            attendees=[],
            kg=kg,
        )
    rendered = ctx.render()
    assert "## Attendees" in rendered
    assert "No attendees specified" in rendered


def test_render_includes_provenance_citations(kg: KnowledgeGraph, tmp_path: Path) -> None:
    kg.add_triple(
        "sarah-chen",
        "works_at",
        "acme-corp",
        source_closet="firm",
        source_file="/sources/onboarding.md",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    rendered = ctx.render()
    assert "firm/sources/onboarding.md" in rendered


# ---------- Round-trip + structural ----------


def test_agent_context_round_trips_through_json(kg: KnowledgeGraph, tmp_path: Path) -> None:
    kg.add_triple("sarah", "works_at", "acme")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah"],
            kg=kg,
        )
    dumped = ctx.model_dump_json()
    reloaded = AgentContext.model_validate_json(dumped)
    assert reloaded.role == "meeting-prep"
    assert reloaded.attendees[0].outgoing_triples[0].object == "acme"


def test_agent_context_fact_count_aggregates(kg: KnowledgeGraph, tmp_path: Path) -> None:
    kg.add_triple("sarah", "works_at", "acme")
    kg.add_triple("sarah", "knows", "bob")
    kg.add_triple("mark", "works_at", "gamma")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah", "mark"],
            kg=kg,
        )
    assert ctx.fact_count == 3
    assert ctx.attendees[0].fact_count == 2
    assert ctx.attendees[1].fact_count == 1


def test_attendee_context_is_frozen() -> None:
    a = AttendeeContext(attendee_id="test")
    with pytest.raises(ValidationError):
        a.attendee_id = "mutated"  # type: ignore[misc]


def test_doctrine_context_default_empty() -> None:
    d = DoctrineContext()
    assert d.pages == []
    assert d.page_count == 0


# ---------- Move 3: Contradiction callout ----------


def _seed_coherence_warning(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Helper — set up a doctrine/decision conflict that will fire a
    CoherenceWarningEvent when promoted."""
    from memory_mission.extraction import IdentityFact, RelationshipFact
    from memory_mission.promotion import ProposalStore, create_proposal, promote

    kg.add_triple("sarah-chen", "works_at", "acme-corp", tier="doctrine")
    store = ProposalStore(tmp_path / "proposals.sqlite3")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                IdentityFact(confidence=0.9, support_quote="sarah", entity_name="sarah-chen"),
                RelationshipFact(
                    confidence=0.9,
                    support_quote="new info",
                    subject="sarah-chen",
                    predicate="works_at",
                    object="beta-fund",
                ),
            ],
            source_report_path="/tmp/new.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="r", rationale="advisory")


def test_compile_populates_coherence_warnings(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """When the observability log has a CoherenceWarningEvent for an
    attendee, compile_agent_context surfaces it on the AttendeeContext."""
    _seed_coherence_warning(kg, tmp_path)

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    [attendee] = ctx.attendees
    assert len(attendee.coherence_warnings) == 1
    warning = attendee.coherence_warnings[0]
    assert warning.subject == "sarah-chen"
    assert warning.predicate == "works_at"
    # The triple was corroborated after landing, so both directions show:
    # new_object is the conflicting proposal; conflicting is the doctrine value
    assert "acme-corp" in {warning.new_object, warning.conflicting_object}
    assert "beta-fund" in {warning.new_object, warning.conflicting_object}


def test_render_emits_contradiction_callout_when_warnings_present(
    kg: KnowledgeGraph, tmp_path: Path
) -> None:
    _seed_coherence_warning(kg, tmp_path)

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    rendered = ctx.render()
    assert "[!contradiction]" in rendered
    assert "Unresolved tier conflict" in rendered


def test_render_no_callout_without_warnings(kg: KnowledgeGraph, tmp_path: Path) -> None:
    """Absent warnings: no callout in output."""
    kg.add_triple("sarah-chen", "works_at", "acme-corp")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        ctx = compile_agent_context(
            role="meeting-prep",
            task="prep",
            attendees=["sarah-chen"],
            kg=kg,
        )
    rendered = ctx.render()
    assert "[!contradiction]" not in rendered


def test_compile_handles_missing_observability_scope(
    kg: KnowledgeGraph,
) -> None:
    """compile_agent_context outside any scope degrades gracefully —
    coherence_warnings comes back empty, no crash."""
    kg.add_triple("sarah-chen", "works_at", "acme-corp")
    ctx = compile_agent_context(
        role="meeting-prep",
        task="prep",
        attendees=["sarah-chen"],
        kg=kg,
    )
    assert ctx.attendees[0].coherence_warnings == []


def test_render_page_emits_contradiction_callout() -> None:
    """render_page accepts an optional coherence_warnings kwarg."""
    from memory_mission.memory import CoherenceWarning
    from memory_mission.memory.pages import render_page

    page = new_page(
        slug="sarah-chen",
        title="Sarah Chen",
        domain="people",
        compiled_truth="Sarah works at Acme.",
    )
    warning = CoherenceWarning(
        subject="sarah-chen",
        predicate="works_at",
        new_object="beta-fund",
        new_tier="decision",
        conflicting_object="acme-corp",
        conflicting_tier="doctrine",
    )
    rendered = render_page(page, coherence_warnings=[warning])
    assert "[!contradiction]" in rendered
    assert "sarah-chen works_at = beta-fund" in rendered
    assert "sarah-chen works_at = acme-corp" in rendered
    # Callout appears above the body, not in the timeline / frontmatter
    callout_line = rendered.index("[!contradiction]")
    body_line = rendered.index("Sarah works at Acme")
    assert callout_line < body_line


def test_render_page_no_callout_by_default() -> None:
    """Pages with no warnings kwarg render as before — no callout."""
    from memory_mission.memory.pages import render_page

    page = new_page(
        slug="x",
        title="X",
        domain="concepts",
        compiled_truth="body",
    )
    assert "[!contradiction]" not in render_page(page)
