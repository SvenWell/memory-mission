"""Tests for the MCP surface (step 18) — auth, context, tools, server registration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp.auth import (
    AuthError,
    ClientEntry,
    Scope,
    load_manifest,
    require_scope,
    resolve_employee,
)
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
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.memory.pages import Page, PageFrontmatter
from memory_mission.promotion.proposals import ProposalStore

# ---------- Manifest fixtures ----------


ALL_SCOPES: frozenset[Scope] = frozenset({Scope.READ, Scope.PROPOSE, Scope.REVIEW})


def _write_manifest(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------- Context fixture ----------


@pytest.fixture
def context(tmp_path: Path) -> Iterator[McpContext]:
    engine = InMemoryEngine()
    engine.connect()
    kg = KnowledgeGraph(tmp_path / "kg.sqlite3")
    store = ProposalStore(tmp_path / "proposals.sqlite3")
    identity = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    obs_root = tmp_path / ".observability"
    obs_root.mkdir()
    client = ClientEntry(employee_id="alice@acme.com", scopes=ALL_SCOPES)
    ctx = McpContext(
        firm_root=tmp_path,
        firm_id="acme",
        client=client,
        observability_root=obs_root,
        engine=engine,
        kg=kg,
        store=store,
        identity=identity,
        policy=None,
    )
    try:
        yield ctx
    finally:
        engine.disconnect()
        kg.close()
        store.close()
        identity.close()


def _read_only_context(context: McpContext, scopes: frozenset[Scope]) -> McpContext:
    client = ClientEntry(employee_id=context.employee_id, scopes=scopes)
    return McpContext(
        firm_root=context.firm_root,
        firm_id=context.firm_id,
        client=client,
        observability_root=context.observability_root,
        engine=context.engine,
        kg=context.kg,
        store=context.store,
        identity=context.identity,
        policy=context.policy,
    )


# ---------- Manifest tests ----------


def test_manifest_loads_clients(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path / "mcp_clients.yaml",
        "alice@acme.com:\n  scopes: [read, propose, review]\nbob@acme.com:\n  scopes: [read]\n",
    )
    manifest = load_manifest(path)
    assert set(manifest.keys()) == {"alice@acme.com", "bob@acme.com"}
    assert manifest["alice@acme.com"].scopes == ALL_SCOPES
    assert manifest["bob@acme.com"].scopes == frozenset({Scope.READ})


def test_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "missing.yaml")


def test_manifest_empty_is_ok(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "mcp_clients.yaml", "")
    assert load_manifest(path) == {}


def test_manifest_rejects_unknown_scope(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path / "mcp_clients.yaml",
        "alice@acme.com:\n  scopes: [read, nonsense]\n",
    )
    with pytest.raises(ValueError, match="unknown scope"):
        load_manifest(path)


def test_manifest_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "mcp_clients.yaml", "- not\n- a\n- mapping\n")
    with pytest.raises(ValueError, match="top level"):
        load_manifest(path)


def test_resolve_employee_unknown_fails_closed() -> None:
    with pytest.raises(AuthError, match="not in MCP client manifest"):
        resolve_employee({}, "nobody@acme.com")


def test_require_scope_raises_when_missing() -> None:
    client = ClientEntry(employee_id="alice@acme.com", scopes=frozenset({Scope.READ}))
    with pytest.raises(AuthError, match="missing required scope: propose"):
        require_scope(client, Scope.PROPOSE)


def test_require_scope_silent_when_present() -> None:
    client = ClientEntry(employee_id="alice@acme.com", scopes=ALL_SCOPES)
    require_scope(client, Scope.REVIEW)  # no raise


# ---------- Context tests ----------


def test_context_require_scope(context: McpContext) -> None:
    context.require_scope(Scope.READ)  # no raise
    restricted = _read_only_context(context, frozenset({Scope.READ}))
    with pytest.raises(AuthError):
        restricted.require_scope(Scope.PROPOSE)


def test_context_tool_scope_opens_observability(context: McpContext) -> None:
    from memory_mission.observability.context import current_employee_id, current_firm_id

    with context.tool_scope():
        assert current_firm_id() == "acme"
        assert current_employee_id() == "alice@acme.com"


# ---------- Read tools ----------


def _put_firm_page(engine: InMemoryEngine, slug: str, title: str, body: str) -> None:
    page = Page(
        frontmatter=PageFrontmatter(
            title=title,
            slug=slug,
            domain="people",
            confidence=0.8,
        ),
        compiled_truth=body,
        timeline=[],
    )
    engine.put_page(page, plane="firm")


def test_query_tool_returns_hits_with_scope(context: McpContext) -> None:
    _put_firm_page(context.engine, "alice-smith", "Alice Smith", "Alice works at Acme Corp.")
    _put_firm_page(context.engine, "bob-jones", "Bob Jones", "Bob joined Initech last year.")

    hits = query_tool(context, question="Alice")
    slugs = [h.slug for h in hits]
    assert "alice-smith" in slugs


def test_query_tool_denies_without_read_scope(context: McpContext) -> None:
    ctx = _read_only_context(context, frozenset())
    with pytest.raises(AuthError):
        query_tool(ctx, question="alice")


def test_get_page_tool_returns_page(context: McpContext) -> None:
    _put_firm_page(context.engine, "acme", "Acme Corp", "A company.")
    page = get_page_tool(context, slug="acme")
    assert page is not None
    assert page.frontmatter.slug == "acme"


def test_get_page_tool_returns_none_for_missing(context: McpContext) -> None:
    assert get_page_tool(context, slug="does-not-exist") is None


def test_search_tool_returns_hits(context: McpContext) -> None:
    _put_firm_page(context.engine, "roadmap", "Roadmap", "Product plans for Q3.")
    hits = search_tool(context, query="roadmap")
    assert any(h.slug == "roadmap" for h in hits)


def test_get_entity_tool_returns_entity(context: McpContext) -> None:
    context.kg.add_entity("acme-corp", entity_type="company")
    entity = get_entity_tool(context, name="acme-corp")
    assert entity is not None
    assert entity.name == "acme-corp"


def test_get_entity_tool_returns_none_for_missing(context: McpContext) -> None:
    assert get_entity_tool(context, name="nonexistent") is None


def test_get_triples_tool_outgoing(context: McpContext) -> None:
    context.kg.add_triple(
        subject="alice",
        predicate="works_at",
        obj="acme",
        source_closet="firm",
        source_file="fixture",
    )
    triples = get_triples_tool(context, entity_name="alice")
    assert len(triples) == 1
    assert triples[0].predicate == "works_at"


def test_check_coherence_tool_flags_conflict(context: McpContext) -> None:
    context.kg.add_triple(
        subject="alice",
        predicate="works_at",
        obj="acme",
        source_closet="firm",
        source_file="fixture",
    )
    warnings = check_coherence_tool(
        context,
        subject="alice",
        predicate="works_at",
        new_object="initech",
    )
    assert len(warnings) == 1
    assert warnings[0].conflicting_object == "acme"


def test_sql_query_readonly_tool_requires_review_scope(context: McpContext) -> None:
    ctx = _read_only_context(context, frozenset({Scope.READ, Scope.PROPOSE}))
    with pytest.raises(AuthError, match="review"):
        sql_query_readonly_tool(ctx, query="SELECT 1 AS n")


def test_sql_query_readonly_tool_runs(context: McpContext) -> None:
    rows = sql_query_readonly_tool(context, query="SELECT COUNT(*) AS n FROM entities")
    assert rows == [{"n": 0}]


def test_sql_query_readonly_tool_rejects_writes(context: McpContext) -> None:
    with pytest.raises(ValueError, match="SELECT or WITH"):
        sql_query_readonly_tool(context, query="DROP TABLE entities")


def test_compile_agent_context_tool_structured(context: McpContext) -> None:
    result = compile_agent_context_tool(
        context,
        role="meeting-prep",
        task="brief on alice",
        attendees=["alice"],
    )
    assert hasattr(result, "attendees")
    assert result.role == "meeting-prep"  # type: ignore[union-attr]


def test_compile_agent_context_tool_rendered(context: McpContext) -> None:
    rendered = compile_agent_context_tool(
        context,
        role="meeting-prep",
        task="brief on alice",
        attendees=["alice"],
        render=True,
    )
    assert isinstance(rendered, str)
    assert "meeting-prep" in rendered


# ---------- Write tools ----------


def _identity_fact(name: str) -> dict[str, object]:
    return {
        "kind": "identity",
        "confidence": 0.95,
        "support_quote": f"mention of {name}",
        "entity_name": name,
        "entity_type": "person",
    }


def _relationship_fact(subject: str, predicate: str, obj: str) -> dict[str, object]:
    return {
        "kind": "relationship",
        "confidence": 0.9,
        "support_quote": f"{subject} {predicate} {obj}",
        "subject": subject,
        "predicate": predicate,
        "object": obj,
    }


def test_create_proposal_tool_stages_proposal(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice")],
        source_report_path="/tmp/report.json",
    )
    assert proposal.status == "pending"
    assert proposal.target_entity == "alice"


def test_create_proposal_tool_requires_propose_scope(context: McpContext) -> None:
    ctx = _read_only_context(context, frozenset({Scope.READ}))
    with pytest.raises(AuthError, match="propose"):
        create_proposal_tool(
            ctx,
            target_entity="alice",
            facts=[_identity_fact("alice")],
            source_report_path="/tmp/report.json",
        )


def test_list_proposals_tool_filters_by_status(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice")],
        source_report_path="/tmp/r.json",
    )
    pending = list_proposals_tool(context, status="pending")
    assert any(p.proposal_id == proposal.proposal_id for p in pending)
    assert list_proposals_tool(context, status="approved") == []


def test_approve_proposal_tool_promotes(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice"), _relationship_fact("alice", "works_at", "acme")],
        source_report_path="/tmp/r.json",
    )
    approved = approve_proposal_tool(
        context,
        proposal_id=proposal.proposal_id,
        rationale="looks right",
    )
    assert approved.status == "approved"
    assert approved.reviewer_id == "alice@acme.com"
    # fact landed on the KG
    assert context.kg.get_entity("alice") is not None


def test_approve_proposal_tool_requires_review_scope(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice")],
        source_report_path="/tmp/r.json",
    )
    ctx = _read_only_context(context, frozenset({Scope.READ, Scope.PROPOSE}))
    with pytest.raises(AuthError, match="review"):
        approve_proposal_tool(
            ctx,
            proposal_id=proposal.proposal_id,
            rationale="looks right",
        )


def test_reject_proposal_tool(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice")],
        source_report_path="/tmp/r.json",
    )
    rejected = reject_proposal_tool(
        context,
        proposal_id=proposal.proposal_id,
        rationale="source looks unreliable",
    )
    assert rejected.status == "rejected"


def test_reopen_proposal_tool_flips_rejected_to_pending(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[_identity_fact("alice")],
        source_report_path="/tmp/r.json",
    )
    reject_proposal_tool(
        context,
        proposal_id=proposal.proposal_id,
        rationale="need more evidence",
    )
    reopened = reopen_proposal_tool(
        context,
        proposal_id=proposal.proposal_id,
        rationale="found the corroborating source",
    )
    assert reopened.status == "pending"


def test_merge_entities_tool_rewrites_triples(context: McpContext) -> None:
    context.kg.add_triple(
        subject="a-smith",
        predicate="works_at",
        obj="acme",
        source_closet="firm",
        source_file="fixture",
    )
    result = merge_entities_tool(
        context,
        source="a-smith",
        target="alice-smith",
        rationale="same person — shared email identifier",
    )
    assert result.triples_rewritten >= 1
    assert get_triples_tool(context, entity_name="a-smith") == []


def test_merge_entities_tool_empty_rationale_raises(context: McpContext) -> None:
    with pytest.raises(ValueError, match="rationale"):
        merge_entities_tool(context, source="a", target="b", rationale="")


# ---------- Round-trip ----------


def test_full_round_trip_create_approve_query(context: McpContext) -> None:
    proposal = create_proposal_tool(
        context,
        target_entity="alice",
        facts=[
            _identity_fact("alice"),
            _relationship_fact("alice", "works_at", "acme"),
        ],
        source_report_path="/tmp/r.json",
    )
    listed = list_proposals_tool(context, status="pending")
    assert any(p.proposal_id == proposal.proposal_id for p in listed)

    approved = approve_proposal_tool(
        context,
        proposal_id=proposal.proposal_id,
        rationale="verified by Alice herself",
    )
    assert approved.status == "approved"

    triples = get_triples_tool(context, entity_name="alice")
    assert any(t.predicate == "works_at" and t.object == "acme" for t in triples)


# ---------- Server registration smoke test ----------


def test_server_registers_fourteen_tools() -> None:
    """FastMCP should register every tool decorated in server.py."""
    from memory_mission.mcp import server

    tools = server.mcp._tool_manager._tools
    expected = {
        "query",
        "get_page",
        "search",
        "get_entity",
        "get_triples",
        "check_coherence",
        "compile_agent_context",
        "sql_query_readonly",
        "create_proposal",
        "list_proposals",
        "approve_proposal",
        "reject_proposal",
        "reopen_proposal",
        "merge_entities",
    }
    registered = set(tools.keys())
    assert registered == expected, f"missing={expected - registered} extra={registered - expected}"


def test_server_initialize_from_handles(tmp_path: Path) -> None:
    """initialize_from_handles installs a context accessible to tools."""
    from memory_mission.mcp import server

    engine = InMemoryEngine()
    engine.connect()
    kg = KnowledgeGraph(tmp_path / "kg.sqlite3")
    store = ProposalStore(tmp_path / "proposals.sqlite3")
    identity = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    obs_root = tmp_path / ".observability"
    obs_root.mkdir()
    client = ClientEntry(employee_id="alice@acme.com", scopes=ALL_SCOPES)

    try:
        ctx = server.initialize_from_handles(
            firm_root=tmp_path,
            firm_id="acme",
            client=client,
            engine=engine,
            kg=kg,
            store=store,
            identity=identity,
            observability_root=obs_root,
        )
        assert ctx.employee_id == "alice@acme.com"
        assert server._ctx() is ctx
    finally:
        server.reset()
        engine.disconnect()
        kg.close()
        store.close()
        identity.close()


def test_server_ctx_raises_when_uninitialized() -> None:
    from memory_mission.mcp import server

    server.reset()
    with pytest.raises(RuntimeError, match="not initialized"):
        server._ctx()
