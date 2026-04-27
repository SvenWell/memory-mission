"""Tests for the Hermes built-in memory → Memory Mission migration adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.integrations.hermes_seed_migrate import (
    SEED_CLOSET,
    classify_entry,
    migrate_hermes_seed,
    parse_hermes_memory_text,
)
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.synthesis.individual_boot import THREAD_STATUS_PREDICATE

# ---------- Fixtures ----------


@pytest.fixture
def hermes_root(tmp_path: Path) -> Path:
    """Synthetic ``$HERMES_HOME`` with two memory files + lock + ignored archive."""
    root = tmp_path / "hermes"
    memories = root / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text(
        "Memory Mission: public repo https://github.com/SvenWell/memory-mission. "
        "Stack: MemPalace evidence recall + individual-agent personal temporal "
        "KG/pages + boot context, with firm plane later.\n"
        "§\n"
        "Hermes gateway/CLI unified on ~/.hermes/hermes-agent/.venv and updated "
        "to origin/main as of 2026-04-27. launchd KeepAlive=true with "
        "ThrottleInterval=60.\n"
        "§\n"
        "Loom side project is deferred for now.\n",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "Sven prefers conversational Telegram replies and shorter, tool-first "
        "technical updates. Hermes is his main operational driver.\n"
        "§\n"
        "Sven wants Loom disabled/archived in Hermes for now; only revisit if "
        "he explicitly asks.\n",
        encoding="utf-8",
    )
    # Lock files — should be ignored.
    (memories / "MEMORY.md.lock").write_text("", encoding="utf-8")
    (memories / "USER.md.lock").write_text("", encoding="utf-8")
    # Session archive — explicitly out of scope.
    (root / "state.db").write_bytes(b"\x00\x01\x02")
    sessions = root / "sessions"
    sessions.mkdir()
    (sessions / "session_2026-04-26.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def mm_root(tmp_path: Path) -> Path:
    return tmp_path / "memory-mission"


# ---------- Parse + classify ----------


def test_parse_hermes_memory_text_splits_on_section_delimiter() -> None:
    raw = "First entry.\n§\n  Second entry.  \n§\nThird entry."
    out = parse_hermes_memory_text(raw)
    assert out == ["First entry.", "Second entry.", "Third entry."]


def test_parse_hermes_memory_text_drops_empty_chunks() -> None:
    raw = "§\nOne\n§\n\n§\nTwo\n§\n"
    assert parse_hermes_memory_text(raw) == ["One", "Two"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sven prefers concise replies.", "preference"),
        ("User prefers tooling like uv over pip.", "preference"),
        ("Sven wants Loom disabled.", "preference"),
        ("Memory Mission: public repo https://github.com/SvenWell/memory-mission.", "project"),
        ("Hermes gateway: see ~/.hermes/hermes-agent/.venv with launchd.", "working_memory"),
        ("Loom side project is deferred for now.", "working_memory"),
    ],
)
def test_classify_entry_buckets(text: str, expected: str) -> None:
    assert classify_entry(text) == expected


# ---------- End-to-end migration ----------


def test_migrate_creates_seed_pages_for_every_entry(hermes_root: Path, mm_root: Path) -> None:
    report = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    # 3 entries in MEMORY.md + 2 entries in USER.md.
    assert report.entries_read == 5
    assert report.seed_pages_written == 5


def test_migrate_writes_preference_triples_for_user_md(hermes_root: Path, mm_root: Path) -> None:
    report = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    # Both USER.md entries are preferences.
    assert report.preference_triples_written == 2

    # Confirm the triples landed under prefers_* on the personal KG.
    resolver = LocalIdentityResolver(mm_root / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=mm_root, employee_id="sven", identity_resolver=resolver
    )
    try:
        triples = kg.query_entity("sven", direction="outgoing")
        prefers = [t for t in triples if t.predicate.startswith("prefers_")]
        assert len(prefers) == 2
        # Provenance points at hermes_builtin_memory_seed.
        for t in prefers:
            assert t.source_closet == SEED_CLOSET
            assert t.source_file is not None and t.source_file.startswith("USER.md#")
    finally:
        kg.close()


def test_migrate_creates_project_page_for_github_entry(hermes_root: Path, mm_root: Path) -> None:
    report = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    # Memory Mission line is the only one matching <Name>: + github URL.
    assert report.project_pages_written == 1


def test_migrate_skips_lock_and_archive_files(hermes_root: Path, mm_root: Path) -> None:
    """Adapter never opens .lock files, state.db, or sessions/."""
    report = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    # Only the two declared files were considered; nothing skipped because both exist.
    assert report.skipped_files == ()
    # And entries_read matches just MEMORY.md + USER.md content.
    assert report.entries_read == 5


def test_migrate_is_idempotent(hermes_root: Path, mm_root: Path) -> None:
    """Re-running the migration overwrites prior seed pages, not duplicates them."""
    first = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    second = migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    assert first.seed_pages_written == second.seed_pages_written
    # Preferences ARE re-written on second pass (KG triples don't dedupe by
    # source_file alone — that's by design, the corroboration count tracks
    # repeats). Just confirm no crash + counts match.
    assert first.preference_triples_written == second.preference_triples_written


def test_migrate_raises_when_hermes_memories_dir_missing(tmp_path: Path, mm_root: Path) -> None:
    bogus = tmp_path / "no-such-hermes"
    with pytest.raises(FileNotFoundError, match="memories"):
        migrate_hermes_seed(hermes_root=bogus, mm_root=mm_root, user_id="sven")


def test_migrate_skips_files_that_dont_exist(tmp_path: Path, mm_root: Path) -> None:
    """If MEMORY.md is missing but USER.md exists, MEMORY.md lands in skipped_files."""
    root = tmp_path / "hermes-partial"
    memories = root / "memories"
    memories.mkdir(parents=True)
    (memories / "USER.md").write_text("Sven prefers concise.\n", encoding="utf-8")
    report = migrate_hermes_seed(hermes_root=root, mm_root=mm_root, user_id="sven")
    assert "MEMORY.md" in report.skipped_files
    assert report.entries_read == 1


def test_migrate_does_not_use_thread_status_predicate_yet(hermes_root: Path, mm_root: Path) -> None:
    """V1 classifier doesn't emit thread_status triples — those need richer parsing.

    This test pins the V1 contract: the classifier intentionally stays
    dumb. Future versions will recognize 'disabled / archived / blocked'
    as thread states.
    """
    migrate_hermes_seed(hermes_root=hermes_root, mm_root=mm_root, user_id="sven")
    resolver = LocalIdentityResolver(mm_root / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=mm_root, employee_id="sven", identity_resolver=resolver
    )
    try:
        thread_triples = kg.query_relationship(THREAD_STATUS_PREDICATE)
        assert thread_triples == []
    finally:
        kg.close()
