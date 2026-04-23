"""Tests for component 0.6 — Durable Execution + Checkpointing."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.durable import (
    CheckpointStore,
    DurableRun,
    durable_run,
)
from memory_mission.observability import observability_scope

# ---------- CheckpointStore: low-level store ----------


def test_store_creates_schema_on_init(tmp_path: Path) -> None:
    """Opening a store creates tables without error. Reopening is idempotent."""
    db_path = tmp_path / "ck.db"
    store = CheckpointStore(db_path)
    store.close()

    # Reopen — should not fail on CREATE IF NOT EXISTS.
    store2 = CheckpointStore(db_path)
    store2.close()


def test_create_and_get_thread(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    thread = store.create_thread(
        thread_id="t-1",
        firm_id="acme",
        employee_id="sarah",
        workflow_type="backfill",
    )
    assert thread.thread_id == "t-1"
    assert thread.status == "running"
    assert thread.state == {}

    retrieved = store.get_thread("t-1")
    assert retrieved == thread


def test_get_missing_thread_returns_none(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    assert store.get_thread("does-not-exist") is None


def test_checkpoint_requires_existing_thread(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    with pytest.raises(ValueError, match="unknown thread"):
        store.write_checkpoint(thread_id="missing", step_name="s", state={})


def test_write_and_read_checkpoint(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    store.create_thread(thread_id="t-1", firm_id="acme", employee_id=None, workflow_type="default")
    store.write_checkpoint(thread_id="t-1", step_name="step-a", state={"n": 1})

    ck = store.get_checkpoint("t-1", "step-a")
    assert ck is not None
    assert ck.state == {"n": 1}


def test_checkpoint_is_idempotent(tmp_path: Path) -> None:
    """Writing the same step twice updates state, doesn't duplicate."""
    store = CheckpointStore(tmp_path / "ck.db")
    store.create_thread(thread_id="t-1", firm_id="acme", employee_id=None, workflow_type="default")
    store.write_checkpoint(thread_id="t-1", step_name="step-a", state={"v": 1})
    store.write_checkpoint(thread_id="t-1", step_name="step-a", state={"v": 2})

    ckpts = store.list_checkpoints("t-1")
    assert len(ckpts) == 1
    assert ckpts[0].state == {"v": 2}


def test_completed_step_names(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    store.create_thread(thread_id="t-1", firm_id="acme", employee_id=None, workflow_type="default")
    for step in ("a", "b", "c"):
        store.write_checkpoint(thread_id="t-1", step_name=step, state={})
    assert store.completed_step_names("t-1") == {"a", "b", "c"}


def test_list_threads_filters(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    store.create_thread(thread_id="t-1", firm_id="acme", employee_id=None, workflow_type="backfill")
    store.create_thread(thread_id="t-2", firm_id="acme", employee_id=None, workflow_type="dreaming")
    store.create_thread(
        thread_id="t-3", firm_id="other", employee_id=None, workflow_type="backfill"
    )
    store.update_thread_status("t-2", "completed")

    acme_all = store.list_threads(firm_id="acme")
    assert {t.thread_id for t in acme_all} == {"t-1", "t-2"}

    acme_running = store.list_threads(firm_id="acme", status="running")
    assert {t.thread_id for t in acme_running} == {"t-1"}


# ---------- DurableRun: high-level API ----------


def test_durable_run_records_and_resumes(tmp_path: Path) -> None:
    """Fresh run records steps; re-running with same thread_id resumes."""
    store = CheckpointStore(tmp_path / "ck.db")
    processed: list[str] = []

    # First invocation: processes all 5 items.
    with durable_run(
        store=store,
        thread_id="backfill-1",
        firm_id="acme",
        workflow_type="backfill",
    ) as run:
        assert not run.is_resumed
        for i in range(5):
            step = f"item-{i}"
            if run.is_done(step):
                continue
            processed.append(step)
            run.mark_done(step, state={"i": i})
        run.complete()

    assert processed == [f"item-{i}" for i in range(5)]
    assert store.get_thread("backfill-1").status == "completed"  # type: ignore[union-attr]

    # Second invocation: all steps already done, nothing is re-processed.
    processed_again: list[str] = []
    with durable_run(
        store=store,
        thread_id="backfill-1",
        firm_id="acme",
        workflow_type="backfill",
    ) as run:
        assert run.is_resumed
        for i in range(5):
            step = f"item-{i}"
            if run.is_done(step):
                continue
            processed_again.append(step)
            run.mark_done(step)

    assert processed_again == []


def test_durable_run_resumes_after_crash(tmp_path: Path) -> None:
    """Simulate crash mid-run; re-running picks up from last checkpoint."""
    store = CheckpointStore(tmp_path / "ck.db")
    crash_point = 3
    total = 7

    class _CrashedError(Exception):
        pass

    processed: list[str] = []

    # First pass: "crash" at item 3.
    with pytest.raises(_CrashedError):
        with durable_run(store=store, thread_id="backfill-1", firm_id="acme") as run:
            for i in range(total):
                if i == crash_point:
                    raise _CrashedError("boom")
                step = f"item-{i}"
                if not run.is_done(step):
                    processed.append(step)
                    run.mark_done(step, state={"i": i})

    # Thread should be marked failed with 3 checkpoints persisted (items 0, 1, 2).
    thread = store.get_thread("backfill-1")
    assert thread is not None
    assert thread.status == "failed"
    assert len(store.list_checkpoints("backfill-1")) == crash_point

    # Second pass: resume, process the rest, complete.
    with durable_run(store=store, thread_id="backfill-1", firm_id="acme") as run:
        assert run.is_resumed
        assert run.completed_step_count() == crash_point
        for i in range(total):
            step = f"item-{i}"
            if run.is_done(step):
                continue
            processed.append(step)
            run.mark_done(step, state={"i": i})
        run.complete()

    # Each item processed exactly once across both runs.
    assert processed == [f"item-{i}" for i in range(total)]
    thread = store.get_thread("backfill-1")
    assert thread is not None
    assert thread.status == "completed"


def test_state_persists_across_runs(tmp_path: Path) -> None:
    """Mutable ``run.state`` survives resumption when ``save_state()`` is called."""
    store = CheckpointStore(tmp_path / "ck.db")

    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        run.state["batch"] = 42
        run.state["last_id"] = "email-99"
        # Context manager auto-saves state on clean exit.

    # Reopen: state should be restored.
    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        assert run.state["batch"] == 42
        assert run.state["last_id"] == "email-99"


def test_cross_firm_thread_access_rejected(tmp_path: Path) -> None:
    """A run opened with the wrong firm_id for an existing thread raises."""
    store = CheckpointStore(tmp_path / "ck.db")

    with durable_run(store=store, thread_id="t-1", firm_id="firm-a"):
        pass

    with pytest.raises(ValueError, match="belongs to firm"):
        with durable_run(store=store, thread_id="t-1", firm_id="firm-b"):
            pass


def test_run_step_convenience(tmp_path: Path) -> None:
    """``run_step()`` = skip-if-done + run + mark."""
    store = CheckpointStore(tmp_path / "ck.db")
    call_count = {"n": 0}

    def do_work() -> int:
        call_count["n"] += 1
        return 42

    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        result1 = run.run_step("work", do_work)
        result2 = run.run_step("work", do_work)  # should skip

    assert result1 == 42
    assert result2 is None  # skipped on second call
    assert call_count["n"] == 1


def test_fail_marks_thread_and_persists_state(tmp_path: Path) -> None:
    """An exception inside ``durable_run()`` marks the thread failed."""
    store = CheckpointStore(tmp_path / "ck.db")

    with pytest.raises(RuntimeError, match="oops"):
        with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
            run.state["progress"] = 0.42
            run.mark_done("half-done")
            raise RuntimeError("oops")

    thread = store.get_thread("t-1")
    assert thread is not None
    assert thread.status == "failed"
    # State and checkpoint both persisted pre-crash.
    assert thread.state["progress"] == 0.42
    assert "half-done" in store.completed_step_names("t-1")


def test_pause_and_resume(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")

    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        run.mark_done("step-a")
        run.pause()

    assert store.get_thread("t-1").status == "paused"  # type: ignore[union-attr]

    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        # Resuming flips back to running.
        assert run.status == "running"
        run.mark_done("step-b")
        run.complete()

    assert store.get_thread("t-1").status == "completed"  # type: ignore[union-attr]


def test_must_start_before_use(tmp_path: Path) -> None:
    """Using a raw ``DurableRun`` without ``start()`` raises."""
    store = CheckpointStore(tmp_path / "ck.db")
    run = DurableRun(store=store, thread_id="t-1", firm_id="acme")
    with pytest.raises(RuntimeError, match="not started"):
        run.is_done("x")


def test_reopening_completed_thread_preserves_status(tmp_path: Path) -> None:
    """HIGH-SEVERITY REGRESSION GUARD (review finding #2).

    A completed terminal thread must stay ``completed`` when reopened without
    calling ``.complete()`` again. Previously the code flipped ANY non-running
    status (including completed) back to running on re-entry, losing the
    terminal state.
    """
    store = CheckpointStore(tmp_path / "ck.db")

    # First run: complete it.
    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        run.mark_done("only-step")
        run.complete()
    assert store.get_thread("t-1").status == "completed"  # type: ignore[union-attr]

    # Reopen without calling complete() again — status must remain "completed".
    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        assert run.status == "completed"
    assert store.get_thread("t-1").status == "completed"  # type: ignore[union-attr]


# ---------- Observability integration ----------


def test_run_records_trace_id_when_scope_active(tmp_path: Path) -> None:
    """When a durable run starts inside an observability_scope, the trace_id
    is written into the thread's state for cross-referencing."""
    store = CheckpointStore(tmp_path / "ck.db")

    with observability_scope(observability_root=tmp_path / "obs", firm_id="acme"):
        with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
            # state was seeded with the current trace_id.
            assert "trace_id" in run.state
            assert isinstance(run.state["trace_id"], str)


def test_run_works_without_observability_scope(tmp_path: Path) -> None:
    """Durable runs don't require an observability scope — they run standalone."""
    store = CheckpointStore(tmp_path / "ck.db")
    with durable_run(store=store, thread_id="t-1", firm_id="acme") as run:
        assert "trace_id" not in run.state
        run.mark_done("s")
