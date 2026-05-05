"""Server-wide context for the Individual-mode MCP server (ADR-0015).

Distinct from ``McpContext`` (firm-mode). Individual mode bundles
per-user handles only — no proposal store, no firm-plane tools, no
firm policy. Single user per process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_mission.identity.base import IdentityResolver
    from memory_mission.memory.engine import BrainEngine
    from memory_mission.personal_brain.backend import PersonalMemoryBackend
    from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


@dataclass
class IndividualMcpContext:
    """Handles the individual-mode MCP server reads/writes through.

    Created once per server process. All MCP tools read from this
    context via the server module's ``_ctx()`` helper. None of the
    handles are recreated per-call — they're long-lived for the
    server lifetime.
    """

    user_id: str
    agent_id: str
    kg: PersonalKnowledgeGraph
    engine: BrainEngine
    identity: IdentityResolver
    observability_root: Path
    # Optional MemPalace-style recall backend. When None, the
    # ``search_recall`` tool returns a structured error rather than
    # crashing — Individual mode is usable without MemPalace wired up.
    backend: PersonalMemoryBackend | None = None
    # Optional eval-capture sqlite path. Set when ``initialize`` resolves
    # ``<root>/personal/<user_id>/eval_captures.sqlite3``. Tools that
    # produce retrieval-quality-relevant results pass this to
    # ``record_eval_capture``; capture is gated by
    # ``MM_CONTRIBUTOR_MODE=1`` and is a no-op otherwise.
    eval_captures_path: Path | None = None
