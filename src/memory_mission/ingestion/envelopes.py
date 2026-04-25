"""Per-app raw payload → ``NormalizedSourceItem`` envelope helpers (P2).

Each connector returns its own raw shape (Gmail: subject + body + labels;
Granola: title + transcript + attendees; Drive: name + content +
permissions). Each helper here picks the right fields out of the raw
payload, asks the firm's ``SystemsManifest`` for ``target_plane`` and
``target_scope`` (via ``map_visibility``), and emits the single typed
envelope every downstream stage consumes.

Helpers are pure functions: they take a raw dict + manifest and return an
envelope. They do not call out to any service. Live HTTP / SDK work
stays in the connectors.

Visibility mapping is fail-closed: an envelope helper that cannot map a
raw payload's visibility surface to a firm scope raises
``VisibilityMappingError`` rather than silently picking a default.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from memory_mission.ingestion.roles import ConnectorRole, NormalizedSourceItem
from memory_mission.ingestion.systems_manifest import (
    RoleBinding,
    SystemsManifest,
    map_visibility,
)


def gmail_message_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Gmail message payload to a ``NormalizedSourceItem``.

    Visibility surface: ``labels`` (Gmail label ids / names),
    ``to`` / ``cc`` (recipient lists). Operator-defined visibility rules
    typically match on label.
    """
    binding = _binding_for(ConnectorRole.EMAIL, expected_app="gmail", manifest=manifest)
    visibility = {
        "labels": list(raw.get("labels", [])),
        "to": list(raw.get("to", [])),
        "cc": list(raw.get("cc", [])),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.EMAIL, manifest=manifest)
    return NormalizedSourceItem(
        source_role=ConnectorRole.EMAIL,
        concrete_app="gmail",
        external_object_type="message",
        external_id=_require_str(raw, ("id", "message_id"), source="gmail"),
        container_id=_optional_str(raw, ("thread_id",)),
        url=_optional_str(raw, ("permalink", "url")),
        modified_at=_require_datetime(raw, ("internal_date", "received_at"), source="gmail"),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("subject", "")),
        body=str(raw.get("body", raw.get("snippet", ""))),
        raw=dict(raw),
    )


def granola_transcript_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Granola transcript payload to a ``NormalizedSourceItem``.

    Visibility surface: ``attendees`` (list of email-shaped strings) and
    optional ``labels``. Granola bindings typically set
    ``default_visibility`` (e.g. ``partner-only``) since transcripts
    rarely carry rich visibility metadata.
    """
    binding = _binding_for(ConnectorRole.TRANSCRIPT, expected_app="granola", manifest=manifest)
    visibility = {
        "attendees": list(raw.get("attendees", [])),
        "labels": list(raw.get("labels", [])),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.TRANSCRIPT, manifest=manifest)
    return NormalizedSourceItem(
        source_role=ConnectorRole.TRANSCRIPT,
        concrete_app="granola",
        external_object_type="transcript",
        external_id=_require_str(raw, ("id", "transcript_id"), source="granola"),
        container_id=_optional_str(raw, ("meeting_id",)),
        url=_optional_str(raw, ("url",)),
        modified_at=_require_datetime(raw, ("created_at", "started_at"), source="granola"),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("title", "")),
        body=str(raw.get("transcript", raw.get("body", ""))),
        raw=dict(raw),
    )


def calendar_event_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Google Calendar event payload to a ``NormalizedSourceItem``.

    Visibility surface: ``gcal_visibility`` (Google Calendar's built-in
    field — ``default`` / ``public`` / ``private`` / ``confidential``)
    surfaced as a top-level metadata key so ``if_field`` rules can match
    it directly; plus ``attendees`` (list of email-shaped strings) and
    optional ``labels``. Calendar bindings typically combine a public/
    private rule with a sensible ``default_visibility`` for events that
    inherit the calendar's default visibility.
    """
    binding = _binding_for(ConnectorRole.CALENDAR, expected_app="gcal", manifest=manifest)
    attendees = _attendee_emails(raw.get("attendees"))
    visibility: dict[str, Any] = {
        "gcal_visibility": raw.get("visibility") or "default",
        "attendees": attendees,
        "labels": list(raw.get("labels", [])),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.CALENDAR, manifest=manifest)
    return NormalizedSourceItem(
        source_role=ConnectorRole.CALENDAR,
        concrete_app="gcal",
        external_object_type="event",
        external_id=_require_str(raw, ("id", "event_id"), source="gcal"),
        container_id=_optional_str(raw, ("calendar_id",)),
        url=_optional_str(raw, ("html_link", "htmlLink", "url")),
        modified_at=_require_datetime(raw, ("updated", "created"), source="gcal"),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("summary", "")),
        body=str(raw.get("description", "")),
        raw=dict(raw),
    )


def drive_file_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Drive file payload to a ``NormalizedSourceItem``.

    Visibility surface: ``permissions`` (list of permission grants),
    ``owners``, plus a synthesized ``drive_anyone`` boolean that is True
    when any permission grants ``anyone`` access. Operator visibility
    rules can match either on raw ``permissions`` entries (via
    ``if_field``) or on the synthesized ``drive_anyone`` flag.
    """
    binding = _binding_for(ConnectorRole.DOCUMENT, expected_app="drive", manifest=manifest)
    permissions = list(raw.get("permissions", []))
    visibility: dict[str, Any] = {
        "permissions": permissions,
        "owners": list(raw.get("owners", [])),
        "drive_anyone": _drive_grants_anyone(permissions),
        "labels": list(raw.get("labels", [])),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.DOCUMENT, manifest=manifest)
    return NormalizedSourceItem(
        source_role=ConnectorRole.DOCUMENT,
        concrete_app="drive",
        external_object_type=str(raw.get("mime_type", "file")),
        external_id=_require_str(raw, ("id", "file_id"), source="drive"),
        container_id=_optional_str(raw, ("folder_id", "parent_id")),
        url=_optional_str(raw, ("web_view_link", "url")),
        modified_at=_require_datetime(raw, ("modified_time", "modified_at"), source="drive"),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("name", "")),
        body=str(raw.get("content", raw.get("body", raw.get("snippet", "")))),
        raw=dict(raw),
    )


# ---------- helpers ----------


def _binding_for(
    role: ConnectorRole,
    *,
    expected_app: str,
    manifest: SystemsManifest,
) -> RoleBinding:
    binding = manifest.binding(role)
    if binding.app != expected_app:
        raise ValueError(
            f"role={role.value!r} is bound to app={binding.app!r} in firm "
            f"{manifest.firm_id!r}, but {expected_app}_*_to_envelope helper "
            f"was invoked. Caller is using the wrong helper for this firm's binding."
        )
    return binding


def _require_str(raw: dict[str, Any], keys: tuple[str, ...], *, source: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"{source} payload missing required string field; tried keys={list(keys)}")


def _optional_str(raw: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _require_datetime(raw: dict[str, Any], keys: tuple[str, ...], *, source: str) -> datetime:
    for key in keys:
        value = raw.get(key)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    raise ValueError(f"{source} payload missing required datetime field; tried keys={list(keys)}")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        # Treat large ints as epoch milliseconds (Gmail internal_date convention),
        # smaller ints as epoch seconds.
        seconds = float(value) / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str) and value:
        # ``datetime.fromisoformat`` accepts ``Z`` suffix on Python 3.11+.
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _attendee_emails(attendees: Any) -> list[str]:
    """Extract email-shaped strings from Google Calendar attendees list."""
    if not isinstance(attendees, list):
        return []
    out: list[str] = []
    for entry in attendees:
        if isinstance(entry, dict):
            email = entry.get("email")
            if isinstance(email, str) and email:
                out.append(email)
        elif isinstance(entry, str) and entry:
            out.append(entry)
    return out


def _drive_grants_anyone(permissions: list[Any]) -> bool:
    for perm in permissions:
        if isinstance(perm, dict) and perm.get("type") == "anyone":
            return True
    return False


__all__ = [
    "calendar_event_to_envelope",
    "drive_file_to_envelope",
    "gmail_message_to_envelope",
    "granola_transcript_to_envelope",
]
