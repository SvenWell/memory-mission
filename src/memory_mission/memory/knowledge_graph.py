"""Temporal knowledge graph — ported from MemPalace's ``knowledge_graph.py``.

Not a dependency: we re-implement MemPalace's pattern here so we own the
schema and can add firm-scoping, observability hooks, and Pydantic-typed
models without forking a third-party package.

What it is: an entity-relationship graph where every fact (``triple``) is
stamped with a validity window and a confidence score. The same subject
can say different things at different times, and queries ask "what was
true on ``as_of``?".

    kg = KnowledgeGraph("/path/firm-acme.kg.sqlite3")
    kg.add_entity("sarah-chen", entity_type="person")
    kg.add_entity("acme-corp", entity_type="company")
    kg.add_triple(
        "sarah-chen", "works_at", "acme-corp",
        valid_from=date(2024, 1, 1),
        confidence=0.95,
        source_file="interactions/2024-01-02-onboarding.md",
    )

    # Later, Sarah moves.
    kg.invalidate("sarah-chen", "works_at", "acme-corp",
                  ended=date(2026, 3, 15))
    kg.add_triple("sarah-chen", "works_at", "beta-fund",
                  valid_from=date(2026, 3, 16), confidence=0.8)

    # Time travel: where did Sarah work in February 2025?
    kg.query_entity("sarah-chen", as_of=date(2025, 2, 1))
    # -> [Triple(sarah-chen, works_at, acme-corp, ...)]

**Firm scoping.** Unlike MemPalace (single-user), we pass the DB path per
firm — the caller is responsible for isolating firms on disk. A firm's
entire graph is one SQLite file.

**Schema.** Two tables. ``entities`` holds canonical entities keyed by
name; ``triples`` holds subject-predicate-object relations with validity
windows. Inserting the same entity is idempotent. Triples are never
deleted — they're invalidated by setting ``valid_to``, preserving history.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

Direction = Literal["outgoing", "incoming", "both"]


# ---------- Models ----------


class Entity(BaseModel):
    """One canonical entity in the graph (keyed by ``name``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    entity_type: str = "unknown"
    properties: dict[str, Any] = Field(default_factory=dict)


class Triple(BaseModel):
    """One (subject, predicate, object) fact with validity + provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    object: str
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: float = 1.0
    source_closet: str | None = None
    source_file: str | None = None

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v

    def is_valid_at(self, as_of: date) -> bool:
        """Return True if this triple is valid on ``as_of``.

        Unknown start (``valid_from is None``) is treated as "always was."
        Unknown end (``valid_to is None``) is treated as "currently true."
        """
        if self.valid_from is not None and as_of < self.valid_from:
            return False
        if self.valid_to is not None and as_of >= self.valid_to:
            return False
        return True


class GraphStats(BaseModel):
    """Shape snapshot of a knowledge graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_count: int
    triple_count: int
    currently_true_triple_count: int


# ---------- Store ----------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'unknown',
    properties TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    source_closet TEXT,
    source_file TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
CREATE INDEX IF NOT EXISTS idx_triples_currently_true
    ON triples(subject, predicate, object) WHERE valid_to IS NULL;
"""


class KnowledgeGraph:
    """Temporal entity-relationship graph backed by SQLite.

    One instance = one firm's graph = one SQLite file. Thread-safe for
    serial access via the built-in ``sqlite3`` connection; open a separate
    ``KnowledgeGraph`` per thread for concurrent writes.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ---------- Lifecycle ----------

    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
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

    # ---------- Entity ops ----------

    def add_entity(
        self,
        name: str,
        *,
        entity_type: str = "unknown",
        properties: dict[str, Any] | None = None,
    ) -> Entity:
        """Insert an entity. If the name already exists, update type/properties.

        Idempotent by name — calling twice with the same arguments is a no-op
        visible as a bumped ``created_at`` only on first insert.
        """
        props_json = json.dumps(properties or {}, sort_keys=True)
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO entities (name, entity_type, properties, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    properties = excluded.properties
                """,
                (name, entity_type, props_json, now),
            )
        return Entity(
            name=name,
            entity_type=entity_type,
            properties=properties or {},
        )

    def get_entity(self, name: str) -> Entity | None:
        row = self._conn.execute(
            "SELECT name, entity_type, properties FROM entities WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return Entity(
            name=row["name"],
            entity_type=row["entity_type"],
            properties=json.loads(row["properties"] or "{}"),
        )

    # ---------- Triple ops ----------

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        valid_from: date | None = None,
        valid_to: date | None = None,
        confidence: float = 1.0,
        source_closet: str | None = None,
        source_file: str | None = None,
    ) -> Triple:
        """Insert a new triple. Triples are append-only; use ``invalidate``
        to end the validity of an existing triple instead of overwriting.
        """
        triple = Triple(
            subject=subject,
            predicate=predicate,
            object=obj,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            source_closet=source_closet,
            source_file=source_file,
        )
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO triples
                    (subject, predicate, object, valid_from, valid_to,
                     confidence, source_closet, source_file, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject,
                    predicate,
                    obj,
                    _iso(valid_from),
                    _iso(valid_to),
                    confidence,
                    source_closet,
                    source_file,
                    now,
                ),
            )
        return triple

    def invalidate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        ended: date | None = None,
    ) -> int:
        """Mark matching currently-true triples as ended on ``ended``.

        A "currently true" triple has ``valid_to IS NULL``. This method sets
        ``valid_to = ended`` (or today, if ``ended`` is omitted) on every
        such triple matching ``(subject, predicate, obj)``. Returns the
        number of triples updated.
        """
        end = ended or date.today()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE triples SET valid_to = ?
                WHERE subject = ? AND predicate = ? AND object = ?
                  AND valid_to IS NULL
                """,
                (end.isoformat(), subject, predicate, obj),
            )
            return cur.rowcount

    # ---------- Queries ----------

    def query_entity(
        self,
        name: str,
        *,
        as_of: date | None = None,
        direction: Direction = "outgoing",
    ) -> list[Triple]:
        """Return triples involving ``name``.

        - ``direction="outgoing"``: triples where ``name`` is the subject
        - ``direction="incoming"``: triples where ``name`` is the object
        - ``direction="both"``: union of the above

        When ``as_of`` is set, only triples valid on that date are returned.
        """
        clauses = []
        params: list[Any] = []
        if direction == "outgoing":
            clauses.append("subject = ?")
            params.append(name)
        elif direction == "incoming":
            clauses.append("object = ?")
            params.append(name)
        else:
            clauses.append("(subject = ? OR object = ?)")
            params.extend([name, name])
        where = " WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(f"SELECT * FROM triples{where}", params).fetchall()
        triples = [_row_to_triple(r) for r in rows]
        if as_of is not None:
            triples = [t for t in triples if t.is_valid_at(as_of)]
        return triples

    def query_relationship(
        self,
        predicate: str,
        *,
        as_of: date | None = None,
    ) -> list[Triple]:
        """Return all triples using ``predicate``, optionally filtered by date."""
        rows = self._conn.execute(
            "SELECT * FROM triples WHERE predicate = ?", (predicate,)
        ).fetchall()
        triples = [_row_to_triple(r) for r in rows]
        if as_of is not None:
            triples = [t for t in triples if t.is_valid_at(as_of)]
        return triples

    def timeline(self, entity_name: str | None = None) -> list[Triple]:
        """Return triples ordered by ``valid_from`` (NULLs first)."""
        if entity_name is None:
            rows = self._conn.execute(
                "SELECT * FROM triples ORDER BY "
                "CASE WHEN valid_from IS NULL THEN 0 ELSE 1 END, "
                "valid_from, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM triples
                WHERE subject = ? OR object = ?
                ORDER BY
                    CASE WHEN valid_from IS NULL THEN 0 ELSE 1 END,
                    valid_from, id
                """,
                (entity_name, entity_name),
            ).fetchall()
        return [_row_to_triple(r) for r in rows]

    # ---------- Bulk + stats ----------

    def seed_from_entity_facts(
        self,
        entity_facts: dict[str, Iterable[dict[str, Any]]],
    ) -> None:
        """Seed the graph from a ``{entity_name: [fact_dict, ...]}`` map.

        Each fact dict must contain at least ``predicate`` and ``object``;
        other fields (validity, confidence, source_*) are passed through.
        The entity itself is created if missing.
        """
        for name, facts in entity_facts.items():
            self.add_entity(name)
            for fact in facts:
                self.add_triple(
                    name,
                    fact["predicate"],
                    fact["object"],
                    valid_from=_parse_date(fact.get("valid_from")),
                    valid_to=_parse_date(fact.get("valid_to")),
                    confidence=fact.get("confidence", 1.0),
                    source_closet=fact.get("source_closet"),
                    source_file=fact.get("source_file"),
                )

    def stats(self) -> GraphStats:
        entity_count = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        triple_count = self._conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
        current = self._conn.execute(
            "SELECT COUNT(*) FROM triples WHERE valid_to IS NULL"
        ).fetchone()[0]
        return GraphStats(
            entity_count=entity_count,
            triple_count=triple_count,
            currently_true_triple_count=current,
        )

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


# ---------- Helpers ----------


def _row_to_triple(row: sqlite3.Row) -> Triple:
    return Triple(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=_parse_date(row["valid_from"]),
        valid_to=_parse_date(row["valid_to"]),
        confidence=row["confidence"],
        source_closet=row["source_closet"],
        source_file=row["source_file"],
    )


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
