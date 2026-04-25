"""Microsoft Outlook backfill connector.

Pulls historical email through Composio. Same harness shape as Gmail:
every invocation flows through ``invoke()`` so observability +
PII-scrub + durability wrap each call.

V1 read action surface:

- ``list_messages`` — enumerate messages with optional folder/query filters
- ``get_message`` — fetch one message by id (full body + recipients +
  categories + sensitivity)
- ``list_mail_folders`` — top-level folder enumeration (inbox / sent /
  custom)
- ``search_messages`` — server-side search (sender / subject / attachment)
- ``get_mail_delta`` — incremental change feed for resume / sync

Auth: OAuth2 via Composio (Microsoft 365 enterprise SSO is handled at
the Composio layer; the firm provisions per-firm OAuth config in
Composio's dashboard).

Live Composio calls are stubbed until credentials are wired (see
``composio.py``).
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

OUTLOOK_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_messages",
        description=(
            "Enumerate Outlook messages, optionally filtered by folder, "
            "query, or modified-since timestamp."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "query": {"type": "string"},
                "modified_since": {"type": "string", "format": "date-time"},
                "page_token": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_message",
        description=(
            "Fetch a single Outlook message by id, including body, recipients, "
            "categories, and sensitivity."
        ),
        input_schema={
            "type": "object",
            "required": ["message_id"],
            "properties": {"message_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="list_mail_folders",
        description="Enumerate the user's top-level mail folders (Inbox / Sent / custom).",
        input_schema={"type": "object"},
    ),
    ConnectorAction(
        name="search_messages",
        description=(
            "Server-side search over Outlook messages by sender, subject, "
            "body content, or attachment name."
        ),
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_mail_delta",
        description=(
            "Incremental delta feed for Outlook messages since a delta token. "
            "Use for resume + ongoing sync."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "delta_token": {"type": "string"},
            },
        },
    ),
)


def make_outlook_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for an Outlook connector. Pass a client to go live."""
    return ComposioConnector(
        name="outlook",
        actions=OUTLOOK_ACTIONS,
        client=client,
        preview_fn=_outlook_preview,
    )


def _outlook_preview(raw: dict[str, Any]) -> str:
    """Sender | subject — body snippet. Harness further redacts + truncates."""
    subject = str(raw.get("subject", "")).strip()
    sender = _extract_email_address(raw.get("from") or raw.get("sender"))
    body = str(raw.get("body", raw.get("body_preview", raw.get("snippet", "")))).strip()
    header = " | ".join(x for x in (sender, subject) if x)
    prefix = f"{header} — " if header else ""
    return (prefix + body)[:500]


def _extract_email_address(field: Any) -> str:
    """Extract email-shaped string from Outlook's nested address fields."""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        # Microsoft Graph: {"emailAddress": {"address": "...", "name": "..."}}
        ea = field.get("emailAddress")
        if isinstance(ea, dict):
            return str(ea.get("address", ""))
        return str(field.get("address", ""))
    return ""
