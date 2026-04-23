"""SQLite-backed ``IdentityResolver`` for V1 — conservative, exact-match only.

One ``LocalIdentityResolver`` instance = one firm's identity store = one
SQLite file. Per-firm isolation is the caller's responsibility (same
shape as ``KnowledgeGraph``).

V1 match policy is deliberately conservative: only exact match on full
``type:value`` strings. Two identifiers for the same person without a
shared typed key stay as two identities until a later extraction
supplies the bridge (e.g., a message that mentions both the email and
the LinkedIn URL). False negatives are recoverable via
``KnowledgeGraph.merge_entities()`` (Step 14b); false positives (merging
unrelated people) are expensive to unwind, so we err on the safe side.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from memory_mission.identity.base import (
    EntityKind,
    Identity,
    IdentityConflictError,
    make_entity_id,
    parse_identifier,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    identifier TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_identity_bindings_identity_id
    ON identity_bindings(identity_id);
"""


class LocalIdentityResolver:
    """Default in-repo resolver: SQLite, exact-match on typed identifiers.

    Thread-safe for serial access via the built-in ``sqlite3`` connection.
    Open a separate instance per thread for concurrent writes (same
    constraint as ``KnowledgeGraph``).
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        # WAL + busy_timeout: identity resolution runs from every MCP
        # process; multiple writers to one file need WAL + a block-on-lock
        # timeout to stay clear of OperationalError.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys = ON")
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

    # ---------- Protocol surface ----------

    def resolve(
        self,
        identifiers: set[str],
        *,
        entity_type: EntityKind = "person",
        canonical_name: str | None = None,
    ) -> str:
        if not identifiers:
            raise ValueError("resolve() requires at least one identifier")
        # Validate format before touching the DB so errors surface early.
        for ident in identifiers:
            parse_identifier(ident)

        matched = self._lookup_many(identifiers)
        existing_ids = {id_ for id_ in matched.values() if id_ is not None}

        if len(existing_ids) > 1:
            raise IdentityConflictError(identifiers, existing_ids)

        if len(existing_ids) == 1:
            identity_id = next(iter(existing_ids))
            # Bind any identifiers from the input that weren't already bound.
            unbound = {i for i in identifiers if matched.get(i) is None}
            if unbound:
                self._bind_all(identity_id, unbound)
            return identity_id

        # No match — create a new identity and bind all identifiers.
        return self._create_identity(
            identifiers,
            entity_type=entity_type,
            canonical_name=canonical_name,
        )

    def lookup(self, identifier: str) -> str | None:
        parse_identifier(identifier)  # raises on malformed
        row = self._conn.execute(
            "SELECT identity_id FROM identity_bindings WHERE identifier = ?",
            (identifier,),
        ).fetchone()
        return None if row is None else row["identity_id"]

    def bindings(self, identity_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT identifier FROM identity_bindings WHERE identity_id = ? ORDER BY identifier",
            (identity_id,),
        ).fetchall()
        return [r["identifier"] for r in rows]

    def get_identity(self, identity_id: str) -> Identity | None:
        row = self._conn.execute(
            "SELECT id, entity_type, canonical_name, created_at FROM identities WHERE id = ?",
            (identity_id,),
        ).fetchone()
        if row is None:
            return None
        return Identity(
            id=row["id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # ---------- Internals ----------

    def _lookup_many(self, identifiers: set[str]) -> dict[str, str | None]:
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        rows = self._conn.execute(
            f"SELECT identifier, identity_id FROM identity_bindings "  # noqa: S608
            f"WHERE identifier IN ({placeholders})",
            tuple(identifiers),
        ).fetchall()
        by_identifier: dict[str, str | None] = dict.fromkeys(identifiers)
        for r in rows:
            by_identifier[r["identifier"]] = r["identity_id"]
        return by_identifier

    def _create_identity(
        self,
        identifiers: set[str],
        *,
        entity_type: EntityKind,
        canonical_name: str | None,
    ) -> str:
        identity_id = make_entity_id(entity_type)
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO identities (id, entity_type, canonical_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (identity_id, entity_type, canonical_name, now),
            )
            cur.executemany(
                """
                INSERT INTO identity_bindings
                    (identity_id, identifier, added_at)
                VALUES (?, ?, ?)
                """,
                [(identity_id, ident, now) for ident in sorted(identifiers)],
            )
        return identity_id

    def _bind_all(self, identity_id: str, identifiers: set[str]) -> None:
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.executemany(
                """
                INSERT INTO identity_bindings
                    (identity_id, identifier, added_at)
                VALUES (?, ?, ?)
                """,
                [(identity_id, ident, now) for ident in sorted(identifiers)],
            )

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


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
