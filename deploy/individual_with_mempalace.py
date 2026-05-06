"""Launch memory-mission's individual MCP server with MemPalace recall wired in.

The shipped CLI (`python -m memory_mission.mcp.individual_server`) calls
`initialize(root, user_id, agent_id)` which doesn't accept a backend.
This launcher uses `initialize_from_handles(... backend=adapter)` instead,
threading a `MemPalaceAdapter` through so `mm_search_recall` returns real
hits from the MemPalace per-employee palace at
`<firm_root>/personal/<user_id>/mempalace/`.

Same stdio MCP protocol on the wire — the consuming agent sees the same v1
surface with the same 9 tools, but `mm_search_recall` is now functional
instead of returning `{"error": "no_recall_backend"}`.

Required env (no defaults — fail loudly if missing):

    MM_USER_ID  e.g. keagan
    MM_AGENT_ID e.g. hermes
    MM_ROOT     e.g. /root/memory-mission-data

Wire example (Hermes):

    mcp_servers:
      memory_mission:
        command: <repo>/.venv/bin/python
        args: [<repo>/deploy/individual_with_mempalace.py]
        env:
          MM_USER_ID: keagan
          MM_AGENT_ID: hermes
          MM_ROOT: /root/memory-mission-data
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# chromadb's protobuf binding mismatch — pin the python impl before any import.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# Make the repo importable without requiring an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp import individual_server as server
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.personal_brain import MemPalaceAdapter
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


def main() -> None:
    user_id = os.environ["MM_USER_ID"]
    agent_id = os.environ["MM_AGENT_ID"]
    root = Path(os.environ["MM_ROOT"]).expanduser()

    # Same handle construction the framework's initialize() does, plus the backend.
    identity = LocalIdentityResolver(root / "identity.db")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=root,
        employee_id=user_id,
        identity_resolver=identity,
    )
    engine = InMemoryEngine()
    engine.connect()

    obs_root = root / ".observability"
    obs_root.mkdir(parents=True, exist_ok=True)

    backend = MemPalaceAdapter(firm_root=root, identity_resolver=identity)

    # Keep stdout reserved for the MCP JSON-RPC stream — pin structlog to stderr.
    # (Mirrors the helper landed in PR #4; inlined here so this launcher works
    # regardless of which branch is checked out.)
    import sys as _sys

    import structlog as _structlog

    _structlog.configure(
        logger_factory=_structlog.PrintLoggerFactory(file=_sys.stderr),
    )

    server.initialize_from_handles(
        user_id=user_id,
        agent_id=agent_id,
        kg=kg,
        engine=engine,
        identity=identity,
        observability_root=obs_root,
        backend=backend,
    )

    server.mcp.run()


if __name__ == "__main__":
    main()
