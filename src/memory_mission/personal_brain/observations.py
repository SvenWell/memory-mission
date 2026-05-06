"""PersonalObservation — read-shape over Triple + triple_sources.

Per ADR-0016: an observation is what we currently believe about an
entity-predicate-object claim, plus how much evidence backs it and
whether the picture is strengthening, stable, or going stale. It is
NOT a separately persisted entity — every field is derived at read
time from rows that already exist in the personal KG.

Why a read-shape, not a stored row:

- Storing freshness as a column would commit us to a rule we'll likely
  iterate. Hindsight's exact `proof_count` increment + `freshness_trend`
  thresholds aren't publicly documented, so we'd be guessing the rule
  upfront. Computing on read lets us tune ``_compute_freshness`` in
  Python with unit tests, without a migration.
- ADR-0004 acceptance criterion #5 says new abstractions must be a
  net complexity reduction or wash. Read-shape adds zero new tables,
  zero new write paths, and one new query method — clear wash.
- Citations contract (ADR-0004 §3) is already satisfied by the
  underlying ``triple_sources`` rows; observations just expose them.

Boundary with the KG:

- Read-only. State changes ride on the existing ``record_facts`` /
  ``invalidate_fact`` MCP tools (Keagan, ``09e4e0d``).
- Corrections happen via ``invalidate_fact`` — not a new
  ``correct_observation`` write. The next read produces an updated
  observation reflecting the change.

Module shape:

- :class:`PersonalObservation` — frozen Pydantic read-shape.
- :class:`ObservationSource` — one corroboration entry, mirrored from
  ``TripleSource``.
- :func:`compute_freshness` — pure function implementing the ADR-0016
  rule. Public so tests can exercise it directly.
- :func:`build_observation` — turn a ``Triple`` + its ordered
  ``TripleSource`` history into a ``PersonalObservation``. Pure too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from memory_mission.memory.knowledge_graph import Triple, TripleSource
from memory_mission.memory.tiers import Tier

FreshnessTrend = Literal[
    "new",
    "strengthening",
    "stable",
    "weakening",
    "stale",
    "contradicted",
]


class ObservationSource(BaseModel):
    """One corroboration entry on a ``PersonalObservation``.

    Mirrors :class:`TripleSource` field-for-field so callers can read
    provenance without reaching for the underlying KG row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_closet: str | None = None
    source_file: str | None = None
    confidence_after: float
    added_at: datetime


class PersonalObservation(BaseModel):
    """Evidence-backed belief about a (subject, predicate, object) triple.

    Fields are derived from the underlying ``Triple`` + the ordered
    ``TripleSource`` history. ``proof_count`` is ``len(history)``;
    ``last_corroborated_at`` is ``MAX(history.added_at)``;
    ``freshness_trend`` is computed by :func:`compute_freshness`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    object: str
    proof_count: int
    freshness_trend: FreshnessTrend
    confidence: float
    last_corroborated_at: datetime
    valid_from: date | None
    valid_to: date | None
    history: list[ObservationSource]
    tier: Tier
    scope: str


@dataclass(frozen=True)
class FreshnessInputs:
    """Pure-data inputs to the freshness rule. Lets tests exercise edges
    without constructing real ``TripleSource`` objects."""

    last_corroborated_at: datetime
    proof_count: int
    sources_in_last_14d: int
    contradicted: bool
    now: datetime


def compute_freshness(inputs: FreshnessInputs) -> FreshnessTrend:
    """Apply the ADR-0016 freshness rule.

    Order of evaluation matters: ``contradicted`` wins over everything;
    ``new`` wins over ``strengthening``; etc. The rule is intentionally
    simple — Hindsight's exact thresholds aren't publicly documented,
    so this is iterable in code.
    """
    if inputs.contradicted:
        return "contradicted"
    age = inputs.now - inputs.last_corroborated_at
    if age < timedelta(days=7) and inputs.proof_count == 1:
        return "new"
    if inputs.sources_in_last_14d >= 2:
        return "strengthening"
    if inputs.proof_count > 1 and timedelta(days=14) <= age <= timedelta(days=30):
        return "stable"
    if timedelta(days=30) < age <= timedelta(days=60):
        return "weakening"
    if age > timedelta(days=60):
        return "stale"
    return "stable"


def build_observation(
    triple: Triple,
    sources: list[TripleSource],
    *,
    contradicted: bool = False,
    now: datetime | None = None,
) -> PersonalObservation:
    """Project a ``Triple`` + its ordered source history into an observation.

    Args:
        triple: the underlying ``Triple`` row from the KG.
        sources: per-corroboration ``TripleSource`` rows. May be empty —
            the substrate seeds at least one source on ``add_triple``,
            but read paths that join LEFT can yield empty lists for
            triples whose sources were never seeded (test fixtures,
            historical data). Empty source list -> ``stale``.
        contradicted: whether a coherence warning is currently open
            against this triple. The KG owns the warning state; the
            caller passes it through. Default ``False``.
        now: override for the freshness computation's "now" anchor.
            Defaults to ``datetime.now(UTC)``. Tests use this to make
            freshness assertions deterministic.

    Returns:
        A frozen :class:`PersonalObservation`.
    """
    now = now or datetime.now(UTC)
    history = [
        ObservationSource(
            source_closet=s.source_closet,
            source_file=s.source_file,
            confidence_after=s.confidence_after,
            added_at=s.added_at,
        )
        for s in sources
    ]
    if history:
        last_at = max(h.added_at for h in history)
        fourteen_days_ago = now - timedelta(days=14)
        sources_in_last_14d = sum(1 for h in history if h.added_at >= fourteen_days_ago)
    else:
        # No sources rows means we can't reason about recency; treat as
        # stale and anchor last_corroborated_at to the unix epoch so
        # downstream sort-by-recency keeps these at the bottom.
        last_at = datetime(1970, 1, 1, tzinfo=UTC)
        sources_in_last_14d = 0
    trend = compute_freshness(
        FreshnessInputs(
            last_corroborated_at=last_at,
            proof_count=len(history),
            sources_in_last_14d=sources_in_last_14d,
            contradicted=contradicted,
            now=now,
        )
    )
    return PersonalObservation(
        subject=triple.subject,
        predicate=triple.predicate,
        object=triple.object,
        proof_count=len(history),
        freshness_trend=trend,
        confidence=triple.confidence,
        last_corroborated_at=last_at,
        valid_from=triple.valid_from,
        valid_to=triple.valid_to,
        history=history,
        tier=triple.tier,
        scope=triple.scope,
    )


__all__ = [
    "FreshnessInputs",
    "FreshnessTrend",
    "ObservationSource",
    "PersonalObservation",
    "build_observation",
    "compute_freshness",
]
