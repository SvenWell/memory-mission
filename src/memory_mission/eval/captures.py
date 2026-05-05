"""SQLite-backed eval capture store. One file per employee.

Schema:

- ``eval_captures`` records each captured tool call: tool name, scrubbed
  args, result signature, optional result body, latency, version.

Per-employee isolation: captures live at
``<root>/personal/<user_id>/eval_captures.sqlite3``, the same fence as
``personal_kg.db``. If you have read access to the personal KG, you
have read access to the captures (consistent with how observability
JSONL events are scoped today).

Capture is opt-in via ``MM_CONTRIBUTOR_MODE=1``. The
``record_eval_capture`` helper is the single call site MCP tool
handlers use; it short-circuits if the env flag isn't set and swallows
all exceptions so capture failure never breaks the tool path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory_mission.eval.pii_scrub import scrub_args_for_capture

CONTRIBUTOR_MODE_ENV = "MM_CONTRIBUTOR_MODE"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_captures (
    capture_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at      TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    tool_name        TEXT NOT NULL,
    args_json        TEXT NOT NULL,
    result_signature TEXT NOT NULL,
    result_json      TEXT,
    latency_ms       INTEGER,
    mm_version       TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_captures_tool ON eval_captures(tool_name);
CREATE INDEX IF NOT EXISTS idx_eval_captures_at ON eval_captures(captured_at);
"""


@dataclass(frozen=True)
class EvalCapture:
    """One captured tool call."""

    capture_id: int
    captured_at: datetime
    user_id: str
    tool_name: str
    args_json: str
    result_signature: str
    result_json: str | None
    latency_ms: int | None
    mm_version: str | None


def is_capture_enabled() -> bool:
    """Cheap env check used at the top of every potential capture site."""
    return os.environ.get(CONTRIBUTOR_MODE_ENV, "").lower() in {"1", "true", "yes", "on"}


def captures_path_for(*, root: Path, user_id: str) -> Path:
    """Canonical path for a given employee's capture store."""
    return root / "personal" / user_id / "eval_captures.sqlite3"


def _result_signature(result: Any) -> str:
    """SHA-256 over canonical-JSON of result. Used for fast equality diff in replay."""
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EvalCapturesStore:
    """SQLite-backed eval capture store. One per employee."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        if self._db_path != Path(":memory:"):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def write(
        self,
        *,
        user_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        latency_ms: int | None = None,
        mm_version: str | None = None,
    ) -> int:
        """Persist one capture. Returns the new ``capture_id``."""
        scrubbed = scrub_args_for_capture(tool_name, args)
        args_json = json.dumps(scrubbed, sort_keys=True, default=str)
        result_sig = _result_signature(result)
        result_json = json.dumps(result, sort_keys=True, default=str)
        cursor = self._conn.execute(
            """
            INSERT INTO eval_captures (
                captured_at, user_id, tool_name,
                args_json, result_signature, result_json,
                latency_ms, mm_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                user_id,
                tool_name,
                args_json,
                result_sig,
                result_json,
                latency_ms,
                mm_version,
            ),
        )
        return cursor.lastrowid or 0

    def list_captures(
        self,
        *,
        tool_name: str | None = None,
        limit: int = 100,
    ) -> list[EvalCapture]:
        """List most-recent captures, optionally filtered by tool name."""
        if tool_name is None:
            rows = self._conn.execute(
                "SELECT * FROM eval_captures ORDER BY capture_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM eval_captures WHERE tool_name = ? ORDER BY capture_id DESC LIMIT ?",
                (tool_name, limit),
            ).fetchall()
        return [_row_to_capture(r) for r in rows]

    def get(self, capture_id: int) -> EvalCapture | None:
        row = self._conn.execute(
            "SELECT * FROM eval_captures WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()
        return _row_to_capture(row) if row else None

    def stats(self) -> dict[str, Any]:
        """Per-tool counts and overall total. Used by ``mm eval status``."""
        per_tool = {
            row["tool_name"]: row["n"]
            for row in self._conn.execute(
                "SELECT tool_name, COUNT(*) AS n FROM eval_captures GROUP BY tool_name"
            )
        }
        total = sum(per_tool.values())
        last_at = self._conn.execute("SELECT MAX(captured_at) AS at FROM eval_captures").fetchone()[
            "at"
        ]
        return {
            "total": total,
            "per_tool": per_tool,
            "last_captured_at": last_at,
        }


def record_eval_capture(
    *,
    captures_path: Path | None,
    user_id: str,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    latency_ms: int | None = None,
    mm_version: str | None = None,
) -> None:
    """Top-level helper for tool functions to call after producing a result.

    No-op if capture is disabled or the path is unset. Failures are
    silently swallowed — capture must NEVER break the tool path.
    """
    if not is_capture_enabled():
        return
    if captures_path is None:
        return
    try:
        store = EvalCapturesStore(captures_path)
        try:
            store.write(
                user_id=user_id,
                tool_name=tool_name,
                args=args,
                result=result,
                latency_ms=latency_ms,
                mm_version=mm_version,
            )
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — capture must never break tool path
        return


def _row_to_capture(row: sqlite3.Row) -> EvalCapture:
    return EvalCapture(
        capture_id=row["capture_id"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        user_id=row["user_id"],
        tool_name=row["tool_name"],
        args_json=row["args_json"],
        result_signature=row["result_signature"],
        result_json=row["result_json"],
        latency_ms=row["latency_ms"],
        mm_version=row["mm_version"],
    )
