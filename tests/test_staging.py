"""Tests for the staging writer (step 7a + plane-aware in step 8a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_mission.ingestion import StagedItem, StagingWriter

# ---------- Helpers ----------


def _personal_writer(
    tmp_path: Path,
    *,
    source: str = "gmail",
    employee_id: str = "alice",
) -> StagingWriter:
    return StagingWriter(
        wiki_root=tmp_path / "wiki",
        source=source,
        target_plane="personal",
        employee_id=employee_id,
    )


def _firm_writer(tmp_path: Path, *, source: str = "drive") -> StagingWriter:
    return StagingWriter(wiki_root=tmp_path / "wiki", source=source, target_plane="firm")


# ---------- Constructor validation ----------


def test_writer_rejects_bad_source(tmp_path: Path) -> None:
    for bad in ["", "../escape", "two words", "/abs", "src/sub"]:
        with pytest.raises(ValueError, match="source"):
            StagingWriter(
                wiki_root=tmp_path,
                source=bad,
                target_plane="firm",
            )


def test_writer_personal_requires_employee_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="personal target_plane requires employee_id"):
        StagingWriter(wiki_root=tmp_path, source="gmail", target_plane="personal")


def test_writer_firm_rejects_employee_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="firm target_plane must not carry"):
        StagingWriter(
            wiki_root=tmp_path,
            source="drive",
            target_plane="firm",
            employee_id="alice",
        )


def test_writer_rejects_bad_employee_id(tmp_path: Path) -> None:
    for bad in ["", "../escape", "with space", "/abs"]:
        with pytest.raises(ValueError):
            StagingWriter(
                wiki_root=tmp_path,
                source="gmail",
                target_plane="personal",
                employee_id=bad,
            )


def test_writer_rejects_bad_item_id(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    for bad in ["", "../escape", "with space", "/abs", "a/b"]:
        with pytest.raises(ValueError, match="item_id"):
            w.write(item_id=bad, raw={}, markdown_body="x")


def test_writer_accepts_realistic_ids(tmp_path: Path) -> None:
    """Gmail message IDs are hex-ish; Granola IDs are UUID-like."""
    w = _personal_writer(tmp_path)
    for good in ["18b3a4c5d6e7f", "msg-123", "uuid_v4-abc", "transcript.42"]:
        item = w.write(item_id=good, raw={"k": "v"}, markdown_body="body")
        assert item.item_id == good


# ---------- Personal plane write + read ----------


def test_personal_write_lands_under_plane_and_employee_dirs(
    tmp_path: Path,
) -> None:
    w = _personal_writer(tmp_path, source="gmail", employee_id="alice")
    item = w.write(
        item_id="msg-1",
        raw={"subject": "Hello", "body": "World"},
        markdown_body="From: alice\n\nHello world.",
    )

    expected_raw = tmp_path / "wiki/staging/personal/alice/gmail/.raw/msg-1.json"
    expected_md = tmp_path / "wiki/staging/personal/alice/gmail/msg-1.md"
    assert item.raw_path == expected_raw
    assert item.markdown_path == expected_md
    assert item.target_plane == "personal"
    assert item.employee_id == "alice"
    assert expected_raw.exists()
    assert expected_md.exists()


def test_firm_write_lands_under_firm_staging(tmp_path: Path) -> None:
    w = _firm_writer(tmp_path, source="drive")
    item = w.write(
        item_id="doc-1",
        raw={"title": "Q3 memo"},
        markdown_body="Q3 memo body",
    )
    expected_md = tmp_path / "wiki/staging/firm/drive/doc-1.md"
    assert item.markdown_path == expected_md
    assert item.target_plane == "firm"
    assert item.employee_id is None
    assert expected_md.exists()


def test_markdown_carries_plane_and_employee_frontmatter(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path, employee_id="alice")
    item = w.write(
        item_id="msg-1",
        raw={},
        markdown_body="hello world",
        frontmatter_extras={"pulled_via": "get_message"},
    )
    text = item.markdown_path.read_text()
    assert "source: gmail" in text
    assert "source_id: msg-1" in text
    assert "target_plane: personal" in text
    assert "employee_id: alice" in text
    assert "pulled_via: get_message" in text


def test_firm_markdown_omits_employee_id(tmp_path: Path) -> None:
    w = _firm_writer(tmp_path, source="drive")
    item = w.write(item_id="doc-1", raw={}, markdown_body="x")
    text = item.markdown_path.read_text()
    assert "target_plane: firm" in text
    assert "employee_id" not in text


def test_extras_cannot_override_canonical_fields(tmp_path: Path) -> None:
    """source / source_id / ingested_at / target_plane / employee_id are owned by the writer."""
    w = _personal_writer(tmp_path, employee_id="alice")
    item = w.write(
        item_id="msg-1",
        raw={},
        markdown_body="x",
        frontmatter_extras={
            "source": "spoofed",
            "source_id": "wrong",
            "ingested_at": "1999-01-01",
            "target_plane": "firm",
            "employee_id": "bob",
            "from": "real@x.com",
        },
    )
    text = item.markdown_path.read_text()
    assert "source: gmail" in text
    assert "source: spoofed" not in text
    assert "source_id: msg-1" in text
    assert "source_id: wrong" not in text
    assert "1999-01-01" not in text
    assert "target_plane: personal" in text
    assert "target_plane: firm" not in text
    assert "employee_id: alice" in text
    assert "employee_id: bob" not in text
    assert "from: real@x.com" in text


def test_write_is_atomic_replace(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    w.write(item_id="msg-1", raw={"v": 1}, markdown_body="first")
    w.write(item_id="msg-1", raw={"v": 2}, markdown_body="second")
    raw_path = tmp_path / "wiki/staging/personal/alice/gmail/.raw/msg-1.json"
    md_path = tmp_path / "wiki/staging/personal/alice/gmail/msg-1.md"
    assert json.loads(raw_path.read_text()) == {"v": 2}
    md = md_path.read_text()
    assert "second" in md
    assert "first" not in md


def test_write_creates_parent_dirs_lazily(tmp_path: Path) -> None:
    w = StagingWriter(
        wiki_root=tmp_path / "fresh-root",
        source="granola",
        target_plane="personal",
        employee_id="alice",
    )
    w.write(item_id="t-1", raw={}, markdown_body="x")
    assert (tmp_path / "fresh-root/staging/personal/alice/granola/t-1.md").exists()


# ---------- get / list / remove ----------


def test_get_returns_pointer_when_both_files_exist(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    w.write(item_id="msg-1", raw={}, markdown_body="x")
    item = w.get("msg-1")
    assert item is not None
    assert item.item_id == "msg-1"
    assert item.source == "gmail"
    assert item.target_plane == "personal"
    assert item.employee_id == "alice"


def test_get_returns_none_when_missing(tmp_path: Path) -> None:
    assert _personal_writer(tmp_path).get("missing") is None


def test_list_pending_sorted_by_id(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    for item_id in ["msg-3", "msg-1", "msg-2"]:
        w.write(item_id=item_id, raw={}, markdown_body="x")
    items = w.list_pending()
    assert [i.item_id for i in items] == ["msg-1", "msg-2", "msg-3"]


def test_list_pending_empty_when_no_directory(tmp_path: Path) -> None:
    assert _personal_writer(tmp_path).list_pending() == []


def test_list_pending_skips_orphan_markdown_without_raw(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    w.write(item_id="msg-1", raw={}, markdown_body="x")
    (tmp_path / "wiki/staging/personal/alice/gmail/.raw/msg-1.json").unlink()
    assert w.list_pending() == []


def test_remove_drops_both_files(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    item = w.write(item_id="msg-1", raw={}, markdown_body="x")
    assert w.remove("msg-1") is True
    assert not item.raw_path.exists()
    assert not item.markdown_path.exists()


def test_remove_idempotent_on_missing(tmp_path: Path) -> None:
    assert _personal_writer(tmp_path).remove("never-existed") is False


# ---------- iter_raw ----------


def test_iter_raw_yields_id_and_payload(tmp_path: Path) -> None:
    w = _personal_writer(tmp_path)
    w.write(item_id="msg-1", raw={"a": 1}, markdown_body="x")
    w.write(item_id="msg-2", raw={"b": 2}, markdown_body="y")
    pairs = list(w.iter_raw())
    assert dict(pairs) == {"msg-1": {"a": 1}, "msg-2": {"b": 2}}


# ---------- Multi-plane isolation ----------


def test_personal_and_firm_staging_isolated(tmp_path: Path) -> None:
    """Personal plane staging doesn't surface in firm plane listings."""
    personal = StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    firm = StagingWriter(wiki_root=tmp_path / "wiki", source="drive", target_plane="firm")
    personal.write(item_id="p-1", raw={"src": "personal"}, markdown_body="p")
    firm.write(item_id="f-1", raw={"src": "firm"}, markdown_body="f")

    assert {i.item_id for i in personal.list_pending()} == {"p-1"}
    assert {i.item_id for i in firm.list_pending()} == {"f-1"}


def test_personal_staging_isolated_across_employees(tmp_path: Path) -> None:
    alice = StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    bob = StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="gmail",
        target_plane="personal",
        employee_id="bob",
    )
    alice.write(item_id="x", raw={"who": "alice"}, markdown_body="a")
    bob.write(item_id="x", raw={"who": "bob"}, markdown_body="b")

    alice_items = alice.list_pending()
    bob_items = bob.list_pending()
    assert len(alice_items) == 1 and len(bob_items) == 1
    assert json.loads(alice_items[0].raw_path.read_text()) == {"who": "alice"}
    assert json.loads(bob_items[0].raw_path.read_text()) == {"who": "bob"}


def test_multiple_sources_isolated_within_plane(tmp_path: Path) -> None:
    gmail = StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    granola = StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="granola",
        target_plane="personal",
        employee_id="alice",
    )
    gmail.write(item_id="x", raw={"src": "gmail"}, markdown_body="g")
    granola.write(item_id="x", raw={"src": "granola"}, markdown_body="r")
    assert len(gmail.list_pending()) == 1
    assert len(granola.list_pending()) == 1


# ---------- StagedItem model ----------


def test_staged_item_is_frozen() -> None:
    item = StagedItem(
        item_id="m",
        source="gmail",
        target_plane="personal",
        employee_id="alice",
        raw_path=Path("/tmp/r.json"),
        markdown_path=Path("/tmp/r.md"),
    )
    with pytest.raises(Exception):  # noqa: B017
        item.item_id = "other"  # type: ignore[misc]


# ---------- External-id length boundary (Google Calendar recurring events) ----------


def test_writer_accepts_long_external_item_id(tmp_path: Path) -> None:
    """Google Calendar recurring-event instance ids routinely exceed 128 chars.

    Example shape: <base-id>_20260414T090000Z where the base alone is
    ~200 hex chars. Staging must accept these — they are real, system-generated
    identifiers that we don't control.
    """
    w = _personal_writer(tmp_path)
    long_id = (
        "_60q30c1g60o30e1i60o4ac1g60rj8gpl88rj2c1h84s34h9g60s30c1g60o30c1g"
        "8oo32g9j8l2k2gpp6csk8ghg64o30c1g60o30c1g60o30c1g60o32c1g60o30c1g"
        "6t1j2cq66gsk8gpi64sjgchk88sk8h2270o34ca68d136hho6l0g_20260414T090000Z"
    )
    assert len(long_id) > 128
    item = w.write(item_id=long_id, raw={"id": long_id}, markdown_body="x")
    assert item.item_id == long_id


def test_writer_accepts_246_char_external_item_id(tmp_path: Path) -> None:
    """246 chars — sized for ext4 (255-byte filename) minus our suffix."""
    w = _personal_writer(tmp_path)
    boundary_id = "a" * 246
    item = w.write(item_id=boundary_id, raw={"id": boundary_id}, markdown_body="x")
    assert item.item_id == boundary_id


def test_writer_rejects_247_char_external_item_id(tmp_path: Path) -> None:
    """One past the ceiling — would exceed ext4 filename limit on disk."""
    w = _personal_writer(tmp_path)
    too_long = "a" * 247
    with pytest.raises(ValueError, match="item_id"):
        w.write(item_id=too_long, raw={"id": too_long}, markdown_body="x")


def test_writer_still_rejects_overlong_source_label(tmp_path: Path) -> None:
    """Source labels are operator-controlled — keep the tight 128-char rule."""
    too_long_source = "a" * 200
    with pytest.raises(ValueError, match="source"):
        StagingWriter(
            wiki_root=tmp_path,
            source=too_long_source,
            target_plane="firm",
        )
