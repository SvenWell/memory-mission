"""Affinity backfill connector — venture-CRM relationship intelligence.

Affinity is the dominant venture-fund CRM (relationship intelligence,
deal pipelines, portfolio tracking). Composio exposes ~20 actions
backed by Affinity's REST API; this connector wraps the read-side
subset every backfill loop needs (list / get for organizations,
persons, opportunities, plus the list metadata that drives visibility
mapping).

V1 read action surface:

- ``list_organizations`` / ``get_organization``
- ``list_persons``     / ``get_person``
- ``list_opportunities`` / ``get_opportunity``
- ``list_lists``       — firm's lists (pipelines, portfolio, LP network, …)
- ``get_list_metadata`` — list field schemas
- ``list_list_entries`` — which records sit in a given list

Affinity uses **API-key auth** at the Composio layer (not OAuth2). The
firm provisions a per-firm Affinity API key in Composio's dashboard;
the connector itself stays credential-free.

Composio also exposes write-side actions (create note, update field,
etc.) — those will route through P5 sync-back, not this backfill
connector.
"""

from __future__ import annotations

from typing import Any

from memory_mission.ingestion.connectors.base import ConnectorAction
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)

AFFINITY_ACTIONS: tuple[ConnectorAction, ...] = (
    ConnectorAction(
        name="list_organizations",
        description="Enumerate organizations (companies) in Affinity.",
        input_schema={
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": {"type": "string"},
                "term": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="get_organization",
        description="Fetch one organization by Affinity id, including list-entries and field data.",
        input_schema={
            "type": "object",
            "required": ["organization_id"],
            "properties": {
                "organization_id": {"type": "integer"},
                "with_interaction_dates": {"type": "boolean"},
            },
        },
    ),
    ConnectorAction(
        name="list_persons",
        description="Enumerate persons in Affinity.",
        input_schema={
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": {"type": "string"},
                "term": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="get_person",
        description="Fetch one person by Affinity id, including list-entries and field data.",
        input_schema={
            "type": "object",
            "required": ["person_id"],
            "properties": {
                "person_id": {"type": "integer"},
                "with_interaction_dates": {"type": "boolean"},
            },
        },
    ),
    ConnectorAction(
        name="list_opportunities",
        description=(
            "Enumerate opportunities (deals) in Affinity. "
            "NOTE: Affinity's pagination yields basic info; field data "
            "requires a separate get_opportunity call per id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": {"type": "string"},
            },
        },
    ),
    ConnectorAction(
        name="get_opportunity",
        description="Fetch one opportunity by Affinity id, including field data and list entry.",
        input_schema={
            "type": "object",
            "required": ["opportunity_id"],
            "properties": {"opportunity_id": {"type": "integer"}},
        },
    ),
    ConnectorAction(
        name="list_lists",
        description=(
            "Enumerate the firm's Affinity lists (pipelines, portfolio, "
            "LP network, etc). Drives visibility mapping — list-membership "
            "is the typical scope signal in venture firms."
        ),
        input_schema={"type": "object"},
    ),
    ConnectorAction(
        name="get_list_metadata",
        description="Fetch a single list's metadata, including field schema.",
        input_schema={
            "type": "object",
            "required": ["list_id"],
            "properties": {"list_id": {"type": "integer"}},
        },
    ),
    ConnectorAction(
        name="list_list_entries",
        description=(
            "Enumerate records (organizations / persons / opportunities) inside a given list."
        ),
        input_schema={
            "type": "object",
            "required": ["list_id"],
            "properties": {
                "list_id": {"type": "integer"},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": {"type": "string"},
            },
        },
    ),
)


def make_affinity_connector(
    client: ComposioClient | None = None,
) -> ComposioConnector:
    """Factory for an Affinity connector. Pass a client to go live."""
    return ComposioConnector(
        name="affinity",
        actions=AFFINITY_ACTIONS,
        client=client,
        preview_fn=_affinity_preview,
    )


def _affinity_preview(raw: dict[str, Any]) -> str:
    """Type-aware preview — name/email/title for orgs/persons/opps."""
    name = str(raw.get("name", "")).strip()
    if name:
        domain = str(raw.get("domain", "")).strip()
        suffix = f" ({domain})" if domain else ""
        return f"{name}{suffix}"[:500]
    first = str(raw.get("first_name", "")).strip()
    last = str(raw.get("last_name", "")).strip()
    full = f"{first} {last}".strip()
    if full:
        email = str(raw.get("primary_email", "")).strip()
        suffix = f" — {email}" if email else ""
        return f"{full}{suffix}"[:500]
    # Opportunity or list entry fallback
    title = str(raw.get("title", "")).strip()
    if title:
        return title[:500]
    return ""
