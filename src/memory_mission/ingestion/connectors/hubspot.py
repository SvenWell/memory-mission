"""HubSpot CRM connector - Composio-backed CRM backfill + sync surface.

HubSpot is a common customer CRM system-of-record for contacts,
companies, deals, and the timeline context attached to them. Composio
exposes HubSpot's CRM object, property, association, note, task, and
schema actions behind OAuth2 or API-key style auth; this connector keeps
Memory Mission credential-free and wraps the stable action surface the
backfill and P5 sync-back flows need.

V1 read action surface:

- ``list_contacts`` / ``get_contact`` / ``search_contacts``
- ``list_companies`` / ``get_company`` / ``search_companies``
- ``list_deals`` / ``get_deal`` / ``search_objects``
- ``read_object`` / ``read_batch``
- ``list_association_types`` / ``list_object_associations`` /
  ``read_associations_batch``
- ``list_properties`` / ``read_property`` / ``list_property_groups``

V1 write/sync action surface:

- ``create_contact`` / ``update_contact``
- ``create_company`` / ``update_company``
- ``create_deal`` / ``update_deal``
- ``create_note``
- ``create_object_association``
- ``create_property_group`` / ``create_property``
- ``create_batch`` / ``update_batch`` / ``upsert_batch``

Auth: Composio-managed HubSpot connection. For customer-facing installs,
that should normally be OAuth through Composio. Private/static HubSpot
tokens are acceptable for internal sandbox tests only when provisioned
inside Composio, not stored in Memory Mission config.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

_HUBSPOT_PAGE_SCHEMA: dict[str, Any] = {
    "after": {"type": "string"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "properties": {"type": "array", "items": {"type": "string"}},
    "associations": {"type": "array", "items": {"type": "string"}},
}

_HUBSPOT_OBJECT_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["object_id"],
    "properties": {
        "object_id": {"type": "string"},
        "properties": {"type": "array", "items": {"type": "string"}},
        "associations": {"type": "array", "items": {"type": "string"}},
        "id_property": {"type": "string"},
    },
}

_HUBSPOT_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "filter_groups": {"type": "array"},
        "sorts": {"type": "array"},
        "properties": {"type": "array", "items": {"type": "string"}},
        "after": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
}

_HUBSPOT_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["properties"],
    "properties": {
        "properties": {"type": "object"},
        "associations": {"type": "array"},
    },
}

_HUBSPOT_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["object_id", "properties"],
    "properties": {
        "object_id": {"type": "string"},
        "properties": {"type": "object"},
        "id_property": {"type": "string"},
    },
}

HUBSPOT_STANDARD_OBJECT_TYPES: dict[str, str] = {
    "contact": "0-1",
    "contacts": "0-1",
    "company": "0-2",
    "companies": "0-2",
    "deal": "0-3",
    "deals": "0-3",
    "note": "0-46",
    "notes": "0-46",
    "meeting": "0-47",
    "meetings": "0-47",
    "call": "0-48",
    "calls": "0-48",
    "email": "0-49",
    "emails": "0-49",
    "task": "0-27",
    "tasks": "0-27",
}

HUBSPOT_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_contacts",
        description="Enumerate HubSpot contacts.",
        input_schema={"type": "object", "properties": dict(_HUBSPOT_PAGE_SCHEMA)},
    ),
    ConnectorAction(
        name="get_contact",
        description="Fetch one HubSpot contact by id or id_property.",
        input_schema=_HUBSPOT_OBJECT_READ_SCHEMA,
    ),
    ConnectorAction(
        name="search_contacts",
        description="Search HubSpot contacts by query or filter groups.",
        input_schema=_HUBSPOT_SEARCH_SCHEMA,
    ),
    ConnectorAction(
        name="list_companies",
        description="Enumerate HubSpot companies.",
        input_schema={"type": "object", "properties": dict(_HUBSPOT_PAGE_SCHEMA)},
    ),
    ConnectorAction(
        name="get_company",
        description="Fetch one HubSpot company by id or id_property.",
        input_schema=_HUBSPOT_OBJECT_READ_SCHEMA,
    ),
    ConnectorAction(
        name="search_companies",
        description="Search HubSpot companies by query or filter groups.",
        input_schema=_HUBSPOT_SEARCH_SCHEMA,
    ),
    ConnectorAction(
        name="list_deals",
        description="Enumerate HubSpot deals.",
        input_schema={"type": "object", "properties": dict(_HUBSPOT_PAGE_SCHEMA)},
    ),
    ConnectorAction(
        name="get_deal",
        description="Fetch one HubSpot deal by id or id_property.",
        input_schema=_HUBSPOT_OBJECT_READ_SCHEMA,
    ),
    ConnectorAction(
        name="search_objects",
        description="Generic HubSpot CRM object search for standard or custom object types.",
        input_schema={
            "type": "object",
            "required": ["object_type"],
            "properties": {
                "object_type": {"type": "string"},
                **_HUBSPOT_SEARCH_SCHEMA["properties"],
            },
        },
    ),
    ConnectorAction(
        name="read_object",
        description="Generic HubSpot CRM object read for standard or custom object types.",
        input_schema={
            "type": "object",
            "required": ["object_type", "object_id"],
            "properties": {
                "object_type": {"type": "string"},
                **_HUBSPOT_OBJECT_READ_SCHEMA["properties"],
            },
        },
    ),
    ConnectorAction(
        name="read_batch",
        description="Batch-read HubSpot CRM objects by id or property value.",
        input_schema={
            "type": "object",
            "required": ["object_type", "inputs"],
            "properties": {
                "object_type": {"type": "string"},
                "inputs": {"type": "array", "maxItems": 100},
                "properties": {"type": "array", "items": {"type": "string"}},
                "id_property": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="list_association_types",
        description="List valid association type ids for a HubSpot object pair.",
        input_schema={
            "type": "object",
            "required": ["from_object_type", "to_object_type"],
            "properties": {
                "from_object_type": {"type": "string"},
                "to_object_type": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="list_object_associations",
        description="List associations from one HubSpot record to another object type.",
        input_schema={
            "type": "object",
            "required": ["from_object_type", "from_object_id", "to_object_type"],
            "properties": {
                "from_object_type": {"type": "string"},
                "from_object_id": {"type": "string"},
                "to_object_type": {"type": "string"},
                "after": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    ),
    ConnectorAction(
        name="read_associations_batch",
        description="Batch-read HubSpot associations for up to 1000 source record ids.",
        input_schema={
            "type": "object",
            "required": ["from_object_type", "to_object_type", "inputs"],
            "properties": {
                "from_object_type": {"type": "string"},
                "to_object_type": {"type": "string"},
                "inputs": {"type": "array", "maxItems": 1000},
            },
        },
    ),
    ConnectorAction(
        name="list_properties",
        description="List HubSpot CRM properties for an object type.",
        input_schema={
            "type": "object",
            "required": ["object_type"],
            "properties": {"object_type": {"type": "string"}, "archived": {"type": "boolean"}},
        },
    ),
    ConnectorAction(
        name="read_property",
        description="Read one HubSpot CRM property definition by internal name.",
        input_schema={
            "type": "object",
            "required": ["object_type", "property_name"],
            "properties": {
                "object_type": {"type": "string"},
                "property_name": {"type": "string"},
                "archived": {"type": "boolean"},
            },
        },
    ),
    ConnectorAction(
        name="list_property_groups",
        description="List HubSpot property groups for an object type.",
        input_schema={
            "type": "object",
            "required": ["object_type"],
            "properties": {"object_type": {"type": "string"}},
        },
    ),
    ConnectorAction(
        name="create_property_group",
        description="Create a HubSpot property group for Memory Mission custom fields.",
        input_schema={
            "type": "object",
            "required": ["object_type", "name", "label"],
            "properties": {
                "object_type": {"type": "string"},
                "name": {"type": "string"},
                "label": {"type": "string"},
                "display_order": {"type": "integer"},
            },
        },
    ),
    ConnectorAction(
        name="create_property",
        description="Create one HubSpot CRM property, optionally unique for stable MM ids.",
        input_schema={
            "type": "object",
            "required": ["object_type", "name", "label", "type", "field_type", "group_name"],
            "properties": {
                "object_type": {"type": "string"},
                "name": {"type": "string"},
                "label": {"type": "string"},
                "type": {"type": "string"},
                "field_type": {"type": "string"},
                "group_name": {"type": "string"},
                "description": {"type": "string"},
                "has_unique_value": {"type": "boolean"},
                "options": {"type": "array"},
            },
        },
    ),
    ConnectorAction(
        name="create_contact",
        description="Create a HubSpot contact. Use search/update first to avoid duplicates.",
        input_schema=_HUBSPOT_CREATE_SCHEMA,
    ),
    ConnectorAction(
        name="update_contact",
        description="Update a HubSpot contact by id or id_property.",
        input_schema=_HUBSPOT_UPDATE_SCHEMA,
    ),
    ConnectorAction(
        name="create_company",
        description="Create a HubSpot company. Use search/update first to avoid duplicates.",
        input_schema=_HUBSPOT_CREATE_SCHEMA,
    ),
    ConnectorAction(
        name="update_company",
        description="Update a HubSpot company by id or id_property.",
        input_schema=_HUBSPOT_UPDATE_SCHEMA,
    ),
    ConnectorAction(
        name="create_deal",
        description="Create a HubSpot deal. Requires caller-supplied pipeline/dealstage.",
        input_schema=_HUBSPOT_CREATE_SCHEMA,
    ),
    ConnectorAction(
        name="update_deal",
        description="Update a HubSpot deal by id or id_property.",
        input_schema=_HUBSPOT_UPDATE_SCHEMA,
    ),
    ConnectorAction(
        name="create_note",
        description="Create a HubSpot note, optionally associated to CRM records.",
        input_schema={
            "type": "object",
            "required": ["hs_timestamp"],
            "properties": {
                "hs_timestamp": {"type": ["string", "integer"]},
                "hs_note_body": {"type": "string"},
                "hubspot_owner_id": {"type": "string"},
                "associations": {"type": "array"},
                "custom_properties": {"type": "object"},
            },
        },
    ),
    ConnectorAction(
        name="create_object_association",
        description="Create or label an association between two HubSpot CRM records.",
        input_schema={
            "type": "object",
            "required": ["object_type", "object_id", "to_object_type", "to_object_id", "labels"],
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "to_object_type": {"type": "string"},
                "to_object_id": {"type": "string"},
                "labels": {"type": "array", "minItems": 1},
            },
        },
    ),
    ConnectorAction(
        name="create_batch",
        description="Batch-create HubSpot CRM objects. Caller keeps batches <= 100.",
        input_schema={
            "type": "object",
            "required": ["object_type", "inputs"],
            "properties": {
                "object_type": {"type": "string"},
                "inputs": {"type": "array", "maxItems": 100},
            },
        },
    ),
    ConnectorAction(
        name="update_batch",
        description="Batch-update HubSpot CRM objects by id or id_property.",
        input_schema={
            "type": "object",
            "required": ["object_type", "inputs"],
            "properties": {
                "object_type": {"type": "string"},
                "inputs": {"type": "array", "maxItems": 100},
                "id_property": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="upsert_batch",
        description=(
            "Batch-upsert HubSpot CRM objects by a unique id_property, such as mm_entity_id. "
            "Hosts may implement this via a first-class Composio tool or Composio proxy."
        ),
        input_schema={
            "type": "object",
            "required": ["object_type", "id_property", "inputs"],
            "properties": {
                "object_type": {"type": "string"},
                "id_property": {"type": "string"},
                "inputs": {"type": "array", "maxItems": 100},
            },
        },
    ),
)


def make_hubspot_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for a HubSpot connector. Pass a client to go live."""
    return ComposioConnector(
        name="hubspot",
        actions=HUBSPOT_ACTIONS,
        client=client,
        preview_fn=_hubspot_preview,
    )


def _hubspot_preview(raw: dict[str, Any]) -> str:
    """Human-readable record preview. Harness further redacts + truncates."""
    props = _properties(raw)
    record_id = str(raw.get("id") or props.get("hs_object_id") or "").strip()
    object_type = _object_type_preview(raw)
    title = _title(props)
    note = str(props.get("hs_note_body") or raw.get("hs_note_body") or "").strip()
    parts = [object_type, record_id, title]
    header = " | ".join(p for p in parts if p)
    if note:
        return f"{header} - {note[:300]}"[:500] if header else note[:500]
    return header[:500]


def _object_type_preview(raw: dict[str, Any]) -> str:
    for key in ("object_type", "objectType", "objectTypeId", "type"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _properties(raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties")
    if isinstance(props, dict):
        return props
    return raw


def _title(props: dict[str, Any]) -> str:
    deal = str(props.get("dealname") or props.get("deal_name") or "").strip()
    if deal:
        return deal
    company = str(props.get("name") or props.get("company") or "").strip()
    domain = str(props.get("domain") or props.get("website") or "").strip()
    if company and domain:
        return f"{company} ({domain})"
    if company:
        return company
    first = str(props.get("firstname") or props.get("first_name") or "").strip()
    last = str(props.get("lastname") or props.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    email = str(props.get("email") or props.get("work_email") or "").strip()
    if full and email:
        return f"{full} ({email})"
    return full or email


__all__ = [
    "HUBSPOT_ACTIONS",
    "HUBSPOT_STANDARD_OBJECT_TYPES",
    "make_hubspot_connector",
]
