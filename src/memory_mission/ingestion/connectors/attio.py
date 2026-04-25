"""Attio backfill connector — schema-flexible CRM.

Attio is a customizable CRM with user-defined object types alongside
the system defaults (people, companies, deals). Composio exposes 11
tools backed by Attio's REST API; this connector wraps the read-side
subset every backfill loop needs.

V1 read action surface:

- ``list_objects`` — enumerate available system + user-defined objects
- ``get_object_details`` — fetch one object's attributes / schema
- ``list_records`` — enumerate records of a given object type
- ``find_record`` — fetch one record by id (or unique attribute)
- ``list_notes`` — enumerate notes attached to a record
- ``list_lists`` — workspace lists (saved views / collections)

Auth: OAuth2 via Composio. Composio also exposes write-side actions
(create_record, create_note, update_record, delete_record,
delete_note) — those route through P5 sync-back, not this backfill
connector.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

ATTIO_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_objects",
        description="Enumerate available object types in Attio (system + user-defined).",
        input_schema={"type": "object"},
    ),
    ConnectorAction(
        name="get_object_details",
        description="Fetch a single object's attribute schema by slug or id.",
        input_schema={
            "type": "object",
            "required": ["object"],
            "properties": {"object": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="list_records",
        description="Enumerate records of a given object type, with optional pagination.",
        input_schema={
            "type": "object",
            "required": ["object"],
            "properties": {
                "object": {"type": "string"},
                "page_token": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    ),
    ConnectorAction(
        name="find_record",
        description="Fetch one record by id (or unique attribute lookup).",
        input_schema={
            "type": "object",
            "required": ["object", "record_id"],
            "properties": {
                "object": {"type": "string"},
                "record_id": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="list_notes",
        description="Enumerate notes attached to a given record.",
        input_schema={
            "type": "object",
            "required": ["object", "record_id"],
            "properties": {
                "object": {"type": "string"},
                "record_id": {"type": "string"},
                "page_token": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="list_lists",
        description="Enumerate the workspace's lists (saved views / collections).",
        input_schema={"type": "object"},
    ),
)


def make_attio_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for an Attio connector. Pass a client to go live."""
    return ComposioConnector(
        name="attio",
        actions=ATTIO_ACTIONS,
        client=client,
        preview_fn=_attio_preview,
    )


def _attio_preview(raw: dict[str, Any]) -> str:
    """Title-from-values preview. Harness further redacts + truncates."""
    name = _attio_first_value(raw, "name")
    if name:
        return name[:500]
    title = _attio_first_value(raw, "title")
    if title:
        return title[:500]
    # Fallback to the record_id if no name/title attribute present.
    rid = _record_id(raw)
    return f"record:{rid}" if rid else ""


def _attio_first_value(raw: dict[str, Any], attribute: str) -> str:
    """Extract the most-recent value of an Attio attribute.

    Attio returns attributes as ``values: {<attr>: [{value: ..., active_from: ...}]}``
    where the array is versioned. The first entry is conventionally
    the currently-active value.
    """
    values = raw.get("values")
    if isinstance(values, dict):
        attr_block = values.get(attribute)
        if isinstance(attr_block, list) and attr_block:
            entry = attr_block[0]
            if isinstance(entry, dict):
                v = entry.get("value")
                if isinstance(v, str):
                    return v
                # Some attributes nest further (e.g., name = {first_name, last_name})
                if isinstance(v, dict):
                    parts = [str(v.get(k, "")) for k in ("first_name", "last_name", "full_name")]
                    return " ".join(p for p in parts if p)
    direct = raw.get(attribute)
    if isinstance(direct, str):
        return direct
    return ""


def _record_id(raw: dict[str, Any]) -> str | None:
    """Extract Attio's record_id from either nested or flat shape."""
    id_block = raw.get("id")
    if isinstance(id_block, dict):
        rid = id_block.get("record_id") or id_block.get("id")
        if isinstance(rid, str):
            return rid
    if isinstance(id_block, str):
        return id_block
    rid = raw.get("record_id")
    if isinstance(rid, str):
        return rid
    return None
