"""Episodic log — what the agent did / observed for one employee.

Per-employee event log distinct from the firm-scoped observability
trail. Records skill invocations, decisions, outcomes, and reflections
the agent saw worth remembering. Salience scoring (recency × pain ×
importance × recurrence) lets ``top_k`` surface the most relevant
entries before agent decisions.

Append-only JSONL — one entry per line — so concurrent writers (rare,
but possible) don't corrupt the file. Read-side parses the whole file
into ``AgentLearning`` objects.

Pairs with ``personal_brain/lessons.py``: episodic events distill into
lessons after enough pattern recurrence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.salience import salience_score
from memory_mission.memory.schema import plane_root, validate_employee_id

EPISODIC_DIR = "episodic"
LEARNINGS_FILENAME = "AGENT_LEARNINGS.jsonl"


class AgentLearning(BaseModel):
    """One episodic memory entry.

    Salience-scoring fields (``pain_score``, ``importance``,
    ``recurrence_count``) feed ``salience_score()`` so ``top_k`` can
    surface the most relevant entries. Defaults are neutral (5/10) so
    callers only set them when they have a real signal.
    """

    model_config = ConfigDict(extra="allow")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    skill: str
    action: str
    outcome: str = "unknown"
    pain_score: float = 5.0
    importance: float = 5.0
    recurrence_count: int = 1
    notes: str = ""

    def salience(self) -> float:
        """Compute this entry's salience using the shared scoring formula."""
        return salience_score(self.model_dump(mode="json"))


def episodic_dir(wiki_root: Path, employee_id: str) -> Path:
    """Absolute episodic/ directory for an employee."""
    validate_employee_id(employee_id)
    return wiki_root / plane_root("personal", employee_id) / EPISODIC_DIR


def learnings_path(wiki_root: Path, employee_id: str) -> Path:
    """Absolute path to AGENT_LEARNINGS.jsonl for an employee."""
    return episodic_dir(wiki_root, employee_id) / LEARNINGS_FILENAME


class EpisodicLog:
    """Append + read interface over one employee's AGENT_LEARNINGS.jsonl."""

    def __init__(self, *, wiki_root: Path, employee_id: str) -> None:
        validate_employee_id(employee_id)
        self._wiki_root = wiki_root
        self._employee_id = employee_id
        self._path = learnings_path(wiki_root, employee_id)

    @property
    def employee_id(self) -> str:
        return self._employee_id

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: AgentLearning) -> None:
        """Append one entry. Atomic per-line via O_APPEND open."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")

    def all(self) -> list[AgentLearning]:
        """Return every entry, in insertion order."""
        if not self._path.exists():
            return []
        out: list[AgentLearning] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            out.append(AgentLearning.model_validate_json(stripped))
        return out

    def top_k(self, k: int = 10) -> list[AgentLearning]:
        """Return the top ``k`` entries by salience (descending).

        Salience uses the shared formula from ``memory.salience``:
        recency × (pain/10) × (importance/10) × min(recurrence, 3).
        """
        scored = [(entry.salience(), entry) for entry in self.all()]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:k]]

    def filter(
        self,
        *,
        skill: str | None = None,
        outcome: str | None = None,
    ) -> list[AgentLearning]:
        """Return entries matching the given filters."""
        out: list[AgentLearning] = []
        for entry in self.all():
            if skill is not None and entry.skill != skill:
                continue
            if outcome is not None and entry.outcome != outcome:
                continue
            out.append(entry)
        return out


def record_learning(
    *,
    wiki_root: Path,
    employee_id: str,
    skill: str,
    action: str,
    outcome: str = "unknown",
    pain_score: float = 5.0,
    importance: float = 5.0,
    notes: str = "",
    recurrence_count: int = 1,
    **extras: Any,
) -> AgentLearning:
    """Convenience: build + append an ``AgentLearning`` in one call."""
    entry = AgentLearning(
        skill=skill,
        action=action,
        outcome=outcome,
        pain_score=pain_score,
        importance=importance,
        recurrence_count=recurrence_count,
        notes=notes,
        **extras,
    )
    EpisodicLog(wiki_root=wiki_root, employee_id=employee_id).append(entry)
    return entry


__all__ = [
    "EPISODIC_DIR",
    "LEARNINGS_FILENAME",
    "AgentLearning",
    "EpisodicLog",
    "episodic_dir",
    "learnings_path",
    "record_learning",
]


# Silence "unused import" for json (referenced indirectly via Pydantic's JSON).
_ = json
