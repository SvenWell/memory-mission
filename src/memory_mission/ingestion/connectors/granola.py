"""Granola transcription connector.

Granola is the meeting-transcription tool in production use. Coverage through
Composio is confirmed. Otter.ai is explicitly NOT covered in V1 — revisit
once Granola-only produces clear wins.

V1 action surface:

- ``list_transcripts`` — enumerate transcripts for the authenticated account
- ``get_transcript`` — fetch a single transcript by id

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

GRANOLA_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_transcripts",
        description="List transcripts available for the authenticated Granola account.",
        input_schema={
            "type": "object",
            "properties": {
                "since": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    ),
    ConnectorAction(
        name="get_transcript",
        description="Fetch a single transcript by Granola id.",
        input_schema={
            "type": "object",
            "required": ["transcript_id"],
            "properties": {"transcript_id": {"type": "string"}},
        },
    ),
)


def make_granola_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Granola connector. Pass a client to go live."""
    return ComposioConnector(
        name="granola",
        actions=GRANOLA_ACTIONS,
        client=client,
        preview_fn=_granola_preview,
    )


def _granola_preview(raw: dict[str, Any]) -> str:
    """Title + leading body snippet. Harness further redacts + truncates."""
    title = str(raw.get("title", "")).strip()
    body = str(raw.get("transcript", raw.get("body", ""))).strip()
    prefix = f"{title}: " if title else ""
    return prefix + body[:400]
