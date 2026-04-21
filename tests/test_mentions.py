"""Tests for the entity mention tracker (step 7a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.ingestion import (
    TIER_THRESHOLDS,
    MentionTracker,
    tier_for_count,
)

# ---------- Tier mapping ----------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "none"),
        (1, "stub"),
        (2, "stub"),
        (3, "enrich"),
        (7, "enrich"),
        (8, "full"),
        (100, "full"),
    ],
)
def test_tier_for_count(count: int, expected: str) -> None:
    assert tier_for_count(count) == expected


def test_thresholds_are_gbrain_starting_values() -> None:
    assert TIER_THRESHOLDS == {"stub": 1, "enrich": 3, "full": 8}


# ---------- Fixtures ----------


@pytest.fixture
def tracker(tmp_path: Path) -> MentionTracker:
    return MentionTracker(tmp_path / "mentions.sqlite3")


# ---------- record() ----------


def test_record_first_mention_returns_none_to_stub(
    tracker: MentionTracker,
) -> None:
    prev, new = tracker.record("sarah-chen")
    assert (prev, new) == ("none", "stub")


def test_record_second_mention_stays_stub(tracker: MentionTracker) -> None:
    tracker.record("sarah-chen")
    prev, new = tracker.record("sarah-chen")
    assert (prev, new) == ("stub", "stub")


def test_record_third_mention_crosses_to_enrich(
    tracker: MentionTracker,
) -> None:
    for _ in range(2):
        tracker.record("sarah-chen")
    prev, new = tracker.record("sarah-chen")
    assert (prev, new) == ("stub", "enrich")


def test_record_eighth_mention_crosses_to_full(
    tracker: MentionTracker,
) -> None:
    for _ in range(7):
        tracker.record("sarah-chen")
    prev, new = tracker.record("sarah-chen")
    assert (prev, new) == ("enrich", "full")


def test_record_after_full_stays_full(tracker: MentionTracker) -> None:
    for _ in range(8):
        tracker.record("sarah-chen")
    prev, new = tracker.record("sarah-chen")
    assert (prev, new) == ("full", "full")


def test_record_rejects_empty_name(tracker: MentionTracker) -> None:
    with pytest.raises(ValueError, match="empty"):
        tracker.record("")


def test_threshold_crossings_can_be_detected(tracker: MentionTracker) -> None:
    """Caller pattern: trigger enrichment only when prev_tier < new_tier."""
    crossings: list[tuple[str, str]] = []
    for _ in range(10):
        prev, new = tracker.record("acme-corp")
        if new != prev:
            crossings.append((prev, new))
    # 1: none→stub, 3: stub→enrich, 8: enrich→full
    assert crossings == [("none", "stub"), ("stub", "enrich"), ("enrich", "full")]


# ---------- get / all / stats ----------


def test_get_returns_record_with_tier(tracker: MentionTracker) -> None:
    for _ in range(4):
        tracker.record("sarah-chen")
    rec = tracker.get("sarah-chen")
    assert rec is not None
    assert rec.name == "sarah-chen"
    assert rec.count == 4
    assert rec.tier == "enrich"
    assert rec.first_seen <= rec.last_seen


def test_get_missing_returns_none(tracker: MentionTracker) -> None:
    assert tracker.get("nobody") is None


def test_all_orders_by_count_desc_then_name(tracker: MentionTracker) -> None:
    tracker.record("acme")  # 1
    for _ in range(3):
        tracker.record("sarah-chen")  # 3
    tracker.record("bob")  # 1
    names = [r.name for r in tracker.all()]
    assert names == ["sarah-chen", "acme", "bob"]


def test_stats_counts_by_tier(tracker: MentionTracker) -> None:
    # one stub, one enrich, one full
    tracker.record("a")
    for _ in range(3):
        tracker.record("b")
    for _ in range(8):
        tracker.record("c")
    stats = tracker.stats()
    assert stats == {"none": 0, "stub": 1, "enrich": 1, "full": 1}


def test_stats_empty_when_no_mentions(tracker: MentionTracker) -> None:
    assert tracker.stats() == {"none": 0, "stub": 0, "enrich": 0, "full": 0}


# ---------- Persistence ----------


def test_tracker_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "mentions.sqlite3"
    with MentionTracker(db) as t1:
        for _ in range(4):
            t1.record("sarah-chen")
    with MentionTracker(db) as t2:
        rec = t2.get("sarah-chen")
        assert rec is not None
        assert rec.count == 4
        assert rec.tier == "enrich"


def test_close_is_idempotent(tmp_path: Path) -> None:
    t = MentionTracker(tmp_path / "x.sqlite3")
    t.close()
    t.close()


def test_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "mentions.sqlite3"
    MentionTracker(nested).close()
    assert nested.exists()


def test_per_firm_isolation(tmp_path: Path) -> None:
    """Different DB paths = different counts. No cross-firm leakage."""
    with (
        MentionTracker(tmp_path / "firm-a.sqlite3") as a,
        MentionTracker(tmp_path / "firm-b.sqlite3") as b,
    ):
        a.record("shared-name")
        a.record("shared-name")
        b.record("shared-name")

        rec_a = a.get("shared-name")
        rec_b = b.get("shared-name")
        assert rec_a is not None and rec_a.count == 2
        assert rec_b is not None and rec_b.count == 1
