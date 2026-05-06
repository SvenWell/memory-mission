"""Tests for the production PersonalObservation read-shape (ADR-0016).

Replaces the throwaway tests/test_observation_spike.py from Phase 0.
Spike validated the design choice (computed-on-read vs stored column);
this file exercises the actual production code path.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.personal_brain.observations import (
    FreshnessInputs,
    PersonalObservation,
    build_observation,
    compute_freshness,
)
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

# A deterministic "now" anchors the freshness rule across tests.
_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)


# ---------- compute_freshness rule (pure-function tests) ----------


def _inputs(
    *,
    age_days: float,
    proof_count: int = 1,
    sources_in_last_14d: int | None = None,
    contradicted: bool = False,
) -> FreshnessInputs:
    last_at = _NOW - timedelta(days=age_days)
    if sources_in_last_14d is None:
        sources_in_last_14d = 1 if age_days < 14 else 0
    return FreshnessInputs(
        last_corroborated_at=last_at,
        proof_count=proof_count,
        sources_in_last_14d=sources_in_last_14d,
        contradicted=contradicted,
        now=_NOW,
    )


def test_freshness_contradicted_wins() -> None:
    """Contradicted overrides every other signal, even fresh corroboration."""
    assert compute_freshness(_inputs(age_days=0.1, contradicted=True)) == "contradicted"


def test_freshness_new_for_recent_single_proof() -> None:
    assert compute_freshness(_inputs(age_days=2, proof_count=1)) == "new"


def test_freshness_strengthening_when_two_recent_corroborations() -> None:
    """Two sources in last 14 days promotes the trend even when single-proof age is fresh."""
    assert (
        compute_freshness(_inputs(age_days=3, proof_count=2, sources_in_last_14d=2))
        == "strengthening"
    )


def test_freshness_stable_when_corroborated_and_aged_14_to_30_days() -> None:
    inputs = _inputs(age_days=20, proof_count=2, sources_in_last_14d=0)
    assert compute_freshness(inputs) == "stable"


def test_freshness_weakening_when_30_to_60_days_old() -> None:
    assert compute_freshness(_inputs(age_days=45, proof_count=1)) == "weakening"


def test_freshness_stale_when_over_60_days() -> None:
    assert compute_freshness(_inputs(age_days=90, proof_count=1)) == "stale"


def test_freshness_new_does_not_apply_when_proof_count_above_one() -> None:
    """A multi-source recent triple is `strengthening` (or `stable`), never `new`."""
    inputs = _inputs(age_days=2, proof_count=3, sources_in_last_14d=3)
    assert compute_freshness(inputs) == "strengthening"


# ---------- end-to-end: PersonalKnowledgeGraph + query_observations ----------


@pytest.fixture
def kg(tmp_path: Path) -> Iterator[PersonalKnowledgeGraph]:
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path / "firm",
        employee_id="sven",
        identity_resolver=resolver,
    )
    yield pkg
    pkg.close()


def test_query_observations_returns_currently_true_only(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    kg.invalidate("alice", "works_at", "acme")
    kg.add_triple("alice", "works_at", "beta", source_closet="ev", source_file="m2")

    observations = kg.query_observations(subject="alice")
    assert len(observations) == 1
    assert observations[0].object == "beta"


def test_query_observations_filters_by_subject(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    kg.add_triple("carol", "knows", "dan", source_closet="ev", source_file="m2")
    observations = kg.query_observations(subject="alice")
    assert len(observations) == 1
    assert observations[0].subject == "alice"


def test_query_observations_filters_by_predicate(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m2")
    observations = kg.query_observations(predicate="works_at")
    assert len(observations) == 1
    assert observations[0].predicate == "works_at"


def test_query_observations_proof_count_matches_corroboration(
    kg: PersonalKnowledgeGraph,
) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    kg.corroborate(
        "alice",
        "works_at",
        "acme",
        confidence=0.9,
        source_closet="ev",
        source_file="m2",
    )
    kg.corroborate(
        "alice",
        "works_at",
        "acme",
        confidence=0.95,
        source_closet="ev",
        source_file="m3",
    )
    observations = kg.query_observations(subject="alice")
    assert len(observations) == 1
    assert observations[0].proof_count == 3


def test_query_observations_detects_contradiction(kg: PersonalKnowledgeGraph) -> None:
    """Two currently-true triples with same subject+predicate, different object, both flagged."""
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    # Add a second currently-true triple by adding directly without
    # invalidating first — simulates two independently-extracted sources
    # that didn't go through the coherence gate. ``add_triple`` accepts
    # this; the contradiction is detected on read.
    kg.add_triple("alice", "works_at", "beta", source_closet="ev", source_file="m2")
    observations = kg.query_observations(subject="alice")
    assert len(observations) == 2
    for obs in observations:
        assert obs.freshness_trend == "contradicted"


def test_query_observations_sorted_by_recency_desc(kg: PersonalKnowledgeGraph) -> None:
    """Most recently corroborated observation comes first."""
    kg.add_triple("a", "p", "x", source_closet="ev", source_file="m1")
    kg.add_triple("b", "p", "x", source_closet="ev", source_file="m2")
    observations = kg.query_observations(predicate="p")
    # Two distinct subjects, both currently true, no shared (subject,
    # predicate) so neither is contradicted. Order: most recent first.
    assert len(observations) == 2
    assert observations[0].last_corroborated_at >= observations[1].last_corroborated_at


def test_query_observations_history_carries_provenance(
    kg: PersonalKnowledgeGraph,
) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="email", source_file="msg1")
    kg.corroborate(
        "alice",
        "works_at",
        "acme",
        confidence=0.9,
        source_closet="linkedin",
        source_file="profile",
    )
    observations = kg.query_observations(subject="alice")
    assert len(observations) == 1
    history = observations[0].history
    assert len(history) == 2
    closets = {h.source_closet for h in history}
    assert closets == {"email", "linkedin"}


def test_query_observations_since_filter(kg: PersonalKnowledgeGraph) -> None:
    """`since` drops observations whose latest corroboration is older."""
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    # All real adds have added_at ~ now; future-dated since drops everything.
    future = (datetime.now(UTC) + timedelta(days=1)).date()
    observations = kg.query_observations(subject="alice", since=future)
    assert observations == []


def test_query_observations_returns_personal_observation_shape(
    kg: PersonalKnowledgeGraph,
) -> None:
    """Every returned object is a frozen PersonalObservation."""
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    observations = kg.query_observations(subject="alice")
    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, PersonalObservation)
    # Frozen — extra/mutation forbidden.
    with pytest.raises(Exception):  # noqa: B017 - frozen-model check via pydantic
        obs.subject = "mutated"  # type: ignore[misc]


def test_build_observation_handles_zero_sources() -> None:
    """Defensive: a triple with no source rows yields stale + epoch anchor."""
    from memory_mission.memory.knowledge_graph import Triple

    triple = Triple(
        subject="alice",
        predicate="knows",
        object="bob",
        confidence=1.0,
    )
    obs = build_observation(triple, [], now=_NOW)
    assert obs.proof_count == 0
    assert obs.freshness_trend == "stale"
    assert obs.history == []
    assert obs.last_corroborated_at == datetime(1970, 1, 1, tzinfo=UTC)


def test_query_observations_uses_now_anchor_for_freshness(
    kg: PersonalKnowledgeGraph,
) -> None:
    """`now` override lets tests exercise the freshness rule deterministically."""
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    # Far-future "now" puts the corroboration into the stale band.
    far_future = datetime(2030, 1, 1, tzinfo=UTC)
    observations = kg.query_observations(subject="alice", now=far_future)
    assert len(observations) == 1
    assert observations[0].freshness_trend == "stale"


def test_query_observations_respects_personal_scope(
    kg: PersonalKnowledgeGraph,
) -> None:
    """PersonalKnowledgeGraph auto-applies viewer_scopes; can't see triples outside scope.

    Sanity check that the wrapper passes the scope filter through. Direct
    KG-level scope tests live in test_personal_kg.py.
    """
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m1")
    observations = kg.query_observations(subject="alice")
    # The wrapper forces scope to employee_<id>; the triple was written
    # via the wrapper so it carries the same scope. Sanity: we get it back.
    assert len(observations) == 1
    assert observations[0].subject == "alice"


# ---------- date imported from datetime is needed for since fixtures ----------
# (silences a stray "unused import" if pytest collection skips)
_ = date
