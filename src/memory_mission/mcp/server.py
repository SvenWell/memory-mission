"""FastMCP server — exposes all 14 Memory Mission tools to host agents.

One process per employee. CLI:

    python -m memory_mission.mcp \\
        --firm-root /path/to/firm \\
        --firm-id acme \\
        --employee-id alice@acme.com

The process loads the firm's MCP client manifest, validates the
employee, opens handles to KG / proposal store / identity resolver /
engine, and serves over stdio.

Tests bypass the CLI via ``initialize_from_handles()`` + ``reset()``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from mcp.server.fastmcp import FastMCP

from memory_mission.identity.base import IdentityResolver
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp.auth import ClientEntry, load_manifest, resolve_employee
from memory_mission.mcp.context import McpContext
from memory_mission.mcp.tools import (
    approve_proposal_tool,
    check_coherence_tool,
    compile_agent_context_tool,
    create_proposal_tool,
    get_entity_tool,
    get_page_tool,
    get_triples_tool,
    list_proposals_tool,
    merge_entities_tool,
    query_tool,
    reject_proposal_tool,
    reopen_proposal_tool,
    search_tool,
    sql_query_readonly_tool,
)
from memory_mission.memory.engine import BrainEngine, InMemoryEngine
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.memory.pages import parse_page
from memory_mission.memory.schema import Plane
from memory_mission.memory.tiers import DEFAULT_TIER, Tier
from memory_mission.permissions.policy import Policy, load_policy
from memory_mission.promotion.proposals import ProposalStore

_context: McpContext | None = None

mcp: FastMCP = FastMCP("memory-mission")


# ---------- Context lifecycle ----------


def _ctx() -> McpContext:
    """Return the active ``McpContext`` or raise if the server is uninitialized."""
    if _context is None:
        raise RuntimeError(
            "MCP server context not initialized — "
            "call initialize() or initialize_from_handles() first"
        )
    return _context


def initialize(
    *,
    firm_root: Path,
    firm_id: str,
    employee_id: str,
) -> McpContext:
    """Open all handles from disk, validate the employee, install context."""
    manifest_path = firm_root / "mcp_clients.yaml"
    manifest = load_manifest(manifest_path)
    client = resolve_employee(manifest, employee_id)

    kg = KnowledgeGraph(firm_root / "knowledge.db")
    store = ProposalStore(firm_root / "proposals.db")
    identity = LocalIdentityResolver(firm_root / "identity.db")

    engine: BrainEngine = InMemoryEngine()
    engine.connect()
    _bootstrap_engine_from_wiki(engine, firm_root / "wiki")

    policy: Policy | None = None
    policy_path = firm_root / "protocols" / "permissions.md"
    if policy_path.exists():
        policy = load_policy(policy_path)

    obs_root = firm_root / ".observability"
    obs_root.mkdir(parents=True, exist_ok=True)

    ctx = McpContext(
        firm_root=firm_root,
        firm_id=firm_id,
        client=client,
        observability_root=obs_root,
        engine=engine,
        kg=kg,
        store=store,
        identity=identity,
        policy=policy,
    )
    install(ctx)
    return ctx


def initialize_from_handles(
    *,
    firm_root: Path,
    firm_id: str,
    client: ClientEntry,
    engine: BrainEngine,
    kg: KnowledgeGraph,
    store: ProposalStore,
    identity: IdentityResolver,
    observability_root: Path,
    policy: Policy | None = None,
) -> McpContext:
    """Install a context from pre-built handles. Used by tests and embedding hosts."""
    ctx = McpContext(
        firm_root=firm_root,
        firm_id=firm_id,
        client=client,
        observability_root=observability_root,
        engine=engine,
        kg=kg,
        store=store,
        identity=identity,
        policy=policy,
    )
    install(ctx)
    return ctx


def install(ctx: McpContext) -> None:
    """Replace the module-level context. Tools read from this slot."""
    global _context
    _context = ctx


def reset() -> None:
    """Clear the module-level context. Tests use this between cases."""
    global _context
    _context = None


# ---------- Engine bootstrap ----------


def _bootstrap_engine_from_wiki(engine: BrainEngine, wiki_root: Path) -> None:
    """Load every ``*.md`` under ``wiki_root`` into ``engine`` by path convention.

    Layout expected:

        wiki_root/firm/<domain>/<slug>.md
        wiki_root/personal/<employee_id>/<domain>/<slug>.md

    Anything that doesn't match is skipped silently — operator files
    (READMEs, drafts, etc.) shouldn't crash the server.
    """
    if not wiki_root.exists():
        return
    for md_file in wiki_root.rglob("*.md"):
        plane, employee_id = _plane_from_path(md_file, wiki_root)
        if plane is None:
            continue
        try:
            raw = md_file.read_text(encoding="utf-8")
            page = parse_page(raw)
        except (OSError, ValueError):
            continue
        try:
            engine.put_page(page, plane=plane, employee_id=employee_id)
        except ValueError:
            continue


def _plane_from_path(md_file: Path, wiki_root: Path) -> tuple[Plane | None, str | None]:
    try:
        rel = md_file.relative_to(wiki_root)
    except ValueError:
        return None, None
    parts = rel.parts
    if len(parts) < 2:
        return None, None
    if parts[0] == "firm":
        return "firm", None
    if parts[0] == "personal" and len(parts) >= 3:
        return "personal", parts[1]
    return None, None


# ---------- Tool registrations ----------


@mcp.tool()
def query(
    question: str,
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Hybrid search the firm or personal plane. Returns ranked page hits."""
    hits = query_tool(
        _ctx(),
        question=question,
        plane=plane,
        tier_floor=tier_floor,
        limit=limit,
    )
    return [hit.model_dump(mode="json") for hit in hits]


@mcp.tool()
def get_page(slug: str, plane: Plane = "firm") -> dict[str, Any] | None:
    """Fetch a single page by slug + plane. Returns None if missing or unreadable."""
    page = get_page_tool(_ctx(), slug=slug, plane=plane)
    if page is None:
        return None
    return page.model_dump(mode="json")


@mcp.tool()
def search(
    query: str,
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Keyword search with permission filtering. Returns ranked page hits."""
    hits = search_tool(
        _ctx(),
        query=query,
        plane=plane,
        tier_floor=tier_floor,
        limit=limit,
    )
    return [hit.model_dump(mode="json") for hit in hits]


@mcp.tool()
def get_entity(name: str) -> dict[str, Any] | None:
    """Fetch one canonical entity by name. Returns None if unknown."""
    entity = get_entity_tool(_ctx(), name=name)
    if entity is None:
        return None
    return entity.model_dump(mode="json")


@mcp.tool()
def get_triples(
    entity_name: str,
    direction: str = "outgoing",
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Triples involving ``entity_name``. Direction: outgoing / incoming / both."""
    if direction not in ("outgoing", "incoming", "both"):
        raise ValueError(f"direction must be outgoing|incoming|both, got {direction!r}")
    triples = get_triples_tool(
        _ctx(),
        entity_name=entity_name,
        direction=direction,  # type: ignore[arg-type]
        as_of=as_of,
    )
    return [t.model_dump(mode="json") for t in triples]


@mcp.tool()
def check_coherence(
    subject: str,
    predicate: str,
    new_object: str,
    new_tier: Tier = DEFAULT_TIER,
) -> list[dict[str, Any]]:
    """Preview coherence warnings for a proposed triple. Non-mutating."""
    warnings = check_coherence_tool(
        _ctx(),
        subject=subject,
        predicate=predicate,
        new_object=new_object,
        new_tier=new_tier,
    )
    return [w.model_dump(mode="json") for w in warnings]


@mcp.tool()
def compile_agent_context(
    role: str,
    task: str,
    attendees: list[str],
    plane: Plane = "firm",
    tier_floor: Tier | None = None,
    as_of: date | None = None,
    render: bool = False,
) -> dict[str, Any] | str:
    """Compile a distilled context package for a workflow task."""
    result = compile_agent_context_tool(
        _ctx(),
        role=role,
        task=task,
        attendees=attendees,
        plane=plane,
        tier_floor=tier_floor,
        as_of=as_of,
        render=render,
    )
    if isinstance(result, str):
        return result
    return result.model_dump(mode="json")


@mcp.tool()
def sql_query_readonly(
    query: str,
    params: list[Any] | None = None,
    row_limit: int = 1000,
) -> list[dict[str, Any]]:
    """Read-only SQL over the KG. Gated behind REVIEW scope — raw graph access."""
    return sql_query_readonly_tool(
        _ctx(),
        query=query,
        params=params,
        row_limit=row_limit,
    )


@mcp.tool()
def create_proposal(
    target_entity: str,
    facts: list[dict[str, Any]],
    source_report_path: str,
    target_plane: Plane = "firm",
    target_employee_id: str | None = None,
    target_scope: str = "public",
) -> dict[str, Any]:
    """Stage a new proposal for review. Requires PROPOSE scope."""
    proposal = create_proposal_tool(
        _ctx(),
        target_entity=target_entity,
        facts=facts,
        source_report_path=source_report_path,
        target_plane=target_plane,
        target_employee_id=target_employee_id,
        target_scope=target_scope,
    )
    return proposal.model_dump(mode="json")


@mcp.tool()
def list_proposals(
    status: str | None = None,
    target_plane: Plane | None = None,
    target_entity: str | None = None,
) -> list[dict[str, Any]]:
    """List proposals — all, or filtered by status / plane / entity."""
    if status is not None and status not in ("pending", "approved", "rejected"):
        raise ValueError(f"status must be pending|approved|rejected, got {status!r}")
    proposals = list_proposals_tool(
        _ctx(),
        status=status,  # type: ignore[arg-type]
        target_plane=target_plane,
        target_entity=target_entity,
    )
    return [p.model_dump(mode="json") for p in proposals]


@mcp.tool()
def approve_proposal(proposal_id: str, rationale: str) -> dict[str, Any]:
    """Approve a pending proposal. Requires REVIEW scope + non-empty rationale."""
    proposal = approve_proposal_tool(
        _ctx(),
        proposal_id=proposal_id,
        rationale=rationale,
    )
    return proposal.model_dump(mode="json")


@mcp.tool()
def reject_proposal(proposal_id: str, rationale: str) -> dict[str, Any]:
    """Reject a pending proposal. Requires REVIEW scope + non-empty rationale."""
    proposal = reject_proposal_tool(
        _ctx(),
        proposal_id=proposal_id,
        rationale=rationale,
    )
    return proposal.model_dump(mode="json")


@mcp.tool()
def reopen_proposal(proposal_id: str, rationale: str) -> dict[str, Any]:
    """Reopen a rejected proposal. Requires REVIEW scope + non-empty rationale."""
    proposal = reopen_proposal_tool(
        _ctx(),
        proposal_id=proposal_id,
        rationale=rationale,
    )
    return proposal.model_dump(mode="json")


@mcp.tool()
def merge_entities(source: str, target: str, rationale: str) -> dict[str, Any]:
    """Rewrite every triple using ``source`` to use ``target`` instead."""
    result = merge_entities_tool(
        _ctx(),
        source=source,
        target=target,
        rationale=rationale,
    )
    return result.model_dump(mode="json")


# ---------- CLI entrypoint ----------


app = typer.Typer(add_completion=False)


@app.command()
def main(
    firm_root: Annotated[
        Path,
        typer.Option("--firm-root", help="Root directory of the firm's data"),
    ],
    firm_id: Annotated[
        str,
        typer.Option("--firm-id", help="Firm identifier (used in observability)"),
    ],
    employee_id: Annotated[
        str,
        typer.Option(
            "--employee-id",
            help="Which employee this server speaks for (must be in mcp_clients.yaml)",
        ),
    ],
) -> None:
    """Launch the MCP server for a single firm + employee over stdio."""
    initialize(firm_root=firm_root, firm_id=firm_id, employee_id=employee_id)
    mcp.run()


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    app()
