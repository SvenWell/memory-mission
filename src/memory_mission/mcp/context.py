"""Server-wide context — one ``McpContext`` per MCP server process.

Holds open handles to the firm's knowledge graph, brain engine, proposal
store, identity resolver, and policy. Also carries the validated employee
identity and scope set for the process, and exposes ``tool_scope()`` to
wrap each tool call in an observability scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from memory_mission.identity.base import IdentityResolver
from memory_mission.mcp.auth import AuthError, Scope, require_scope
from memory_mission.mcp.auth import ClientEntry as _ClientEntry
from memory_mission.memory.engine import BrainEngine
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.observability.context import observability_scope
from memory_mission.permissions.policy import Policy
from memory_mission.promotion.proposals import ProposalStore


class McpContext:
    """Bundle of server-wide handles + this process's scoped identity.

    Instantiated once per server process. Tools close over a single
    ``McpContext`` via the module-level holder in ``mcp.server``.
    """

    def __init__(
        self,
        *,
        firm_root: Path,
        firm_id: str,
        client: _ClientEntry,
        observability_root: Path,
        engine: BrainEngine,
        kg: KnowledgeGraph,
        store: ProposalStore,
        identity: IdentityResolver,
        policy: Policy | None = None,
    ) -> None:
        self.firm_root = firm_root
        self.firm_id = firm_id
        self.client = client
        self.employee_id = client.employee_id
        self.scopes: frozenset[Scope] = client.scopes
        self.observability_root = observability_root
        self.engine = engine
        self.kg = kg
        self.store = store
        self.identity = identity
        self.policy = policy

    def require_scope(self, scope: Scope) -> None:
        """Raise ``AuthError`` if this process's client lacks ``scope``."""
        require_scope(self.client, scope)

    @contextmanager
    def tool_scope(self) -> Iterator[None]:
        """Open an ``observability_scope`` for the duration of a tool call.

        Every MCP tool call is one logical unit of work. The scope makes
        audit entries (retrieval, promotion, coherence) land on the
        firm's append-only JSONL with employee_id attached.
        """
        with observability_scope(
            observability_root=self.observability_root,
            firm_id=self.firm_id,
            employee_id=self.employee_id,
        ):
            yield


__all__ = ["AuthError", "McpContext", "Scope"]
