"""Tests for the native tasks layer Phase A — write surface + list_tasks.

Covers the four MCP tools (mm_create_task / mm_update_task_status /
mm_complete_task / mm_list_tasks) via both the Hermes provider dispatch
path AND the substrate-level invariants (provenance mandatory, status
enum enforced, completion never deletes, soft co-existence with
commitments).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.integrations.hermes_provider import (
    TOOL_COMPLETE_TASK,
    TOOL_CREATE_TASK,
    TOOL_LIST_TASKS,
    TOOL_UPDATE_TASK_STATUS,
    MemoryMissionProvider,
)
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.synthesis.individual_boot import (
    TASK_ACTIVE_STATUSES,
    TASK_COMPLETED_AT_PREDICATE,
    TASK_DUE_PREDICATE,
    TASK_LINKED_THREAD_PREDICATE,
    TASK_OUTCOME_PREDICATE,
    TASK_OWNER_PREDICATE,
    TASK_STATUS_PREDICATE,
    TASK_STATUS_VALUES,
    TASK_TITLE_PREDICATE,
)


@pytest.fixture
def provider(tmp_path: Path) -> Iterator[MemoryMissionProvider]:
    """Provider wired to a tmp KG/engine/identity — no env vars needed."""
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path / "firm",
        employee_id="sven",
        identity_resolver=resolver,
    )
    engine = InMemoryEngine()
    engine.connect()
    p = MemoryMissionProvider()
    p.install_handles_for_test(
        user_id="sven",
        kg=kg,
        engine=engine,
        identity=resolver,
    )
    try:
        yield p
    finally:
        kg.close()


# ---------- Status enum invariants ----------


def test_task_status_values_match_eight_states() -> None:
    """The 8-state enum is the substrate's contract; pinning makes drift visible."""
    assert TASK_STATUS_VALUES == frozenset(
        {
            "open",
            "in_progress",
            "waiting",
            "blocked",
            "deferred",
            "completed",
            "cancelled",
            "superseded",
        }
    )


def test_active_subset_excludes_completed_cancelled_superseded() -> None:
    assert TASK_ACTIVE_STATUSES == frozenset(
        {"open", "in_progress", "waiting", "blocked", "deferred"}
    )
    assert "completed" not in TASK_ACTIVE_STATUSES
    assert "cancelled" not in TASK_ACTIVE_STATUSES
    assert "superseded" not in TASK_ACTIVE_STATUSES


# ---------- mm_create_task ----------


def test_create_task_returns_generated_task_id_with_prefix(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Intro Justin to Aaron",
            "source_closet": "telegram",
            "source_file": "msg-123",
        },
    )
    assert out["task_id"].startswith("task_")
    assert len(out["task_id"]) > len("task_")  # uuid hex appended


def test_create_task_writes_status_open_title_owner_triples(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Send the deck",
            "source_closet": "conversational",
            "source_file": "session-1",
        },
    )
    triples = out["triples"]
    predicates = {t["predicate"] for t in triples}
    assert TASK_STATUS_PREDICATE in predicates
    assert TASK_TITLE_PREDICATE in predicates
    assert TASK_OWNER_PREDICATE in predicates
    status_triple = next(t for t in triples if t["predicate"] == TASK_STATUS_PREDICATE)
    assert status_triple["object"] == "open"


def test_create_task_owner_defaults_to_user_id(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Default-owner task",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    assert out["owner"] == "sven"


def test_create_task_owner_override(provider: MemoryMissionProvider) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Delegated task",
            "owner": "alice",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    assert out["owner"] == "alice"


def test_create_task_with_due_at_writes_due_triple(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Time-sensitive",
            "due_at": "2026-05-10",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    due_triples = [t for t in out["triples"] if t["predicate"] == TASK_DUE_PREDICATE]
    assert len(due_triples) == 1
    assert due_triples[0]["object"] == "2026-05-10"


def test_create_task_with_linked_thread_writes_link_triple(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "Threaded work",
            "linked_thread": "memory-mission-build",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    link_triples = [t for t in out["triples"] if t["predicate"] == TASK_LINKED_THREAD_PREDICATE]
    assert len(link_triples) == 1
    assert link_triples[0]["object"] == "memory-mission-build"


def test_create_task_requires_provenance(provider: MemoryMissionProvider) -> None:
    """Empty source_closet + source_file is rejected per ADR-0015 invariant."""
    with pytest.raises(ValueError, match="source_closet"):
        provider.handle_tool_call(
            TOOL_CREATE_TASK,
            {"title": "no provenance", "source_closet": "", "source_file": "x"},
        )
    with pytest.raises(ValueError, match="source_file"):
        provider.handle_tool_call(
            TOOL_CREATE_TASK,
            {"title": "no source", "source_closet": "x", "source_file": ""},
        )


def test_create_task_returns_distinct_ids_across_calls(
    provider: MemoryMissionProvider,
) -> None:
    """uuid4 hex avoids collision; two creates -> two distinct task_ids."""
    a = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "first", "source_closet": "ev", "source_file": "f1"},
    )
    b = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "second", "source_closet": "ev", "source_file": "f2"},
    )
    assert a["task_id"] != b["task_id"]


# ---------- mm_update_task_status ----------


def test_update_task_status_invalidates_prior_status(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "transitioning", "source_closet": "ev", "source_file": "f1"},
    )
    task_id = create["task_id"]
    provider.handle_tool_call(
        TOOL_UPDATE_TASK_STATUS,
        {
            "task_id": task_id,
            "new_status": "in_progress",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    listed = provider.handle_tool_call(TOOL_LIST_TASKS, {})
    matching = [t for t in listed if t["task_id"] == task_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "in_progress"


def test_update_task_status_rejects_unknown_status(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "x", "source_closet": "ev", "source_file": "f1"},
    )
    with pytest.raises(ValueError, match="new_status must be one of"):
        provider.handle_tool_call(
            TOOL_UPDATE_TASK_STATUS,
            {
                "task_id": create["task_id"],
                "new_status": "halfway",
                "source_closet": "ev",
                "source_file": "f2",
            },
        )


def test_update_task_status_supports_all_eight_states(
    provider: MemoryMissionProvider,
) -> None:
    """Every documented status is acceptable as new_status."""
    for state in TASK_STATUS_VALUES:
        create = provider.handle_tool_call(
            TOOL_CREATE_TASK,
            {"title": f"to-{state}", "source_closet": "ev", "source_file": "f1"},
        )
        provider.handle_tool_call(
            TOOL_UPDATE_TASK_STATUS,
            {
                "task_id": create["task_id"],
                "new_status": state,
                "source_closet": "ev",
                "source_file": "f2",
            },
        )


def test_update_task_status_requires_provenance(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "x", "source_closet": "ev", "source_file": "f1"},
    )
    with pytest.raises(ValueError, match="source_closet"):
        provider.handle_tool_call(
            TOOL_UPDATE_TASK_STATUS,
            {
                "task_id": create["task_id"],
                "new_status": "in_progress",
                "source_closet": "",
                "source_file": "f2",
            },
        )


# ---------- mm_complete_task ----------


def test_complete_task_sets_status_completed_and_writes_completed_at(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "doable", "source_closet": "ev", "source_file": "f1"},
    )
    task_id = create["task_id"]
    out = provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": task_id,
            "completed_at": "2026-05-06",
            "source_closet": "conversational",
            "source_file": "session-2",
        },
    )
    assert out["completed_at"] == "2026-05-06"
    predicates = {t["predicate"] for t in out["triples"]}
    assert TASK_STATUS_PREDICATE in predicates
    assert TASK_COMPLETED_AT_PREDICATE in predicates
    status_triple = next(t for t in out["triples"] if t["predicate"] == TASK_STATUS_PREDICATE)
    assert status_triple["object"] == "completed"


def test_complete_task_with_outcome_writes_outcome_triple(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "intro", "source_closet": "ev", "source_file": "f1"},
    )
    out = provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": create["task_id"],
            "outcome": "Sven confirmed the intro was sent.",
            "source_closet": "telegram",
            "source_file": "msg-456",
        },
    )
    outcome_triples = [t for t in out["triples"] if t["predicate"] == TASK_OUTCOME_PREDICATE]
    assert len(outcome_triples) == 1
    assert outcome_triples[0]["object"] == "Sven confirmed the intro was sent."


def test_complete_task_omitting_completed_at_uses_today(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "today", "source_closet": "ev", "source_file": "f1"},
    )
    out = provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": create["task_id"],
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    assert out["completed_at"] == date.today().isoformat()


def test_complete_task_never_deletes_task_remains_listable(
    provider: MemoryMissionProvider,
) -> None:
    """Hermes brief invariant: a task is not removed when completed; it changes state."""
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "permanent", "source_closet": "ev", "source_file": "f1"},
    )
    task_id = create["task_id"]
    provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": task_id,
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    # Listing all (no status filter) still returns it as completed.
    all_tasks = provider.handle_tool_call(TOOL_LIST_TASKS, {})
    matching = [t for t in all_tasks if t["task_id"] == task_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "completed"


def test_complete_task_excluded_by_status_active_filter(
    provider: MemoryMissionProvider,
) -> None:
    """Active filter drops completed tasks."""
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "doneable", "source_closet": "ev", "source_file": "f1"},
    )
    provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": create["task_id"],
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    active = provider.handle_tool_call(TOOL_LIST_TASKS, {"status": "active"})
    assert all(t["task_id"] != create["task_id"] for t in active)


# ---------- mm_list_tasks ----------


def test_list_tasks_empty_returns_empty_list(provider: MemoryMissionProvider) -> None:
    assert provider.handle_tool_call(TOOL_LIST_TASKS, {}) == []


def test_list_tasks_filters_by_explicit_status(
    provider: MemoryMissionProvider,
) -> None:
    a = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "open one", "source_closet": "ev", "source_file": "f1"},
    )
    b = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "deferred one", "source_closet": "ev", "source_file": "f2"},
    )
    provider.handle_tool_call(
        TOOL_UPDATE_TASK_STATUS,
        {
            "task_id": b["task_id"],
            "new_status": "deferred",
            "source_closet": "ev",
            "source_file": "f3",
        },
    )
    open_only = provider.handle_tool_call(TOOL_LIST_TASKS, {"status": "open"})
    assert {t["task_id"] for t in open_only} == {a["task_id"]}


def test_list_tasks_active_includes_blocked_and_deferred(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "stuck", "source_closet": "ev", "source_file": "f1"},
    )
    provider.handle_tool_call(
        TOOL_UPDATE_TASK_STATUS,
        {
            "task_id": create["task_id"],
            "new_status": "blocked",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    active = provider.handle_tool_call(TOOL_LIST_TASKS, {"status": "active"})
    assert any(t["task_id"] == create["task_id"] for t in active)


def test_list_tasks_filters_by_owner(provider: MemoryMissionProvider) -> None:
    a = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "for alice",
            "owner": "alice",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    b = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "for sven",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    alice_tasks = provider.handle_tool_call(TOOL_LIST_TASKS, {"owner": "alice"})
    assert {t["task_id"] for t in alice_tasks} == {a["task_id"]}
    sven_tasks = provider.handle_tool_call(TOOL_LIST_TASKS, {"owner": "sven"})
    assert {t["task_id"] for t in sven_tasks} == {b["task_id"]}


def test_list_tasks_filters_by_linked_thread(provider: MemoryMissionProvider) -> None:
    a = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "in thread",
            "linked_thread": "wealthpoint-discovery",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "free-floating", "source_closet": "ev", "source_file": "f2"},
    )
    out = provider.handle_tool_call(TOOL_LIST_TASKS, {"linked_thread": "wealthpoint-discovery"})
    assert {t["task_id"] for t in out} == {a["task_id"]}


def test_list_tasks_filters_by_due_before(provider: MemoryMissionProvider) -> None:
    soon = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "soon",
            "due_at": "2026-05-08",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    later = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "later",
            "due_at": "2026-12-31",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    out = provider.handle_tool_call(TOOL_LIST_TASKS, {"due_before": "2026-06-01"})
    ids = {t["task_id"] for t in out}
    assert soon["task_id"] in ids
    assert later["task_id"] not in ids


def test_list_tasks_rejects_unknown_status(provider: MemoryMissionProvider) -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        provider.handle_tool_call(TOOL_LIST_TASKS, {"status": "halfway"})


def test_list_tasks_sort_due_at_asc_nulls_last(provider: MemoryMissionProvider) -> None:
    """Tasks with due_at sort first ascending; tasks without due_at come last."""
    no_due = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "open-ended", "source_closet": "ev", "source_file": "f1"},
    )
    later = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "later",
            "due_at": "2027-01-01",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    sooner = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "sooner",
            "due_at": "2026-05-08",
            "source_closet": "ev",
            "source_file": "f3",
        },
    )
    out = provider.handle_tool_call(TOOL_LIST_TASKS, {})
    ids = [t["task_id"] for t in out]
    # Sooner before later, both before no-due.
    assert ids.index(sooner["task_id"]) < ids.index(later["task_id"])
    assert ids.index(later["task_id"]) < ids.index(no_due["task_id"])


def test_list_tasks_includes_owner_and_linked_thread_fields(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {
            "title": "rich task",
            "owner": "kai",
            "linked_thread": "deal-acme",
            "due_at": "2026-06-01",
            "source_closet": "ev",
            "source_file": "f1",
        },
    )
    out = provider.handle_tool_call(TOOL_LIST_TASKS, {})
    matching = [t for t in out if t["task_id"] == create["task_id"]]
    assert len(matching) == 1
    task = matching[0]
    assert task["title"] == "rich task"
    assert task["owner"] == "kai"
    assert task["linked_thread"] == "deal-acme"
    assert task["due_at"] == "2026-06-01"


def test_list_tasks_completed_carries_completed_at_and_outcome(
    provider: MemoryMissionProvider,
) -> None:
    create = provider.handle_tool_call(
        TOOL_CREATE_TASK,
        {"title": "to-complete", "source_closet": "ev", "source_file": "f1"},
    )
    provider.handle_tool_call(
        TOOL_COMPLETE_TASK,
        {
            "task_id": create["task_id"],
            "outcome": "shipped on time",
            "completed_at": "2026-05-06",
            "source_closet": "ev",
            "source_file": "f2",
        },
    )
    out = provider.handle_tool_call(TOOL_LIST_TASKS, {"status": "completed"})
    matching = [t for t in out if t["task_id"] == create["task_id"]]
    assert len(matching) == 1
    assert matching[0]["completed_at"] == "2026-05-06"
    assert matching[0]["outcome"] == "shipped on time"
