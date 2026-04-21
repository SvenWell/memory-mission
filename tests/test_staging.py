"""Tests for the staging writer (step 7a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_mission.ingestion import StagedItem, StagingWriter

# ---------- Helpers ----------


def _writer(tmp_path: Path, source: str = "gmail") -> StagingWriter:
    return StagingWriter(wiki_root=tmp_path / "wiki", source=source)


# ---------- Source / item_id validation ----------


def test_writer_rejects_bad_source(tmp_path: Path) -> None:
    for bad in ["", "../escape", "two words", "/abs", "src/sub"]:
        with pytest.raises(ValueError, match="source"):
            StagingWriter(wiki_root=tmp_path, source=bad)


def test_writer_rejects_bad_item_id(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    for bad in ["", "../escape", "with space", "/abs", "a/b"]:
        with pytest.raises(ValueError, match="item_id"):
            w.write(item_id=bad, raw={}, markdown_body="x")


def test_writer_accepts_realistic_ids(tmp_path: Path) -> None:
    """Gmail message IDs are hex-ish; Granola IDs are UUID-like."""
    w = _writer(tmp_path)
    for good in ["18b3a4c5d6e7f", "msg-123", "uuid_v4-abc", "transcript.42"]:
        item = w.write(item_id=good, raw={"k": "v"}, markdown_body="body")
        assert item.item_id == good


# ---------- Write + read ----------


def test_write_creates_raw_and_markdown_under_source(tmp_path: Path) -> None:
    w = _writer(tmp_path, source="gmail")
    item = w.write(
        item_id="msg-1",
        raw={"subject": "Hello", "body": "World"},
        markdown_body="From: alice\n\nHello world.",
    )

    assert item.raw_path == tmp_path / "wiki" / "staging" / "gmail" / ".raw" / "msg-1.json"
    assert item.markdown_path == tmp_path / "wiki" / "staging" / "gmail" / "msg-1.md"
    assert item.raw_path.exists()
    assert item.markdown_path.exists()

    raw_data = json.loads(item.raw_path.read_text())
    assert raw_data == {"subject": "Hello", "body": "World"}


def test_markdown_carries_frontmatter(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    item = w.write(
        item_id="msg-1",
        raw={},
        markdown_body="hello world",
        frontmatter_extras={"pulled_via": "get_message", "from": "alice@x.com"},
    )
    text = item.markdown_path.read_text()
    assert text.startswith("---\n")
    assert "source: gmail" in text
    assert "source_id: msg-1" in text
    assert "ingested_at:" in text
    assert "pulled_via: get_message" in text
    assert "hello world" in text


def test_extras_cannot_override_canonical_fields(tmp_path: Path) -> None:
    """source / source_id / ingested_at are owned by the writer, not the caller."""
    w = _writer(tmp_path)
    item = w.write(
        item_id="msg-1",
        raw={},
        markdown_body="x",
        frontmatter_extras={
            "source": "spoofed",
            "source_id": "wrong",
            "ingested_at": "1999-01-01",
            "from": "real@x.com",
        },
    )
    text = item.markdown_path.read_text()
    assert "source: gmail" in text
    assert "source: spoofed" not in text
    assert "source_id: msg-1" in text
    assert "source_id: wrong" not in text
    assert "1999-01-01" not in text
    assert "from: real@x.com" in text


def test_write_is_atomic_replace(tmp_path: Path) -> None:
    """Re-writing the same item replaces both files cleanly."""
    w = _writer(tmp_path)
    w.write(item_id="msg-1", raw={"v": 1}, markdown_body="first")
    w.write(item_id="msg-1", raw={"v": 2}, markdown_body="second")
    raw = json.loads((tmp_path / "wiki/staging/gmail/.raw/msg-1.json").read_text())
    assert raw == {"v": 2}
    md = (tmp_path / "wiki/staging/gmail/msg-1.md").read_text()
    assert "second" in md
    assert "first" not in md


def test_write_creates_parent_dirs_lazily(tmp_path: Path) -> None:
    """No need to mkdir before first write."""
    w = StagingWriter(wiki_root=tmp_path / "fresh-root", source="granola")
    w.write(item_id="t-1", raw={}, markdown_body="x")
    assert (tmp_path / "fresh-root/staging/granola/t-1.md").exists()


# ---------- get / list / remove ----------


def test_get_returns_pointer_when_both_files_exist(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    w.write(item_id="msg-1", raw={}, markdown_body="x")
    item = w.get("msg-1")
    assert item is not None
    assert item.item_id == "msg-1"
    assert item.source == "gmail"


def test_get_returns_none_when_missing(tmp_path: Path) -> None:
    assert _writer(tmp_path).get("missing") is None


def test_list_pending_sorted_by_id(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    for item_id in ["msg-3", "msg-1", "msg-2"]:
        w.write(item_id=item_id, raw={}, markdown_body="x")
    items = w.list_pending()
    assert [i.item_id for i in items] == ["msg-1", "msg-2", "msg-3"]


def test_list_pending_empty_when_no_directory(tmp_path: Path) -> None:
    assert _writer(tmp_path).list_pending() == []


def test_list_pending_skips_orphan_markdown_without_raw(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    w.write(item_id="msg-1", raw={}, markdown_body="x")
    # Manually delete the raw — list_pending should drop the orphan.
    (tmp_path / "wiki/staging/gmail/.raw/msg-1.json").unlink()
    assert w.list_pending() == []


def test_remove_drops_both_files(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    item = w.write(item_id="msg-1", raw={}, markdown_body="x")
    assert w.remove("msg-1") is True
    assert not item.raw_path.exists()
    assert not item.markdown_path.exists()


def test_remove_idempotent_on_missing(tmp_path: Path) -> None:
    assert _writer(tmp_path).remove("never-existed") is False


# ---------- iter_raw ----------


def test_iter_raw_yields_id_and_payload(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    w.write(item_id="msg-1", raw={"a": 1}, markdown_body="x")
    w.write(item_id="msg-2", raw={"b": 2}, markdown_body="y")
    pairs = list(w.iter_raw())
    by_id = dict(pairs)
    assert by_id == {"msg-1": {"a": 1}, "msg-2": {"b": 2}}


# ---------- Multi-source isolation ----------


def test_multiple_sources_isolated_under_separate_dirs(tmp_path: Path) -> None:
    gmail = StagingWriter(wiki_root=tmp_path / "wiki", source="gmail")
    granola = StagingWriter(wiki_root=tmp_path / "wiki", source="granola")
    gmail.write(item_id="x", raw={"src": "gmail"}, markdown_body="g")
    granola.write(item_id="x", raw={"src": "granola"}, markdown_body="r")

    g_items = gmail.list_pending()
    r_items = granola.list_pending()
    assert len(g_items) == 1 and len(r_items) == 1
    assert json.loads(g_items[0].raw_path.read_text()) == {"src": "gmail"}
    assert json.loads(r_items[0].raw_path.read_text()) == {"src": "granola"}


# ---------- StagedItem model ----------


def test_staged_item_is_frozen() -> None:
    item = StagedItem(
        item_id="m",
        source="gmail",
        raw_path=Path("/tmp/r.json"),
        markdown_path=Path("/tmp/r.md"),
    )
    with pytest.raises(Exception):  # noqa: B017
        item.item_id = "other"  # type: ignore[misc]
