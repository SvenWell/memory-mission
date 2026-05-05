"""Tests for the eval-capture infrastructure (BrainBench-Real-style).

Covers:

- ``is_capture_enabled`` env gating
- ``EvalCapturesStore`` round-trip + stats
- ``record_eval_capture`` opt-in semantics + failure swallowing
- ``scrub_args_for_capture`` redacts free-text + passes entity names
- Result signature is deterministic + sensitive to changes
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from memory_mission.eval.captures import (
    CONTRIBUTOR_MODE_ENV,
    EvalCapturesStore,
    _result_signature,
    captures_path_for,
    is_capture_enabled,
    record_eval_capture,
)
from memory_mission.eval.pii_scrub import scrub_args_for_capture


@pytest.fixture
def disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTRIBUTOR_MODE_ENV, raising=False)


@pytest.fixture
def enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTRIBUTOR_MODE_ENV, "1")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EvalCapturesStore]:
    s = EvalCapturesStore(tmp_path / "captures.sqlite3")
    try:
        yield s
    finally:
        s.close()


# ---------- env gating ----------


def test_is_capture_enabled_off_by_default(disabled_env: None) -> None:
    assert is_capture_enabled() is False


def test_is_capture_enabled_recognizes_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for truthy in ("1", "true", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv(CONTRIBUTOR_MODE_ENV, truthy)
        assert is_capture_enabled() is True


def test_is_capture_enabled_rejects_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for falsy in ("0", "false", "no", "off", "", "random"):
        monkeypatch.setenv(CONTRIBUTOR_MODE_ENV, falsy)
        assert is_capture_enabled() is False


# ---------- captures_path_for ----------


def test_captures_path_for_uses_personal_layout() -> None:
    p = captures_path_for(root=Path("/x/y"), user_id="sven")
    assert p == Path("/x/y/personal/sven/eval_captures.sqlite3")


# ---------- store round-trip ----------


def test_store_write_returns_capture_id(store: EvalCapturesStore) -> None:
    cid = store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={"task_hint": "draft email", "token_budget": 2000},
        result={"render": "## boot\n", "active_threads": []},
        latency_ms=42,
        mm_version="0.1.4",
    )
    assert cid > 0


def test_store_list_returns_most_recent_first(store: EvalCapturesStore) -> None:
    for i in range(3):
        store.write(
            user_id="sven",
            tool_name="mm_query_entity",
            args={"name": f"alice-{i}"},
            result=[],
        )
    captures = store.list_captures(limit=10)
    assert len(captures) == 3
    assert captures[0].capture_id > captures[1].capture_id > captures[2].capture_id


def test_store_filter_by_tool_name(store: EvalCapturesStore) -> None:
    store.write(user_id="sven", tool_name="mm_boot_context", args={}, result={})
    store.write(user_id="sven", tool_name="mm_query_entity", args={"name": "x"}, result=[])
    boot_only = store.list_captures(tool_name="mm_boot_context")
    assert len(boot_only) == 1
    assert boot_only[0].tool_name == "mm_boot_context"


def test_store_get_returns_full_capture(store: EvalCapturesStore) -> None:
    cid = store.write(
        user_id="sven",
        tool_name="mm_boot_context",
        args={"task_hint": "x", "token_budget": 1000},
        result={"render": "ok"},
        latency_ms=5,
    )
    captured = store.get(cid)
    assert captured is not None
    assert captured.tool_name == "mm_boot_context"
    assert captured.user_id == "sven"
    assert captured.latency_ms == 5
    assert captured.result_json is not None
    assert "ok" in captured.result_json


def test_store_get_returns_none_for_unknown_id(store: EvalCapturesStore) -> None:
    assert store.get(99999) is None


def test_store_stats_reports_per_tool_counts(store: EvalCapturesStore) -> None:
    store.write(user_id="sven", tool_name="mm_boot_context", args={}, result={})
    store.write(user_id="sven", tool_name="mm_boot_context", args={}, result={})
    store.write(user_id="sven", tool_name="mm_query_entity", args={"name": "x"}, result=[])
    stats = store.stats()
    assert stats["total"] == 3
    assert stats["per_tool"]["mm_boot_context"] == 2
    assert stats["per_tool"]["mm_query_entity"] == 1


# ---------- record_eval_capture (top-level helper) ----------


def test_record_eval_capture_noop_when_disabled(disabled_env: None, tmp_path: Path) -> None:
    record_eval_capture(
        captures_path=tmp_path / "captures.sqlite3",
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={},
    )
    # File never created when capture is disabled.
    assert not (tmp_path / "captures.sqlite3").exists()


def test_record_eval_capture_writes_when_enabled(enabled_env: None, tmp_path: Path) -> None:
    record_eval_capture(
        captures_path=tmp_path / "captures.sqlite3",
        user_id="sven",
        tool_name="mm_boot_context",
        args={"task_hint": "x"},
        result={"render": "ok"},
        latency_ms=10,
        mm_version="0.1.4",
    )
    s = EvalCapturesStore(tmp_path / "captures.sqlite3")
    try:
        captures = s.list_captures()
        assert len(captures) == 1
        assert captures[0].tool_name == "mm_boot_context"
        assert captures[0].mm_version == "0.1.4"
    finally:
        s.close()


def test_record_eval_capture_noop_when_path_is_none(enabled_env: None) -> None:
    # No exception even though we'd be enabled — capture just doesn't
    # write. This is the safe path when the context lacks a captures
    # path (e.g. a test harness that didn't set one).
    record_eval_capture(
        captures_path=None,
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={},
    )


def test_record_eval_capture_swallows_failures(enabled_env: None, tmp_path: Path) -> None:
    # Pass a path inside a non-existent root that we can't create
    # (read-only filesystem simulation via a file shadow). Capture
    # failure must not propagate.
    bad_parent = tmp_path / "blocked"
    bad_parent.write_text("i am a file, not a directory")
    bad_path = bad_parent / "captures.sqlite3"
    # Should NOT raise.
    record_eval_capture(
        captures_path=bad_path,
        user_id="sven",
        tool_name="mm_boot_context",
        args={},
        result={},
    )


# ---------- PII scrubbing ----------


def test_scrub_redacts_task_hint() -> None:
    out = scrub_args_for_capture("mm_boot_context", {"task_hint": "Prep call with Alice"})
    redacted = out["task_hint"]
    assert isinstance(redacted, dict)
    assert redacted["_redacted"] is True
    assert redacted["length"] == len("Prep call with Alice")
    assert isinstance(redacted["hash"], str)
    assert len(redacted["hash"]) == 16


def test_scrub_redacts_query_field() -> None:
    out = scrub_args_for_capture("mm_search_recall", {"query": "Wealthpoint follow-up"})
    assert out["query"]["_redacted"] is True


def test_scrub_passes_entity_name_through() -> None:
    # Entity names are the queries; scrubbing them breaks replay.
    out = scrub_args_for_capture("mm_query_entity", {"name": "alice"})
    assert out == {"name": "alice"}


def test_scrub_passes_non_string_values_through() -> None:
    out = scrub_args_for_capture(
        "mm_boot_context",
        {"token_budget": 4000, "as_of": None, "direction": "outgoing"},
    )
    assert out == {"token_budget": 4000, "as_of": None, "direction": "outgoing"}


def test_scrub_same_text_produces_same_hash() -> None:
    a = scrub_args_for_capture("mm_boot_context", {"task_hint": "draft email"})
    b = scrub_args_for_capture("mm_boot_context", {"task_hint": "draft email"})
    assert a["task_hint"]["hash"] == b["task_hint"]["hash"]


def test_scrub_different_text_produces_different_hash() -> None:
    a = scrub_args_for_capture("mm_boot_context", {"task_hint": "draft email"})
    b = scrub_args_for_capture("mm_boot_context", {"task_hint": "draft notes"})
    assert a["task_hint"]["hash"] != b["task_hint"]["hash"]


def test_scrub_does_not_mutate_input() -> None:
    args = {"task_hint": "do the thing", "name": "alice"}
    snapshot = dict(args)
    scrub_args_for_capture("mm_boot_context", args)
    assert args == snapshot


# ---------- Result signature ----------


def test_result_signature_is_deterministic() -> None:
    a = _result_signature({"render": "x", "n": 1})
    b = _result_signature({"n": 1, "render": "x"})  # different key order
    assert a == b


def test_result_signature_changes_on_value_change() -> None:
    a = _result_signature({"render": "x"})
    b = _result_signature({"render": "y"})
    assert a != b


def test_result_signature_handles_lists() -> None:
    sig = _result_signature([{"a": 1}, {"b": 2}])
    assert isinstance(sig, str)
    assert len(sig) == 64  # sha256 hex


# ---------- End-to-end capture (no integration with full server) ----------


def test_capture_pii_redaction_persists_to_disk(enabled_env: None, tmp_path: Path) -> None:
    """Sanity check: scrubbed args are what end up in the DB, not raw text."""
    record_eval_capture(
        captures_path=tmp_path / "captures.sqlite3",
        user_id="sven",
        tool_name="mm_boot_context",
        args={"task_hint": "highly sensitive content", "token_budget": 2000},
        result={"render": "ok"},
    )
    s = EvalCapturesStore(tmp_path / "captures.sqlite3")
    try:
        captures = s.list_captures()
        assert len(captures) == 1
        assert "highly sensitive content" not in captures[0].args_json
        assert "_redacted" in captures[0].args_json
    finally:
        s.close()
