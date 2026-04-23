"""MCP server surface — exposes Memory Mission as tools to host agents.

One server process per employee. Launch with
``python -m memory_mission.mcp --firm-root <path> --employee-id <id>``.
Scope enforcement reads from ``firm/mcp_clients.yaml``; every mutating
tool opens an ``observability_scope`` so the audit trail covers every
write that comes through MCP.

See ``docs/adr/0003-mcp-as-agent-surface.md`` for the rationale and
``docs/recipes/mcp-integration.md`` for the operator guide.
"""

from memory_mission.mcp.auth import (
    AuthError,
    ClientEntry,
    Scope,
    load_manifest,
    resolve_employee,
)
from memory_mission.mcp.context import McpContext

__all__ = [
    "AuthError",
    "ClientEntry",
    "McpContext",
    "Scope",
    "load_manifest",
    "resolve_employee",
]
