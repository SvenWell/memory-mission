"""Notion backfill connector.

Notion is many firms' wiki + project database substrate. Composio
exposes 27 tools backed by Notion's REST API; this connector wraps
the read-side subset every backfill loop needs.

V1 read action surface:

- ``search`` — workspace-wide search by title across pages + databases
- ``list_users`` — workspace members
- ``get_page`` — fetch a single page (properties + parent + last_edited)
- ``get_block_children`` — page or block children (the rendered content tree)
- ``query_database`` — query a database with filters / sorts
- ``get_database`` — fetch a database's schema
- ``get_comments`` — comments on a page or block

Auth: OAuth2 (typical) or API Key (integration tokens) via Composio.

Composio also exposes write-side actions (create_page, update_page,
add_block, etc.) — those route through P5 sync-back, not this
backfill connector.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

NOTION_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="search",
        description="Workspace-wide search by title across pages and databases.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filter": {"type": "object"},
                "page_token": {"type": "string"},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
    ConnectorAction(
        name="list_users",
        description="Enumerate workspace members.",
        input_schema={
            "type": "object",
            "properties": {
                "page_token": {"type": "string"},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
    ConnectorAction(
        name="get_page",
        description=(
            "Fetch a single page by id, including properties, parent, and last_edited metadata."
        ),
        input_schema={
            "type": "object",
            "required": ["page_id"],
            "properties": {"page_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="get_block_children",
        description=(
            "Fetch the children blocks of a page or container block "
            "(the rendered content tree). Recurse to assemble full body."
        ),
        input_schema={
            "type": "object",
            "required": ["block_id"],
            "properties": {
                "block_id": {"type": "string"},
                "page_token": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="query_database",
        description="Query a Notion database with filters and sorts.",
        input_schema={
            "type": "object",
            "required": ["database_id"],
            "properties": {
                "database_id": {"type": "string"},
                "filter": {"type": "object"},
                "sorts": {"type": "array"},
                "page_token": {"type": "string"},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
    ConnectorAction(
        name="get_database",
        description="Fetch a database's metadata and property schema by id.",
        input_schema={
            "type": "object",
            "required": ["database_id"],
            "properties": {"database_id": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="get_comments",
        description="Fetch comments on a page or block.",
        input_schema={
            "type": "object",
            "required": ["block_id"],
            "properties": {"block_id": {"type": "string"}},
        },
    ),
)


def make_notion_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a Notion connector. Pass a client to go live."""
    return ComposioConnector(
        name="notion",
        actions=NOTION_ACTIONS,
        client=client,
        preview_fn=_notion_preview,
    )


def _notion_preview(raw: dict[str, Any]) -> str:
    """Title preview for pages or databases. Harness further redacts + truncates."""
    title = _notion_title(raw)
    object_type = str(raw.get("object", "")).strip()
    parent_type = ""
    parent = raw.get("parent")
    if isinstance(parent, dict):
        parent_type = str(parent.get("type", "")).strip()
    parts = [object_type, parent_type, title]
    return " | ".join(p for p in parts if p)[:500]


def _notion_title(raw: dict[str, Any]) -> str:
    """Notion stores titles inconsistently — try several common shapes."""
    # Database-level: top-level "title" array of rich text fragments
    title_block = raw.get("title")
    if isinstance(title_block, list):
        plain = "".join(_notion_rich_text_plain(rt) for rt in title_block)
        if plain:
            return plain
    # Page-level: properties.title (or properties.Name) - title array
    props = raw.get("properties")
    if isinstance(props, dict):
        for key in ("title", "Title", "Name", "name"):
            block = props.get(key)
            if isinstance(block, dict):
                title_arr = block.get("title")
                if isinstance(title_arr, list):
                    plain = "".join(_notion_rich_text_plain(rt) for rt in title_arr)
                    if plain:
                        return plain
    return ""


def _notion_rich_text_plain(rt: Any) -> str:
    if not isinstance(rt, dict):
        return ""
    plain = rt.get("plain_text")
    if isinstance(plain, str):
        return plain
    text = rt.get("text")
    if isinstance(text, dict):
        content = text.get("content")
        if isinstance(content, str):
            return content
    return ""
