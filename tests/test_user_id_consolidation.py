"""Tests for the multi-agent identifier-coordination fix (v0.1.2).

Covers:

- ``list_personal_user_ids(root)`` discovery — surface what's already
  on disk so a new agent can detect drift before silently creating
  another silo.
- ``migrate_personal_kg(from, to, root)`` consolidation — copy triples
  between two per-user KGs. Idempotent.

Background: ``project_multi_agent_identifier_gap.md`` (2026-04-27).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.integrations.migrate_user_id import (
    MigrateUserIdReport,
    migrate_personal_kg,
)
from memory_mission.personal_brain.discovery import list_personal_user_ids
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

# ---------- list_personal_user_ids ----------


def test_list_personal_user_ids_returns_empty_when_root_missing(tmp_path: Path) -> None:
    assert list_personal_user_ids(tmp_path / "no-such-root") == []


def test_list_personal_user_ids_returns_empty_when_personal_dir_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "personal").mkdir(parents=True, exist_ok=False)
    # Empty directory.
    assert list_personal_user_ids(tmp_path) == []


def test_list_personal_user_ids_finds_existing_kgs(tmp_path: Path) -> None:
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    for uid in ("alice", "bob", "carol"):
        kg = PersonalKnowledgeGraph.for_employee(
            firm_root=tmp_path, employee_id=uid, identity_resolver=resolver
        )
        kg.close()
    assert list_personal_user_ids(tmp_path) == ["alice", "bob", "carol"]


def test_list_personal_user_ids_skips_dirs_without_db_file(tmp_path: Path) -> None:
    """A bare directory without personal_kg.db is not a populated user."""
    (tmp_path / "personal" / "in-progress").mkdir(parents=True)
    # No db file; should be skipped.
    assert list_personal_user_ids(tmp_path) == []


def test_list_personal_user_ids_filters_path_unsafe_dirs(tmp_path: Path) -> None:
    """Defense-in-depth: ignore anything that doesn't pass validate_employee_id."""
    bad = tmp_path / "personal" / ".hidden"
    bad.mkdir(parents=True)
    (bad / "personal_kg.db").write_bytes(b"")  # placeholder file
    assert list_personal_user_ids(tmp_path) == []


# ---------- migrate_personal_kg ----------


def test_migrate_copies_triples_from_source_to_destination(tmp_path: Path) -> None:
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    src = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path, employee_id="sven", identity_resolver=resolver
    )
    src.add_triple(
        "sven",
        "prefers_reply",
        "concise",
        source_closet="conversational",
        source_file="session-1",
    )
    src.add_triple(
        "thread-x",
        "thread_status",
        "active",
        source_closet="conversational",
        source_file="session-1",
    )
    src.close()

    report = migrate_personal_kg(from_user_id="sven", to_user_id="6052376253", root=tmp_path)
    assert report.triples_read == 2
    assert report.triples_written == 2
    assert report.triples_skipped_already_present == 0

    dst = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path, employee_id="6052376253", identity_resolver=resolver
    )
    try:
        triples = dst.timeline()
        assert {(t.subject, t.predicate, t.object) for t in triples} == {
            ("sven", "prefers_reply", "concise"),
            ("thread-x", "thread_status", "active"),
        }
        # Provenance preserved.
        for t in triples:
            assert t.source_closet == "conversational"
            assert t.source_file == "session-1"
    finally:
        dst.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """Re-running yields zero new writes; existing triples skipped."""
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    src = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path, employee_id="sven", identity_resolver=resolver
    )
    src.add_triple(
        "sven",
        "prefers_x",
        "y",
        source_closet="c",
        source_file="f",
    )
    src.close()

    first = migrate_personal_kg(from_user_id="sven", to_user_id="6052376253", root=tmp_path)
    second = migrate_personal_kg(from_user_id="sven", to_user_id="6052376253", root=tmp_path)
    assert first.triples_written == 1
    assert second.triples_written == 0
    assert second.triples_skipped_already_present == 1


def test_migrate_rejects_same_from_and_to(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must differ"):
        migrate_personal_kg(from_user_id="x", to_user_id="x", root=tmp_path)


def test_migrate_rejects_path_unsafe_user_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        migrate_personal_kg(from_user_id="../escape", to_user_id="ok", root=tmp_path)
    with pytest.raises(ValueError):
        migrate_personal_kg(from_user_id="ok", to_user_id="/etc/passwd", root=tmp_path)


def test_migrate_raises_when_source_kg_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="source personal KG"):
        migrate_personal_kg(from_user_id="never-existed", to_user_id="dst", root=tmp_path)


def test_migrate_report_is_frozen() -> None:
    report = MigrateUserIdReport(triples_read=1, triples_written=1)
    with pytest.raises(Exception):  # noqa: B017, PT011 - frozen dataclass
        report.triples_read = 99  # type: ignore[misc]
