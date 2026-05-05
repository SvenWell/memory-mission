"""Tests for eval replay — re-run captured tool calls and diff signatures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from memory_mission.eval.captures import (
    EvalCapturesStore,
    _result_signature,
)
from memory_mission.eval.replay import REPLAYABLE_TOOLS, replay_captures
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

# ---------- fixtures ----------


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


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EvalCapturesStore]:
    s = EvalCapturesStore(tmp_path / "captures.sqlite3")
    try:
        yield s
    finally:
        s.close()


# ---------- replayable tools allowlist ----------


def test_replayable_tools_lists_query_entity() -> None:
    assert "mm_query_entity" in REPLAYABLE_TOOLS


def test_boot_context_not_replayable_yet() -> None:
    """task_hint redaction blocks faithful replay; deferred to follow-up."""
    assert "mm_boot_context" not in REPLAYABLE_TOOLS


# ---------- replay basic outcomes ----------


def _record_query_capture(store: EvalCapturesStore, kg: PersonalKnowledgeGraph, name: str) -> int:
    """Capture an mm_query_entity-shaped result so replay can target it.

    We compute the result the same way the live tool does (filtered to
    currently-true triples + serialized model_dump) so replay-at-HEAD
    matches the captured signature when the substrate is unchanged.
    """
    triples = kg.query_entity(name, direction="outgoing")
    triples = [t for t in triples if t.valid_to is None]
    result = [t.model_dump(mode="json") for t in triples]
    return store.write(
        user_id="sven",
        tool_name="mm_query_entity",
        args={"name": name, "direction": "outgoing", "as_of": None},
        result=result,
    )


def test_replay_matches_when_kg_unchanged(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    _record_query_capture(store, kg, "alice")
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.total == 1
    assert result.matches == 1
    assert result.differs == 0
    assert result.skipped == 0
    assert result.match_rate == 1.0


def test_replay_detects_drift_when_triple_added(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    _record_query_capture(store, kg, "alice")
    # New triple lands AFTER capture — replay should differ.
    kg.add_triple("alice", "knows", "bob", source_closet="ev", source_file="m2")
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.matches == 0
    assert result.differs == 1
    assert result.match_rate == 0.0


def test_replay_skips_unsupported_tool(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={"task_hint": {"_redacted": True, "length": 10, "hash": "x"}},
        result={"render": "ignored"},
    )
    result = replay_captures(store=store, kg=kg, limit=10)
    assert result.skipped == 1
    assert result.matches == 0
    assert result.differs == 0
    assert any("tool_not_replayable" in r for r in result.skip_reasons)


def test_replay_filters_by_tool_name(store: EvalCapturesStore, kg: PersonalKnowledgeGraph) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={"render": "x"},
    )
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    _record_query_capture(store, kg, "alice")
    # Filter to only mm_query_entity — boot_context capture is excluded.
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.total == 1
    assert result.matches == 1


def test_replay_skips_capture_with_invalid_args_json(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    # Manually insert a row with bad args_json — simulates corruption.
    store._conn.execute(  # noqa: SLF001 - test-only access
        """
        INSERT INTO eval_captures (
            captured_at, user_id, tool_name, args_json,
            result_signature, result_json, latency_ms, mm_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-05T00:00:00+00:00",
            "sven",
            "mm_query_entity",
            "this is not json",
            _result_signature([]),
            "[]",
            None,
            None,
        ),
    )
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.skipped == 1
    assert result.skip_reasons.get("args_json_invalid") == 1


def test_replay_skips_capture_with_missing_name(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_query_entity",
        args={"direction": "outgoing"},  # name missing
        result=[],
    )
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.skipped == 1
    assert result.skip_reasons.get("name_missing") == 1


def test_replay_skips_capture_with_invalid_direction(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_query_entity",
        args={"name": "alice", "direction": "sideways"},
        result=[],
    )
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.skipped == 1
    assert result.skip_reasons.get("direction_invalid") == 1


def test_replay_skips_capture_with_invalid_as_of(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_query_entity",
        args={"name": "alice", "direction": "outgoing", "as_of": "not-a-date"},
        result=[],
    )
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    assert result.skipped == 1
    assert result.skip_reasons.get("as_of_invalid") == 1


# ---------- summary helpers ----------


def test_summary_includes_match_rate_when_replayed(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    _record_query_capture(store, kg, "alice")
    result = replay_captures(store=store, kg=kg, tool_name="mm_query_entity", limit=10)
    text = result.summary()
    assert "Total captures replayed: 1" in text
    assert "matches:  1" in text
    assert "Match rate: 100.0%" in text


def test_summary_includes_skip_reasons(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={"render": "x"},
    )
    result = replay_captures(store=store, kg=kg, limit=10)
    text = result.summary()
    assert "skipped:  1" in text
    assert "Skip reasons" in text


def test_summary_omits_match_rate_when_all_skipped(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={},
    )
    result = replay_captures(store=store, kg=kg, limit=10)
    assert "Match rate" not in result.summary()


def test_match_rate_excludes_skipped(store: EvalCapturesStore, kg: PersonalKnowledgeGraph) -> None:
    """Skipped captures must not affect match_rate (denominator)."""
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    _record_query_capture(store, kg, "alice")
    # Add one skipped capture (unsupported tool).
    store.write(user_id="sven", tool_name="mm_boot_context", args={}, result={})
    result = replay_captures(store=store, kg=kg, limit=10)
    assert result.matches == 1
    assert result.skipped == 1
    assert result.match_rate == 1.0  # 1 match / (1 match + 0 differs) — skipped excluded


def test_replay_handles_empty_store(store: EvalCapturesStore, kg: PersonalKnowledgeGraph) -> None:
    result = replay_captures(store=store, kg=kg, limit=10)
    assert result.total == 0
    assert result.matches == 0
    assert result.differs == 0
    assert result.skipped == 0
    assert result.match_rate == 0.0


# ---------- args_json shape sanity ----------


def test_captured_args_json_is_canonical_for_query_entity(
    store: EvalCapturesStore, kg: PersonalKnowledgeGraph
) -> None:
    """mm_query_entity args are pass-through (no scrubbing) — replay relies on this."""
    kg.add_triple("alice", "works_at", "acme", source_closet="ev", source_file="m1")
    cid = _record_query_capture(store, kg, "alice")
    capture = store.get(cid)
    assert capture is not None
    args = json.loads(capture.args_json)
    assert args["name"] == "alice"
    assert args["direction"] == "outgoing"
    assert args["as_of"] is None
