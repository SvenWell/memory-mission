"""Slack backfill connector — message-stream firm-comms substrate.

Slack is the universal team-comms substrate at venture firms (and
most other 5-50-person firms). Composio exposes 106 tools backed by
Slack's Web API; this connector wraps the read-side subset every
backfill loop needs.

V1 read action surface:

- ``list_channels``  — conversations.list (public + private + DMs + MPDMs)
- ``get_channel``    — conversations.info for a single channel
- ``list_messages``  — conversations.history for a channel
- ``get_replies``    — conversations.replies for a thread
- ``search_messages``— search.messages with date + author filters
- ``list_users``     — users.list
- ``get_user``       — users.info for a single user

Auth: OAuth2 / Bearer token via Composio. Token requires the
appropriate scopes for each action category (channels:history,
groups:history, im:history, mpim:history for messages; users:read
for users; etc.).

Composio also exposes write-side actions (chat.postMessage,
reactions.add, channels.archive, etc.) — those route through P5
sync-back, not this backfill connector.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

SLACK_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_channels",
        description=(
            "Enumerate channels the bot has access to: public, private, "
            "DMs (im), and group DMs (mpim). Paginated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "types": {
                    "type": "string",
                    "description": (
                        "Comma-separated channel types: public_channel, "
                        "private_channel, mpim, im. Default: public_channel."
                    ),
                },
                "exclude_archived": {"type": "boolean"},
                "page_token": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_channel",
        description="Fetch a single channel's metadata (members, topic, purpose, type flags).",
        input_schema={
            "type": "object",
            "required": ["channel"],
            "properties": {"channel": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="list_messages",
        description=(
            "Enumerate messages in a channel. Use oldest/latest to bound "
            "the time window; paginate for large channels."
        ),
        input_schema={
            "type": "object",
            "required": ["channel"],
            "properties": {
                "channel": {"type": "string"},
                "oldest": {"type": "string"},
                "latest": {"type": "string"},
                "page_token": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_replies",
        description="Fetch all replies in a thread by channel + thread_ts.",
        input_schema={
            "type": "object",
            "required": ["channel", "thread_ts"],
            "properties": {
                "channel": {"type": "string"},
                "thread_ts": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="search_messages",
        description=(
            "Server-side search across messages by query, channel, sender, "
            "or date. Use for incremental discovery between backfill runs."
        ),
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "page_token": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
    ConnectorAction(
        name="list_users",
        description="Enumerate workspace users (including bots, deactivated, deleted).",
        input_schema={
            "type": "object",
            "properties": {
                "page_token": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_user",
        description="Fetch a single user's profile (display name, real name, email, role).",
        input_schema={
            "type": "object",
            "required": ["user"],
            "properties": {"user": {"type": "string"}},
        },
    ),
)


def make_slack_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Slack connector. Pass a client to go live."""
    return ComposioConnector(
        name="slack",
        actions=SLACK_ACTIONS,
        client=client,
        preview_fn=_slack_preview,
    )


def _slack_preview(raw: dict[str, Any]) -> str:
    """User | channel — text snippet. Harness further redacts + truncates."""
    user = str(raw.get("user", "")).strip()
    channel = str(raw.get("channel", raw.get("channel_id", ""))).strip()
    text = str(raw.get("text", "")).strip()
    header = " | ".join(x for x in (user, channel) if x)
    prefix = f"{header} — " if header else ""
    return (prefix + text)[:500]
