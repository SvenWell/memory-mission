---
type: ADR
id: "0003"
title: "MCP as the multi-agent access surface"
status: active
date: 2026-04-23
---

## Context

Through V1, every host agent integrated with Memory Mission by importing the Python package and holding direct handles to `KnowledgeGraph` + `BrainEngine` + `ProposalStore` + `Policy`. This works for a single Python process running the user's agent, but it does not scale past that:

1. **No protocol boundary.** Codex, Cursor, Claude Desktop, Hermes, and remote agents all need their own bespoke Python adapter. Each adapter could drift from the primitives — the whole surface has to be re-exposed every time.
2. **Multi-user access is stuck in one process.** Bob's agent cannot read the firm KG from a different machine without either (a) Bob's agent running in the same Python process as Alice's, or (b) Memory Mission becoming a network service we haven't designed.
3. **No standard tool registry.** Host agents have to discover what's callable by reading source. There's no self-describing surface.

The original Hermes adapter stub at `src/memory_mission/runtime/hermes_adapter.py` captured all three with a TODO: "Expose employee memory + firm wiki via MCP tools. Honor access control metadata when routing queries." The TODO is load-bearing; the implementation is the whole point.

Two shapes of "add a protocol layer" are possible:

- A bespoke RPC (HTTP + JSON, gRPC, or similar).
- MCP — the Model Context Protocol, already spoken by Claude Desktop / Cursor / Codex / most agentic hosts in 2026.

## Decision

**Ship an MCP server at `src/memory_mission/mcp/` that wraps the existing engine + KG + promotion surfaces. One server process per employee. 14 tools total — 8 read, 6 write. Auth via a per-firm YAML manifest. Every mutating tool opens an `observability_scope` so audit trail coverage is complete over MCP, not just over the Python API.**

- New package: `src/memory_mission/mcp/{__init__, auth, context, tools, server, __main__}.py`.
- CLI: `python -m memory_mission.mcp --firm-root <path> --firm-id <id> --employee-id <id>`.
- Auth: `firm/mcp_clients.yaml` maps employee_id → `{scopes: [read, propose, review]}`. Unknown employees fail closed at startup; missing scopes fail closed per tool call.
- Tool reuse: every tool is a thin wrapper over existing primitives. No new domain logic lives in `mcp/tools.py`.
- Tests: 37 new, covering manifest loading, scope enforcement, each tool, full round-trip, and server registration. `tests/test_mcp_server.py`.

## Tool set

Eight read tools (scope: `read`):

1. `query(question, plane, tier_floor, limit)` — hybrid search
2. `get_page(slug, plane)` — one page by slug
3. `search(query, plane, tier_floor, limit)` — keyword search
4. `get_entity(name)` — one canonical entity
5. `get_triples(entity_name, direction, as_of)` — outgoing / incoming / both
6. `check_coherence(subject, predicate, new_object, new_tier)` — non-mutating preview
7. `compile_agent_context(role, task, attendees, plane, tier_floor, as_of, render)` — distilled package
8. `sql_query_readonly(query, params, row_limit)` — raw KG read (gated behind `review`)

Six write tools (scope: `propose` or `review`):

9. `create_proposal(target_entity, facts, source_report_path, target_plane, target_employee_id, target_scope)` — stage (scope: `propose`)
10. `list_proposals(status, target_plane, target_entity)` — list (scope: `propose`)
11. `approve_proposal(proposal_id, rationale)` — promote (scope: `review`)
12. `reject_proposal(proposal_id, rationale)` — reject (scope: `review`)
13. `reopen_proposal(proposal_id, rationale)` — reopen (scope: `review`)
14. `merge_entities(source, target, rationale)` — graph rewrite (scope: `review`)

`sql_query_readonly` sits at `review` scope because raw SQL is the one read surface that can enumerate the whole KG regardless of page-level permissions. Page / entity / triple lookups are routed through `can_read`; SQL isn't. Different guardrail.

## Options considered

- **Option A (chosen): MCP, one process per employee.** Pros: standard protocol, every major host agent speaks it, clean scope model (process identity = employee identity), minimal auth surface (YAML manifest at startup). Cons: one Python process per simultaneously-active employee. Fine — firms have tens of employees, not millions.
- **Option B: MCP, one shared process + per-call client_id.** Pros: process count stays at 1. Cons: introduces protocol-level auth (MCP doesn't standardise this yet), multiplies context-var complexity (one employee per call inside one scope), breaks the "observability_scope per employee" invariant.
- **Option C: Bespoke HTTP + JSON RPC.** Pros: familiar. Cons: every host agent writes its own adapter; we maintain a spec; we don't get the broad MCP client ecosystem for free.
- **Option D: gRPC.** Pros: typed wire format. Cons: operational weight (proto files, codegen pipelines) for no benefit over MCP in this context.
- **Option E: Defer — keep Python-only access.** Pros: no new work. Cons: the multi-user gap stays open; pilot firms with >1 agent-user can't integrate.

Option A is the right call because every host agent we care about already speaks MCP. Writing a bespoke protocol would duplicate effort the ecosystem has already absorbed.

## Consequences

- **Hermes adapter stub is superseded.** `src/memory_mission/runtime/hermes_adapter.py` is now a one-line pointer to `memory_mission.mcp`. Any host-specific adapter work happens on top of MCP, not around it.
- **Audit trail coverage is complete.** Before MCP, skills called primitives through `observability_scope`; but nothing stopped an adventurous host-agent author from calling primitives directly without scoping. MCP's tool-call boundary enforces scope at a higher level — every tool call opens one.
- **Permission model stays unchanged.** `can_read` / `can_propose` still decide what passes. MCP adds a coarser scope layer (`read` / `propose` / `review`) that gates access to the tool at all; the existing fine-grained policy still decides what pages / scopes a given employee sees.
- **Testability improved.** `server.initialize_from_handles()` lets tests inject pre-built engine + KG + store + identity resolver without CLI parsing. The same seam lets embedding hosts (Hermes, bespoke agent frameworks) drive the server in-process.
- **Bootstrap loader surfaces real ops limits.** `_bootstrap_engine_from_wiki` walks `wiki/firm/**/*.md` and `wiki/personal/<emp>/**/*.md` and loads everything into the `InMemoryEngine`. Fine for hundreds of pages; not for a million. Re-evaluate when dogfooding shows it.
- **MCP manifest is per-firm metadata.** Lives at `firm/mcp_clients.yaml`. Firms without MCP clients can omit the file; the server refuses to start but other integrations are unaffected.

## Re-evaluation triggers

- **Process count becomes a problem.** If a firm runs dozens of simultaneous MCP sessions and Python process overhead matters, revisit Option B with proper protocol-level auth. Not before.
- **MCP protocol evolves.** The `mcp` Python SDK is at 1.x. If a breaking change lands, pin a version before upgrading.
- **Bootstrap loader is a bottleneck.** If startup time on a pilot firm is >5s, replace the wiki walk with a lazy-loading backend or an sqlite-backed engine.
- **A host agent wants admin tools** (federated detector, stats, eval corpus export). Add a fourth scope — `admin` — and expose those via MCP. Deferred per the post-V1 roadmap.
- **Cross-firm use case.** Today one MCP process = one firm. If a platform wants to multiplex firms, that's a different architecture; revisit deliberately.
