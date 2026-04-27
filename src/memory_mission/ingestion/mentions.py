"""Entity mention tracker — drives GBrain's enrichment-tier escalation.

GBrain's pattern: don't enrich every entity to the full pipeline. Track
how often each entity is mentioned across processed items, then escalate:

- 1 mention            → ``stub``    (create page, minimal data)
- 3+ mentions          → ``enrich``  (run light enrichment APIs)
- 8+ mentions or       → ``full``    (full enrichment pipeline + curator
  meeting attendance               attention)

The backfill loop calls ``record(name)`` for each entity it sees in a
pulled item. ``record()`` returns ``(previous_tier, new_tier)`` so the
caller can detect a threshold crossing — that's the moment to schedule
enrichment, not on every mention.

Per-firm SQLite store. The same shape as ``KnowledgeGraph`` /
``CheckpointStore``: each firm gets its own DB file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

Tier = Literal["none", "stub", "enrich", "full"]

# GBrain's published thresholds. Tunable per firm later via config.
TIER_THRESHOLDS: dict[Tier, int] = {
    "stub": 1,
    "enrich": 3,
    "full": 8,
}


def tier_for_count(count: int) -> Tier:
    """Map a mention count to the corresponding enrichment tier."""
    if count >= TIER_THRESHOLDS["full"]:
        return "full"
    if count >= TIER_THRESHOLDS["enrich"]:
        return "enrich"
    if count >= TIER_THRESHOLDS["stub"]:
        return "stub"
    return "none"


class MentionRecord(BaseModel):
    """Row from the mention store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    count: int
    tier: Tier
    first_seen: datetime
    last_seen: datetime


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entity_mentions (
    name TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_count
    ON entity_mentions(count);
"""


class MentionTracker:
    """Per-firm SQLite store of entity mention counts + tier transitions."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False``: see ``knowledge_graph.py`` for
        # the full rationale. Agent-runtime threads share connections.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ---------- Lifecycle ----------

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---------- Mention ops ----------

    def record(self, name: str) -> tuple[Tier, Tier]:
        """Increment the count for ``name``; return ``(prev_tier, new_tier)``.

        ``new_tier > prev_tier`` means this mention crossed a threshold and
        the caller should schedule the matching enrichment work.
        """
        if not name:
            raise ValueError("entity name cannot be empty")

        now = _utcnow_iso()
        with self._tx() as cur:
            row = cur.execute(
                "SELECT count FROM entity_mentions WHERE name = ?", (name,)
            ).fetchone()
            previous_count = row["count"] if row is not None else 0
            new_count = previous_count + 1
            if row is None:
                cur.execute(
                    """
                    INSERT INTO entity_mentions
                        (name, count, first_seen, last_seen)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, new_count, now, now),
                )
            else:
                cur.execute(
                    """
                    UPDATE entity_mentions
                    SET count = ?, last_seen = ?
                    WHERE name = ?
                    """,
                    (new_count, now, name),
                )

        return tier_for_count(previous_count), tier_for_count(new_count)

    def get(self, name: str) -> MentionRecord | None:
        row = self._conn.execute(
            "SELECT name, count, first_seen, last_seen FROM entity_mentions WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def all(self) -> list[MentionRecord]:
        """Return every tracked entity, ordered by descending count."""
        rows = self._conn.execute(
            "SELECT name, count, first_seen, last_seen "
            "FROM entity_mentions ORDER BY count DESC, name ASC"
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def stats(self) -> dict[Tier, int]:
        """Counts of entities currently sitting in each tier."""
        rows = self._conn.execute("SELECT count FROM entity_mentions").fetchall()
        out: dict[Tier, int] = {"none": 0, "stub": 0, "enrich": 0, "full": 0}
        for row in rows:
            out[tier_for_count(row["count"])] += 1
        return out

    # ---------- Internals ----------

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()


def _row_to_record(row: sqlite3.Row) -> MentionRecord:
    return MentionRecord(
        name=row["name"],
        count=row["count"],
        tier=tier_for_count(row["count"]),
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
    )


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
