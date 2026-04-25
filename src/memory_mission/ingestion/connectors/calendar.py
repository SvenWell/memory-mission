"""Google Calendar backfill connector.

Pulls historical calendar events through Composio. Same harness shape as
Gmail / Granola / Drive: every invocation flows through ``invoke()`` so
observability + PII-scrub + durability wrap each call.

V1 action surface:

- ``list_events`` — enumerate events for a calendar id, optionally
  filtered by time window or query string
- ``get_event`` — fetch one event by id (returns full attendee list,
  description, visibility, organizer)

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

CALENDAR_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_events",
        description=(
            "Enumerate Google Calendar events for a calendar id, "
            "optionally filtered by time window or text query."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "time_min": {"type": "string", "format": "date-time"},
                "time_max": {"type": "string", "format": "date-time"},
                "query": {"type": "string"},
                "page_token": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 2500},
            },
        },
    ),
    ConnectorAction(
        name="get_event",
        description=(
            "Fetch a single Google Calendar event by id, including attendees, "
            "description, visibility, and organizer."
        ),
        input_schema={
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "calendar_id": {"type": "string"},
                "event_id": {"type": "string"},
            },
        },
    ),
)


def make_calendar_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Google Calendar connector. Pass a client to go live."""
    return ComposioConnector(
        name="gcal",
        actions=CALENDAR_ACTIONS,
        client=client,
        preview_fn=_calendar_preview,
    )


def _calendar_preview(raw: dict[str, Any]) -> str:
    """Summary | start — attendees count. Harness further redacts + truncates."""
    summary = str(raw.get("summary", "")).strip()
    start_block = raw.get("start") or {}
    if isinstance(start_block, dict):
        start = str(start_block.get("dateTime") or start_block.get("date") or "").strip()
    else:
        start = str(start_block).strip()
    attendees = raw.get("attendees") or []
    attendee_count = len(attendees) if isinstance(attendees, list) else 0
    header = " | ".join(x for x in (summary, start) if x)
    suffix = f" — {attendee_count} attendee{'s' if attendee_count != 1 else ''}"
    return (header + suffix)[:500]
