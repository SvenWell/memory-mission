"""Gmail backfill connector.

Pulls historical email via Composio. Implementation deliberately tracks
GBrain's ``sync_gmail.ts`` pattern (list message ids, fetch by id, hand off
to extraction). That pattern has run in production for months — no need to
re-prove it here.

What IS new is that every invocation threads through our observability
(0.4), middleware (0.7), and durable (0.6) harness. Callers wrap a backfill
loop in ``durable_run`` so crashes resume from the last processed message;
each ``invoke()`` logs a connector invocation event with latency and a
PII-scrubbed preview.

V1 action surface:

- ``list_message_ids`` — enumerate message ids, optionally filtered by query
- ``get_message`` — fetch one message by id

Live Composio calls are stubbed until credentials are wired.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

GMAIL_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_message_ids",
        description="Enumerate Gmail message ids, optionally filtered by a Gmail query.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "page_token": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    ),
    ConnectorAction(
        name="get_message",
        description="Fetch a single Gmail message by id, including headers + body.",
        input_schema={
            "type": "object",
            "required": ["message_id"],
            "properties": {"message_id": {"type": "string"}},
        },
    ),
)


def make_gmail_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Gmail connector. Pass a client to go live."""
    return ComposioConnector(
        name="gmail",
        actions=GMAIL_ACTIONS,
        client=client,
        preview_fn=_gmail_preview,
    )


def _gmail_preview(raw: dict[str, Any]) -> str:
    """Sender | subject — snippet. Harness further redacts + truncates."""
    subject = str(raw.get("subject", "")).strip()
    sender = str(raw.get("from", "")).strip()
    snippet = str(raw.get("snippet", raw.get("body", ""))).strip()
    header = " | ".join(x for x in (sender, subject) if x)
    prefix = f"{header} — " if header else ""
    return prefix + snippet[:400]
