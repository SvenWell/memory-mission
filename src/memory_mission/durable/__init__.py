"""Component 0.6 — Durable Execution + Checkpointing.

Long-running operations (backfill = 24h+, dreaming loop, HITL pauses) must
survive worker crashes and deploys. Every super-step writes a checkpoint
keyed by thread_id. On crash, re-running with the same thread_id resumes
from the latest checkpoint — no re-run from scratch.

Usage:

    from memory_mission.durable import CheckpointStore, durable_run

    store = CheckpointStore("./.checkpoints/firm-acme.db")

    with durable_run(
        store=store,
        thread_id="backfill-firm-acme-emp-1",
        firm_id="firm-acme",
        employee_id="sarah-chen",
        workflow_type="backfill-email",
    ) as run:
        for email in emails:
            step = f"email-{email.id}"
            if run.is_done(step):
                continue
            process(email)
            run.mark_done(step, state={"email_id": email.id})

        run.complete()

Storage: SQLite-per-firm. Atomic per-step commits. WAL journal.
Migration to Postgres later is straightforward (same schema, same queries).
"""

from memory_mission.durable.run import DurableRun, durable_run
from memory_mission.durable.store import (
    CheckpointRecord,
    CheckpointStore,
    ThreadRecord,
    ThreadStatus,
)

__all__ = [
    "CheckpointRecord",
    "CheckpointStore",
    "DurableRun",
    "ThreadRecord",
    "ThreadStatus",
    "durable_run",
]
