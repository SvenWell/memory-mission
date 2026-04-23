"""Per-employee lessons — distilled patterns the agent learned.

Two files: ``lessons.jsonl`` is the source of truth (one JSON line
per lesson, append-only); ``LESSONS.md`` is rendered from it for
human reading and Obsidian display. NEVER hand-edit ``LESSONS.md`` —
it's regenerated on every append. Hand-edit ``lessons.jsonl`` (or
use the ``append`` API).

Same shape as agentic-stack's lessons layer adapted to per-employee
scope. The agent appends as it works; ``render`` produces the
markdown view.

Pairs with the future federated cross-employee detector
(`project_federated_pattern_detector.md`): when the same lesson
recurs across multiple employees, the detector can promote it to a
firm-plane Proposal for institutional adoption.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.schema import plane_root, validate_employee_id

LESSONS_DIR = "lessons"
LESSONS_JSONL = "lessons.jsonl"
LESSONS_MARKDOWN = "LESSONS.md"


class Lesson(BaseModel):
    """One distilled lesson the agent learned.

    ``rule`` is the single-sentence takeaway ("always serialize
    timestamps in UTC"); ``rationale`` explains why it was learned
    (which incident / pattern triggered it). ``source_skill`` (if
    set) lets ``filter`` find lessons that came out of a specific
    workflow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lesson_id: str
    rule: str
    rationale: str
    learned_at: datetime
    source_skill: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


def lesson_id(rule: str) -> str:
    """Deterministic hash of the rule text — same rule = same id."""
    digest = hashlib.sha256(rule.strip().lower().encode("utf-8")).digest()
    return digest[:12].hex()


def lessons_dir(wiki_root: Path, employee_id: str) -> Path:
    """Absolute lessons/ directory for an employee."""
    validate_employee_id(employee_id)
    return wiki_root / plane_root("personal", employee_id) / LESSONS_DIR


def jsonl_path(wiki_root: Path, employee_id: str) -> Path:
    """Absolute path to lessons.jsonl for an employee."""
    return lessons_dir(wiki_root, employee_id) / LESSONS_JSONL


def markdown_path(wiki_root: Path, employee_id: str) -> Path:
    """Absolute path to LESSONS.md for an employee."""
    return lessons_dir(wiki_root, employee_id) / LESSONS_MARKDOWN


class LessonsStore:
    """Append + read interface over an employee's lessons."""

    def __init__(self, *, wiki_root: Path, employee_id: str) -> None:
        validate_employee_id(employee_id)
        self._wiki_root = wiki_root
        self._employee_id = employee_id
        self._jsonl = jsonl_path(wiki_root, employee_id)
        self._md = markdown_path(wiki_root, employee_id)

    @property
    def employee_id(self) -> str:
        return self._employee_id

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl

    @property
    def markdown_path(self) -> Path:
        return self._md

    def append(
        self,
        *,
        rule: str,
        rationale: str,
        source_skill: str | None = None,
        source_episode_id: str | None = None,
        confidence: float = 1.0,
    ) -> Lesson:
        """Append a new lesson; re-render LESSONS.md. Idempotent on rule text."""
        if not rule.strip():
            raise ValueError("lesson rule cannot be empty")
        if not rationale.strip():
            raise ValueError("lesson rationale cannot be empty")

        lid = lesson_id(rule)
        if any(existing.lesson_id == lid for existing in self.all()):
            # Same rule already learned — return the existing lesson (idempotent).
            for existing in self.all():
                if existing.lesson_id == lid:
                    return existing

        lesson = Lesson(
            lesson_id=lid,
            rule=rule.strip(),
            rationale=rationale.strip(),
            learned_at=datetime.now(UTC),
            source_skill=source_skill,
            source_episode_id=source_episode_id,
            confidence=confidence,
        )
        self._jsonl.parent.mkdir(parents=True, exist_ok=True)
        with self._jsonl.open("a", encoding="utf-8") as fh:
            fh.write(lesson.model_dump_json() + "\n")
        self._render_to_disk()
        return lesson

    def all(self) -> list[Lesson]:
        """Return every lesson in insertion order."""
        if not self._jsonl.exists():
            return []
        out: list[Lesson] = []
        for line in self._jsonl.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            out.append(Lesson.model_validate_json(stripped))
        return out

    def filter(self, *, source_skill: str | None = None) -> list[Lesson]:
        """Return lessons matching the given filter."""
        out = self.all()
        if source_skill is not None:
            out = [lsn for lsn in out if lsn.source_skill == source_skill]
        return out

    def render(self) -> str:
        """Render the current lesson list as a markdown view."""
        lessons = self.all()
        parts = ["# Lessons"]
        parts.append("")
        parts.append(
            "_Auto-generated from `lessons.jsonl`. Do not hand-edit; edit the source instead._"
        )
        parts.append("")
        if not lessons:
            parts.append("(no lessons learned yet)")
            parts.append("")
            return "\n".join(parts)

        # Newest first so the most recent learning is most visible
        for lesson in sorted(lessons, key=lambda lsn: lsn.learned_at, reverse=True):
            parts.append(f"## {lesson.rule}")
            parts.append("")
            parts.append(f"_Why:_ {lesson.rationale}")
            parts.append("")
            stamp = lesson.learned_at.strftime("%Y-%m-%d")
            meta = [f"learned {stamp}"]
            if lesson.source_skill:
                meta.append(f"from `{lesson.source_skill}`")
            if lesson.confidence != 1.0:
                meta.append(f"confidence {lesson.confidence:.2f}")
            parts.append(f"_({' · '.join(meta)})_")
            parts.append("")
        return "\n".join(parts)

    def _render_to_disk(self) -> None:
        """Atomic write of LESSONS.md."""
        text = self.render()
        tmp = self._md.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._md)


__all__ = [
    "LESSONS_DIR",
    "LESSONS_JSONL",
    "LESSONS_MARKDOWN",
    "Lesson",
    "LessonsStore",
    "jsonl_path",
    "lesson_id",
    "lessons_dir",
    "markdown_path",
]
