"""Hermes built-in memory → Memory Mission Individual seed migration.

Hermes stores its durable operating memory as two plaintext Markdown
files separated by a standalone ``§`` delimiter:

- ``$HERMES_HOME/memories/MEMORY.md`` — assistant / environment /
  project operating memory (repo paths, system facts, project
  context, durable operational notes).
- ``$HERMES_HOME/memories/USER.md`` — user profile / preferences
  (communication style, stable personal/workflow facts).

This adapter reads those two files, splits each on ``§``, and writes
the entries into the per-user Memory Mission substrate so a fresh
Hermes connection isn't cold. Two writes per entry:

1. **Always**: a working-memory page (``domain=concepts``,
   ``extra.type=working_memory``) capturing the raw entry text. This
   is lossless — we never drop content, even from entries the
   classifier doesn't recognize.
2. **When recognized**: a structured record (preference triple,
   project page, etc.) so the entry is queryable via KG primitives
   and surfaces in ``compile_individual_boot_context``.

Classification (intentionally dumb per Hermes' V1 guidance):

- ``Sven prefers …`` / ``User prefers …`` → ``(user_id, prefers_<slug>, <value>)``
- Contains ``github.com/`` or starts with ``<Project>:`` → project
  page (``type=project``, slug derived from the project name).
- Otherwise: only the seed working-memory page is written.

Provenance on every write:

- ``source_closet = "hermes_builtin_memory_seed"``
- ``source_file = "MEMORY.md"`` or ``"USER.md"``
- ``source_span = entry index in the file``

Slugs are deterministic (``hermes-seed-<file>-<idx:03d>``) so
re-running the migration overwrites prior entries instead of
duplicating them. Idempotent.

**Out of scope for V1:**

- ``$HERMES_HOME/state.db`` — conversation/session archive.
- ``$HERMES_HOME/sessions/*.jsonl`` / ``session_*.json`` — same.
- ``.lock`` files in ``memories/`` — empty lock files, not content.

Those stay in Hermes session search; a future commit can ingest
them as evidence (MemPalace recall layer), not operating state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.engine import BrainEngine, InMemoryEngine
from memory_mission.memory.schema import validate_employee_id
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.personal_brain.working_pages import (
    new_project_page,
    new_working_memory_page,
)
from memory_mission.synthesis.individual_boot import PREFERENCE_PREDICATE_PREFIX

if TYPE_CHECKING:
    from memory_mission.memory.pages import Page

SEED_CLOSET = "hermes_builtin_memory_seed"
ENTRY_DELIMITER = "§"
DEFAULT_HERMES_MEMORY_FILES: tuple[str, ...] = ("MEMORY.md", "USER.md")

EntryClass = Literal["preference", "project", "working_memory"]


@dataclass(frozen=True)
class MigrationReport:
    """Counts + slugs produced by one migration run."""

    entries_read: int = 0
    seed_pages_written: int = 0
    preference_triples_written: int = 0
    project_pages_written: int = 0
    skipped_files: tuple[str, ...] = ()

    def merge(self, other: MigrationReport) -> MigrationReport:
        return MigrationReport(
            entries_read=self.entries_read + other.entries_read,
            seed_pages_written=self.seed_pages_written + other.seed_pages_written,
            preference_triples_written=self.preference_triples_written
            + other.preference_triples_written,
            project_pages_written=self.project_pages_written + other.project_pages_written,
            skipped_files=self.skipped_files + other.skipped_files,
        )


# ---------- Public API ----------


def migrate_hermes_seed(
    *,
    hermes_root: Path | str,
    mm_root: Path | str,
    user_id: str,
    files: tuple[str, ...] = DEFAULT_HERMES_MEMORY_FILES,
) -> MigrationReport:
    """Migrate Hermes built-in memory into Memory Mission Individual.

    Args:
        hermes_root: Path to ``$HERMES_HOME`` (containing ``memories/``).
        mm_root: Path to ``$MM_ROOT`` (will be created if missing).
        user_id: Memory Mission user id (e.g. ``"sven"``).
        files: File basenames inside ``hermes_root/memories/`` to migrate.
            Defaults to ``("MEMORY.md", "USER.md")``. ``.lock`` /
            ``state.db`` / ``sessions/`` are NEVER read.

    Returns:
        ``MigrationReport`` with counts. Idempotent — slug derivation
        means re-running overwrites prior seed pages rather than
        duplicating.
    """
    hermes_root = Path(hermes_root).expanduser()
    mm_root = Path(mm_root).expanduser()
    validate_employee_id(user_id)

    memories_dir = hermes_root / "memories"
    if not memories_dir.is_dir():
        raise FileNotFoundError(
            f"Hermes memories dir not found at {memories_dir}. "
            "Pass hermes_root pointing at $HERMES_HOME (the dir containing memories/)."
        )

    # Open per-user substrate
    mm_root.mkdir(parents=True, exist_ok=True)
    resolver = LocalIdentityResolver(mm_root / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=mm_root,
        employee_id=user_id,
        identity_resolver=resolver,
    )
    engine: BrainEngine = InMemoryEngine()
    engine.connect()

    report = MigrationReport()
    try:
        for fname in files:
            path = memories_dir / fname
            if not path.is_file():
                report = report.merge(MigrationReport(skipped_files=(fname,)))
                continue
            file_report = _migrate_file(
                path=path,
                source_basename=fname,
                user_id=user_id,
                kg=kg,
                engine=engine,
            )
            report = report.merge(file_report)
    finally:
        kg.close()

    return report


# ---------- Per-file ----------


def _migrate_file(
    *,
    path: Path,
    source_basename: str,
    user_id: str,
    kg: PersonalKnowledgeGraph,
    engine: BrainEngine,
) -> MigrationReport:
    raw = path.read_text(encoding="utf-8")
    entries = parse_hermes_memory_text(raw)
    report = MigrationReport(entries_read=len(entries))

    for idx, text in enumerate(entries):
        seed_slug = f"hermes-seed-{_slugify(source_basename)}-{idx:03d}"
        seed_page = new_working_memory_page(
            slug=seed_slug,
            title=f"Hermes seed — {source_basename} entry {idx}",
            compiled_truth=text,
            sources=[f"{SEED_CLOSET}:{source_basename}#{idx}"],
            extras={"hermes_seed_index": idx, "hermes_seed_file": source_basename},
        )
        engine.put_page(seed_page, plane="personal", employee_id=user_id)
        report = report.merge(MigrationReport(seed_pages_written=1))

        # Structured extraction (best-effort, intentionally narrow).
        cls = classify_entry(text)
        if cls == "preference":
            extra = _extract_preference(text)
            if extra is not None:
                predicate, value = extra
                kg.add_triple(
                    user_id,
                    predicate,
                    value,
                    source_closet=SEED_CLOSET,
                    source_file=f"{source_basename}#{idx}",
                )
                report = report.merge(MigrationReport(preference_triples_written=1))
        elif cls == "project":
            project_page = _extract_project_page(
                text=text, source_basename=source_basename, idx=idx
            )
            if project_page is not None:
                engine.put_page(project_page, plane="personal", employee_id=user_id)
                report = report.merge(MigrationReport(project_pages_written=1))

    return report


# ---------- Parsing + classification ----------


def parse_hermes_memory_text(raw: str) -> list[str]:
    """Split a Hermes memory file into trimmed non-empty entries.

    Entries are separated by lines containing only ``§`` (with
    optional surrounding whitespace). Empty entries are dropped.
    Public for tests + downstream tooling.
    """
    return [chunk.strip() for chunk in raw.split(ENTRY_DELIMITER) if chunk.strip()]


_PREFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:Sven|User)\s+prefers?\s+(?P<value>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*(?:Sven|User)\s+wants?\s+(?P<value>.+)$", re.IGNORECASE | re.DOTALL),
)
_PROJECT_HEADER_RE = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z0-9 _-]{1,80})\s*:\s")
_GITHUB_URL_RE = re.compile(r"github\.com/[A-Za-z0-9_./-]+")


def classify_entry(text: str) -> EntryClass:
    """Best-effort bucketing — see module docstring for V1 rules."""
    if any(p.match(text) for p in _PREFERENCE_PATTERNS):
        return "preference"
    if _PROJECT_HEADER_RE.match(text) and _GITHUB_URL_RE.search(text):
        return "project"
    return "working_memory"


def _extract_preference(text: str) -> tuple[str, str] | None:
    """Return ``(predicate, value)`` for a preference entry, or None.

    Predicate slug is built from the first 1–3 verbs/nouns after
    ``prefers`` / ``wants``. Value is the remainder of the entry.
    """
    for pattern in _PREFERENCE_PATTERNS:
        m = pattern.match(text)
        if m is None:
            continue
        value = m.group("value").strip()
        predicate_seed = " ".join(value.split()[:3])
        slug = _slugify(predicate_seed) or "general"
        predicate = f"{PREFERENCE_PREDICATE_PREFIX}{slug.replace('-', '_')}"
        return predicate, value
    return None


def _extract_project_page(
    *,
    text: str,
    source_basename: str,
    idx: int,
) -> Page | None:
    """Return a project page (``type=project``) for a recognized entry."""
    header = _PROJECT_HEADER_RE.match(text)
    if header is None:
        return None
    project_name = header.group("name").strip()
    slug = f"hermes-seed-project-{_slugify(project_name)}"
    return new_project_page(
        slug=slug,
        title=project_name,
        compiled_truth=text,
        sources=[f"{SEED_CLOSET}:{source_basename}#{idx}"],
        extras={"hermes_seed_origin": True},
    )


# ---------- Helpers ----------


def _slugify(text: str) -> str:
    """Lowercase + non-alnum-collapsed kebab slug. Empty → empty."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned[:80]


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate Hermes built-in memory into Memory Mission Individual."
    )
    parser.add_argument(
        "--hermes-root",
        type=Path,
        default=Path("~/.hermes").expanduser(),
        help="$HERMES_HOME (the dir containing memories/). Default ~/.hermes",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("~/.memory-mission").expanduser(),
        help="$MM_ROOT — Memory Mission root. Default ~/.memory-mission",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="Memory Mission user id (matches MM_USER_ID).",
    )
    args = parser.parse_args(argv)

    report = migrate_hermes_seed(
        hermes_root=args.hermes_root,
        mm_root=args.root,
        user_id=args.user_id,
    )
    print(f"entries_read              {report.entries_read}")
    print(f"seed_pages_written        {report.seed_pages_written}")
    print(f"preference_triples        {report.preference_triples_written}")
    print(f"project_pages             {report.project_pages_written}")
    if report.skipped_files:
        print(f"skipped_files             {', '.join(report.skipped_files)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())


__all__ = [
    "DEFAULT_HERMES_MEMORY_FILES",
    "ENTRY_DELIMITER",
    "SEED_CLOSET",
    "EntryClass",
    "MigrationReport",
    "classify_entry",
    "main",
    "migrate_hermes_seed",
    "parse_hermes_memory_text",
]
