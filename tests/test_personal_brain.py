"""Tests for the per-employee brain layers (step 12b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.personal_brain import (
    AgentLearning,
    EpisodicLog,
    Lesson,
    LessonsStore,
    Preferences,
    WorkingState,
    archive_stale,
    episodic_dir,
    learnings_path,
    lesson_id,
    lessons_dir,
    markdown_path,
    preferences_dir,
    preferences_path,
    read_preferences,
    read_working_state,
    record_learning,
    update_preferences,
    workspace_path,
    write_preferences,
    write_working_state,
)

# ---------- Path layout (every primitive lives under personal/<emp>/<layer>/) ----------


def test_working_dir_under_personal_layer(tmp_path: Path) -> None:
    assert workspace_path(tmp_path, "alice") == (tmp_path / "personal/alice/working/WORKSPACE.md")


def test_episodic_dir_under_personal_layer(tmp_path: Path) -> None:
    assert learnings_path(tmp_path, "alice") == (
        tmp_path / "personal/alice/episodic/AGENT_LEARNINGS.jsonl"
    )


def test_preferences_dir_under_personal_layer(tmp_path: Path) -> None:
    assert preferences_path(tmp_path, "alice") == (
        tmp_path / "personal/alice/preferences/PREFERENCES.md"
    )


def test_lessons_dir_under_personal_layer(tmp_path: Path) -> None:
    assert markdown_path(tmp_path, "alice") == (tmp_path / "personal/alice/lessons/LESSONS.md")


def test_layer_dirs_reject_bad_employee_id(tmp_path: Path) -> None:
    for bad in ["", "../escape", "with space"]:
        with pytest.raises(ValueError):
            episodic_dir(tmp_path, bad)
        with pytest.raises(ValueError):
            lessons_dir(tmp_path, bad)
        with pytest.raises(ValueError):
            preferences_dir(tmp_path, bad)


# ---------- Working state ----------


def test_working_state_round_trip(tmp_path: Path) -> None:
    state = WorkingState(
        employee_id="alice",
        focus="Drafting Q3 LP letter",
        open_items=["Get Q2 numbers from finance", "Schedule LP review call"],
        body="## Notes\n\nLast year's letter ran long; aim for 2 pages.",
    )
    write_working_state(tmp_path, state)
    loaded = read_working_state(tmp_path, "alice")
    assert loaded is not None
    assert loaded.focus == state.focus
    assert loaded.open_items == state.open_items
    assert "2 pages" in loaded.body


def test_working_state_missing_returns_none(tmp_path: Path) -> None:
    assert read_working_state(tmp_path, "alice") is None


def test_working_state_overwrite_replaces(tmp_path: Path) -> None:
    write_working_state(
        tmp_path,
        WorkingState(employee_id="alice", focus="first", open_items=["a"]),
    )
    write_working_state(
        tmp_path,
        WorkingState(employee_id="alice", focus="second", open_items=["b"]),
    )
    loaded = read_working_state(tmp_path, "alice")
    assert loaded is not None
    assert loaded.focus == "second"
    assert loaded.open_items == ["b"]


def test_archive_stale_no_op_when_recent(tmp_path: Path) -> None:
    write_working_state(
        tmp_path,
        WorkingState(employee_id="alice", focus="recent"),
    )
    archived = archive_stale(tmp_path, "alice", older_than=timedelta(days=2))
    assert archived is None
    assert workspace_path(tmp_path, "alice").exists()


def test_archive_stale_moves_old_workspace(tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=5)
    write_working_state(
        tmp_path,
        WorkingState(employee_id="alice", focus="ancient", updated_at=old),
    )
    archived = archive_stale(tmp_path, "alice", older_than=timedelta(days=2))
    assert archived is not None
    assert archived.exists()
    assert not workspace_path(tmp_path, "alice").exists()
    assert ".archive" in str(archived)


# ---------- Episodic log ----------


def test_episodic_append_then_all(tmp_path: Path) -> None:
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    log.append(AgentLearning(skill="extract", action="parsed report", outcome="success"))
    log.append(AgentLearning(skill="ship", action="opened PR", outcome="success"))
    entries = log.all()
    assert len(entries) == 2
    assert entries[0].skill == "extract"
    assert entries[1].skill == "ship"


def test_episodic_filter_by_skill(tmp_path: Path) -> None:
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    log.append(AgentLearning(skill="extract", action="x", outcome="success"))
    log.append(AgentLearning(skill="ship", action="y", outcome="error"))
    log.append(AgentLearning(skill="extract", action="z", outcome="success"))
    extracts = log.filter(skill="extract")
    assert len(extracts) == 2
    assert all(e.skill == "extract" for e in extracts)


def test_episodic_filter_by_outcome(tmp_path: Path) -> None:
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    log.append(AgentLearning(skill="extract", action="x", outcome="success"))
    log.append(AgentLearning(skill="extract", action="y", outcome="error"))
    errors = log.filter(outcome="error")
    assert len(errors) == 1


def test_episodic_top_k_uses_salience(tmp_path: Path) -> None:
    """High-pain recent recurring entry beats an old neutral one."""
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    log.append(
        AgentLearning(
            skill="ship",
            action="ancient mistake",
            outcome="error",
            timestamp=datetime.now(UTC) - timedelta(days=20),
            pain_score=5,
            importance=5,
        )
    )
    log.append(
        AgentLearning(
            skill="extract",
            action="painful recent recurring problem",
            outcome="error",
            pain_score=10,
            importance=10,
            recurrence_count=3,
        )
    )
    top = log.top_k(2)
    assert top[0].action == "painful recent recurring problem"


def test_episodic_top_k_respects_k(tmp_path: Path) -> None:
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    for i in range(5):
        log.append(AgentLearning(skill="extract", action=f"step-{i}", outcome="success"))
    assert len(log.top_k(2)) == 2
    assert len(log.top_k(10)) == 5


def test_record_learning_convenience(tmp_path: Path) -> None:
    entry = record_learning(
        wiki_root=tmp_path,
        employee_id="alice",
        skill="extract",
        action="parsed report",
        outcome="success",
        pain_score=3,
        importance=8,
    )
    assert entry.skill == "extract"
    log = EpisodicLog(wiki_root=tmp_path, employee_id="alice")
    assert len(log.all()) == 1


# ---------- Preferences ----------


def test_preferences_round_trip(tmp_path: Path) -> None:
    prefs = Preferences(
        employee_id="alice",
        name="Alice Chen",
        timezone="America/New_York",
        communication_style="direct, numbers-heavy",
        body="## Notes\n\nPrefer one-line summaries before details.",
    )
    write_preferences(tmp_path, prefs)
    loaded = read_preferences(tmp_path, "alice")
    assert loaded is not None
    assert loaded.name == "Alice Chen"
    assert loaded.communication_style == "direct, numbers-heavy"
    assert "one-line summaries" in loaded.body


def test_preferences_missing_returns_none(tmp_path: Path) -> None:
    assert read_preferences(tmp_path, "alice") is None


def test_update_preferences_merges_fields(tmp_path: Path) -> None:
    write_preferences(
        tmp_path,
        Preferences(
            employee_id="alice",
            name="Alice",
            timezone="UTC",
            communication_style="direct",
        ),
    )
    updated = update_preferences(tmp_path, "alice", explanation_style="terse")
    assert updated.name == "Alice"  # preserved
    assert updated.timezone == "UTC"  # preserved
    assert updated.communication_style == "direct"  # preserved
    assert updated.explanation_style == "terse"  # new


def test_update_preferences_creates_when_missing(tmp_path: Path) -> None:
    updated = update_preferences(tmp_path, "alice", name="Alice", timezone="UTC")
    assert updated.employee_id == "alice"
    assert updated.name == "Alice"


def test_preferences_extra_fields_round_trip(tmp_path: Path) -> None:
    """Custom firm-specific fields land in extras and survive round-trip."""
    write_preferences(
        tmp_path,
        Preferences(
            employee_id="alice",
            firm_role="partner",  # type: ignore[call-arg]
            preferred_models=["claude", "gemini"],  # type: ignore[call-arg]
        ),
    )
    raw_text = preferences_path(tmp_path, "alice").read_text()
    assert "firm_role: partner" in raw_text
    loaded = read_preferences(tmp_path, "alice")
    assert loaded is not None
    extras = loaded.model_extra or {}
    assert extras.get("firm_role") == "partner"
    assert extras.get("preferred_models") == ["claude", "gemini"]


# ---------- Lessons ----------


def test_lesson_id_deterministic_and_case_insensitive() -> None:
    a = lesson_id("Always serialize timestamps in UTC")
    b = lesson_id("ALWAYS SERIALIZE TIMESTAMPS IN UTC")
    c = lesson_id("Always serialize timestamps in UTC")
    assert a == b == c


def test_lessons_append_and_read_back(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    store.append(
        rule="Always serialize timestamps in UTC",
        rationale="cross-region bug bit us last quarter",
        source_skill="ship",
    )
    lessons = store.all()
    assert len(lessons) == 1
    assert lessons[0].source_skill == "ship"


def test_lessons_append_is_idempotent_by_rule(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    store.append(rule="UTC always", rationale="first")
    store.append(rule="UTC always", rationale="second")
    assert len(store.all()) == 1


def test_lessons_render_writes_markdown(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    store.append(
        rule="Always serialize timestamps in UTC",
        rationale="cross-region bug",
        source_skill="ship",
    )
    md = store.markdown_path.read_text()
    assert "Always serialize timestamps in UTC" in md
    assert "cross-region bug" in md
    assert "from `ship`" in md


def test_lessons_render_handles_empty(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    text = store.render()
    assert "no lessons learned yet" in text


def test_lessons_filter_by_source_skill(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    store.append(rule="A", rationale="x", source_skill="ship")
    store.append(rule="B", rationale="y", source_skill="extract")
    store.append(rule="C", rationale="z", source_skill="ship")
    ship = store.filter(source_skill="ship")
    assert {lsn.rule for lsn in ship} == {"A", "C"}


def test_lessons_render_orders_newest_first(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    store.append(rule="first", rationale="x")
    store.append(rule="second", rationale="y")
    store.append(rule="third", rationale="z")
    md = store.render()
    # 'third' (newest) should appear before 'first' in the rendered view.
    assert md.index("third") < md.index("first")


def test_lessons_rejects_empty_inputs(tmp_path: Path) -> None:
    store = LessonsStore(wiki_root=tmp_path, employee_id="alice")
    with pytest.raises(ValueError, match="rule"):
        store.append(rule="", rationale="x")
    with pytest.raises(ValueError, match="rationale"):
        store.append(rule="x", rationale="")


def test_lesson_confidence_must_be_in_range() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Lesson(
            lesson_id="x",
            rule="r",
            rationale="why",
            learned_at=datetime.now(UTC),
            confidence=1.5,
        )
