"""MCP client manifest — enumerates which employees may connect + their scopes.

The manifest is the firm's list of MCP-eligible employees. Format:

    # firm/mcp_clients.yaml
    alice@acme.com:
      scopes: [read, propose, review]
    bob@acme.com:
      scopes: [read, propose]
    carol@acme.com:
      scopes: [read]

Unknown employees fail closed — the server refuses to start for an
employee_id absent from the manifest.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class Scope(StrEnum):
    """Capability granted to an MCP client.

    ``READ`` covers every non-mutating tool (query, get_page, search,
    get_entity, get_triples, compile_agent_context, check_coherence).
    ``PROPOSE`` adds create_proposal + list_proposals. ``REVIEW`` adds
    approve / reject / reopen + merge_entities + sql_query_readonly —
    it's the reviewer tier, gated behind explicit firm policy.
    """

    READ = "read"
    PROPOSE = "propose"
    REVIEW = "review"


class ClientEntry(BaseModel):
    """One employee permitted to run an MCP server process."""

    model_config = ConfigDict(frozen=True)

    employee_id: str
    scopes: frozenset[Scope]


class AuthError(Exception):
    """Raised when an employee is not in the manifest or lacks a required scope."""


def load_manifest(path: Path) -> dict[str, ClientEntry]:
    """Load and validate the MCP client manifest from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"MCP client manifest not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw_text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a YAML mapping at top level, got {type(data).__name__}")

    result: dict[str, ClientEntry] = {}
    for employee_id, fields in data.items():
        if not isinstance(employee_id, str) or not employee_id:
            raise ValueError(f"employee_id must be a non-empty string, got {employee_id!r}")
        if not isinstance(fields, dict):
            raise ValueError(
                f"entry for {employee_id!r} must be a mapping, got {type(fields).__name__}"
            )
        scopes_raw = fields.get("scopes", [])
        if not isinstance(scopes_raw, list):
            raise ValueError(f"scopes for {employee_id!r} must be a list")
        scopes: set[Scope] = set()
        for s in scopes_raw:
            if not isinstance(s, str):
                raise ValueError(f"scope for {employee_id!r} must be a string, got {s!r}")
            try:
                scopes.add(Scope(s))
            except ValueError as exc:
                valid = ", ".join(sc.value for sc in Scope)
                raise ValueError(
                    f"unknown scope {s!r} for {employee_id!r}; valid scopes: {valid}"
                ) from exc
        result[employee_id] = ClientEntry(employee_id=employee_id, scopes=frozenset(scopes))

    return result


def resolve_employee(
    manifest: dict[str, ClientEntry],
    employee_id: str,
) -> ClientEntry:
    """Return the manifest entry for ``employee_id`` or raise ``AuthError``."""
    entry = manifest.get(employee_id)
    if entry is None:
        raise AuthError(f"employee not in MCP client manifest: {employee_id}")
    return entry


def require_scope(entry: ClientEntry, scope: Scope) -> None:
    """Raise ``AuthError`` if ``entry`` is missing ``scope``."""
    if scope not in entry.scopes:
        raise AuthError(f"employee {entry.employee_id!r} missing required scope: {scope.value}")
