"""``DurableRun`` — the ergonomic API long-running agents use.

Usage (linear resume-safe loop):

    from memory_mission.durable import CheckpointStore, durable_run

    store = CheckpointStore("./.checkpoints/firm-acme.db")

    with durable_run(
        store=store,
        thread_id="backfill-firm-acme-emp-1",
        firm_id="firm-acme",
        employee_id="sarah-chen",
        workflow_type="backfill-email",
    ) as run:
        for email in fetch_emails():
            step = f"email-{email.id}"
            if run.is_done(step):
                continue
            process(email)
            run.mark_done(step, state={"email_id": email.id})

        run.complete()

If the process crashes mid-loop, re-running with the same ``thread_id`` picks
up exactly where it left off — already-processed emails are skipped.

Design notes:
- Checkpoints are per-step, atomic — each ``mark_done()`` commits before returning.
- Thread-level state (``run.state``) is free-form dict for carrying context
  (current batch index, accumulated counts) across resumptions.
- Lifecycle events emit to the observability log when a scope is active.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from memory_mission.durable.store import (
    CheckpointStore,
    ThreadRecord,
    ThreadStatus,
)

_log = logging.getLogger(__name__)


class DurableRun:
    """One long-running workflow, identified by ``thread_id``.

    Construct via ``durable_run()`` (context manager) so start/complete/fail
    lifecycle is handled automatically. Direct construction is allowed but
    you must call ``start()`` and ``complete()``/``fail()`` yourself.
    """

    def __init__(
        self,
        *,
        store: CheckpointStore,
        thread_id: str,
        firm_id: str,
        employee_id: str | None = None,
        workflow_type: str = "default",
    ) -> None:
        self._store = store
        self._thread_id = thread_id
        self._firm_id = firm_id
        self._employee_id = employee_id
        self._workflow_type = workflow_type
        self._thread: ThreadRecord | None = None
        self._completed_steps: set[str] = set()
        self._state: dict[str, Any] = {}

    # ---------- Properties ----------

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def firm_id(self) -> str:
        return self._firm_id

    @property
    def employee_id(self) -> str | None:
        return self._employee_id

    @property
    def workflow_type(self) -> str:
        return self._workflow_type

    @property
    def status(self) -> ThreadStatus:
        self._require_started()
        assert self._thread is not None
        # Re-read in case another process updated it.
        refreshed = self._store.get_thread(self._thread_id)
        assert refreshed is not None
        return refreshed.status

    @property
    def state(self) -> dict[str, Any]:
        """Mutable per-thread state dict. Persist via ``save_state()``."""
        return self._state

    @property
    def is_resumed(self) -> bool:
        """True if this run picked up an existing thread (vs created fresh)."""
        return bool(self._completed_steps) or (
            self._thread is not None and self._thread.updated_at != self._thread.created_at
        )

    # ---------- Lifecycle ----------

    def start(self) -> None:
        """Create the thread if new; load checkpoint state if resuming."""
        existing = self._store.get_thread(self._thread_id)
        initial_state = self._initial_state_with_trace()
        if existing is None:
            self._thread = self._store.create_thread(
                thread_id=self._thread_id,
                firm_id=self._firm_id,
                employee_id=self._employee_id,
                workflow_type=self._workflow_type,
                state=initial_state,
            )
            self._completed_steps = set()
            self._state = dict(initial_state)
            _log.info("durable_run.start.fresh thread_id=%s", self._thread_id)
        else:
            if existing.firm_id != self._firm_id:
                raise ValueError(
                    f"Thread {self._thread_id!r} exists but belongs to firm "
                    f"{existing.firm_id!r}, not {self._firm_id!r}."
                )
            self._thread = existing
            self._completed_steps = self._store.completed_step_names(self._thread_id)
            self._state = dict(existing.state)
            if existing.status == "completed":
                # Reopening a completed run is allowed and a no-op — leave the
                # terminal status alone. Callers that want to run MORE steps on
                # the same thread should either create a new thread or call
                # an explicit reopen method (not implemented).
                _log.info("durable_run.start.completed.noop thread_id=%s", self._thread_id)
            else:
                if existing.status != "running":
                    # paused or failed → flip to running for the resumed work.
                    self._store.update_thread_status(self._thread_id, "running")
                _log.info(
                    "durable_run.start.resume thread_id=%s completed_steps=%d",
                    self._thread_id,
                    len(self._completed_steps),
                )

    def complete(self) -> None:
        self._require_started()
        self._store.update_thread_state(self._thread_id, self._state)
        self._store.update_thread_status(self._thread_id, "completed")
        _log.info("durable_run.complete thread_id=%s", self._thread_id)

    def fail(self, reason: str | None = None) -> None:
        self._require_started()
        # Persist current state so it's observable post-mortem.
        self._store.update_thread_state(self._thread_id, self._state)
        self._store.update_thread_status(self._thread_id, "failed")
        _log.error("durable_run.fail thread_id=%s reason=%s", self._thread_id, reason)

    def pause(self) -> None:
        """Mark paused (e.g., awaiting HITL). State is preserved; resume by reopening."""
        self._require_started()
        self._store.update_thread_state(self._thread_id, self._state)
        self._store.update_thread_status(self._thread_id, "paused")
        _log.info("durable_run.pause thread_id=%s", self._thread_id)

    # ---------- Step operations ----------

    def is_done(self, step_name: str) -> bool:
        """True if the step was already recorded in this or a previous run."""
        self._require_started()
        return step_name in self._completed_steps

    def mark_done(self, step_name: str, state: dict[str, Any] | None = None) -> None:
        """Record a super-step as completed. Idempotent."""
        self._require_started()
        self._store.write_checkpoint(thread_id=self._thread_id, step_name=step_name, state=state)
        self._completed_steps.add(step_name)

    def run_step(self, step_name: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Convenience: skip-if-done + run + mark.

        Returns ``None`` if the step was already done (caller should not depend
        on the return value in that case). Otherwise returns ``fn(*args, **kwargs)``.
        """
        if self.is_done(step_name):
            return None
        result = fn(*args, **kwargs)
        self.mark_done(step_name)
        return result

    def save_state(self) -> None:
        """Persist ``self.state`` to the thread row. Call periodically in long loops."""
        self._require_started()
        self._store.update_thread_state(self._thread_id, self._state)

    def completed_step_count(self) -> int:
        self._require_started()
        return len(self._completed_steps)

    # ---------- Internal ----------

    def _require_started(self) -> None:
        if self._thread is None:
            raise RuntimeError(
                "DurableRun not started. Call .start() or use the durable_run() context manager."
            )

    def _initial_state_with_trace(self) -> dict[str, Any]:
        """If an observability scope is active, seed state with the trace_id.

        This lets anyone inspecting a durable run cross-reference audit events
        via the ``trace_id`` recorded at run start. Silently no-op if there's
        no active observability scope — durable runs don't require one.
        """
        try:
            # Lazy import to avoid a hard dependency on observability in module load.
            from memory_mission.observability.context import _trace_id

            current_trace = _trace_id.get()
        except Exception:
            return {}
        if current_trace is None:
            return {}
        return {"trace_id": str(current_trace)}


@contextmanager
def durable_run(
    *,
    store: CheckpointStore,
    thread_id: str,
    firm_id: str,
    employee_id: str | None = None,
    workflow_type: str = "default",
) -> Iterator[DurableRun]:
    """Context manager: start on enter, complete on clean exit, fail on exception.

    If the run was already completed by a previous invocation, re-entering is
    a no-op — the body can still run idempotently (every step skip-if-done).
    """
    run = DurableRun(
        store=store,
        thread_id=thread_id,
        firm_id=firm_id,
        employee_id=employee_id,
        workflow_type=workflow_type,
    )
    run.start()
    try:
        yield run
    except Exception as exc:
        run.fail(reason=repr(exc))
        raise
    else:
        # Caller is responsible for calling .complete() explicitly if the
        # workflow has a meaningful terminal state. We do NOT auto-complete
        # because partial exits (e.g., interactive loops) are valid.
        run.save_state()
