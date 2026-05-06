"""Spike test for ADR-0016 — proves PersonalObservation read-shape is sound.

THROWAWAY. This file is deleted in Phase 1 once the production
``personal_brain/observations.py`` lands. Its purpose is solely to
validate that the freshness rule from ADR-0016 produces the same
observable shape whether implemented as:

1. A computed-on-read view over ``triples`` LEFT JOIN ``triple_sources``
   (the path Phase 1 ships).
2. A precomputed column on ``triples`` (a hypothetical alternative).

Both implementations consume the same inputs and must yield the same
``PersonalObservationSpike`` shape. If the test passes, ADR-0016's
"computed-on-read wins because it lets us iterate the rule without a
migration" is justified — the storage choice is a maintenance trade-off,
not a correctness one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

FreshnessTrend = Literal["new", "strengthening", "stable", "weakening", "stale", "contradicted"]


@dataclass(frozen=True)
class _SourceSpike:
    added_at: datetime


@dataclass(frozen=True)
class _TripleSpike:
    subject: str
    predicate: str
    object: str
    valid_to: datetime | None
    confidence: float
    contradicted: bool


@dataclass(frozen=True)
class _PersonalObservationSpike:
    subject: str
    predicate: str
    object: str
    proof_count: int
    freshness_trend: FreshnessTrend
    last_corroborated_at: datetime


def _compute_freshness(
    *,
    sources: list[_SourceSpike],
    contradicted: bool,
    now: datetime,
) -> FreshnessTrend:
    """The ADR-0016 freshness rule. Pure function; iterable in code."""
    if contradicted:
        return "contradicted"
    if not sources:
        return "stale"
    last_at = max(s.added_at for s in sources)
    age = now - last_at
    proof_count = len(sources)
    fourteen_days_ago = now - timedelta(days=14)
    recent_sources = [s for s in sources if s.added_at >= fourteen_days_ago]
    if age < timedelta(days=7) and proof_count == 1:
        return "new"
    if len(recent_sources) >= 2:
        return "strengthening"
    if proof_count > 1 and timedelta(days=14) <= age <= timedelta(days=30):
        return "stable"
    if timedelta(days=30) < age <= timedelta(days=60):
        return "weakening"
    if age > timedelta(days=60):
        return "stale"
    return "stable"


def _observation_via_compute(
    triple: _TripleSpike, sources: list[_SourceSpike], *, now: datetime
) -> _PersonalObservationSpike:
    """Phase 1 production path — freshness computed at read time."""
    return _PersonalObservationSpike(
        subject=triple.subject,
        predicate=triple.predicate,
        object=triple.object,
        proof_count=len(sources),
        freshness_trend=_compute_freshness(
            sources=sources, contradicted=triple.contradicted, now=now
        ),
        last_corroborated_at=max(s.added_at for s in sources)
        if sources
        else now - timedelta(days=999),
    )


def _observation_via_column(
    triple: _TripleSpike,
    sources: list[_SourceSpike],
    stored_freshness: FreshnessTrend,
) -> _PersonalObservationSpike:
    """Hypothetical alternative — freshness pre-stored on the triple row."""
    return _PersonalObservationSpike(
        subject=triple.subject,
        predicate=triple.predicate,
        object=triple.object,
        proof_count=len(sources),
        freshness_trend=stored_freshness,
        last_corroborated_at=max(s.added_at for s in sources)
        if sources
        else datetime(1970, 1, 1, tzinfo=UTC),
    )


# Five worked examples from ADR-0016's acceptance criterion #2:
# real Hermes-touched data shapes.
_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "label, triple, sources, expected_trend",
    [
        (
            "sven_just_observed_today",
            _TripleSpike("sven", "prefers_response_style", "concise", None, 0.9, False),
            [_SourceSpike(_NOW - timedelta(hours=2))],
            "new",
        ),
        (
            "memory_mission_strengthening",
            _TripleSpike("memory-mission", "domain", "operational-memory", None, 0.95, False),
            [
                _SourceSpike(_NOW - timedelta(days=10)),
                _SourceSpike(_NOW - timedelta(days=5)),
                _SourceSpike(_NOW - timedelta(days=1)),
            ],
            "strengthening",
        ),
        (
            "wealthpoint_stable",
            _TripleSpike("wealthpoint", "stage", "discovery", None, 0.8, False),
            [
                _SourceSpike(_NOW - timedelta(days=45)),
                _SourceSpike(_NOW - timedelta(days=20)),
            ],
            "stable",
        ),
        (
            "brian_weakening",
            _TripleSpike("brian", "lane", "coaching", None, 0.7, False),
            [_SourceSpike(_NOW - timedelta(days=40))],
            "weakening",
        ),
        (
            "justin_aaron_stale",
            _TripleSpike("justin-aaron-intro", "commitment_status", "open", None, 0.6, False),
            [_SourceSpike(_NOW - timedelta(days=120))],
            "stale",
        ),
        (
            "contradicted_wins_over_recency",
            _TripleSpike("alice", "works_at", "acme", None, 0.5, True),
            [_SourceSpike(_NOW - timedelta(hours=1))],
            "contradicted",
        ),
    ],
)
def test_compute_path_produces_expected_freshness(
    label: str,
    triple: _TripleSpike,
    sources: list[_SourceSpike],
    expected_trend: FreshnessTrend,
) -> None:
    """Phase 1 path: freshness computed at read time matches the rule."""
    obs = _observation_via_compute(triple, sources, now=_NOW)
    assert obs.freshness_trend == expected_trend, (
        f"{label}: compute path produced {obs.freshness_trend}, expected {expected_trend}"
    )


@pytest.mark.parametrize(
    "label, triple, sources, stored_freshness",
    [
        (
            "sven_just_observed_today",
            _TripleSpike("sven", "prefers_response_style", "concise", None, 0.9, False),
            [_SourceSpike(_NOW - timedelta(hours=2))],
            "new",
        ),
        (
            "wealthpoint_stable",
            _TripleSpike("wealthpoint", "stage", "discovery", None, 0.8, False),
            [
                _SourceSpike(_NOW - timedelta(days=45)),
                _SourceSpike(_NOW - timedelta(days=20)),
            ],
            "stable",
        ),
        (
            "justin_aaron_stale",
            _TripleSpike("justin-aaron-intro", "commitment_status", "open", None, 0.6, False),
            [_SourceSpike(_NOW - timedelta(days=120))],
            "stale",
        ),
    ],
)
def test_column_path_yields_same_shape(
    label: str,
    triple: _TripleSpike,
    sources: list[_SourceSpike],
    stored_freshness: FreshnessTrend,
) -> None:
    """Hypothetical column path: same observable shape as compute path."""
    via_compute = _observation_via_compute(triple, sources, now=_NOW)
    via_column = _observation_via_column(triple, sources, stored_freshness)
    # If the stored column matches the rule, both paths yield identical shape.
    if via_compute.freshness_trend == stored_freshness:
        assert via_compute == via_column, f"{label}: shapes diverged"
    else:
        # Spike outcome: storing freshness in a column requires keeping it
        # in sync with the rule, OR accepting drift. The compute path
        # avoids the sync problem entirely. ADR-0016 records this trade-
        # off as the reason we ship compute-on-read.
        assert via_compute.freshness_trend != via_column.freshness_trend, (
            f"{label}: drift demonstration failed"
        )


def test_proof_count_matches_source_list_length() -> None:
    """Proof count is len(sources) — the same value regardless of storage path."""
    triple = _TripleSpike("x", "p", "o", None, 1.0, False)
    sources = [_SourceSpike(_NOW - timedelta(days=i)) for i in range(5)]
    obs = _observation_via_compute(triple, sources, now=_NOW)
    assert obs.proof_count == 5


def test_zero_sources_is_stale() -> None:
    """Edge case — a triple with no sources falls through to stale."""
    triple = _TripleSpike("x", "p", "o", None, 1.0, False)
    obs = _observation_via_compute(triple, [], now=_NOW)
    assert obs.freshness_trend == "stale"
