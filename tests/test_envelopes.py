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
    affinity_record_to_envelope,
    calendar_event_to_envelope,
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
            ConnectorRole.CALENDAR: RoleBinding(
                app="gcal",
                target_plane="personal",
                visibility_rules=(
                    VisibilityRule(if_field={"gcal_visibility": "public"}, scope="external-shared"),
                    VisibilityRule(
                        if_field={"gcal_visibility": "private"}, scope="employee-private"
                    ),
                ),
                default_visibility="employee-private",
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


# ---------- Calendar ----------


def test_calendar_envelope_round_trip_public_event() -> None:
    raw = {
        "id": "ev-1",
        "calendar_id": "primary",
        "summary": "Q3 partner sync",
        "description": "Quarterly review with partners",
        "start": {"dateTime": "2026-09-01T15:00:00Z"},
        "end": {"dateTime": "2026-09-01T16:00:00Z"},
        "attendees": [
            {"email": "alice@northpoint.fund"},
            {"email": "sarah@example.com", "responseStatus": "accepted"},
        ],
        "visibility": "public",
        "updated": "2026-08-25T09:00:00Z",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())

    assert item.source_role == ConnectorRole.CALENDAR
    assert item.concrete_app == "gcal"
    assert item.external_object_type == "event"
    assert item.external_id == "ev-1"
    assert item.container_id == "primary"
    assert item.url == "https://calendar.google.com/event?eid=abc"
    assert item.title == "Q3 partner sync"
    assert item.body == "Quarterly review with partners"
    assert item.target_scope == "external-shared"
    assert item.target_plane == "personal"
    assert item.modified_at == datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    assert item.visibility_metadata["gcal_visibility"] == "public"
    assert item.visibility_metadata["attendees"] == [
        "alice@northpoint.fund",
        "sarah@example.com",
    ]


def test_calendar_envelope_private_event_maps_to_employee_private() -> None:
    raw = {
        "id": "ev-2",
        "calendar_id": "primary",
        "summary": "1:1 with Bob",
        "visibility": "private",
        "updated": "2026-08-25T09:00:00Z",
        "attendees": [{"email": "alice@northpoint.fund"}, {"email": "bob@northpoint.fund"}],
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.target_scope == "employee-private"


def test_calendar_envelope_default_visibility_uses_manifest_fallback() -> None:
    raw = {
        "id": "ev-3",
        "calendar_id": "primary",
        "summary": "Default-vis event",
        "visibility": "default",  # neither public nor private rule matches
        "updated": "2026-08-25T09:00:00Z",
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.target_scope == "employee-private"


def test_calendar_envelope_missing_visibility_treated_as_default() -> None:
    raw = {
        "id": "ev-4",
        "summary": "x",
        "updated": "2026-08-25T09:00:00Z",
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())
    # Missing visibility => "default" => no rule matches => manifest default
    assert item.visibility_metadata["gcal_visibility"] == "default"
    assert item.target_scope == "employee-private"


def test_calendar_envelope_extracts_attendee_emails_from_dicts() -> None:
    raw = {
        "id": "ev-5",
        "summary": "x",
        "visibility": "private",
        "updated": "2026-08-25T09:00:00Z",
        "attendees": [
            {"email": "alice@northpoint.fund", "responseStatus": "accepted"},
            {"email": "", "responseStatus": "needsAction"},  # filtered out
            {"displayName": "Bob"},  # no email — filtered out
            "raw@example.com",  # raw string also accepted
        ],
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.visibility_metadata["attendees"] == ["alice@northpoint.fund", "raw@example.com"]


def test_calendar_envelope_missing_id_raises() -> None:
    raw = {"summary": "x", "updated": "2026-08-25T09:00:00Z", "visibility": "private"}
    with pytest.raises(ValueError, match="missing required string field"):
        calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())


def test_calendar_envelope_missing_updated_raises() -> None:
    raw = {"id": "ev-x", "summary": "x", "visibility": "private"}
    with pytest.raises(ValueError, match="missing required datetime field"):
        calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())


def test_calendar_envelope_falls_back_to_created_when_updated_missing() -> None:
    raw = {
        "id": "ev-6",
        "summary": "x",
        "visibility": "private",
        "created": "2026-08-25T09:00:00Z",
    }
    item = calendar_event_to_envelope(raw, manifest=_manifest_with_email_default())
    assert item.modified_at == datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def test_calendar_envelope_rejects_wrong_app_binding() -> None:
    manifest = SystemsManifest(
        firm_id="x",
        bindings={
            ConnectorRole.CALENDAR: RoleBinding(
                app="outlook_calendar",
                target_plane="personal",
                default_visibility="employee-private",
            ),
        },
    )
    raw = {"id": "ev-x", "summary": "x", "updated": "2026-08-25T09:00:00Z", "visibility": "private"}
    with pytest.raises(ValueError, match="bound to app='outlook_calendar'"):
        calendar_event_to_envelope(raw, manifest=manifest)


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


# ---------- Affinity ----------


def _affinity_manifest() -> SystemsManifest:
    return SystemsManifest(
        firm_id="northpoint",
        bindings={
            ConnectorRole.WORKSPACE: RoleBinding(
                app="affinity",
                target_plane="firm",
                visibility_rules=(
                    VisibilityRule(if_label="list:42", scope="partner-only"),
                    VisibilityRule(if_label="list:91", scope="firm-internal"),
                    VisibilityRule(if_label="global", scope="external-shared"),
                ),
                default_visibility="firm-internal",
            ),
        },
    )


def test_affinity_organization_envelope_round_trip() -> None:
    raw = {
        "id": 12345,
        "name": "Northpoint Capital",
        "domain": "northpoint.fund",
        "domains": ["northpoint.fund", "northpoint.vc"],
        "global": False,
        "list_entries": [
            {"id": 1, "list_id": 42, "entity_id": 12345, "entity_type": "organization"},
            {"id": 2, "list_id": 91, "entity_id": 12345, "entity_type": "organization"},
        ],
        "interaction_dates_last_interaction_date": "2026-04-01T09:00:00Z",
        "creator_id": 7,
    }
    item = affinity_record_to_envelope(
        raw, object_type="organization", manifest=_affinity_manifest()
    )

    assert item.source_role == ConnectorRole.WORKSPACE
    assert item.concrete_app == "affinity"
    assert item.external_object_type == "organization"
    assert item.external_id == "org_12345"
    assert item.container_id == "list_42"  # first list_id
    assert item.target_plane == "firm"
    assert item.target_scope == "partner-only"  # first matching rule (list:42)
    assert item.title == "Northpoint Capital"
    assert "Domain: northpoint.fund" in item.body
    assert "Other domains: northpoint.vc" in item.body
    assert "Last interaction: 2026-04-01T09:00:00Z" in item.body
    assert item.modified_at == datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
    assert item.visibility_metadata["labels"] == ["list:42", "list:91"]
    assert item.visibility_metadata["affinity_object_type"] == "organization"
    assert item.visibility_metadata["affinity_owner_id"] == 7


def test_affinity_global_organization_maps_to_external_shared() -> None:
    raw = {
        "id": 99,
        "name": "Apple",
        "domain": "apple.com",
        "global": True,
        "list_entries": [],
        "dates_modified_date": "2026-03-15T10:00:00Z",
    }
    item = affinity_record_to_envelope(
        raw, object_type="organization", manifest=_affinity_manifest()
    )
    assert item.target_scope == "external-shared"
    assert "global" in item.visibility_metadata["labels"]


def test_affinity_record_with_no_lists_uses_default_visibility() -> None:
    raw = {
        "id": 1,
        "name": "Stealth Co",
        "global": False,
        "list_entries": [],
        "dates_created_date": "2026-04-01T09:00:00Z",
    }
    item = affinity_record_to_envelope(
        raw, object_type="organization", manifest=_affinity_manifest()
    )
    assert item.target_scope == "firm-internal"  # default_visibility
    assert item.container_id is None


def test_affinity_person_envelope_uses_first_last_email() -> None:
    raw = {
        "id": 7,
        "first_name": "Sarah",
        "last_name": "Chen",
        "primary_email": "sarah@example.com",
        "emails": ["sarah@example.com", "sarah.chen@northpoint.fund"],
        "organization_ids": [12345],
        "global": False,
        "list_entries": [{"list_id": 91, "entity_id": 7, "entity_type": "person"}],
        "dates_modified_date": "2026-04-01T09:00:00Z",
    }
    item = affinity_record_to_envelope(raw, object_type="person", manifest=_affinity_manifest())
    assert item.external_object_type == "person"
    assert item.external_id == "person_7"
    assert item.title == "Sarah Chen"
    assert "Email: sarah@example.com" in item.body
    assert "Other emails: sarah.chen@northpoint.fund" in item.body
    assert "Organizations: 12345" in item.body
    assert item.target_scope == "firm-internal"  # list:91


def test_affinity_opportunity_envelope_uses_name_and_list() -> None:
    raw = {
        "id": 555,
        "name": "Acme Corp Series A",
        "list_id": 42,
        "list_entries": [{"list_id": 42, "entity_id": 555, "entity_type": "opportunity"}],
        "global": False,
        "dates_created_date": "2026-04-01T09:00:00Z",
    }
    item = affinity_record_to_envelope(
        raw, object_type="opportunity", manifest=_affinity_manifest()
    )
    assert item.external_object_type == "opportunity"
    assert item.external_id == "opp_555"
    assert item.title == "Acme Corp Series A"
    assert "List: 42" in item.body
    assert item.target_scope == "partner-only"  # list:42


def test_affinity_envelope_rejects_unknown_object_type() -> None:
    raw = {"id": 1, "dates_created_date": "2026-04-01T09:00:00Z"}
    with pytest.raises(ValueError, match="object_type must be one of"):
        affinity_record_to_envelope(raw, object_type="bogus", manifest=_affinity_manifest())


def test_affinity_envelope_rejects_missing_id() -> None:
    raw = {"name": "x", "dates_created_date": "2026-04-01T09:00:00Z", "list_entries": []}
    with pytest.raises(ValueError, match="missing required integer id"):
        affinity_record_to_envelope(raw, object_type="organization", manifest=_affinity_manifest())


def test_affinity_envelope_rejects_wrong_app_binding() -> None:
    manifest = SystemsManifest(
        firm_id="x",
        bindings={
            ConnectorRole.WORKSPACE: RoleBinding(
                app="attio",
                target_plane="firm",
                default_visibility="firm-internal",
            ),
        },
    )
    raw = {"id": 1, "name": "x", "dates_created_date": "2026-04-01T09:00:00Z", "list_entries": []}
    with pytest.raises(ValueError, match="bound to app='attio'"):
        affinity_record_to_envelope(raw, object_type="organization", manifest=manifest)
