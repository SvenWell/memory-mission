"""Tests for the MemPalace-ported temporal knowledge graph (step 6b)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.memory import (
    Entity,
    GraphStats,
    KnowledgeGraph,
    Triple,
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
