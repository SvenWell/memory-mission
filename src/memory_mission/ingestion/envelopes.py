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


def outlook_message_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Outlook (Microsoft 365) message payload to a ``NormalizedSourceItem``.

    Visibility surface:

    - ``outlook_sensitivity`` — Outlook's built-in field
      (``normal`` / ``personal`` / ``private`` / ``confidential``)
      surfaced as a top-level metadata key for direct ``if_field``
      matching. Mirrors the ``gcal_visibility`` shape.
    - ``categories`` — Outlook's user-assigned category strings
      (Outlook's equivalent of Gmail labels), surfaced as ``labels``
      so ``if_label`` rules match like Gmail.
    - ``to`` / ``cc`` — recipient lists.
    """
    binding = _binding_for(ConnectorRole.EMAIL, expected_app="outlook", manifest=manifest)
    categories = list(raw.get("categories", []))
    visibility: dict[str, Any] = {
        "labels": categories,
        "outlook_sensitivity": str(raw.get("sensitivity", "normal")),
        "to": _outlook_recipients(raw.get("to_recipients") or raw.get("to")),
        "cc": _outlook_recipients(raw.get("cc_recipients") or raw.get("cc")),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.EMAIL, manifest=manifest)
    return NormalizedSourceItem(
        source_role=ConnectorRole.EMAIL,
        concrete_app="outlook",
        external_object_type="message",
        external_id=_require_str(raw, ("id", "message_id"), source="outlook"),
        container_id=_optional_str(raw, ("conversation_id", "parent_folder_id")),
        url=_optional_str(raw, ("web_link", "webLink")),
        modified_at=_require_datetime(
            raw,
            ("received_date_time", "receivedDateTime", "sent_date_time", "last_modified_date_time"),
            source="outlook",
        ),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("subject", "")),
        body=str(raw.get("body", raw.get("body_preview", ""))),
        raw=dict(raw),
    )


def onedrive_item_to_envelope(
    raw: dict[str, Any],
    *,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio OneDrive / SharePoint drive item to a ``NormalizedSourceItem``.

    Single helper handles personal OneDrive AND SharePoint document
    libraries — Microsoft Graph treats both as drives. SharePoint
    pages and list items have different shapes (separate helpers when
    they're needed).

    Visibility surface:

    - ``drive_anyone`` — synthesized: True iff any permission grants
      an anonymous link. Mirrors the Drive helper's shape.
    - ``drive_organization_link`` — synthesized: True iff any
      permission grants an organization-scoped link.
    - ``is_sharepoint`` — True when ``parentReference.siteId`` is set
      (item lives in a SharePoint document library, not personal
      OneDrive).
    - ``sharepoint_site_id`` — site id when ``is_sharepoint``.
    - ``permissions`` — raw Microsoft Graph permission grants.
    - ``owners`` — owner display names.
    - ``labels`` — Outlook-style categories if present.
    """
    binding = _binding_for(ConnectorRole.DOCUMENT, expected_app="one_drive", manifest=manifest)
    permissions = list(raw.get("permissions", []))
    parent_ref = raw.get("parent_reference") or raw.get("parentReference") or {}
    if not isinstance(parent_ref, dict):
        parent_ref = {}
    site_id = parent_ref.get("site_id") or parent_ref.get("siteId")
    visibility: dict[str, Any] = {
        "permissions": permissions,
        "owners": _onedrive_owners(raw),
        "drive_anyone": _onedrive_link_scope_present(permissions, "anonymous"),
        "drive_organization_link": _onedrive_link_scope_present(permissions, "organization"),
        "is_sharepoint": bool(site_id),
        "sharepoint_site_id": site_id if isinstance(site_id, str) else None,
        "labels": list(raw.get("categories", [])),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.DOCUMENT, manifest=manifest)
    file_block = raw.get("file") or {}
    mime = ""
    if isinstance(file_block, dict):
        mime = str(file_block.get("mime_type") or file_block.get("mimeType", ""))
    object_type = mime or ("folder" if raw.get("folder") else "drive_item")
    return NormalizedSourceItem(
        source_role=ConnectorRole.DOCUMENT,
        concrete_app="one_drive",
        external_object_type=object_type,
        external_id=_require_str(raw, ("id", "item_id"), source="one_drive"),
        container_id=_optional_str_from_dict(parent_ref, ("id", "drive_id", "driveId")),
        url=_optional_str(raw, ("web_url", "webUrl", "url")),
        modified_at=_require_datetime(
            raw,
            (
                "last_modified_date_time",
                "lastModifiedDateTime",
                "modified_at",
                "created_date_time",
                "createdDateTime",
            ),
            source="one_drive",
        ),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=str(raw.get("name", "")),
        body=str(raw.get("content", raw.get("body", ""))),
        raw=dict(raw),
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


_AFFINITY_OBJECT_TYPES: tuple[str, ...] = ("organization", "person", "opportunity")
_AFFINITY_ID_PREFIX: dict[str, str] = {
    "organization": "org",
    "person": "person",
    "opportunity": "opp",
}


def affinity_record_to_envelope(
    raw: dict[str, Any],
    *,
    object_type: str,
    manifest: SystemsManifest,
) -> NormalizedSourceItem:
    """Map a Composio Affinity record (org / person / opportunity) to an envelope.

    ``object_type`` is set by the caller (the skill knows which connector
    action it called). It must be one of ``organization`` / ``person`` /
    ``opportunity``.

    Visibility surface:

    - ``labels`` — one entry per Affinity list the record is in
      (``list:<list_id>``) plus ``"global"`` when Affinity flags the
      record as a globally-known entity. Operator visibility rules
      typically match on ``if_label: list:<id>``.
    - ``affinity_object_type`` — for ``if_field`` rules that want to
      treat orgs / persons / opps differently.
    - ``affinity_owner_id`` — Affinity creator or owner id when present.

    Affinity records are not documents; the envelope ``body`` is a
    structured summary (key fields rendered as text) so downstream
    extraction has something to read without parsing the raw payload.
    """
    if object_type not in _AFFINITY_OBJECT_TYPES:
        raise ValueError(
            f"object_type must be one of {list(_AFFINITY_OBJECT_TYPES)}; got {object_type!r}"
        )
    binding = _binding_for(ConnectorRole.WORKSPACE, expected_app="affinity", manifest=manifest)
    list_ids = _affinity_list_ids(raw)
    labels: list[str] = [f"list:{lid}" for lid in list_ids]
    if raw.get("global") is True:
        labels.append("global")
    visibility: dict[str, Any] = {
        "labels": labels,
        "affinity_object_type": object_type,
        "affinity_owner_id": _affinity_owner_id(raw),
    }
    target_scope = map_visibility(visibility, role=ConnectorRole.WORKSPACE, manifest=manifest)
    record_id = _require_int_id(raw, ("id",), source="affinity")
    external_id = f"{_AFFINITY_ID_PREFIX[object_type]}_{record_id}"
    title = _affinity_title(raw, object_type=object_type, fallback=external_id)
    body = _affinity_body(raw, object_type=object_type)
    return NormalizedSourceItem(
        source_role=ConnectorRole.WORKSPACE,
        concrete_app="affinity",
        external_object_type=object_type,
        external_id=external_id,
        container_id=_affinity_primary_list(list_ids),
        url=_optional_str(raw, ("url",)),
        modified_at=_require_datetime(
            raw,
            (
                "interaction_dates_last_interaction_date",
                "dates_modified_date",
                "dates_created_date",
                "modified_at",
                "created_at",
            ),
            source="affinity",
        ),
        visibility_metadata=visibility,
        target_scope=target_scope,
        target_plane=binding.target_plane,
        title=title,
        body=body,
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


def _require_int_id(raw: dict[str, Any], keys: tuple[str, ...], *, source: str) -> int:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    raise ValueError(f"{source} payload missing required integer id; tried keys={list(keys)}")


def _affinity_list_ids(raw: dict[str, Any]) -> list[int]:
    """Extract list ids from an Affinity record's list_entries."""
    entries = raw.get("list_entries")
    if not isinstance(entries, list):
        return []
    out: list[int] = []
    for entry in entries:
        if isinstance(entry, dict):
            lid = entry.get("list_id")
            if isinstance(lid, int) and not isinstance(lid, bool):
                out.append(lid)
            elif isinstance(lid, str) and lid.isdigit():
                out.append(int(lid))
    return out


def _affinity_primary_list(list_ids: list[int]) -> str | None:
    """Use the first list id as the envelope container_id (ordering = Affinity's order)."""
    if not list_ids:
        return None
    return f"list_{list_ids[0]}"


def _affinity_owner_id(raw: dict[str, Any]) -> int | None:
    for key in ("creator_id", "owner_id", "current_user_id"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _affinity_title(raw: dict[str, Any], *, object_type: str, fallback: str) -> str:
    if object_type == "organization":
        return str(raw.get("name", "")) or fallback
    if object_type == "person":
        first = str(raw.get("first_name", "")).strip()
        last = str(raw.get("last_name", "")).strip()
        full = f"{first} {last}".strip()
        if full:
            return full
        return str(raw.get("primary_email", "")) or fallback
    # opportunity
    return str(raw.get("name", raw.get("title", ""))) or fallback


def _affinity_body(raw: dict[str, Any], *, object_type: str) -> str:
    """Render a structured summary as text — Affinity records aren't documents."""
    parts: list[str] = []
    if object_type == "organization":
        domain = str(raw.get("domain", "")).strip()
        if domain:
            parts.append(f"Domain: {domain}")
        domains = raw.get("domains") or []
        if isinstance(domains, list) and len(domains) > 1:
            parts.append("Other domains: " + ", ".join(str(d) for d in domains[1:]))
    elif object_type == "person":
        email = str(raw.get("primary_email", "")).strip()
        if email:
            parts.append(f"Email: {email}")
        emails = raw.get("emails") or []
        if isinstance(emails, list) and len(emails) > 1:
            parts.append("Other emails: " + ", ".join(str(e) for e in emails[1:]))
        org_ids = raw.get("organization_ids") or []
        if isinstance(org_ids, list) and org_ids:
            parts.append(f"Organizations: {', '.join(str(o) for o in org_ids)}")
    elif object_type == "opportunity":
        list_id = raw.get("list_id")
        if list_id is not None:
            parts.append(f"List: {list_id}")
    last_interaction = raw.get("interaction_dates_last_interaction_date") or raw.get(
        "dates_last_interaction_date"
    )
    if last_interaction:
        parts.append(f"Last interaction: {last_interaction}")
    return "\n".join(parts)


def _outlook_recipients(field: Any) -> list[str]:
    """Extract email-shaped strings from Outlook's nested recipient lists."""
    if not isinstance(field, list):
        return []
    out: list[str] = []
    for entry in field:
        if isinstance(entry, dict):
            ea = entry.get("email_address") or entry.get("emailAddress")
            if isinstance(ea, dict):
                addr = ea.get("address")
                if isinstance(addr, str) and addr:
                    out.append(addr)
                    continue
            addr = entry.get("address")
            if isinstance(addr, str) and addr:
                out.append(addr)
        elif isinstance(entry, str) and entry:
            out.append(entry)
    return out


def _onedrive_link_scope_present(permissions: list[Any], scope: str) -> bool:
    """True if any permission grants a sharing link with the given scope."""
    for perm in permissions:
        if not isinstance(perm, dict):
            continue
        link = perm.get("link") or {}
        if isinstance(link, dict) and link.get("scope") == scope:
            return True
    return False


def _onedrive_owners(raw: dict[str, Any]) -> list[str]:
    """Extract owner display names from a OneDrive item's createdBy / owner fields."""
    out: list[str] = []
    for key in ("created_by", "createdBy", "owner"):
        block = raw.get(key)
        if isinstance(block, dict):
            user = block.get("user") or block
            if isinstance(user, dict):
                name = user.get("display_name") or user.get("displayName") or user.get("email")
                if isinstance(name, str) and name:
                    out.append(name)
    return out


def _optional_str_from_dict(d: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value:
            return value
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
    "affinity_record_to_envelope",
    "calendar_event_to_envelope",
    "drive_file_to_envelope",
    "gmail_message_to_envelope",
    "granola_transcript_to_envelope",
    "onedrive_item_to_envelope",
    "outlook_message_to_envelope",
]
