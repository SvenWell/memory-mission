"""Tests for ``salience_score`` (formula ported from agentic-stack)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from memory_mission.memory import (
    MAX_RECENCY,
    NEUTRAL_SCORE,
    RECENCY_DECAY_PER_DAY,
    RECURRENCE_CAP,
    salience_score,
)

# ---------- Helpers ----------


def _entry(*, age_days: float = 0.0, **fields: Any) -> dict[str, Any]:
    ts = datetime.now(UTC) - timedelta(days=age_days)
    return {"timestamp": ts.isoformat(), **fields}


# ---------- Constants ----------


def test_constants_match_agentic_stack_starting_values() -> None:
    assert MAX_RECENCY == 10.0
    assert RECENCY_DECAY_PER_DAY == 0.3
    assert NEUTRAL_SCORE == 5.0
    assert RECURRENCE_CAP == 3


# ---------- Missing / malformed timestamp ----------


def test_missing_timestamp_returns_zero() -> None:
    assert salience_score({}) == 0.0


def test_blank_timestamp_returns_zero() -> None:
    assert salience_score({"timestamp": ""}) == 0.0


def test_unparseable_timestamp_returns_zero() -> None:
    assert salience_score({"timestamp": "not-a-date"}) == 0.0


# ---------- Recency dominates ----------


def test_brand_new_neutral_entry_scores_2_5() -> None:
    """Brand new (age=0) → recency=10. Neutral 5/5 → factor 0.25. Recurrence 1.
    10 * 0.5 * 0.5 * 1 = 2.5."""
    score = salience_score(_entry(age_days=0))
    assert score == pytest.approx(2.5)


def test_recency_decays_linearly() -> None:
    """Same neutral entry, 10 days old: recency = 10 - 10*0.3 = 7."""
    score = salience_score(_entry(age_days=10))
    expected = 7.0 * 0.5 * 0.5 * 1
    assert score == pytest.approx(expected)


def test_recency_floors_at_zero() -> None:
    """Past ~33 days, recency hits zero and the whole score collapses."""
    assert salience_score(_entry(age_days=100)) == 0.0


# ---------- Pain / importance multiply ----------


def test_pain_scales_score() -> None:
    base = salience_score(_entry(age_days=0))
    high_pain = salience_score(_entry(age_days=0, pain_score=10))
    # pain 10/10 vs default 5/10 → 2x
    assert high_pain == pytest.approx(base * 2)


def test_importance_scales_score() -> None:
    base = salience_score(_entry(age_days=0))
    high = salience_score(_entry(age_days=0, importance=10))
    assert high == pytest.approx(base * 2)


def test_zero_pain_zeroes_score() -> None:
    assert salience_score(_entry(age_days=0, pain_score=0)) == 0.0


def test_zero_importance_zeroes_score() -> None:
    assert salience_score(_entry(age_days=0, importance=0)) == 0.0


# ---------- Recurrence ----------


def test_recurrence_one_is_baseline() -> None:
    base = salience_score(_entry(age_days=0))
    again = salience_score(_entry(age_days=0, recurrence_count=1))
    assert base == pytest.approx(again)


def test_recurrence_two_doubles() -> None:
    base = salience_score(_entry(age_days=0))
    twice = salience_score(_entry(age_days=0, recurrence_count=2))
    assert twice == pytest.approx(base * 2)


def test_recurrence_caps_at_three() -> None:
    """Cap = 3; a count of 99 contributes the same as 3."""
    capped = salience_score(_entry(age_days=0, recurrence_count=99))
    triple = salience_score(_entry(age_days=0, recurrence_count=3))
    assert capped == pytest.approx(triple)


# ---------- Naive vs aware datetimes ----------


def test_naive_timestamp_handled_via_utc_default() -> None:
    """Old agent-stack entries used naive ISO strings — must still parse."""
    naive_now = datetime.now(UTC).replace(tzinfo=None)
    entry = {"timestamp": naive_now.isoformat(), "pain_score": 5}
    score = salience_score(entry)
    # Same shape as a freshly-stamped entry: ~2.5
    assert score == pytest.approx(2.5, rel=0.1)


# ---------- Composite ranking sanity ----------


def test_painful_recurring_old_can_outrank_fresh_neutral() -> None:
    """The point of the formula: a 30-day-old urgent recurring entry
    should outrank a fresh boring one."""
    fresh_neutral = salience_score(_entry(age_days=0))
    old_urgent = salience_score(
        _entry(age_days=20, pain_score=10, importance=10, recurrence_count=3)
    )
    assert old_urgent > fresh_neutral
