"""Salience scoring: recent + painful + important + recurring = surface first.

Ported from agentic-stack's ``memory/salience.py`` (Apache 2.0,
https://github.com/codejunkie99/agentic-stack). The formula:

    salience = recency * (pain / 10) * (importance / 10) * min(recurrence, 3)

- ``recency``  = ``max(0, 10 - age_days * 0.3)`` — decays linearly to zero at
  ~33 days; an entry from today scores 10.
- ``pain``     = how much it hurt when we got it wrong / how loud the user
  complained. Default 5 (neutral) when unknown.
- ``importance`` = how much downstream work depends on it. Default 5.
- ``recurrence`` = how many times this pattern has come up. Capped at 3 so
  one persistent fact can't drown out genuinely new signal.

We adapted the input shape: callers pass any dict with optional
``timestamp`` (ISO-8601), ``pain_score``, ``importance``, and
``recurrence_count`` keys. Missing keys take neutral defaults so the
function works on observability events, KG triples, promotion
candidates, or any future record type without forcing a schema change.

Use cases (now and later):
- Promotion pipeline (Step 9) ranks staged candidates by salience
- Retrieval ranker can weight episodic memory by salience
- Decay / archive policies use salience as the "should we keep it" gate
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

NEUTRAL_SCORE = 5.0
RECURRENCE_CAP = 3
MAX_RECENCY = 10.0
RECENCY_DECAY_PER_DAY = 0.3


def salience_score(entry: dict[str, Any]) -> float:
    """Score an entry's "surface this first" priority.

    Returns ``0.0`` when ``timestamp`` is missing or unparseable — entries
    without time can't be ranked by recency, and recency dominates the
    formula. Score is unbounded above (high pain × high importance × old
    recurring entry will exceed 10) so callers can use raw thresholds.
    """
    ts_str = entry.get("timestamp")
    if not ts_str:
        return 0.0
    try:
        ts = datetime.fromisoformat(str(ts_str))
    except ValueError:
        return 0.0

    # Naive timestamps (legacy entries) get treated as UTC so subtraction
    # against ``now`` doesn't crash with mixed-aware-naive errors.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    recency = max(0.0, MAX_RECENCY - age_days * RECENCY_DECAY_PER_DAY)

    pain = float(entry.get("pain_score", NEUTRAL_SCORE))
    importance = float(entry.get("importance", NEUTRAL_SCORE))
    recurrence = min(int(entry.get("recurrence_count", 1)), RECURRENCE_CAP)

    return recency * (pain / 10.0) * (importance / 10.0) * recurrence
