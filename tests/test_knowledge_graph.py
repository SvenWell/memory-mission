"""Tests for the MemPalace-ported temporal knowledge graph (step 6b).

Bayesian corroboration tests (``corroborate`` / ``find_current_triple`` /
``triple_sources``) live at the bottom of the file, under Step 13.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.memory import (
    ALL_TIERS,
    CORROBORATION_CAP,
    DEFAULT_TIER,
    Entity,
    GraphStats,
    KnowledgeGraph,
    MergeResult,
    Triple,
    TripleSource,
    is_above,
    is_at_least,
    tier_level,
)

# ---------- Triple model ----------


def test_triple_is_frozen() -> None:
    t = Triple(subject="a", predicate="knows", object="b")
    with pytest.raises(ValidationError):
        t.confidence = 0.5  # type: ignore[misc]


def test_triple_confidence_must_be_in_range() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Triple(subject="a", predicate="p", object="b", confidence=1.5)


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "as_of", "expected"),
    [
        # Always-true triple
        (None, None, date(2024, 1, 1), True),
        (None, None, date(2030, 1, 1), True),
        # Bounded triple
        (date(2024, 1, 1), date(2025, 1, 1), date(2024, 6, 1), True),
        (date(2024, 1, 1), date(2025, 1, 1), date(2023, 6, 1), False),
        (date(2024, 1, 1), date(2025, 1, 1), date(2025, 6, 1), False),
        # valid_to is EXCLUSIVE: "ended on" means already over by that day
        (date(2024, 1, 1), date(2025, 1, 1), date(2025, 1, 1), False),
        # Open-ended (currently true)
        (date(2024, 1, 1), None, date(2030, 1, 1), True),
        (date(2024, 1, 1), None, date(2023, 12, 31), False),
    ],
)
def test_triple_is_valid_at(
    valid_from: date | None,
    valid_to: date | None,
    as_of: date,
    expected: bool,
) -> None:
    t = Triple(
        subject="a",
        predicate="p",
        object="b",
        valid_from=valid_from,
        valid_to=valid_to,
    )
    assert t.is_valid_at(as_of) is expected


# ---------- Fixtures ----------


@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph(tmp_path / "test.kg.sqlite3")


# ---------- Entity ops ----------


def test_add_entity_returns_pydantic_entity(kg: KnowledgeGraph) -> None:
    entity = kg.add_entity("sarah-chen", entity_type="person", properties={"aliases": ["Sarah"]})
    assert entity == Entity(
        name="sarah-chen",
        entity_type="person",
        properties={"aliases": ["Sarah"]},
    )


def test_add_entity_is_idempotent(kg: KnowledgeGraph) -> None:
    kg.add_entity("sarah-chen", entity_type="person")
    kg.add_entity("sarah-chen", entity_type="person")
    assert kg.stats().entity_count == 1


def test_add_entity_updates_type_on_conflict(kg: KnowledgeGraph) -> None:
    kg.add_entity("acme", entity_type="unknown")
    kg.add_entity("acme", entity_type="company", properties={"industry": "fintech"})
    fetched = kg.get_entity("acme")
    assert fetched is not None
    assert fetched.entity_type == "company"
    assert fetched.properties == {"industry": "fintech"}


def test_get_entity_missing_returns_none(kg: KnowledgeGraph) -> None:
    assert kg.get_entity("nobody") is None


# ---------- Triple ops ----------


def test_add_triple_persists_all_fields(kg: KnowledgeGraph) -> None:
    kg.add_entity("sarah-chen")
    kg.add_entity("acme")
    triple = kg.add_triple(
        "sarah-chen",
        "works_at",
        "acme",
        valid_from=date(2024, 1, 1),
        confidence=0.95,
        source_closet="interactions",
        source_file="2024-01-02-onboarding.md",
    )
    assert triple.subject == "sarah-chen"
    assert triple.valid_from == date(2024, 1, 1)
    assert triple.confidence == 0.95
    assert triple.source_file == "2024-01-02-onboarding.md"

    fetched = kg.query_entity("sarah-chen")
    assert fetched == [triple]


def test_add_triple_confidence_validation(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        kg.add_triple("a", "p", "b", confidence=-0.1)


def test_multiple_triples_with_same_subject_predicate_coexist(
    kg: KnowledgeGraph,
) -> None:
    """Append-only: two 'works_at' triples both live until invalidated."""
    kg.add_triple("sarah-chen", "works_at", "acme", valid_from=date(2024, 1, 1))
    kg.add_triple("sarah-chen", "works_at", "beta", valid_from=date(2026, 4, 1))
    assert kg.stats().triple_count == 2
    assert kg.stats().currently_true_triple_count == 2


# ---------- Invalidate ----------


def test_invalidate_sets_valid_to(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah-chen", "works_at", "acme", valid_from=date(2024, 1, 1))
    n = kg.invalidate("sarah-chen", "works_at", "acme", ended=date(2026, 3, 15))
    assert n == 1

    results = kg.query_entity("sarah-chen")
    assert len(results) == 1
    assert results[0].valid_to == date(2026, 3, 15)


def test_invalidate_returns_zero_when_no_match(kg: KnowledgeGraph) -> None:
    assert kg.invalidate("nobody", "does", "nothing") == 0


def test_invalidate_only_touches_currently_true(kg: KnowledgeGraph) -> None:
    """Already-ended triples are left alone."""
    kg.add_triple(
        "sarah-chen",
        "works_at",
        "acme",
        valid_from=date(2020, 1, 1),
        valid_to=date(2023, 1, 1),
    )
    kg.add_triple("sarah-chen", "works_at", "acme", valid_from=date(2024, 1, 1))
    n = kg.invalidate("sarah-chen", "works_at", "acme", ended=date(2026, 3, 15))
    assert n == 1  # only the currently-true one

    results = kg.query_entity("sarah-chen")
    ended_dates = sorted(t.valid_to for t in results if t.valid_to is not None)
    assert ended_dates == [date(2023, 1, 1), date(2026, 3, 15)]


# ---------- Queries ----------


def test_query_entity_outgoing_by_default(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah-chen", "works_at", "acme")
    kg.add_triple("bob", "reports_to", "sarah-chen")
    results = kg.query_entity("sarah-chen")
    assert len(results) == 1
    assert results[0].object == "acme"


def test_query_entity_incoming(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah-chen", "works_at", "acme")
    kg.add_triple("bob", "reports_to", "sarah-chen")
    results = kg.query_entity("sarah-chen", direction="incoming")
    assert len(results) == 1
    assert results[0].subject == "bob"


def test_query_entity_both_directions(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah-chen", "works_at", "acme")
    kg.add_triple("bob", "reports_to", "sarah-chen")
    results = kg.query_entity("sarah-chen", direction="both")
    assert {(t.subject, t.object) for t in results} == {
        ("sarah-chen", "acme"),
        ("bob", "sarah-chen"),
    }


def test_query_entity_as_of_filters_by_validity(kg: KnowledgeGraph) -> None:
    kg.add_triple(
        "sarah-chen",
        "works_at",
        "acme",
        valid_from=date(2024, 1, 1),
        valid_to=date(2026, 3, 15),
    )
    kg.add_triple("sarah-chen", "works_at", "beta", valid_from=date(2026, 3, 16))
    # Feb 2025: Sarah is at acme
    r1 = kg.query_entity("sarah-chen", as_of=date(2025, 2, 1))
    assert len(r1) == 1 and r1[0].object == "acme"
    # April 2026: Sarah is at beta
    r2 = kg.query_entity("sarah-chen", as_of=date(2026, 4, 1))
    assert len(r2) == 1 and r2[0].object == "beta"
    # No as_of: both return
    assert len(kg.query_entity("sarah-chen")) == 2


def test_query_relationship_returns_all_matching_predicate(
    kg: KnowledgeGraph,
) -> None:
    kg.add_triple("sarah", "works_at", "acme")
    kg.add_triple("bob", "works_at", "acme")
    kg.add_triple("sarah", "knows", "bob")
    results = kg.query_relationship("works_at")
    assert {(t.subject, t.object) for t in results} == {
        ("sarah", "acme"),
        ("bob", "acme"),
    }


def test_query_relationship_as_of_filters(kg: KnowledgeGraph) -> None:
    kg.add_triple(
        "sarah",
        "works_at",
        "acme",
        valid_from=date(2024, 1, 1),
        valid_to=date(2025, 1, 1),
    )
    kg.add_triple("bob", "works_at", "acme", valid_from=date(2025, 6, 1))
    q = kg.query_relationship("works_at", as_of=date(2024, 6, 1))
    assert [(t.subject, t.object) for t in q] == [("sarah", "acme")]


# ---------- Timeline ----------


def test_timeline_orders_chronologically(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "beta", valid_from=date(2026, 4, 1))
    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2020, 1, 1))
    kg.add_triple("sarah", "works_at", "gamma", valid_from=date(2023, 6, 1))
    dates = [t.valid_from for t in kg.timeline("sarah")]
    assert dates == [date(2020, 1, 1), date(2023, 6, 1), date(2026, 4, 1)]


def test_timeline_global_when_entity_is_none(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "knows", "bob", valid_from=date(2020, 1, 1))
    kg.add_triple("carol", "knows", "dave", valid_from=date(2021, 1, 1))
    assert len(kg.timeline()) == 2


def test_timeline_filters_by_either_subject_or_object(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2024, 1, 1))
    kg.add_triple("bob", "reports_to", "sarah", valid_from=date(2024, 2, 1))
    kg.add_triple("other", "no_relation", "whoever", valid_from=date(2024, 3, 1))
    results = kg.timeline("sarah")
    assert {(t.subject, t.object) for t in results} == {
        ("sarah", "acme"),
        ("bob", "sarah"),
    }


def test_timeline_places_null_valid_from_first(kg: KnowledgeGraph) -> None:
    kg.add_triple("a", "x", "b", valid_from=date(2024, 1, 1))
    kg.add_triple("a", "x", "c")  # no valid_from
    triples = kg.timeline("a")
    assert triples[0].valid_from is None
    assert triples[1].valid_from == date(2024, 1, 1)


# ---------- Bulk + stats ----------


def test_seed_from_entity_facts(kg: KnowledgeGraph) -> None:
    kg.seed_from_entity_facts(
        {
            "sarah-chen": [
                {
                    "predicate": "works_at",
                    "object": "acme",
                    "valid_from": "2024-01-01",
                    "confidence": 0.9,
                    "source_file": "onboarding.md",
                },
                {"predicate": "role", "object": "CEO", "confidence": 0.95},
            ],
            "acme": [{"predicate": "industry", "object": "fintech"}],
        }
    )
    assert kg.stats().entity_count == 2
    assert kg.stats().triple_count == 3
    sarah = kg.query_entity("sarah-chen")
    assert len(sarah) == 2
    # String valid_from coerced to date
    works_at = next(t for t in sarah if t.predicate == "works_at")
    assert works_at.valid_from == date(2024, 1, 1)


def test_stats_tracks_currently_true_count(kg: KnowledgeGraph) -> None:
    kg.add_triple("a", "p", "b", valid_from=date(2024, 1, 1))  # currently true
    kg.add_triple(
        "a",
        "p",
        "c",
        valid_from=date(2024, 1, 1),
        valid_to=date(2025, 1, 1),
    )  # ended
    stats = kg.stats()
    assert isinstance(stats, GraphStats)
    assert stats.triple_count == 2
    assert stats.currently_true_triple_count == 1


# ---------- Persistence ----------


def test_graph_persists_across_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.kg.sqlite3"
    with KnowledgeGraph(db_path) as kg1:
        kg1.add_entity("sarah-chen", entity_type="person")
        kg1.add_triple("sarah-chen", "works_at", "acme", valid_from=date(2024, 1, 1))

    with KnowledgeGraph(db_path) as kg2:
        assert kg2.get_entity("sarah-chen") is not None
        assert len(kg2.query_entity("sarah-chen")) == 1


def test_close_is_idempotent(tmp_path: Path) -> None:
    kg = KnowledgeGraph(tmp_path / "x.kg.sqlite3")
    kg.close()
    kg.close()  # must not raise


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    with KnowledgeGraph(tmp_path / "cm.kg.sqlite3") as kg:
        kg.add_entity("a")
    # Can open again without lock contention
    with KnowledgeGraph(tmp_path / "cm.kg.sqlite3") as kg2:
        assert kg2.get_entity("a") is not None


def test_per_firm_isolation(tmp_path: Path) -> None:
    """Different DB paths = different graphs. No cross-firm leakage."""
    with (
        KnowledgeGraph(tmp_path / "firm-a.sqlite3") as kg_a,
        KnowledgeGraph(tmp_path / "firm-b.sqlite3") as kg_b,
    ):
        kg_a.add_entity("shared-name", entity_type="acme-version")
        kg_b.add_entity("shared-name", entity_type="beta-version")

        ent_a = kg_a.get_entity("shared-name")
        ent_b = kg_b.get_entity("shared-name")
        assert ent_a is not None and ent_a.entity_type == "acme-version"
        assert ent_b is not None and ent_b.entity_type == "beta-version"


def test_creates_parent_directory(tmp_path: Path) -> None:
    """Nested parent paths are created on first open."""
    nested = tmp_path / "a" / "b" / "c" / "kg.sqlite3"
    KnowledgeGraph(nested).close()
    assert nested.exists()


# ---------- Step 13: Bayesian corroboration ----------


def test_triple_carries_corroboration_count_default_zero() -> None:
    t = Triple(subject="a", predicate="p", object="b")
    assert t.corroboration_count == 0


def test_triple_corroboration_count_rejects_negative() -> None:
    with pytest.raises(ValidationError, match="corroboration_count"):
        Triple(subject="a", predicate="p", object="b", corroboration_count=-1)


def test_add_triple_seeds_triple_sources_row(kg: KnowledgeGraph) -> None:
    """Every ``add_triple`` creates exactly one ``triple_sources`` row."""
    kg.add_triple(
        "sarah",
        "works_at",
        "acme",
        confidence=0.8,
        source_closet="firm",
        source_file="/tmp/evidence.json",
    )
    sources = kg.triple_sources("sarah", "works_at", "acme")
    assert len(sources) == 1
    s = sources[0]
    assert isinstance(s, TripleSource)
    assert s.source_closet == "firm"
    assert s.source_file == "/tmp/evidence.json"
    assert s.confidence_after == 0.8


def test_find_current_triple_returns_matching(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", confidence=0.7)
    found = kg.find_current_triple("sarah", "works_at", "acme")
    assert found is not None
    assert found.confidence == 0.7


def test_find_current_triple_returns_none_when_no_match(kg: KnowledgeGraph) -> None:
    assert kg.find_current_triple("nobody", "noop", "nowhere") is None


def test_find_current_triple_skips_invalidated(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2020, 1, 1))
    kg.invalidate("sarah", "works_at", "acme", ended=date(2024, 1, 1))
    assert kg.find_current_triple("sarah", "works_at", "acme") is None


def test_corroborate_applies_noisy_or(kg: KnowledgeGraph) -> None:
    """``new = 1 - (1 - old) * (1 - incoming)`` — two independent sources."""
    kg.add_triple("sarah", "works_at", "acme", confidence=0.6)
    updated = kg.corroborate("sarah", "works_at", "acme", confidence=0.7, source_closet="firm")
    assert updated is not None
    assert updated.confidence == pytest.approx(1.0 - (0.4 * 0.3))  # 0.88
    assert updated.corroboration_count == 1


def test_corroborate_caps_at_099(kg: KnowledgeGraph) -> None:
    """Accumulated evidence can never push confidence above 0.99."""
    kg.add_triple("sarah", "works_at", "acme", confidence=0.95)
    updated = kg.corroborate("sarah", "works_at", "acme", confidence=0.95)
    assert updated is not None
    assert updated.confidence == CORROBORATION_CAP
    assert updated.confidence == 0.99


def test_corroborate_cap_holds_with_initial_1_0(kg: KnowledgeGraph) -> None:
    """Even starting at 1.0, corroborate result caps at 0.99 — no auto-certainty."""
    kg.add_triple("sarah", "works_at", "acme", confidence=1.0)
    updated = kg.corroborate("sarah", "works_at", "acme", confidence=0.5)
    assert updated is not None
    assert updated.confidence == CORROBORATION_CAP


def test_corroborate_returns_none_when_no_match(kg: KnowledgeGraph) -> None:
    result = kg.corroborate("nobody", "noop", "nowhere", confidence=0.9)
    assert result is None


def test_corroborate_skips_invalidated_triples(kg: KnowledgeGraph) -> None:
    """Re-extracting an ended fact does NOT corroborate the historical row."""
    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2020, 1, 1))
    kg.invalidate("sarah", "works_at", "acme", ended=date(2024, 1, 1))
    result = kg.corroborate("sarah", "works_at", "acme", confidence=0.9)
    assert result is None


def test_corroborate_increments_count(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", confidence=0.5)
    kg.corroborate("sarah", "works_at", "acme", confidence=0.5)
    kg.corroborate("sarah", "works_at", "acme", confidence=0.5)
    kg.corroborate("sarah", "works_at", "acme", confidence=0.5)
    current = kg.find_current_triple("sarah", "works_at", "acme")
    assert current is not None
    assert current.corroboration_count == 3


def test_corroborate_preserves_triple_identity(kg: KnowledgeGraph) -> None:
    """Corroboration updates in place — no duplicate rows."""
    kg.add_triple("sarah", "works_at", "acme", confidence=0.6)
    kg.corroborate("sarah", "works_at", "acme", confidence=0.7)
    kg.corroborate("sarah", "works_at", "acme", confidence=0.5)
    triples = kg.query_relationship("works_at")
    assert len(triples) == 1


def test_corroborate_accumulates_sources_in_order(kg: KnowledgeGraph) -> None:
    """``triple_sources`` returns oldest-first with the full provenance chain."""
    kg.add_triple(
        "sarah",
        "works_at",
        "acme",
        confidence=0.5,
        source_closet="firm",
        source_file="/tmp/first.json",
    )
    kg.corroborate(
        "sarah",
        "works_at",
        "acme",
        confidence=0.6,
        source_closet="personal/alice",
        source_file="/tmp/second.json",
    )
    kg.corroborate(
        "sarah",
        "works_at",
        "acme",
        confidence=0.7,
        source_closet="personal/bob",
        source_file="/tmp/third.json",
    )
    sources = kg.triple_sources("sarah", "works_at", "acme")
    assert [s.source_closet for s in sources] == [
        "firm",
        "personal/alice",
        "personal/bob",
    ]
    # Confidence climbs monotonically with each corroboration
    confidences = [s.confidence_after for s in sources]
    assert confidences == sorted(confidences)


def test_corroborate_rejects_out_of_range_confidence(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", confidence=0.5)
    with pytest.raises(ValueError, match="confidence"):
        kg.corroborate("sarah", "works_at", "acme", confidence=1.5)


def test_triple_sources_returns_empty_list_when_no_match(kg: KnowledgeGraph) -> None:
    assert kg.triple_sources("nobody", "noop", "nowhere") == []


def test_corroborate_persists_across_sessions(tmp_path: Path) -> None:
    """Confidence bump + sources survive close/reopen."""
    db_path = tmp_path / "persist.kg.sqlite3"
    with KnowledgeGraph(db_path) as kg1:
        kg1.add_triple(
            "sarah",
            "works_at",
            "acme",
            confidence=0.6,
            source_closet="firm",
        )
        kg1.corroborate(
            "sarah",
            "works_at",
            "acme",
            confidence=0.7,
            source_closet="personal/alice",
        )

    with KnowledgeGraph(db_path) as kg2:
        current = kg2.find_current_triple("sarah", "works_at", "acme")
        assert current is not None
        assert current.confidence == pytest.approx(0.88)
        assert current.corroboration_count == 1
        sources = kg2.triple_sources("sarah", "works_at", "acme")
        assert len(sources) == 2


# ---------- Step 14b: Entity merge ----------


def test_merge_rewrites_subject_triples(kg: KnowledgeGraph) -> None:
    """Triples where source is the subject are rewritten to target."""
    kg.add_entity("alice-smith")
    kg.add_entity("p_alice")
    kg.add_entity("acme")
    kg.add_triple("alice-smith", "works_at", "acme")
    kg.add_triple("alice-smith", "knows", "bob")

    result = kg.merge_entities(
        "alice-smith",
        "p_alice",
        reviewer_id="reviewer",
        rationale="resolved via shared email",
    )

    assert result.triples_rewritten == 2
    triples = kg.query_entity("p_alice")
    assert len(triples) == 2
    assert all(t.subject == "p_alice" for t in triples)


def test_merge_rewrites_object_triples(kg: KnowledgeGraph) -> None:
    """Triples where source is the object are rewritten to target."""
    kg.add_entity("p_alice")
    kg.add_entity("alice-smith")
    kg.add_triple("bob", "knows", "alice-smith")
    kg.add_triple("carol", "reports_to", "alice-smith")

    result = kg.merge_entities(
        "alice-smith",
        "p_alice",
        reviewer_id="reviewer",
        rationale="resolved via shared linkedin",
    )

    assert result.triples_rewritten == 2
    incoming = kg.query_entity("p_alice", direction="incoming")
    assert len(incoming) == 2
    assert all(t.object == "p_alice" for t in incoming)


def test_merge_deletes_source_entity(kg: KnowledgeGraph) -> None:
    """Source becomes an alias that no longer exists as a distinct node."""
    kg.add_entity("alice-smith", entity_type="person")
    kg.add_entity("p_alice", entity_type="person")
    kg.merge_entities(
        "alice-smith",
        "p_alice",
        reviewer_id="reviewer",
        rationale="merge",
    )
    assert kg.get_entity("alice-smith") is None
    assert kg.get_entity("p_alice") is not None


def test_merge_preserves_triple_sources_provenance(kg: KnowledgeGraph) -> None:
    """Merge leaves ``triple_sources`` untouched — full audit chain survives."""
    kg.add_entity("alice-smith")
    kg.add_entity("p_alice")
    kg.add_triple(
        "alice-smith",
        "works_at",
        "acme",
        source_closet="personal/alice",
        source_file="/tmp/source.json",
    )
    sources_before = kg.triple_sources("alice-smith", "works_at", "acme")
    assert len(sources_before) == 1

    kg.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="ok")

    sources_after = kg.triple_sources("p_alice", "works_at", "acme")
    assert len(sources_after) == 1
    assert sources_after[0].source_closet == "personal/alice"
    assert sources_after[0].source_file == "/tmp/source.json"


def test_merge_records_audit_event(kg: KnowledgeGraph) -> None:
    """Every merge lives in ``entity_merges`` with who/why/when."""
    kg.add_entity("alice-smith")
    kg.add_entity("p_alice")
    kg.add_triple("alice-smith", "works_at", "acme")

    kg.merge_entities(
        "alice-smith",
        "p_alice",
        reviewer_id="reviewer-123",
        rationale="shared email discovered in onboarding doc",
    )

    history = kg.merge_history("p_alice")
    assert len(history) == 1
    event = history[0]
    assert isinstance(event, MergeResult)
    assert event.source_entity == "alice-smith"
    assert event.target_entity == "p_alice"
    assert event.reviewer_id == "reviewer-123"
    assert "shared email" in event.rationale
    assert event.triples_rewritten == 1


def test_merge_history_queryable_by_source_or_target(
    kg: KnowledgeGraph,
) -> None:
    """``merge_history`` finds merges whether the entity was source or target."""
    for alias in ("alice-smith", "a-smith"):
        kg.add_entity(alias)
    kg.add_entity("p_alice")
    kg.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="ok")
    kg.merge_entities("a-smith", "p_alice", reviewer_id="r", rationale="ok")

    # Target sees both merges
    assert len(kg.merge_history("p_alice")) == 2
    # Each source sees its own merge
    assert len(kg.merge_history("alice-smith")) == 1
    assert len(kg.merge_history("a-smith")) == 1


def test_merge_requires_non_empty_rationale(kg: KnowledgeGraph) -> None:
    kg.add_entity("alice-smith")
    kg.add_entity("p_alice")
    with pytest.raises(ValueError, match="rationale"):
        kg.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="")
    with pytest.raises(ValueError, match="rationale"):
        kg.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="   ")


def test_merge_rejects_source_equal_to_target(kg: KnowledgeGraph) -> None:
    kg.add_entity("alice-smith")
    with pytest.raises(ValueError, match="differ"):
        kg.merge_entities(
            "alice-smith",
            "alice-smith",
            reviewer_id="r",
            rationale="ok",
        )


def test_merge_returns_zero_when_source_has_no_triples(kg: KnowledgeGraph) -> None:
    """Merge of an entity with no triples still records the event (idempotent)."""
    kg.add_entity("orphan")
    kg.add_entity("p_target")
    result = kg.merge_entities(
        "orphan",
        "p_target",
        reviewer_id="r",
        rationale="cleanup",
    )
    assert result.triples_rewritten == 0
    # Audit row still written
    assert len(kg.merge_history("p_target")) == 1


def test_merge_history_empty_for_unknown_entity(kg: KnowledgeGraph) -> None:
    assert kg.merge_history("nobody") == []


def test_merge_persists_across_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "merge-persist.kg.sqlite3"
    with KnowledgeGraph(db_path) as kg1:
        kg1.add_entity("alice-smith")
        kg1.add_entity("p_alice")
        kg1.add_triple("alice-smith", "works_at", "acme")
        kg1.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="ok")

    with KnowledgeGraph(db_path) as kg2:
        assert kg2.get_entity("alice-smith") is None
        assert kg2.get_entity("p_alice") is not None
        triples = kg2.query_entity("p_alice")
        assert len(triples) == 1
        assert triples[0].subject == "p_alice"
        assert len(kg2.merge_history("p_alice")) == 1


# ---------- Step 15a: Tier field ----------


def test_tier_level_order() -> None:
    """Constitution is highest authority, decision is lowest."""
    assert tier_level("constitution") > tier_level("doctrine")
    assert tier_level("doctrine") > tier_level("policy")
    assert tier_level("policy") > tier_level("decision")


def test_tier_is_above_and_is_at_least() -> None:
    assert is_above("doctrine", "decision") is True
    assert is_above("decision", "doctrine") is False
    assert is_above("policy", "policy") is False
    assert is_at_least("doctrine", "decision") is True
    assert is_at_least("policy", "policy") is True
    assert is_at_least("decision", "policy") is False


def test_all_tiers_covers_every_value() -> None:
    assert set(ALL_TIERS) == {"constitution", "doctrine", "policy", "decision"}


def test_triple_defaults_to_decision_tier() -> None:
    t = Triple(subject="a", predicate="p", object="b")
    assert t.tier == DEFAULT_TIER
    assert t.tier == "decision"


def test_add_triple_without_tier_stores_decision(kg: KnowledgeGraph) -> None:
    triple = kg.add_triple("sarah", "works_at", "acme")
    assert triple.tier == "decision"
    fetched = kg.query_entity("sarah")[0]
    assert fetched.tier == "decision"


def test_add_triple_with_explicit_tier_persists(kg: KnowledgeGraph) -> None:
    kg.add_triple("firm", "mission", "preserve-client-capital", tier="constitution")
    kg.add_triple("firm", "believes", "compound-interest-wins", tier="doctrine")
    kg.add_triple("firm", "policy", "review-allocations-quarterly", tier="policy")
    kg.add_triple("sarah", "works_at", "acme")  # default decision

    triples = kg.query_relationship("mission")
    assert triples[0].tier == "constitution"
    believes = kg.query_relationship("believes")
    assert believes[0].tier == "doctrine"
    policy = kg.query_relationship("policy")
    assert policy[0].tier == "policy"
    work = kg.query_relationship("works_at")
    assert work[0].tier == "decision"


def test_corroborate_preserves_tier(kg: KnowledgeGraph) -> None:
    """Corroboration adds evidence; it does not change the fact's tier."""
    kg.add_triple("firm", "believes", "compound-interest-wins", tier="doctrine", confidence=0.7)
    updated = kg.corroborate("firm", "believes", "compound-interest-wins", confidence=0.6)
    assert updated is not None
    assert updated.tier == "doctrine"


def test_merge_preserves_tier_on_rewritten_triples(kg: KnowledgeGraph) -> None:
    """merge_entities renames subjects/objects but leaves tier alone."""
    kg.add_entity("alice-smith")
    kg.add_entity("p_alice")
    kg.add_triple("alice-smith", "signatory_for", "investment-policy", tier="policy")
    kg.merge_entities("alice-smith", "p_alice", reviewer_id="r", rationale="merge")
    fetched = kg.query_entity("p_alice")[0]
    assert fetched.subject == "p_alice"
    assert fetched.tier == "policy"


def test_migration_adds_tier_to_existing_db(tmp_path: Path) -> None:
    """Pre-Step-15 DBs get the tier column via ``_run_migrations``."""
    import sqlite3

    db_path = tmp_path / "pre-tier.kg.sqlite3"
    # Create a DB with the pre-Step-15 schema by hand — no tier column.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            entity_type TEXT NOT NULL DEFAULT 'unknown',
            properties TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            source_closet TEXT,
            source_file TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO entities (name, created_at)
        VALUES ('sarah', '2026-04-01T00:00:00+00:00');
        INSERT INTO triples (subject, predicate, object, confidence, created_at)
        VALUES ('sarah', 'works_at', 'acme', 0.9, '2026-04-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    # Open via KnowledgeGraph — migration should run, tier should default.
    with KnowledgeGraph(db_path) as kg:
        triples = kg.query_entity("sarah")
        assert len(triples) == 1
        assert triples[0].tier == "decision"
        # Fresh inserts accept the new tier kwarg
        kg.add_triple("firm", "mission", "test", tier="constitution")
        fetched = kg.query_relationship("mission")[0]
        assert fetched.tier == "constitution"


# ---------- Step 16.5: SQL-over-KG read primitive ----------


def test_sql_query_basic_select(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme")
    kg.add_triple("bob", "works_at", "beta")
    rows = kg.sql_query("SELECT subject, predicate, object FROM triples ORDER BY subject")
    assert rows == [
        {"subject": "bob", "predicate": "works_at", "object": "beta"},
        {"subject": "sarah", "predicate": "works_at", "object": "acme"},
    ]


def test_sql_query_parameterized(kg: KnowledgeGraph) -> None:
    kg.add_triple("sarah", "works_at", "acme", tier="policy")
    kg.add_triple("sarah", "knows", "bob", tier="decision")
    rows = kg.sql_query(
        "SELECT predicate FROM triples WHERE subject = ? AND tier = ?",
        ("sarah", "policy"),
    )
    assert rows == [{"predicate": "works_at"}]


def test_sql_query_allows_with_cte(kg: KnowledgeGraph) -> None:
    kg.add_triple("a", "p", "b")
    rows = kg.sql_query(
        "WITH counts AS (SELECT subject, COUNT(*) AS n FROM triples "
        "GROUP BY subject) SELECT * FROM counts"
    )
    assert rows == [{"subject": "a", "n": 1}]


def test_sql_query_rejects_insert(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="SELECT or WITH"):
        kg.sql_query("INSERT INTO entities (name, created_at) VALUES ('x', 'y')")


def test_sql_query_rejects_update(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="SELECT or WITH"):
        kg.sql_query("UPDATE triples SET confidence = 1.0")


def test_sql_query_rejects_delete(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="SELECT or WITH"):
        kg.sql_query("DELETE FROM triples")


def test_sql_query_engine_blocks_writes_if_validation_bypassed(
    kg: KnowledgeGraph,
) -> None:
    """Even if a write slipped past the string check, the read-only
    connection refuses at the engine level. Use a leading comment to
    simulate that scenario.
    """
    # The string check would reject this too, but the point is: the
    # engine is the real line of defense. This test documents that.
    with pytest.raises(ValueError):
        kg.sql_query("/* comment */ INSERT INTO entities (name, created_at) VALUES ('x', 'y')")


def test_sql_query_row_limit_enforced(kg: KnowledgeGraph) -> None:
    for i in range(5):
        kg.add_triple(f"s{i}", "p", "o")
    with pytest.raises(ValueError, match="row_limit=3"):
        kg.sql_query("SELECT * FROM triples", row_limit=3)


def test_sql_query_at_row_limit_returns_ok(kg: KnowledgeGraph) -> None:
    for i in range(3):
        kg.add_triple(f"s{i}", "p", "o")
    rows = kg.sql_query("SELECT subject FROM triples ORDER BY subject", row_limit=3)
    assert len(rows) == 3


def test_sql_query_rejects_nonsense_row_limit(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="row_limit"):
        kg.sql_query("SELECT 1", row_limit=0)


def test_sql_query_spans_triple_sources_join(kg: KnowledgeGraph) -> None:
    """End-to-end: the kind of question workflow agents actually ask."""
    kg.add_triple(
        "sarah",
        "works_at",
        "acme",
        source_closet="firm",
        source_file="/a.md",
    )
    kg.corroborate(
        "sarah",
        "works_at",
        "acme",
        confidence=0.7,
        source_closet="personal/alice",
        source_file="/b.md",
    )
    rows = kg.sql_query(
        "SELECT ts.source_closet, ts.source_file FROM triples t "
        "JOIN triple_sources ts ON ts.triple_id = t.id "
        "WHERE t.subject = ? AND t.predicate = ? "
        "ORDER BY ts.id",
        ("sarah", "works_at"),
    )
    assert rows == [
        {"source_closet": "firm", "source_file": "/a.md"},
        {"source_closet": "personal/alice", "source_file": "/b.md"},
    ]


# ---------- Scope column (access-control enforcement) ----------


def test_triple_model_defaults_scope_to_public() -> None:
    t = Triple(subject="a", predicate="p", object="b")
    assert t.scope == "public"


def test_add_triple_persists_scope(kg: KnowledgeGraph) -> None:
    kg.add_triple(
        subject="alice",
        predicate="works_at",
        obj="acme",
        source_closet="firm",
        source_file="/f.md",
        scope="partner-only",
    )
    triples = kg.query_entity("alice")
    assert len(triples) == 1
    assert triples[0].scope == "partner-only"


def test_query_entity_filters_by_viewer_scopes(kg: KnowledgeGraph) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="firm", scope="public")
    kg.add_triple("alice", "compensation", "7m", source_closet="firm", scope="partner-only")

    public_only = kg.query_entity("alice", viewer_scopes=frozenset({"public"}))
    partner = kg.query_entity("alice", viewer_scopes=frozenset({"public", "partner-only"}))
    all_triples = kg.query_entity("alice")

    assert {t.predicate for t in public_only} == {"works_at"}
    assert {t.predicate for t in partner} == {"works_at", "compensation"}
    assert {t.predicate for t in all_triples} == {"works_at", "compensation"}  # None = no filter


def test_query_relationship_filters_by_viewer_scopes(kg: KnowledgeGraph) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="firm", scope="public")
    kg.add_triple("bob", "works_at", "secret-co", source_closet="firm", scope="partner-only")

    public_only = kg.query_relationship("works_at", viewer_scopes=frozenset({"public"}))
    assert {t.subject for t in public_only} == {"alice"}


def test_timeline_filters_by_viewer_scopes(kg: KnowledgeGraph) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="firm", scope="public")
    kg.add_triple("alice", "compensation", "7m", source_closet="firm", scope="partner-only")

    filtered = kg.timeline("alice", viewer_scopes=frozenset({"public"}))
    assert {t.predicate for t in filtered} == {"works_at"}


def test_corroborate_raises_on_scope_mismatch(kg: KnowledgeGraph) -> None:
    kg.add_triple(
        "alice",
        "works_at",
        "acme",
        confidence=0.7,
        source_closet="firm",
        scope="public",
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        kg.corroborate(
            "alice",
            "works_at",
            "acme",
            confidence=0.8,
            source_closet="firm",
            scope="partner-only",
        )


def test_sql_query_rejects_attach(kg: KnowledgeGraph) -> None:
    """ATTACH DATABASE crosses firm isolation — reject."""
    with pytest.raises(ValueError, match="ATTACH"):
        kg.sql_query("SELECT * FROM triples; ATTACH DATABASE 'x.db' AS leak")
    # And inside a WITH / CTE
    with pytest.raises(ValueError, match="ATTACH"):
        kg.sql_query("WITH x AS (SELECT 1) SELECT * FROM x; ATTACH DATABASE 'x.db' AS leak")


def test_sql_query_rejects_pragma(kg: KnowledgeGraph) -> None:
    """PRAGMA is a side-effect surface — reject in sql_query."""
    with pytest.raises(ValueError, match="PRAGMA"):
        kg.sql_query("SELECT 1; PRAGMA table_list")


def test_merge_entities_rejects_scope_conflict(kg: KnowledgeGraph) -> None:
    """Merge that would colocate differing scopes on one (s,p,o) raises."""
    kg.add_triple("alice-s", "works_at", "acme", scope="partner-only")
    kg.add_triple("alice-smith", "works_at", "acme", scope="public")
    with pytest.raises(ValueError, match="differing scopes"):
        kg.merge_entities(
            source="alice-s",
            target="alice-smith",
            reviewer_id="reviewer",
            rationale="same person",
        )


def test_merge_entities_succeeds_when_scopes_match(kg: KnowledgeGraph) -> None:
    """Matching scopes on overlapping triples merge fine."""
    kg.add_triple("alice-s", "works_at", "acme", scope="partner-only")
    kg.add_triple("alice-smith", "works_at", "acme", scope="partner-only")
    result = kg.merge_entities(
        source="alice-s",
        target="alice-smith",
        reviewer_id="reviewer",
        rationale="same person",
    )
    assert result.triples_rewritten >= 1


def test_has_triple_source_reports_existing_source(kg: KnowledgeGraph) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="firm", source_file="/tmp/seed.json")
    assert (
        kg.has_triple_source(
            subject="alice", predicate="works_at", obj="acme", source_file="/tmp/seed.json"
        )
        is True
    )
    assert (
        kg.has_triple_source(
            subject="alice", predicate="works_at", obj="acme", source_file="/tmp/other.json"
        )
        is False
    )


def test_corroborate_accepts_matching_scope(kg: KnowledgeGraph) -> None:
    kg.add_triple(
        "alice",
        "works_at",
        "acme",
        confidence=0.7,
        source_closet="firm",
        scope="partner-only",
    )
    updated = kg.corroborate(
        "alice",
        "works_at",
        "acme",
        confidence=0.6,
        source_closet="firm",
        scope="partner-only",
    )
    assert updated is not None
    assert updated.scope == "partner-only"
    assert updated.corroboration_count == 1
