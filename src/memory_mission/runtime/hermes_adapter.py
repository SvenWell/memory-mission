"""Hermes Agent adapter — superseded by ``memory_mission.mcp``.

The MCP server at ``memory_mission.mcp.server`` is now the supported
integration surface. Host agents (Hermes, Claude Code, Cursor, Codex)
connect by spawning the MCP server as a subprocess; per-employee
observability + scope enforcement is enforced there.

See ``docs/adr/0003-mcp-as-agent-surface.md``.
"""
