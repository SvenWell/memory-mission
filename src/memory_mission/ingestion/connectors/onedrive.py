"""Microsoft OneDrive + SharePoint backfill connector.

Composio's OneDrive toolkit covers BOTH personal/business OneDrive AND
SharePoint document libraries through a single API surface (Microsoft
Graph treats both as drives). Pages and list items live behind
specialized SharePoint actions.

V1 read action surface:

- ``list_drive_items`` — enumerate items in a drive / folder
- ``get_item`` — fetch one drive item by id (file or folder)
- ``list_recent_items`` — recently-accessed items for quick triage
- ``search_items`` — full-text search across drive items
- ``get_item_metadata`` — metadata + sharing + extended properties
- ``get_item_permissions`` — per-item permission grants
- ``get_sharepoint_site_details`` — site metadata
- ``list_site_subsites`` — child sites enumeration
- ``get_sharepoint_list_items`` — items in a SharePoint list
- ``get_sharepoint_site_page_content`` — modern site page content

Auth: OAuth2 via Composio.

Live Composio calls are stubbed until credentials are wired.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

ONEDRIVE_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_drive_items",
        description="Enumerate drive items in a OneDrive or SharePoint document library.",
        input_schema={
            "type": "object",
            "properties": {
                "drive_id": {"type": "string"},
                "folder_id": {"type": "string"},
                "page_token": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    ),
    ConnectorAction(
        name="get_item",
        description="Fetch a single drive item (file or folder) by id, including metadata.",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {
                "item_id": {"type": "string"},
                "drive_id": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="list_recent_items",
        description="Recently-accessed items in the authenticated user's OneDrive.",
        input_schema={
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    ),
    ConnectorAction(
        name="search_items",
        description="Full-text search across drive items the user has access to.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    ),
    ConnectorAction(
        name="get_item_metadata",
        description="Fetch extended metadata + sharing info for a single drive item.",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="get_item_permissions",
        description="List the permission grants on a drive item (sharing links + direct grants).",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="get_sharepoint_site_details",
        description=(
            "Fetch SharePoint site metadata (display name, web URL, created/modified dates)."
        ),
        input_schema={
            "type": "object",
            "required": ["site_id"],
            "properties": {"site_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="list_site_subsites",
        description="Enumerate child SharePoint sites under a parent site id.",
        input_schema={
            "type": "object",
            "required": ["site_id"],
            "properties": {"site_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="get_sharepoint_list_items",
        description="Get items inside a SharePoint list on a given site.",
        input_schema={
            "type": "object",
            "required": ["site_id", "list_id"],
            "properties": {
                "site_id": {"type": "string"},
                "list_id": {"type": "string"},
                "page_token": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="get_sharepoint_site_page_content",
        description="Fetch the rendered content of a modern SharePoint site page.",
        input_schema={
            "type": "object",
            "required": ["site_id", "page_id"],
            "properties": {
                "site_id": {"type": "string"},
                "page_id": {"type": "string"},
            },
        },
    ),
)


def make_onedrive_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a OneDrive + SharePoint connector. Pass a client to go live."""
    return ComposioConnector(
        name="one_drive",
        actions=ONEDRIVE_ACTIONS,
        client=client,
        preview_fn=_onedrive_preview,
    )


def _onedrive_preview(raw: dict[str, Any]) -> str:
    """File name + mime — body snippet. Harness further scrubs + truncates."""
    name = str(raw.get("name", "")).strip()
    file_block = raw.get("file") or {}
    mime = ""
    if isinstance(file_block, dict):
        mime = str(file_block.get("mimeType", "")).strip()
    body = str(raw.get("content", raw.get("body", raw.get("snippet", "")))).strip()
    header = " | ".join(x for x in (name, mime) if x)
    prefix = f"{header} — " if header else ""
    return (prefix + body)[:500]
