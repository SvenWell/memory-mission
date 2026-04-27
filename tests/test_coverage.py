"""Tests for the context-farming coverage primitives (ADR-0012).

Each of the 5 farming primitives gets:

- A happy-path fixture exercising the typical operator question
- An empty-substrate case (so the primitive doesn't crash on a fresh firm)
- A threshold-edge case where applicable
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from memory_mission.memory.engine import InMemoryEngine
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.memory.pages import new_page
from memory_mission.promotion.proposals import Proposal, ProposalStore
from memory_mission.synthesis.coverage import (
    AttributionDebt,
    DecayedPage,
    DomainCoverage,
    LowCorroborationCluster,
    MissingPageCoverage,
    compute_domain_coverage,
    find_attribution_debt,
    find_decayed_pages,
    find_low_corroboration_clusters,
    find_missing_page_coverage,
)

# ---------- 1. Per-domain coverage ----------


def test_compute_domain_coverage_groups_by_domain_and_tier() -> None:
    engine = InMemoryEngine()
    engine.put_page(
        new_page(slug="acme-corp", title="Acme", domain="companies", tier="doctrine"),
        plane="firm",
    )
    engine.put_page(
        new_page(slug="beta-corp", title="Beta", domain="companies", tier="decision"),
        plane="firm",
    )
    engine.put_page(
        new_page(slug="charlie", title="Charlie", domain="people", tier="decision"),
        plane="firm",
    )

    out = compute_domain_coverage(engine, plane="firm")

    by_domain = {c.domain: c for c in out}
    assert by_domain["companies"].page_count == 2
    assert by_domain["companies"].by_tier == {"doctrine": 1, "decision": 1}
    assert by_domain["people"].page_count == 1
    assert by_domain["people"].by_tier == {"decision": 1}
    # Sorted by page_count descending — companies comes before people
    assert out[0].domain == "companies"


def test_compute_domain_coverage_empty_engine_returns_empty_list() -> None:
    engine = InMemoryEngine()
    assert compute_domain_coverage(engine, plane="firm") == []


# ---------- 2. Decay flags ----------


def test_find_decayed_pages_flags_old_doctrine() -> None:
    engine = InMemoryEngine()
    # Doctrine-tier page with valid_from far in the past — decayed
    engine.put_page(
        new_page(
            slug="old-doctrine",
            title="Old Doctrine",
            domain="concepts",
            tier="doctrine",
            valid_from=date(2025, 1, 1),
        ),
        plane="firm",
    )
    # Decision-tier page same age — under tier floor, ignored
    engine.put_page(
        new_page(
            slug="old-decision",
            title="Old Decision",
            domain="concepts",
            tier="decision",
            valid_from=date(2025, 1, 1),
        ),
        plane="firm",
    )

    out = find_decayed_pages(engine, plane="firm", min_age_days=90)

    slugs = [d.slug for d in out]
    assert "old-doctrine" in slugs
    assert "old-decision" not in slugs  # tier-floor filter
    decayed = next(d for d in out if d.slug == "old-doctrine")
    assert decayed.tier == "doctrine"
    assert decayed.age_days >= 90


def test_find_decayed_pages_threshold_edge() -> None:
    engine = InMemoryEngine()
    fixed_now = datetime(2026, 4, 27, tzinfo=UTC)
    # Exactly 90 days old → at the boundary; the function uses
    # `age < threshold` so equal returns the page (decayed).
    boundary = (fixed_now - timedelta(days=90)).date()
    engine.put_page(
        new_page(
            slug="ninety-day-doctrine",
            title="Ninety Day",
            domain="concepts",
            tier="doctrine",
            valid_from=boundary,
        ),
        plane="firm",
    )
    out = find_decayed_pages(engine, plane="firm", min_age_days=90, now=fixed_now)
    assert len(out) == 1
    assert out[0].slug == "ninety-day-doctrine"


def test_find_decayed_pages_skips_pages_with_no_temporal_signal() -> None:
    engine = InMemoryEngine()
    # No valid_from, no reviewed_at → "never touched" — function returns nothing for it
    engine.put_page(
        new_page(
            slug="undated-doctrine",
            title="Undated",
            domain="concepts",
            tier="doctrine",
        ),
        plane="firm",
    )
    assert find_decayed_pages(engine, plane="firm") == []


def test_find_decayed_pages_empty_engine() -> None:
    engine = InMemoryEngine()
    assert find_decayed_pages(engine, plane="firm") == []


# ---------- 3. Missing page coverage ----------


def test_find_missing_page_coverage_surfaces_under_documented_entities(
    tmp_path: Path,
) -> None:
    engine = InMemoryEngine()
    # Existing doctrine page for charlie — should NOT surface
    engine.put_page(
        new_page(slug="charlie", title="Charlie", domain="people", tier="doctrine"),
        plane="firm",
    )

    kg = KnowledgeGraph(tmp_path / "kg.db")
    # acme: 4 mentions across triples → exceeds default threshold (3)
    kg.add_triple("acme", "raised_at", "20m", confidence=0.9)
    kg.add_triple("acme", "founded_by", "alice", confidence=0.9)
    kg.add_triple("acme", "based_in", "sf", confidence=0.9)
    kg.add_triple("alice", "works_at", "acme", confidence=0.9)
    # charlie: 3 mentions but has a page → should NOT surface
    kg.add_triple("charlie", "works_at", "acme", confidence=0.9)
    kg.add_triple("charlie", "advises", "acme", confidence=0.9)
    kg.add_triple("charlie", "based_in", "ny", confidence=0.9)

    out = find_missing_page_coverage(engine, kg, plane="firm")

    surfaced = {m.entity_name: m for m in out}
    assert "acme" in surfaced
    assert surfaced["acme"].triple_mention_count >= 3
    assert "charlie" not in surfaced  # has doctrine page
    kg.close()


def test_find_missing_page_coverage_includes_proposals(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    kg = KnowledgeGraph(tmp_path / "kg.db")
    store = ProposalStore(tmp_path / "proposals.db")

    # delta has 0 triple mentions but appears in 2 proposals
    for i in range(2):
        store.insert(
            Proposal(
                proposal_id=f"prop-{i}",
                target_plane="firm",
                target_scope="public",
                target_entity="delta",
                proposer_agent_id="agent-1",
                proposer_employee_id="emp-1",
                facts=[],
                source_report_path=f"/tmp/report-{i}.md",
            )
        )

    out = find_missing_page_coverage(
        engine,
        kg,
        store,
        plane="firm",
        min_triple_mentions=999,  # disable triple threshold
        min_proposal_mentions=2,
    )
    delta = next(m for m in out if m.entity_name == "delta")
    assert delta.proposal_mention_count == 2
    kg.close()
    store.close()


def test_find_missing_page_coverage_empty_substrate(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    kg = KnowledgeGraph(tmp_path / "kg.db")
    assert find_missing_page_coverage(engine, kg, plane="firm") == []
    kg.close()


# ---------- 4. Source-attribution debt ----------


def test_find_attribution_debt_surfaces_unprovenanced_triples(
    tmp_path: Path,
) -> None:
    kg = KnowledgeGraph(tmp_path / "kg.db")
    # Fully-provenanced — should NOT surface
    kg.add_triple(
        "acme",
        "raised_at",
        "20m",
        source_closet="firm/companies",
        source_file="companies/acme.md",
    )
    # Missing source_file
    kg.add_triple("acme", "based_in", "sf", source_closet="firm/companies")
    # Missing both
    kg.add_triple("alice", "works_at", "acme")

    out = find_attribution_debt(kg)

    triples = {(d.subject, d.predicate, d.object): d for d in out}
    assert ("acme", "based_in", "sf") in triples
    assert ("alice", "works_at", "acme") in triples
    assert ("acme", "raised_at", "20m") not in triples  # fully provenanced

    # Validate the flags on the surfaced rows
    based_in = triples[("acme", "based_in", "sf")]
    assert based_in.has_source_closet is True
    assert based_in.has_source_file is False
    works_at = triples[("alice", "works_at", "acme")]
    assert works_at.has_source_closet is False
    assert works_at.has_source_file is False
    kg.close()


def test_find_attribution_debt_empty_kg(tmp_path: Path) -> None:
    kg = KnowledgeGraph(tmp_path / "kg.db")
    assert find_attribution_debt(kg) == []
    kg.close()


def test_find_attribution_debt_excludes_invalidated_triples(
    tmp_path: Path,
) -> None:
    kg = KnowledgeGraph(tmp_path / "kg.db")
    kg.add_triple("acme", "based_in", "sf")  # missing provenance
    kg.invalidate("acme", "based_in", "sf")  # now invalidated
    # Still currently-true? No — valid_to was set
    out = find_attribution_debt(kg)
    assert out == []
    kg.close()


# ---------- 5. Low-corroboration concentrations ----------


def test_find_low_corroboration_clusters_groups_weak_evidence(
    tmp_path: Path,
) -> None:
    kg = KnowledgeGraph(tmp_path / "kg.db")
    # acme has 4 weak triples — exceeds min_cluster_size=3
    kg.add_triple("acme", "raised_at", "20m", confidence=0.5)
    kg.add_triple("acme", "based_in", "sf", confidence=0.4)
    kg.add_triple("acme", "founded_by", "alice", confidence=0.6)
    kg.add_triple("acme", "uses_tool", "stripe", confidence=0.3)
    # delta has 1 weak triple — below cluster size, ignored
    kg.add_triple("delta", "based_in", "ny", confidence=0.4)
    # beta has confident triples — ignored
    kg.add_triple("beta", "raised_at", "10m", confidence=0.95)
    kg.add_triple("beta", "based_in", "la", confidence=0.92)
    kg.add_triple("beta", "founded_by", "bob", confidence=0.91)

    out = find_low_corroboration_clusters(kg, confidence_floor=0.7, min_cluster_size=3)

    by_entity = {c.entity_name: c for c in out}
    assert "acme" in by_entity
    acme = by_entity["acme"]
    assert acme.weak_triple_count >= 4
    assert acme.weakest_confidence == 0.3
    assert "delta" not in by_entity  # below cluster size
    assert "beta" not in by_entity  # all confident
    kg.close()


def test_find_low_corroboration_clusters_empty_kg(tmp_path: Path) -> None:
    kg = KnowledgeGraph(tmp_path / "kg.db")
    assert find_low_corroboration_clusters(kg) == []
    kg.close()


def test_find_low_corroboration_clusters_threshold_inclusive(
    tmp_path: Path,
) -> None:
    """The floor is strict: confidence must be < floor to count as weak."""
    kg = KnowledgeGraph(tmp_path / "kg.db")
    # All exactly at floor → not weak
    for i in range(3):
        kg.add_triple("acme", f"pred-{i}", f"obj-{i}", confidence=0.7)
    out = find_low_corroboration_clusters(kg, confidence_floor=0.7, min_cluster_size=3)
    assert out == []
    kg.close()


# ---------- Pydantic shape sanity ----------


def test_aggregate_models_are_frozen() -> None:
    """All aggregates are frozen — operator can't mutate them by accident."""
    domain = DomainCoverage(domain="people", page_count=1, by_tier={"decision": 1})
    decayed = DecayedPage(slug="x", domain="x", tier="doctrine", age_days=100)
    missing = MissingPageCoverage(entity_name="x", triple_mention_count=3)
    debt = AttributionDebt(
        subject="x", predicate="x", object="x", has_source_closet=False, has_source_file=False
    )
    cluster = LowCorroborationCluster(entity_name="x", weak_triple_count=3, weakest_confidence=0.4)

    from pydantic import ValidationError

    for model in (domain, decayed, missing, debt, cluster):
        with pytest.raises(ValidationError):
            model.x = 1  # type: ignore[attr-defined]
