"""Proposal model + per-firm SQLite store.

V1's centerpiece. Emile's design in code:

- Every change to the firm plane (or an employee's personal plane)
  flows through a Proposal.
- Each Proposal is a bundle of extracted facts (identity, relationship,
  preference, event, update) that would be applied together if
  approved.
- Proposals live in a per-firm SQLite queue with lifecycle metadata:
  status (pending / approved / rejected), decision history (audit
  trail of every approve / reject / reopen), rejection count (how many
  times this has come back around).
- The review skill surfaces pending proposals to a human; the human
  approves or rejects WITH RATIONALE — rubber-stamping is structurally
  impossible because the rationale is a required field.
- Rejected proposals retain their full history so recurring churn is
  visible, not fresh each time the pattern repeats.

The store writes and reads proposals; the pipeline module
(`promotion.pipeline`) handles `create_proposal` / `promote` / `reject`
/ `reopen` — those functions combine store updates with KG writes and
observability events.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.extraction.schema import ExtractedFact
from memory_mission.memory.schema import Plane, validate_employee_id

ProposalStatus = Literal["pending", "approved", "rejected"]


class DecisionEntry(BaseModel):
    """One entry in a proposal's decision history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["approved", "rejected", "reopened"]
    reviewer_id: str
    rationale: str
    at: datetime


class Proposal(BaseModel):
    """A bundle of extracted facts proposed for promotion.

    Immutable (frozen). Lifecycle changes produce new copies via
    ``model_copy`` — the store handles persistence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    target_plane: Plane
    target_employee_id: str | None = None
    target_scope: str = "public"
    target_entity: str
    proposer_agent_id: str
    proposer_employee_id: str
    facts: list[ExtractedFact]
    source_report_path: str

    status: ProposalStatus = "pending"
    rationale: str | None = None
    reviewer_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decision_history: list[DecisionEntry] = Field(default_factory=list)
    rejection_count: int = 0

    def expected_proposal_id(self) -> str:
        """Recompute the deterministic id from the proposal's identity-bearing fields.

        Useful for integrity verification: a proposal whose stored
        ``proposal_id`` no longer matches this recomputed value has had
        its identity-bearing fields (target plane / employee / entity /
        source_report_path / facts) mutated since creation. The
        promotion pipeline calls this before any state change to refuse
        approving a tampered proposal — same shape as SomaOS's
        ``context_hash`` invariant for governed actions.
        """
        return generate_proposal_id(
            target_plane=self.target_plane,
            target_employee_id=self.target_employee_id,
            target_entity=self.target_entity,
            source_report_path=self.source_report_path,
            facts=self.facts,
        )

    def integrity_ok(self) -> bool:
        """``True`` when the stored ``proposal_id`` matches the recomputed hash."""
        return self.proposal_id == self.expected_proposal_id()


def generate_proposal_id(
    *,
    target_plane: Plane,
    target_employee_id: str | None,
    target_entity: str,
    source_report_path: str,
    facts: list[ExtractedFact],
) -> str:
    """Deterministic proposal id — same inputs produce the same id.

    Lets callers check "have we already proposed this?" before
    creating a duplicate. Callers that want non-idempotent behavior
    (e.g., re-proposing after facts changed) pass a distinct
    ``source_report_path`` or fact list.
    """
    key_parts = [
        target_plane,
        target_employee_id or "",
        target_entity,
        source_report_path,
        json.dumps(
            [fact.model_dump(mode="json") for fact in facts],
            sort_keys=True,
        ),
    ]
    digest = hashlib.sha256("\x00".join(key_parts).encode("utf-8")).digest()
    return digest[:16].hex()


# ---------- SQLite store ----------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    target_plane TEXT NOT NULL,
    target_employee_id TEXT,
    target_scope TEXT NOT NULL,
    target_entity TEXT NOT NULL,
    proposer_agent_id TEXT NOT NULL,
    proposer_employee_id TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    source_report_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    rationale TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decision_history_json TEXT NOT NULL DEFAULT '[]',
    rejection_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_target_entity
    ON proposals(target_entity);
CREATE INDEX IF NOT EXISTS idx_proposals_target_plane
    ON proposals(target_plane);
"""


class ProposalStore:
    """Per-firm SQLite queue for Proposals.

    Mirrors the shape of ``KnowledgeGraph`` / ``CheckpointStore`` /
    ``MentionTracker``: one firm = one SQLite file on disk. Never share
    across firms.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False``: agent runtimes routinely open in
        # one thread and dispatch from another. SQLite's serialized
        # lock still protects writes. Same pattern as
        # ``durable/store.py``, ``identity/local.py``, ``knowledge_graph.py``.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + busy_timeout: one MCP process per employee means multiple
        # writers against the same firm's proposal store. WAL + busy_timeout
        # keep contention manageable instead of raising OperationalError.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
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

    # ---------- Writes ----------

    def insert(self, proposal: Proposal) -> None:
        """Insert a new proposal. Raises on primary-key conflict."""
        _validate_plane_args(proposal.target_plane, proposal.target_employee_id)
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO proposals (
                    proposal_id, target_plane, target_employee_id,
                    target_scope, target_entity,
                    proposer_agent_id, proposer_employee_id,
                    facts_json, source_report_path,
                    status, rationale, reviewer_id,
                    created_at, decided_at,
                    decision_history_json, rejection_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.target_plane,
                    proposal.target_employee_id,
                    proposal.target_scope,
                    proposal.target_entity,
                    proposal.proposer_agent_id,
                    proposal.proposer_employee_id,
                    _facts_to_json(proposal.facts),
                    proposal.source_report_path,
                    proposal.status,
                    proposal.rationale,
                    proposal.reviewer_id,
                    proposal.created_at.isoformat(),
                    (proposal.decided_at.isoformat() if proposal.decided_at is not None else None),
                    _history_to_json(proposal.decision_history),
                    proposal.rejection_count,
                ),
            )

    def save(self, proposal: Proposal) -> None:
        """Upsert — insert if missing, replace if present."""
        _validate_plane_args(proposal.target_plane, proposal.target_employee_id)
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO proposals (
                    proposal_id, target_plane, target_employee_id,
                    target_scope, target_entity,
                    proposer_agent_id, proposer_employee_id,
                    facts_json, source_report_path,
                    status, rationale, reviewer_id,
                    created_at, decided_at,
                    decision_history_json, rejection_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.target_plane,
                    proposal.target_employee_id,
                    proposal.target_scope,
                    proposal.target_entity,
                    proposal.proposer_agent_id,
                    proposal.proposer_employee_id,
                    _facts_to_json(proposal.facts),
                    proposal.source_report_path,
                    proposal.status,
                    proposal.rationale,
                    proposal.reviewer_id,
                    proposal.created_at.isoformat(),
                    (proposal.decided_at.isoformat() if proposal.decided_at is not None else None),
                    _history_to_json(proposal.decision_history),
                    proposal.rejection_count,
                ),
            )

    # ---------- Reads ----------

    def get(self, proposal_id: str) -> Proposal | None:
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_proposal(row)

    def list(
        self,
        *,
        status: ProposalStatus | None = None,
        target_plane: Plane | None = None,
        target_entity: str | None = None,
    ) -> list[Proposal]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if target_plane is not None:
            clauses.append("target_plane = ?")
            params.append(target_plane)
        if target_entity is not None:
            clauses.append("target_entity = ?")
            params.append(target_entity)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM proposals{where} ORDER BY created_at ASC",
            params,
        ).fetchall()
        return [_row_to_proposal(r) for r in rows]

    def stats(self) -> dict[ProposalStatus, int]:
        out: dict[ProposalStatus, int] = {
            "pending": 0,
            "approved": 0,
            "rejected": 0,
        }
        for row in self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM proposals GROUP BY status"
        ).fetchall():
            status = row["status"]
            if status in out:
                out[status] = row["n"]
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


# ---------- Helpers ----------


def _validate_plane_args(plane: Plane, employee_id: str | None) -> None:
    if plane == "personal":
        if not employee_id:
            raise ValueError("personal target_plane requires target_employee_id")
        validate_employee_id(employee_id)
    elif plane == "firm":
        if employee_id is not None:
            raise ValueError("firm target_plane must not carry a target_employee_id")
    else:
        raise ValueError(f"unknown target_plane: {plane!r}")


def _facts_to_json(facts: list[ExtractedFact]) -> str:
    return json.dumps([f.model_dump(mode="json") for f in facts])


def _facts_from_json(text: str) -> list[ExtractedFact]:
    # Round-trip through a wrapper model so discriminator validation runs.
    from memory_mission.extraction.schema import ExtractionReport

    wrapper = ExtractionReport.model_validate(
        {
            "source": "_store",
            "source_id": "_store",
            "target_plane": "firm",
            "employee_id": None,
            "facts": json.loads(text),
        }
    )
    return wrapper.facts


def _history_to_json(history: list[DecisionEntry]) -> str:
    return json.dumps([entry.model_dump(mode="json") for entry in history])


def _history_from_json(text: str) -> list[DecisionEntry]:
    return [DecisionEntry.model_validate(e) for e in json.loads(text)]


def _row_to_proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(
        proposal_id=row["proposal_id"],
        target_plane=row["target_plane"],
        target_employee_id=row["target_employee_id"],
        target_scope=row["target_scope"],
        target_entity=row["target_entity"],
        proposer_agent_id=row["proposer_agent_id"],
        proposer_employee_id=row["proposer_employee_id"],
        facts=_facts_from_json(row["facts_json"]),
        source_report_path=row["source_report_path"],
        status=row["status"],
        rationale=row["rationale"],
        reviewer_id=row["reviewer_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        decided_at=(
            datetime.fromisoformat(row["decided_at"]) if row["decided_at"] is not None else None
        ),
        decision_history=_history_from_json(row["decision_history_json"]),
        rejection_count=row["rejection_count"],
    )


__all__ = [
    "DecisionEntry",
    "Proposal",
    "ProposalStatus",
    "ProposalStore",
    "generate_proposal_id",
]
