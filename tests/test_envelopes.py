"""Per-app envelope helper tests (P2).

Each helper takes a fake raw payload shaped like a Composio response,
plus a SystemsManifest, and must produce a ``NormalizedSourceItem``
that:

- carries the right ``source_role`` + ``concrete_app``
- copies the canonical id, title, body, modified_at out of the raw
- threads ``visibility_metadata`` through ``map_visibility`` so the
  ``target_scope`` reflects firm config (NOT the helper's choice)
- gets ``target_plane`` from the manifest binding
- preserves the raw payload verbatim under ``raw``
- raises ``VisibilityMappingError`` when no rule matches and no default
- raises ``ValueError`` when invoked against a manifest binding that
  names a different concrete app
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memory_mission.ingestion.envelopes import (
    drive_file_to_envelope,
    gmail_message_to_envelope,
    granola_transcript_to_envelope,
)
from memory_mission.ingestion.roles import ConnectorRole
from memory_mission.ingestion.systems_manifest import (
    RoleBinding,
    SystemsManifest,
    VisibilityMappingError,
    VisibilityRule,
)


def _manifest_with_email_default() -> SystemsManifest:
    return SystemsManifest(
        firm_id="northpoint",
        bindings={
            ConnectorRole.EMAIL: RoleBinding(
                app="gmail",
                target_plane="personal",
                visibility_rules=(
                    VisibilityRule(if_label="external-shared", scope="external-shared"),
                ),
                default_visibility="employee-private",
            ),
            ConnectorRole.TRANSCRIPT: RoleBinding(
                app="granola",
                target_plane="personal",
                default_visibility="partner-only",
            ),
            ConnectorRole.DOCUMENT: RoleBinding(
                app="drive",
                target_plane="firm",
                visibility_rules=(VisibilityRule(if_field={"drive_anyone": True}, scope="public"),),
                default_visibility="client-confidential",
            ),
        },
    )


def _manifest_email_fail_closed() -> SystemsManifest:
    return SystemsManifest(
        firm_id="northpoint",
        bindings={
            ConnectorRole.EMAIL: RoleBinding(
                app="gmail",
                target_plane="personal",
                visibility_rules=(
                    VisibilityRule(if_label="external-shared", scope="external-shared"),
                ),
                default_visibility=None,
            ),
        },
    )


# ---------- Gmail ----------


def test_gmail_envelope_round_trip_with_label_match() -> None:
    raw = {
        "id": "msg-123",
        "thread_id": "thread-9",
        "subject": "Re: deal flow",
        "body": "Following up on…",
        "snippet": "Following up…",
        "labels": ["external-shared", "important"],
        "to": ["alice@northpoint.fund"],
        "cc": [],
        "internal_date": "2026-04-01T09:00:00Z",
        "permalink": "https://mail.google.com/mail/u/0/#all/abc",
    }

    item = gmail_message_to_envelope(raw, manifest=_manifest_with_email_default())

    assert item.source_role == ConnectorRole.EMAIL
    assert item.concrete_app == "gmail"
    assert item.external_object_type == "message"
    assert item.external_id == "msg-123"
    assert item.container_id == "thread-9"
    assert item.url == "https://mail.google.com/mail/u/0/#all/abc"
    assert item.target_scope == "external-shared"
    assert item.target_plane == "personal"
    assert item.title == "Re: deal flow"
    assert item.body == "Following up on…"
    assert item.modified_at == datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
    assert item.visibility_metadata["labels"] == ["external-shared", "important"]
    assert item.raw == raw  # exact preservation


def test_gmail_envelope_uses_manifest_default_when_no_label_matches() -> None:
    raw = {
        "id": "msg-2",
        "thread_id": None,
        "subject": "Internal note",
        "body": "Internal-only…",
        "labels": ["important"],
        "internal_date": "2026-04-01T09:00:00Z",
    }
    item = gmail_message_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.target_scope == "employee-private"


def test_gmail_envelope_fail_closed_when_no_default() -> None:
    raw = {
        "id": "msg-3",
        "subject": "Sensitive",
        "body": "secret",
        "labels": ["random"],
        "internal_date": "2026-04-01T09:00:00Z",
    }
    with pytest.raises(VisibilityMappingError):
        gmail_message_to_envelope(raw, manifest=_manifest_email_fail_closed())


def test_gmail_envelope_handles_epoch_ms_internal_date() -> None:
    raw = {
        "id": "msg-4",
        "subject": "x",
        "body": "x",
        "labels": ["external-shared"],
        "internal_date": 1_711_972_800_000,  # 2024-04-01 12:00 UTC, epoch ms
    }
    item = gmail_message_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.modified_at == datetime(2024, 4, 1, 12, 0, tzinfo=UTC)


def test_gmail_envelope_missing_id_raises() -> None:
    raw = {"subject": "x", "internal_date": "2026-04-01T09:00:00Z", "labels": ["external-shared"]}
    with pytest.raises(ValueError, match="missing required string field"):
        gmail_message_to_envelope(raw, manifest=_manifest_with_email_default())


def test_gmail_envelope_missing_internal_date_raises() -> None:
    raw = {"id": "x", "subject": "x", "labels": ["external-shared"]}
    with pytest.raises(ValueError, match="missing required datetime field"):
        gmail_message_to_envelope(raw, manifest=_manifest_with_email_default())


def test_gmail_envelope_rejects_wrong_app_binding() -> None:
    manifest = SystemsManifest(
        firm_id="x",
        bindings={
            ConnectorRole.EMAIL: RoleBinding(
                app="outlook",
                target_plane="personal",
                default_visibility="partner-only",
            ),
        },
    )
    raw = {"id": "x", "subject": "x", "internal_date": "2026-04-01T09:00:00Z"}
    with pytest.raises(ValueError, match="bound to app='outlook'"):
        gmail_message_to_envelope(raw, manifest=manifest)


# ---------- Granola ----------


def test_granola_envelope_round_trip() -> None:
    raw = {
        "id": "tr-1",
        "meeting_id": "mtg-1",
        "title": "Sarah / Northpoint sync",
        "transcript": "Sarah said…",
        "attendees": ["alice@northpoint.fund", "sarah@example.com"],
        "created_at": "2026-04-02T14:00:00Z",
        "url": "https://granola.app/t/tr-1",
    }
    item = granola_transcript_to_envelope(raw, manifest=_manifest_with_email_default())

    assert item.source_role == ConnectorRole.TRANSCRIPT
    assert item.concrete_app == "granola"
    assert item.external_object_type == "transcript"
    assert item.external_id == "tr-1"
    assert item.container_id == "mtg-1"
    assert item.url == "https://granola.app/t/tr-1"
    assert item.title == "Sarah / Northpoint sync"
    assert item.body == "Sarah said…"
    assert item.target_scope == "partner-only"
    assert item.target_plane == "personal"
    assert item.visibility_metadata["attendees"] == [
        "alice@northpoint.fund",
        "sarah@example.com",
    ]
    assert item.raw == raw


def test_granola_envelope_missing_id_raises() -> None:
    raw = {"title": "x", "created_at": "2026-04-02T14:00:00Z"}
    with pytest.raises(ValueError, match="missing required string field"):
        granola_transcript_to_envelope(raw, manifest=_manifest_with_email_default())


# ---------- Drive ----------


def test_drive_envelope_round_trip_anyone_grants_public() -> None:
    raw = {
        "id": "file-1",
        "name": "Q4 LP Update.md",
        "mime_type": "text/markdown",
        "content": "# Q4…",
        "permissions": [{"type": "anyone", "role": "reader"}],
        "owners": ["alice@northpoint.fund"],
        "modified_time": "2026-03-31T18:00:00Z",
        "web_view_link": "https://drive.google.com/file/d/file-1/view",
        "folder_id": "fold-9",
    }
    item = drive_file_to_envelope(raw, manifest=_manifest_with_email_default())

    assert item.source_role == ConnectorRole.DOCUMENT
    assert item.concrete_app == "drive"
    assert item.external_object_type == "text/markdown"
    assert item.external_id == "file-1"
    assert item.container_id == "fold-9"
    assert item.url == "https://drive.google.com/file/d/file-1/view"
    assert item.target_scope == "public"
    assert item.target_plane == "firm"
    assert item.title == "Q4 LP Update.md"
    assert item.body == "# Q4…"
    assert item.visibility_metadata["drive_anyone"] is True


def test_drive_envelope_falls_back_to_manifest_default_for_internal_files() -> None:
    raw = {
        "id": "file-2",
        "name": "Internal memo",
        "mime_type": "text/plain",
        "content": "internal",
        "permissions": [{"type": "user", "role": "reader", "email": "x@y.z"}],
        "modified_time": "2026-03-31T18:00:00Z",
    }
    item = drive_file_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.target_scope == "client-confidential"
    assert item.visibility_metadata["drive_anyone"] is False


def test_drive_envelope_rejects_wrong_app_binding() -> None:
    manifest = SystemsManifest(
        firm_id="x",
        bindings={
            ConnectorRole.DOCUMENT: RoleBinding(
                app="sharepoint",
                target_plane="firm",
                default_visibility="public",
            ),
        },
    )
    raw = {"id": "x", "name": "x", "modified_time": "2026-03-31T18:00:00Z", "permissions": []}
    with pytest.raises(ValueError, match="bound to app='sharepoint'"):
        drive_file_to_envelope(raw, manifest=manifest)
