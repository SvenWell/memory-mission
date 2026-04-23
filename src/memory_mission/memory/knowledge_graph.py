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
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_mission.memory.tiers import DEFAULT_TIER, Tier

Direction = Literal["outgoing", "incoming", "both"]

# Bayesian corroboration never reaches certainty without human override.
# Confidence cap for corroborate(); initial add_triple() calls can still
# start at 1.0 if the caller is explicit.
CORROBORATION_CAP: float = 0.99


# ---------- Models ----------


class Entity(BaseModel):
    """One canonical entity in the graph (keyed by ``name``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    entity_type: str = "unknown"
    properties: dict[str, Any] = Field(default_factory=dict)


class Triple(BaseModel):
    """One (subject, predicate, object) fact with validity + provenance.

    ``scope`` carries the page-level access-control scope this fact lives
    under (``"public"`` / ``"partner-only"`` / firm-defined names).
    Copied from the source ``Proposal.target_scope`` on promotion. Read
    paths that want permission filtering pass ``viewer_scopes`` into
    ``query_entity`` / ``query_relationship`` / ``timeline`` and the KG
    drops rows whose scope is outside the viewer's set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    object: str
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: float = 1.0
    source_closet: str | None = None
    source_file: str | None = None
    corroboration_count: int = 0
    tier: Tier = DEFAULT_TIER
    scope: str = "public"

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v

    @field_validator("corroboration_count")
    @classmethod
    def _count_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"corroboration_count must be >= 0, got {v}")
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


class TripleSource(BaseModel):
    """One source that contributed to a triple.

    Every triple has at least one source (seeded on ``add_triple``). Each
    corroboration appends one more, preserving full provenance history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_closet: str | None = None
    source_file: str | None = None
    confidence_after: float
    added_at: datetime


class GraphStats(BaseModel):
    """Shape snapshot of a knowledge graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_count: int
    triple_count: int
    currently_true_triple_count: int


class MergeResult(BaseModel):
    """Outcome of a ``merge_entities`` call — what changed, who approved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_entity: str
    target_entity: str
    reviewer_id: str
    rationale: str
    merged_at: datetime
    triples_rewritten: int


class CoherenceWarning(BaseModel):
    """A proposed fact conflicts with an existing currently-true fact.

    Emitted by ``KnowledgeGraph.check_coherence`` when a new triple
    would contradict a currently-true triple on the same
    ``(subject, predicate)`` with a different ``object``.

    Structured so downstream tools (reviewers, observability, future
    eval sets) can reason about it without parsing text. Eval-friendly:
    the fields are the labels (same subject-predicate, different
    object, tier delta), and the set of warnings observed in production
    becomes the labeled corpus for section 2.7 of ``docs/EVALS.md``.

    ``conflict_type`` is extensible — V1 only ships
    ``same_predicate_different_object`` (the only case
    ``check_coherence`` detects today). Future work may add
    ``subsumed_by_higher_tier`` or ``contradicts_by_negation``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    new_object: str
    new_tier: Tier
    conflicting_object: str
    conflicting_tier: Tier
    conflict_type: Literal["same_predicate_different_object"] = "same_predicate_different_object"

    @property
    def higher_tier(self) -> Tier:
        """Whichever of ``new_tier`` / ``conflicting_tier`` has more authority."""
        from memory_mission.memory.tiers import is_above

        return (
            self.conflicting_tier
            if is_above(self.conflicting_tier, self.new_tier)
            else self.new_tier
        )

    @property
    def lower_tier(self) -> Tier:
        """Whichever of the two has less authority."""
        return self.new_tier if self.higher_tier == self.conflicting_tier else self.conflicting_tier


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
    created_at TEXT NOT NULL,
    corroboration_count INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'decision',
    scope TEXT NOT NULL DEFAULT 'public'
);

CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
CREATE INDEX IF NOT EXISTS idx_triples_currently_true
    ON triples(subject, predicate, object) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS triple_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triple_id INTEGER NOT NULL REFERENCES triples(id) ON DELETE CASCADE,
    source_closet TEXT,
    source_file TEXT,
    confidence_after REAL NOT NULL,
    added_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_triple_sources_triple_id
    ON triple_sources(triple_id);

CREATE TABLE IF NOT EXISTS entity_merges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity TEXT NOT NULL,
    target_entity TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    rationale TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    triples_rewritten INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_merges_source
    ON entity_merges(source_entity);
CREATE INDEX IF NOT EXISTS idx_entity_merges_target
    ON entity_merges(target_entity);
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
        # WAL + busy_timeout: one MCP process per employee means multiple
        # writers against the same firm DB (ADR-0003). WAL lets readers
        # run while a writer holds the lock; busy_timeout blocks writers
        # briefly instead of raising OperationalError on contention.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._run_migrations()
        self._conn.commit()

    def _run_migrations(self) -> None:
        """Apply additive migrations for DBs created before this schema version.

        SQLite lacks ``ALTER TABLE ADD COLUMN IF NOT EXISTS``, so we
        introspect ``PRAGMA table_info`` and add missing columns. Safe on
        fresh DBs because the schema already includes these columns.

        Scope default is ``'public'``: this is correct for V1 only because
        no firm had scoped triples before this column landed. Any future
        schema change that adds a different scope-like column MUST NOT
        default to a permissive value — back-fill with an explicit
        admin step so pre-existing rows don't silently leak.
        """
        triple_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(triples)")}
        if "corroboration_count" not in triple_cols:
            self._conn.execute(
                "ALTER TABLE triples ADD COLUMN corroboration_count INTEGER NOT NULL DEFAULT 0"
            )
        if "tier" not in triple_cols:
            self._conn.execute(
                "ALTER TABLE triples ADD COLUMN tier TEXT NOT NULL DEFAULT 'decision'"
            )
        if "scope" not in triple_cols:
            self._conn.execute(
                "ALTER TABLE triples ADD COLUMN scope TEXT NOT NULL DEFAULT 'public'"
            )

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
        tier: Tier = DEFAULT_TIER,
        scope: str = "public",
    ) -> Triple:
        """Insert a new triple. Triples are append-only; use ``invalidate``
        to end the validity of an existing triple instead of overwriting.

        Seeds ``triple_sources`` with the initial source row so every
        triple has at least one provenance entry. Later corroborations
        (see ``corroborate``) append additional rows.

        ``tier`` tags the fact with its authority level (see
        ``memory.tiers``). Default ``decision`` means "specific observed
        fact" and has the lowest authority. Promoting to ``policy`` /
        ``doctrine`` / ``constitution`` is a deliberate editorial act by
        the reviewer; the default makes most everyday extractions
        land safely as decisions that higher tiers can override.

        ``scope`` carries the page-level access-control scope (``public``
        by default). Read paths filter by this field when the caller
        passes ``viewer_scopes``; promotion copies the
        ``Proposal.target_scope`` onto each triple it writes.
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
            tier=tier,
            scope=scope,
        )
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO triples
                    (subject, predicate, object, valid_from, valid_to,
                     confidence, source_closet, source_file, created_at,
                     corroboration_count, tier, scope)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
                    tier,
                    scope,
                ),
            )
            triple_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO triple_sources
                    (triple_id, source_closet, source_file,
                     confidence_after, added_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (triple_id, source_closet, source_file, confidence, now),
            )
        return triple

    def has_triple_source(
        self,
        *,
        subject: str,
        predicate: str,
        obj: str,
        source_file: str | None,
    ) -> bool:
        """Return True if a currently-true triple for ``(s, p, o)`` already
        records ``source_file`` in its ``triple_sources`` provenance log.

        Used by promotion to make ``_apply_facts`` idempotent: a retry on
        the same proposal (e.g., after a transient store-save failure)
        must not double-corroborate the same source. ``None`` source_file
        matches the ``NULL`` provenance entries that can be written by
        internal paths without a source report.
        """
        if source_file is None:
            rows = self._conn.execute(
                """
                SELECT 1 FROM triples t
                JOIN triple_sources ts ON ts.triple_id = t.id
                WHERE t.subject = ? AND t.predicate = ? AND t.object = ?
                  AND t.valid_to IS NULL AND ts.source_file IS NULL
                LIMIT 1
                """,
                (subject, predicate, obj),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT 1 FROM triples t
                JOIN triple_sources ts ON ts.triple_id = t.id
                WHERE t.subject = ? AND t.predicate = ? AND t.object = ?
                  AND t.valid_to IS NULL AND ts.source_file = ?
                LIMIT 1
                """,
                (subject, predicate, obj, source_file),
            ).fetchall()
        return len(rows) > 0

    def find_current_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
    ) -> Triple | None:
        """Return the currently-true triple matching (subject, predicate, obj).

        "Currently true" = ``valid_to IS NULL``. Used by the promotion
        pipeline to decide whether to corroborate an existing fact or
        add a new one.
        """
        row = self._conn.execute(
            """
            SELECT * FROM triples
            WHERE subject = ? AND predicate = ? AND object = ?
              AND valid_to IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (subject, predicate, obj),
        ).fetchone()
        return None if row is None else _row_to_triple(row)

    def corroborate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        confidence: float,
        source_closet: str | None = None,
        source_file: str | None = None,
        scope: str = "public",
    ) -> Triple | None:
        """Bump confidence on a matching currently-true triple.

        Uses the Noisy-OR (Bayesian independent-evidence) update:
        ``new = 1 - (1 - old) * (1 - incoming)``, capped at
        ``CORROBORATION_CAP`` (0.99). Appends the new source to
        ``triple_sources`` and increments ``corroboration_count``.

        Returns the updated ``Triple`` on success, or ``None`` if no
        currently-true triple matches — the caller is expected to fall
        back to ``add_triple`` in that case.

        Rationale: re-extracting the same fact from a new source should
        strengthen belief, not create a duplicate. The cap keeps
        certainty (1.0) reachable only through explicit human override,
        never via accumulated agent corroboration.

        Raises ``ValueError`` when the incoming ``scope`` differs from
        the existing triple's scope. Scope changes are editorial — the
        reviewer must reject the proposal or handle the scope delta
        explicitly (via ``invalidate`` + re-add under the new scope).
        Silent scope drift would let a ``partner-only`` proposal weaken
        an existing ``partner-only`` fact to ``public`` (or vice versa)
        without a human on the path.
        """
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {confidence}")

        row = self._conn.execute(
            """
            SELECT id, confidence, corroboration_count, scope FROM triples
            WHERE subject = ? AND predicate = ? AND object = ?
              AND valid_to IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (subject, predicate, obj),
        ).fetchone()
        if row is None:
            return None

        existing_scope = row["scope"] if row["scope"] is not None else "public"
        if existing_scope != scope:
            raise ValueError(
                f"scope mismatch on corroborate ({subject!r}, {predicate!r}, "
                f"{obj!r}): existing={existing_scope!r} incoming={scope!r} — "
                "split the proposal, change scope deliberately via invalidate, "
                "or reject"
            )

        triple_id = row["id"]
        old_confidence = row["confidence"]
        new_confidence = min(
            CORROBORATION_CAP,
            1.0 - (1.0 - old_confidence) * (1.0 - confidence),
        )
        now = _utcnow_iso()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE triples
                SET confidence = ?,
                    corroboration_count = corroboration_count + 1
                WHERE id = ?
                """,
                (new_confidence, triple_id),
            )
            cur.execute(
                """
                INSERT INTO triple_sources
                    (triple_id, source_closet, source_file,
                     confidence_after, added_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (triple_id, source_closet, source_file, new_confidence, now),
            )

        updated = self._conn.execute("SELECT * FROM triples WHERE id = ?", (triple_id,)).fetchone()
        return _row_to_triple(updated)

    def check_coherence(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        new_tier: Tier = DEFAULT_TIER,
    ) -> list[CoherenceWarning]:
        """Return warnings for currently-true triples that contradict
        ``(subject, predicate, obj)`` at tier ``new_tier``.

        V1 detection: one warning per currently-true triple that shares
        ``(subject, predicate)`` with a different ``object``. "Currently
        true" = ``valid_to IS NULL``. Corroboration (same subject +
        predicate + object) is NOT a conflict and never surfaces here.

        Deterministic: no LLM, no fuzzy matching. That's intentional —
        per ``docs/EVALS.md`` P7, prefer deterministic graders when
        possible. Structured output makes the set of warnings a natural
        labeled corpus for eval 2.7 when distillation lands.

        Returns ``[]`` if no conflict is detected. Callers decide
        whether to log (advisory) or raise (blocking, constitutional
        mode) — this method has no side effects.
        """
        rows = self._conn.execute(
            """
            SELECT object, tier FROM triples
            WHERE subject = ? AND predicate = ? AND valid_to IS NULL
              AND object != ?
            ORDER BY id ASC
            """,
            (subject, predicate, obj),
        ).fetchall()
        warnings: list[CoherenceWarning] = []
        for row in rows:
            existing_tier = row["tier"] if row["tier"] else DEFAULT_TIER
            warnings.append(
                CoherenceWarning(
                    subject=subject,
                    predicate=predicate,
                    new_object=obj,
                    new_tier=new_tier,
                    conflicting_object=row["object"],
                    conflicting_tier=existing_tier,
                )
            )
        return warnings

    def sql_query(
        self,
        query: str,
        params: Sequence[Any] = (),
        *,
        row_limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read-only SQL over the KG's tables.

        Exposes the full relational surface to workflow agents, eval
        scripts, and debugging sessions without needing a new method
        per question. Backed by a dedicated SQLite read-only
        connection — the database engine itself rejects any write
        statement, so even if the SELECT/WITH string check misses
        something, no mutation can land.

        Tables available (see ``_SCHEMA_SQL`` for column definitions):

        - ``entities`` — canonical entity rows
        - ``triples`` — subject/predicate/object + tier + confidence
        - ``triple_sources`` — per-source provenance history
        - ``entity_merges`` — audit log of ``merge_entities`` calls

        Example::

            rows = kg.sql_query(
                "SELECT subject, COUNT(*) AS n FROM triples "
                "WHERE tier = ? AND valid_to IS NULL GROUP BY subject",
                ("doctrine",),
            )

        Args:
            query: SELECT or WITH statement. Parameterize user/agent
                input via ``?`` placeholders in the query + the
                ``params`` tuple — do NOT f-string untrusted text.
            params: Positional parameters for the query.
            row_limit: Cap on returned rows. Default 1000. Raises if
                the result would exceed this; raise the limit
                deliberately (with a reason) when you need more.

        Returns:
            List of dicts, keys are column names from the SELECT.
        """
        stripped = query.strip()
        upper = stripped.upper()
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            raise ValueError(
                f"sql_query accepts only SELECT or WITH statements; got: {stripped[:60]!r}"
            )
        # Block cross-database + PRAGMA paths. ATTACH DATABASE would let a
        # caller reach another firm's SQLite file, crossing the one-firm-
        # one-DB isolation boundary (core rule 6). PRAGMA is the only
        # remaining SQLite side-effect surface under query_only mode;
        # reject it explicitly so operators don't accidentally leak
        # schema or change connection state.
        if re.search(r"\b(ATTACH|DETACH|PRAGMA)\b", upper):
            raise ValueError("sql_query rejects ATTACH / DETACH / PRAGMA statements")
        if row_limit < 1:
            raise ValueError(f"row_limit must be >= 1, got {row_limit}")

        # Dedicated read-only connection: even a malformed validation
        # cannot land a write because the engine refuses. ``query_only``
        # is defense-in-depth — it blocks side-effect statements
        # (PRAGMA, temp-table creation) that mode=ro alone tolerates.
        ro_conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        ro_conn.row_factory = sqlite3.Row
        ro_conn.execute("PRAGMA query_only = ON")
        try:
            # Fetch one extra row so we can detect overflow.
            rows = ro_conn.execute(query, params).fetchmany(row_limit + 1)
        finally:
            ro_conn.close()
        if len(rows) > row_limit:
            raise ValueError(
                f"query returned more than row_limit={row_limit} rows; "
                "add LIMIT to the query or pass a higher row_limit"
            )
        return [dict(row) for row in rows]

    def scan_triple_sources(
        self,
        *,
        closet_prefix: str | None = None,
        currently_true_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Cross-triple scan joining ``triples`` and ``triple_sources``.

        Used by the federated detector (Step 16) to aggregate evidence
        across employees' personal planes: pass
        ``closet_prefix="personal/"`` to see only personal-plane
        provenance rows. Each returned dict carries the fields a
        downstream clustering step needs — ``subject``, ``predicate``,
        ``object``, ``tier``, ``confidence``, ``corroboration_count``,
        ``source_closet``, ``source_file``, ``triple_id``.

        Keep this as a list-of-dicts (not Pydantic) so detectors can
        group with cheap dict ops. One row per ``triple_sources`` entry
        — a triple with three corroborations produces three rows.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if currently_true_only:
            clauses.append("t.valid_to IS NULL")
        if closet_prefix is not None:
            clauses.append("ts.source_closet LIKE ?")
            params.append(f"{closet_prefix}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT
                t.id AS triple_id,
                t.subject AS subject,
                t.predicate AS predicate,
                t.object AS object,
                t.tier AS tier,
                t.confidence AS confidence,
                t.corroboration_count AS corroboration_count,
                ts.source_closet AS source_closet,
                ts.source_file AS source_file
            FROM triples t
            JOIN triple_sources ts ON ts.triple_id = t.id
            {where}
            ORDER BY t.id ASC, ts.id ASC
            """,  # noqa: S608
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def triple_sources(
        self,
        subject: str,
        predicate: str,
        obj: str,
    ) -> list[TripleSource]:
        """Return the provenance history for the currently-true matching triple.

        Returns an empty list if no currently-true triple matches. The
        list is ordered oldest-first — the initial source seeded on
        ``add_triple`` comes first, subsequent corroborations follow.
        """
        triple_row = self._conn.execute(
            """
            SELECT id FROM triples
            WHERE subject = ? AND predicate = ? AND object = ?
              AND valid_to IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (subject, predicate, obj),
        ).fetchone()
        if triple_row is None:
            return []
        rows = self._conn.execute(
            """
            SELECT source_closet, source_file, confidence_after, added_at
            FROM triple_sources
            WHERE triple_id = ?
            ORDER BY id ASC
            """,
            (triple_row["id"],),
        ).fetchall()
        return [
            TripleSource(
                source_closet=r["source_closet"],
                source_file=r["source_file"],
                confidence_after=r["confidence_after"],
                added_at=datetime.fromisoformat(r["added_at"]),
            )
            for r in rows
        ]

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
        viewer_scopes: frozenset[str] | None = None,
    ) -> list[Triple]:
        """Return triples involving ``name``.

        - ``direction="outgoing"``: triples where ``name`` is the subject
        - ``direction="incoming"``: triples where ``name`` is the object
        - ``direction="both"``: union of the above

        When ``as_of`` is set, only triples valid on that date are returned.

        When ``viewer_scopes`` is set, triples whose scope isn't in the
        viewer's set are dropped. ``None`` disables the filter (callers
        without a policy context, and internal promotion-side callers).
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
        if viewer_scopes is not None:
            triples = [t for t in triples if t.scope in viewer_scopes]
        return triples

    def query_relationship(
        self,
        predicate: str,
        *,
        as_of: date | None = None,
        viewer_scopes: frozenset[str] | None = None,
    ) -> list[Triple]:
        """Return all triples using ``predicate``, optionally filtered by date.

        ``viewer_scopes`` — same semantics as ``query_entity``.
        """
        rows = self._conn.execute(
            "SELECT * FROM triples WHERE predicate = ?", (predicate,)
        ).fetchall()
        triples = [_row_to_triple(r) for r in rows]
        if as_of is not None:
            triples = [t for t in triples if t.is_valid_at(as_of)]
        if viewer_scopes is not None:
            triples = [t for t in triples if t.scope in viewer_scopes]
        return triples

    def timeline(
        self,
        entity_name: str | None = None,
        *,
        viewer_scopes: frozenset[str] | None = None,
    ) -> list[Triple]:
        """Return triples ordered by ``valid_from`` (NULLs first).

        ``viewer_scopes`` — same semantics as ``query_entity``.
        """
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
        triples = [_row_to_triple(r) for r in rows]
        if viewer_scopes is not None:
            triples = [t for t in triples if t.scope in viewer_scopes]
        return triples

    # ---------- Entity merge ----------

    def merge_entities(
        self,
        source: str,
        target: str,
        *,
        reviewer_id: str,
        rationale: str,
    ) -> MergeResult:
        """Rewrite every triple using ``source`` to use ``target`` instead.

        Used when identity resolution reveals that two previously-separate
        entities are actually the same person/org (e.g., "alice-smith" and
        "a-smith" after an extraction supplied a shared email). Requires
        a reviewer decision with rationale — same discipline as
        ``promote()`` to prevent silent graph rewrites.

        Semantics:

        - Every triple where ``subject = source`` gets its subject updated
          to ``target``.
        - Every triple where ``object = source`` gets its object updated
          to ``target``.
        - The ``source`` entity row is deleted — it's now an alias that
          no longer exists as a distinct node.
        - ``triple_sources`` provenance rows are untouched (they key by
          ``triple_id``, not by entity name), preserving the full
          audit chain.
        - A row in ``entity_merges`` records the who/why/when for audit.

        ``merge_entities`` does NOT automatically corroborate or dedupe
        triples that might collapse (e.g., if both source and target
        already had ``works_at acme``, you end up with two triples after
        rewrite). That dedupe is a separate concern and can land later.

        Raises ``ValueError`` on empty rationale, ``source == target``,
        or when the rewrite would colocate triples of differing scopes
        on the same ``(subject, predicate, object)`` — scope drift via
        merge must be handled explicitly (split the merge, invalidate
        the conflicting triple, then re-add under the chosen scope).
        """
        if not rationale or not rationale.strip():
            raise ValueError("rationale is required on every merge")
        if source == target:
            raise ValueError("source and target must differ")

        # Pre-check: would the rewrite create (s,p,o) collisions with
        # differing scopes on the target entity? If so, raise BEFORE any
        # write — merge must not be the path that silently reclassifies.
        conflicts = self._merge_scope_conflicts(source, target)
        if conflicts:
            summary = "; ".join(conflicts)
            raise ValueError(
                f"merge would colocate differing scopes on {len(conflicts)} triple(s): {summary}"
            )

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        with self._tx() as cur:
            cur.execute(
                "UPDATE triples SET subject = ? WHERE subject = ?",
                (target, source),
            )
            subj_rewrites = cur.rowcount
            cur.execute(
                "UPDATE triples SET object = ? WHERE object = ?",
                (target, source),
            )
            obj_rewrites = cur.rowcount
            cur.execute(
                "DELETE FROM entities WHERE name = ?",
                (source,),
            )
            total = subj_rewrites + obj_rewrites
            cur.execute(
                """
                INSERT INTO entity_merges
                    (source_entity, target_entity, reviewer_id,
                     rationale, merged_at, triples_rewritten)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, target, reviewer_id, rationale, now_iso, total),
            )

        return MergeResult(
            source_entity=source,
            target_entity=target,
            reviewer_id=reviewer_id,
            rationale=rationale,
            merged_at=now,
            triples_rewritten=total,
        )

    def _merge_scope_conflicts(self, source: str, target: str) -> list[str]:
        """Return a list of (s,p,o) rewrites that would create scope collisions.

        A merge rewrites every triple ``source → target``. If after the
        rewrite two currently-true triples on the same ``(subject,
        predicate, object)`` exist at different scopes, that is a silent
        reclassification — merge refuses and the reviewer splits the work.
        """
        # Get all currently-true triples that reference source or target.
        rows = self._conn.execute(
            """
            SELECT subject, predicate, object, scope FROM triples
            WHERE valid_to IS NULL
              AND (subject = ? OR object = ? OR subject = ? OR object = ?)
            """,
            (source, source, target, target),
        ).fetchall()

        # Simulate the rewrite: source → target on subject + object.
        rewritten: dict[tuple[str, str, str], set[str]] = {}
        for r in rows:
            subj = target if r["subject"] == source else r["subject"]
            obj = target if r["object"] == source else r["object"]
            key = (subj, r["predicate"], obj)
            scope = r["scope"] if r["scope"] is not None else "public"
            rewritten.setdefault(key, set()).add(scope)

        conflicts: list[str] = []
        for (subj, pred, obj), scopes in rewritten.items():
            if len(scopes) > 1:
                scope_list = sorted(scopes)
                conflicts.append(f"{subj} {pred} {obj}: scopes={scope_list}")
        return conflicts

    def merge_history(self, entity_name: str) -> list[MergeResult]:
        """Return every merge touching ``entity_name`` (as source or target).

        Useful for audit: "why does this entity ID exist? who merged
        what into it?" Results are ordered oldest-first.
        """
        rows = self._conn.execute(
            """
            SELECT source_entity, target_entity, reviewer_id,
                   rationale, merged_at, triples_rewritten
            FROM entity_merges
            WHERE source_entity = ? OR target_entity = ?
            ORDER BY id ASC
            """,
            (entity_name, entity_name),
        ).fetchall()
        return [
            MergeResult(
                source_entity=r["source_entity"],
                target_entity=r["target_entity"],
                reviewer_id=r["reviewer_id"],
                rationale=r["rationale"],
                merged_at=datetime.fromisoformat(r["merged_at"]),
                triples_rewritten=r["triples_rewritten"],
            )
            for r in rows
        ]

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
    # Added-later columns default defensively for rows from pre-migration
    # DBs; fresh DBs always have the column.
    try:
        count = row["corroboration_count"]
    except (IndexError, KeyError):
        count = 0
    try:
        tier_value = row["tier"]
    except (IndexError, KeyError):
        tier_value = DEFAULT_TIER
    try:
        scope_value = row["scope"]
    except (IndexError, KeyError):
        scope_value = "public"
    return Triple(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=_parse_date(row["valid_from"]),
        valid_to=_parse_date(row["valid_to"]),
        confidence=row["confidence"],
        source_closet=row["source_closet"],
        source_file=row["source_file"],
        corroboration_count=count if count is not None else 0,
        tier=tier_value if tier_value is not None else DEFAULT_TIER,
        scope=scope_value if scope_value is not None else "public",
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
