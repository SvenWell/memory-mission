"""Tests for ``StagingWriter.write_envelope`` (P2).

Asserts the envelope round-trips into staging with the right
frontmatter, that mismatched plane/app envelopes are rejected, and that
the raw payload is preserved verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from memory_mission.ingestion.roles import ConnectorRole, NormalizedSourceItem
from memory_mission.ingestion.staging import StagingWriter


def _personal_writer(tmp_path: Path, source: str = "gmail") -> StagingWriter:
    return StagingWriter(
        wiki_root=tmp_path,
        source=source,
        target_plane="personal",
        employee_id="alice-northpoint",
    )


def _firm_writer(tmp_path: Path, source: str = "drive") -> StagingWriter:
    return StagingWriter(
        wiki_root=tmp_path,
        source=source,
        target_plane="firm",
    )


def _gmail_envelope(
    *,
    target_plane: str = "personal",
    concrete_app: str = "gmail",
    target_scope: str = "external-shared",
) -> NormalizedSourceItem:
    return NormalizedSourceItem(
        source_role=ConnectorRole.EMAIL,
        concrete_app=concrete_app,
        external_object_type="message",
        external_id="msg-123",
        container_id="thread-9",
        url="https://mail.google.com/mail/u/0/#all/abc",
        modified_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        visibility_metadata={"labels": ["external-shared"]},
        target_scope=target_scope,
        target_plane=target_plane,  # type: ignore[arg-type]
        title="Re: deal flow",
        body="Following up on…",
        raw={"id": "msg-123", "subject": "Re: deal flow"},
    )


def test_write_envelope_round_trips(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path)
    item = _gmail_envelope()

    staged = writer.write_envelope(item)

    assert staged.item_id == "msg-123"
    assert staged.target_plane == "personal"
    assert staged.employee_id == "alice-northpoint"
    assert staged.raw_path.exists()
    assert staged.markdown_path.exists()


def test_write_envelope_persists_raw_payload_verbatim(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path)
    item = _gmail_envelope()

    staged = writer.write_envelope(item)

    raw_text = staged.raw_path.read_text(encoding="utf-8")
    assert '"subject": "Re: deal flow"' in raw_text
    assert '"id": "msg-123"' in raw_text


def test_write_envelope_writes_envelope_fields_into_frontmatter(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path)
    item = _gmail_envelope(target_scope="external-shared")

    staged = writer.write_envelope(item)

    md_text = staged.markdown_path.read_text(encoding="utf-8")
    fm_yaml = md_text.split("---", 2)[1]
    fm = yaml.safe_load(fm_yaml)

    assert fm["source_role"] == "email"
    assert fm["external_object_type"] == "message"
    assert fm["target_scope"] == "external-shared"
    assert fm["container_id"] == "thread-9"
    assert fm["url"] == "https://mail.google.com/mail/u/0/#all/abc"
    assert fm["modified_at"] == "2026-04-01T09:00:00+00:00"
    # Canonical fields the base writer always sets:
    assert fm["source"] == "gmail"
    assert fm["source_id"] == "msg-123"
    assert fm["target_plane"] == "personal"
    assert fm["employee_id"] == "alice-northpoint"


def test_write_envelope_renders_title_and_body_into_markdown(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path)
    item = _gmail_envelope()

    staged = writer.write_envelope(item)

    md = staged.markdown_path.read_text(encoding="utf-8")
    body = md.split("---", 2)[2].strip()
    assert body.startswith("# Re: deal flow")
    assert "Following up on…" in body


def test_write_envelope_rejects_plane_mismatch(tmp_path: Path) -> None:
    writer = _firm_writer(tmp_path, source="gmail")
    item = _gmail_envelope(target_plane="personal")  # writer is firm

    with pytest.raises(ValueError, match="target_plane"):
        writer.write_envelope(item)


def test_write_envelope_rejects_concrete_app_mismatch(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path, source="granola")
    item = _gmail_envelope()  # concrete_app=gmail, writer source=granola

    with pytest.raises(ValueError, match="concrete_app"):
        writer.write_envelope(item)


def test_write_envelope_omits_optional_fields_when_unset(tmp_path: Path) -> None:
    writer = _personal_writer(tmp_path)
    item = NormalizedSourceItem(
        source_role=ConnectorRole.EMAIL,
        concrete_app="gmail",
        external_object_type="message",
        external_id="msg-x",
        container_id=None,
        url=None,
        modified_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        visibility_metadata={},
        target_scope="employee-private",
        target_plane="personal",
        title="hi",
        body="x",
        raw={"id": "msg-x"},
    )
    staged = writer.write_envelope(item)
    fm_yaml = staged.markdown_path.read_text(encoding="utf-8").split("---", 2)[1]
    fm = yaml.safe_load(fm_yaml)
    assert "container_id" not in fm
    assert "url" not in fm
