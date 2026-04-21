"""SQLite checkpoint store for durable execution.

Schema:
- ``threads`` tracks per-run state: firm_id, workflow_type, status, timestamps.
- ``checkpoints`` records each completed super-step with a JSON state blob.

SQLite chosen for V1:
- Zero deps (stdlib)
- Atomic commit semantics are good enough
- Single-writer is fine for per-firm-instance deployments (our resolved multi-tenancy choice)
- Migration to Postgres later is straightforward — same schema, same queries

Every checkpoint write is wrapped in a transaction and commits before returning,
so a crash between ``mark_done()`` calls leaves the store in a consistent state
(all-or-nothing per step).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ThreadStatus = Literal["running", "paused", "completed", "failed"]


@dataclass(frozen=True)
class ThreadRecord:
    """A durable thread's metadata."""

    thread_id: str
    firm_id: str
    employee_id: str | None
    workflow_type: str
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime
    # Free-form per-thread state dict, persisted as JSON.
    state: dict[str, Any]


@dataclass(frozen=True)
class CheckpointRecord:
    """A single completed super-step."""

    thread_id: str
    step_name: str
    state: dict[str, Any]
    created_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id     TEXT PRIMARY KEY,
    firm_id       TEXT NOT NULL,
    employee_id   TEXT,
    workflow_type TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running', 'paused', 'completed', 'failed')),
    state_json    TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_firm ON threads(firm_id);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id   TEXT NOT NULL,
    step_name   TEXT NOT NULL,
    state_json  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (thread_id, step_name),
    FOREIGN KEY (thread_id) REFERENCES threads(thread_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_thread ON checkpoints(thread_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CheckpointStore:
    """SQLite-backed checkpoint store."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        if self._db_path != Path(":memory:"):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` is safe because we serialize access with
        # the connection's implicit transaction model and don't hold refs to
        # cursors across threads. Per-process use only.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------- Threads ----------

    def create_thread(
        self,
        *,
        thread_id: str,
        firm_id: str,
        employee_id: str | None,
        workflow_type: str,
        state: dict[str, Any] | None = None,
    ) -> ThreadRecord:
        """Create a new thread. Raises if thread_id already exists."""
        now = _now()
        self._conn.execute(
            """
            INSERT INTO threads (
                thread_id, firm_id, employee_id, workflow_type,
                status, state_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                thread_id,
                firm_id,
                employee_id,
                workflow_type,
                json.dumps(state or {}),
                now,
                now,
            ),
        )
        record = self.get_thread(thread_id)
        assert record is not None
        return record

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        row = self._conn.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_thread(row)

    def update_thread_status(self, thread_id: str, status: ThreadStatus) -> None:
        self._conn.execute(
            "UPDATE threads SET status = ?, updated_at = ? WHERE thread_id = ?",
            (status, _now(), thread_id),
        )

    def update_thread_state(self, thread_id: str, state: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE threads SET state_json = ?, updated_at = ? WHERE thread_id = ?",
            (json.dumps(state), _now(), thread_id),
        )

    def list_threads(
        self,
        *,
        firm_id: str | None = None,
        status: ThreadStatus | None = None,
    ) -> list[ThreadRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        if firm_id is not None:
            conditions.append("firm_id = ?")
            params.append(firm_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM threads {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [_row_to_thread(r) for r in rows]

    # ---------- Checkpoints ----------

    def write_checkpoint(
        self,
        *,
        thread_id: str,
        step_name: str,
        state: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        """Idempotent: INSERT OR REPLACE on (thread_id, step_name)."""
        if self.get_thread(thread_id) is None:
            raise ValueError(f"Cannot checkpoint unknown thread: {thread_id!r}")
        now = _now()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints (thread_id, step_name, state_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, step_name, json.dumps(state or {}), now),
        )
        # Bump thread updated_at so list queries reflect activity.
        self._conn.execute(
            "UPDATE threads SET updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        return CheckpointRecord(
            thread_id=thread_id,
            step_name=step_name,
            state=state or {},
            created_at=datetime.fromisoformat(now),
        )

    def get_checkpoint(self, thread_id: str, step_name: str) -> CheckpointRecord | None:
        row = self._conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? AND step_name = ?",
            (thread_id, step_name),
        ).fetchone()
        if row is None:
            return None
        return _row_to_checkpoint(row)

    def list_checkpoints(self, thread_id: str) -> list[CheckpointRecord]:
        rows = self._conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? ORDER BY created_at ASC",
            (thread_id,),
        ).fetchall()
        return [_row_to_checkpoint(r) for r in rows]

    def completed_step_names(self, thread_id: str) -> set[str]:
        """Return the set of step names completed for a thread. Fast path for resume."""
        rows = self._conn.execute(
            "SELECT step_name FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
        return {r["step_name"] for r in rows}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group multiple writes into a single atomic transaction."""
        self._conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")


def _row_to_thread(row: sqlite3.Row) -> ThreadRecord:
    return ThreadRecord(
        thread_id=row["thread_id"],
        firm_id=row["firm_id"],
        employee_id=row["employee_id"],
        workflow_type=row["workflow_type"],
        status=row["status"],
        state=json.loads(row["state_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_checkpoint(row: sqlite3.Row) -> CheckpointRecord:
    return CheckpointRecord(
        thread_id=row["thread_id"],
        step_name=row["step_name"],
        state=json.loads(row["state_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
