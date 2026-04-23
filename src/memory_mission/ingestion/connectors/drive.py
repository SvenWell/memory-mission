"""Google Drive backfill connector.

Pulls firm documents via Composio — memos, decks, training docs,
quarterly updates, board material, LP letters. The primary cold-start
source for the firm plane. Emile's authority-problem answer: firm
knowledge is seeded from firm-authored documents (with the
administrator's blessing via the promotion pipeline), not from one
employee agent's extracted opinions.

V1 action surface:

- ``list_files`` — enumerate Drive files, optionally filtered by
  folder / mime_type / modified-since
- ``get_file`` — fetch one file by id, handling Google Docs export
  to markdown and other common mime types server-side (Composio
  abstracts the export machinery)

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

DRIVE_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_files",
        description=(
            "Enumerate Drive files, optionally filtered by folder, mime type, or modified-since."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "mime_type": {"type": "string"},
                "modified_since": {"type": "string", "format": "date-time"},
                "page_token": {"type": "string"},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
    ),
    ConnectorAction(
        name="get_file",
        description=(
            "Fetch a single Drive file by id. Google Docs are exported to markdown server-side."
        ),
        input_schema={
            "type": "object",
            "required": ["file_id"],
            "properties": {"file_id": {"type": "string"}},
        },
    ),
)


def make_drive_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Drive connector. Pass a client to go live."""
    return ComposioConnector(
        name="drive",
        actions=DRIVE_ACTIONS,
        client=client,
        preview_fn=_drive_preview,
    )


def _drive_preview(raw: dict[str, Any]) -> str:
    """File name + mime type — body snippet. Harness further scrubs + truncates."""
    name = str(raw.get("name", "")).strip()
    mime = str(raw.get("mime_type", "")).strip()
    body = str(raw.get("content", raw.get("body", raw.get("snippet", "")))).strip()
    header = " | ".join(x for x in (name, mime) if x)
    prefix = f"{header} — " if header else ""
    return prefix + body[:400]
