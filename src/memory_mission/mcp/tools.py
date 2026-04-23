"""MCP tool implementations — thin wrappers over the existing engine / KG / promotion surfaces.

Every tool takes a ``McpContext`` as its first argument, checks the
required scope, opens an observability scope, and calls into the
in-process primitives. No new domain logic lives here — this file is
the protocol boundary, not a business layer.

Fourteen tools: eight read, six write. See ``docs/adr/0003-mcp-as-agent-surface.md``
for the rationale on tool count and scope mapping.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from memory_mission.extraction.schema import ExtractedFact, ExtractionReport
from memory_mission.mcp.auth import AuthError, Scope
from memory_mission.mcp.context import McpContext
from memory_mission.memory.engine import SearchHit
from memory_mission.memory.knowledge_graph import (
    CoherenceWarning,
    Entity,
    MergeResult,
    Triple,
)
from memory_mission.memory.pages import Page
from memory_mission.memory.schema import Plane
from memory_mission.memory.tiers import DEFAULT_TIER, Tier
from memory_mission.permissions.policy import can_propose, viewer_scopes
from memory_mission.promotion.pipeline import (
    create_proposal as _create_proposal,
)
from memory_mission.promotion.pipeline import (
    promote as _promote,
)
from memory_mission.promotion.pipeline import (
    reject as _reject,
)
from memory_mission.promotion.pipeline import (
    reopen as _reopen,
)
from memory_mission.promotion.proposals import Proposal
from memory_mission.synthesis.compile import compile_agent_context as _compile_agent_context
from memory_mission.synthesis.context import AgentContext

ProposalStatus = Literal["pending", "approved", "rejected"]
Direction = Literal["outgoing", "incoming", "both"]


# ---------- Read tools ----------


def query_tool(
    ctx: McpContext,
    *,
    question: str,
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Hybrid search with permission filtering. Returns ranked page hits."""
    ctx.require_scope(Scope.READ)
    employee_id = ctx.employee_id if plane == "personal" else None
    with ctx.tool_scope():
        return ctx.engine.query(
            question,
            plane=plane,
            employee_id=employee_id,
            tier_floor=tier_floor,
            viewer_id=ctx.employee_id,
            policy=ctx.policy,
            limit=limit,
        )


def get_page_tool(
    ctx: McpContext,
    *,
    slug: str,
    plane: Plane = "firm",
) -> Page | None:
    """Fetch a page by slug + plane. Returns None if missing or unreadable."""
    ctx.require_scope(Scope.READ)
    employee_id = ctx.employee_id if plane == "personal" else None
    with ctx.tool_scope():
        return ctx.engine.get_page(
            slug,
            plane=plane,
            employee_id=employee_id,
            viewer_id=ctx.employee_id,
            policy=ctx.policy,
        )


def search_tool(
    ctx: McpContext,
    *,
    query: str,
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Keyword search with permission filtering. Returns ranked page hits."""
    ctx.require_scope(Scope.READ)
    employee_id = ctx.employee_id if plane == "personal" else None
    with ctx.tool_scope():
        return ctx.engine.search(
            query,
            plane=plane,
            employee_id=employee_id,
            tier_floor=tier_floor,
            viewer_id=ctx.employee_id,
            policy=ctx.policy,
            limit=limit,
        )


def get_entity_tool(ctx: McpContext, *, name: str) -> Entity | None:
    """Fetch one canonical entity by name. Returns None if unknown."""
    ctx.require_scope(Scope.READ)
    with ctx.tool_scope():
        return ctx.kg.get_entity(name)


def get_triples_tool(
    ctx: McpContext,
    *,
    entity_name: str,
    direction: Direction = "outgoing",
    as_of: date | None = None,
) -> list[Triple]:
    """Triples involving ``entity_name``. Direction: outgoing / incoming / both.

    Filters by the viewer's policy scopes when ``ctx.policy`` is set —
    triples whose ``scope`` the viewer cannot read are dropped. Without
    a policy, returns all matching triples (backwards compat for firms
    that haven't configured one).
    """
    ctx.require_scope(Scope.READ)
    scopes = _viewer_scopes_or_none(ctx)
    with ctx.tool_scope():
        return ctx.kg.query_entity(
            entity_name,
            direction=direction,
            as_of=as_of,
            viewer_scopes=scopes,
        )


def check_coherence_tool(
    ctx: McpContext,
    *,
    subject: str,
    predicate: str,
    new_object: str,
    new_tier: Tier = DEFAULT_TIER,
) -> list[CoherenceWarning]:
    """Preview coherence warnings for a proposed triple. Non-mutating."""
    ctx.require_scope(Scope.READ)
    with ctx.tool_scope():
        return ctx.kg.check_coherence(subject, predicate, new_object, new_tier=new_tier)


def compile_agent_context_tool(
    ctx: McpContext,
    *,
    role: str,
    task: str,
    attendees: list[str],
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    as_of: date | None = None,
    render: bool = False,
) -> AgentContext | str:
    """Compile the distilled context package for a workflow task.

    Set ``render=True`` to return the markdown string directly instead
    of the structured ``AgentContext`` Pydantic model.

    Threads ``viewer_id`` + ``policy`` into ``compile_agent_context`` so
    KG triples outside the viewer's scopes and firm-plane doctrine
    pages the viewer cannot read under ``can_read`` are dropped before
    the packet is built.
    """
    ctx.require_scope(Scope.READ)
    employee_id = ctx.employee_id if plane == "personal" else None
    with ctx.tool_scope():
        packet = _compile_agent_context(
            role=role,
            task=task,
            attendees=attendees,
            kg=ctx.kg,
            engine=ctx.engine,
            plane=plane,
            employee_id=employee_id,
            tier_floor=tier_floor,
            as_of=as_of,
            identity_resolver=ctx.identity,
            viewer_id=ctx.employee_id,
            policy=ctx.policy,
        )
    if render:
        return packet.render()
    return packet


def sql_query_readonly_tool(
    ctx: McpContext,
    *,
    query: str,
    params: list[Any] | None = None,
    row_limit: int = 1000,
) -> list[dict[str, Any]]:
    """Read-only SQL over the KG. Gated behind REVIEW scope — it's raw graph access."""
    ctx.require_scope(Scope.REVIEW)
    with ctx.tool_scope():
        return ctx.kg.sql_query(query, params or (), row_limit=row_limit)


# ---------- Write tools ----------


def create_proposal_tool(
    ctx: McpContext,
    *,
    target_entity: str,
    facts: list[dict[str, Any]],
    source_report_path: str,
    target_plane: Plane = "firm",
    target_employee_id: str | None = None,
    target_scope: str = "public",
) -> Proposal:
    """Stage a new proposal for review. Requires PROPOSE scope.

    ``facts`` is a list of dicts matching the ``ExtractedFact`` discriminated
    union — each dict must have a ``kind`` field plus the fields the
    variant requires. See ``extraction/schema.py`` for the full shape.

    Enforces the no-escalation rule: when ``ctx.policy`` is set, the
    proposer must have read access to ``target_scope`` via
    ``can_propose``. Without this check, a PROPOSE-scoped employee
    could stage a ``partner-only`` fact even if their policy scopes
    don't include ``partner-only`` — a permission-uplift path.
    """
    ctx.require_scope(Scope.PROPOSE)
    if ctx.policy is not None and not can_propose(ctx.policy, ctx.employee_id, target_scope):
        raise AuthError(
            f"employee {ctx.employee_id!r} cannot propose into scope "
            f"{target_scope!r} under this firm's policy"
        )
    parsed_facts: list[ExtractedFact] = [_parse_fact(f) for f in facts]
    with ctx.tool_scope():
        return _create_proposal(
            ctx.store,
            target_plane=target_plane,
            target_entity=target_entity,
            facts=parsed_facts,
            source_report_path=source_report_path,
            proposer_agent_id=f"mcp:{ctx.employee_id}",
            proposer_employee_id=ctx.employee_id,
            target_employee_id=target_employee_id,
            target_scope=target_scope,
        )


def list_proposals_tool(
    ctx: McpContext,
    *,
    status: ProposalStatus | None = None,
    target_plane: Plane | None = None,
    target_entity: str | None = None,
) -> list[Proposal]:
    """List proposals — all, or filtered by status / plane / entity."""
    ctx.require_scope(Scope.PROPOSE)
    with ctx.tool_scope():
        return ctx.store.list(
            status=status,
            target_plane=target_plane,
            target_entity=target_entity,
        )


def approve_proposal_tool(
    ctx: McpContext,
    *,
    proposal_id: str,
    rationale: str,
) -> Proposal:
    """Approve a pending proposal. Requires REVIEW scope + non-empty rationale."""
    ctx.require_scope(Scope.REVIEW)
    with ctx.tool_scope():
        return _promote(
            ctx.store,
            ctx.kg,
            proposal_id,
            reviewer_id=ctx.employee_id,
            rationale=rationale,
            policy=ctx.policy,
        )


def reject_proposal_tool(
    ctx: McpContext,
    *,
    proposal_id: str,
    rationale: str,
) -> Proposal:
    """Reject a pending proposal. Requires REVIEW scope + non-empty rationale."""
    ctx.require_scope(Scope.REVIEW)
    with ctx.tool_scope():
        return _reject(
            ctx.store,
            proposal_id,
            reviewer_id=ctx.employee_id,
            rationale=rationale,
        )


def reopen_proposal_tool(
    ctx: McpContext,
    *,
    proposal_id: str,
    rationale: str,
) -> Proposal:
    """Reopen a rejected proposal. Requires REVIEW scope + non-empty rationale."""
    ctx.require_scope(Scope.REVIEW)
    with ctx.tool_scope():
        return _reopen(
            ctx.store,
            proposal_id,
            reviewer_id=ctx.employee_id,
            rationale=rationale,
        )


def merge_entities_tool(
    ctx: McpContext,
    *,
    source: str,
    target: str,
    rationale: str,
) -> MergeResult:
    """Rewrite every triple using ``source`` to use ``target`` instead.

    Requires REVIEW scope. Rationale is required — empty strings raise.
    """
    ctx.require_scope(Scope.REVIEW)
    with ctx.tool_scope():
        return ctx.kg.merge_entities(
            source,
            target,
            reviewer_id=ctx.employee_id,
            rationale=rationale,
        )


# ---------- Helpers ----------


def _viewer_scopes_or_none(ctx: McpContext) -> frozenset[str] | None:
    """Return the viewer's effective scope set, or None if the firm has no policy.

    Threaded into KG read methods' ``viewer_scopes`` kwarg. ``None`` tells
    the KG to skip filtering — right default for firms that haven't
    configured a permissions policy.
    """
    if ctx.policy is None:
        return None
    return viewer_scopes(ctx.policy, ctx.employee_id)


def _parse_fact(raw: dict[str, Any]) -> ExtractedFact:
    """Parse one dict into the ``ExtractedFact`` discriminated union.

    Uses an ExtractionReport with a single fact to reuse the discriminator
    machinery already in place — keeps the validation surface identical
    to what ``ingest_facts`` expects.
    """
    report = ExtractionReport.model_validate(
        {
            "source": "mcp",
            "source_id": "inline",
            "target_plane": "firm",
            "facts": [raw],
        }
    )
    return report.facts[0]


__all__ = [
    "approve_proposal_tool",
    "check_coherence_tool",
    "compile_agent_context_tool",
    "create_proposal_tool",
    "get_entity_tool",
    "get_page_tool",
    "get_triples_tool",
    "list_proposals_tool",
    "merge_entities_tool",
    "query_tool",
    "reject_proposal_tool",
    "reopen_proposal_tool",
    "search_tool",
    "sql_query_readonly_tool",
]
