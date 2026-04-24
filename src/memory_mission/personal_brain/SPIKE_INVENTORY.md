# Personal-plane call-site inventory (MemPalace spike)

> Scratch notes for the `SvenWell/mempalace-spike` decision.
> Not committed to `main`. See ADR-0004.

## Executive finding

**`src/memory_mission/personal_brain/` is imported by nothing in `src/`.**
Only `tests/test_personal_brain.py` references its exports. The package
is a designed-but-unwired layer from Step 12.

That is itself a decision point: regardless of whether we adopt MemPalace,
`personal_brain/` is dead weight in production today. It can be either:

- **Deleted outright** (along with its 30 tests) — lose nothing from
  production code, lose the working/episodic/lessons/preferences layers
  we planned but never wired.
- **Replaced by MemPalace** — MemPalace's `palace`, `palace_graph`,
  `knowledge_graph`, `searcher`, `backends`, `mcp_server` modules
  subsume what `personal_brain/` was meant to cover, at the cost of
  an external dependency.
- **Kept as-is** — leave for a future wiring step. Costs 857 LOC of
  unused code staying in tree.

## How the personal plane actually works today

The personal plane is live in production, implemented via **plane-scoped
access on the shared engine + KG**, not via `personal_brain/`:

- **Pages:** `src/memory_mission/memory/engine.py` — `BrainEngine.get_page`,
  `put_page`, `list_pages`, `search`, `query` all accept a `plane` kwarg.
  `plane="personal"` routes to `personal/<employee_id>/` paths via
  `memory/schema.py:plane_root`.
- **KG triples:** `src/memory_mission/memory/knowledge_graph.py` — triples
  on the same shared `firm/knowledge.db` are scope-tagged with
  `source_closet="personal/<employee_id>"`. Personal triples are
  distinguished from firm triples by provenance, not by storage location.
- **Staging:** `src/memory_mission/ingestion/staging.py` — `StagingWriter`
  writes to `staging/personal/<employee>/<source>/` when
  `target_plane="personal"`.
- **Extraction:** `src/memory_mission/extraction/ingest.py` — produces
  `ExtractionReport` with `target_plane="personal"` for personal-plane
  extractions.
- **Compile:** `src/memory_mission/synthesis/compile.py` — `compile_agent_context`
  accepts `plane="personal"` + `employee_id="..."` to scope to one
  employee's neighborhood.
- **Promotion:** `src/memory_mission/promotion/proposals.py` — `Proposal`
  has `target_plane: Plane` + `target_employee_id: str | None`. Personal
  promotions set both; firm promotions set plane=firm with no employee_id.
- **MCP:** `src/memory_mission/mcp/tools.py` — the MCP read tools
  (`query_tool`, `get_page_tool`, `search_tool`, `compile_agent_context_tool`)
  route `plane="personal"` through the shared engine with the viewer's
  `employee_id`.

**No code path today goes through `personal_brain/*.py`.** The test suite
(`tests/test_personal_brain.py`, ~30 tests) verifies that the working /
episodic / lessons / preferences writers behave as designed, but nothing
calls them for real work.

## What MemPalace 3.3.2 exposes

Top-level modules relevant to adoption:

| Module | Role |
|---|---|
| `mempalace.palace` | Closet / drawer API — verbatim conversation storage + retrieval |
| `mempalace.palace_graph` | Knowledge graph (lineage of our own KG via Step 6b) |
| `mempalace.knowledge_graph` | KG ops |
| `mempalace.searcher` | Hybrid search (BM25 + vector via ChromaDB backend default) |
| `mempalace.backends` | Pluggable storage — ChromaDB default, others droppable |
| `mempalace.mcp_server` | Their 29 MCP tools (vs our 14) |
| `mempalace.general_extractor` | Extraction — we own this via host LLM, would skip |
| `mempalace.entity_registry` | Identity-like feature |
| `mempalace.config` | Config loader |
| `mempalace.sources/` | Source-type adapters |
| `mempalace.diary_ingest`, `mempalace.convo_miner` | Claude Code conversation mining |

Benchmark: **96.6% R@5 on LongMemEval raw** (no LLM, verbatim storage).

## The three spike questions, reframed

Given `personal_brain/` has no production callers, the spike is narrower
than I first wrote it:

### Q1: Should we use MemPalace as the personal-plane retrieval substrate?

Today: `BrainEngine.query(plane="personal", employee_id="alice")` does
keyword + vector hybrid over markdown pages scoped to Alice's directory.

With MemPalace: `MemPalaceInstance(employee_id="alice").query(...)` does
the same thing with their tuned hybrid search — which benchmarks at
96.6% R@5 on LongMemEval, better than our `HashEmbedder` stub.

**Question:** is MemPalace's retrieval worth the external dep + adapter
surface?

### Q2: Should we delete `personal_brain/` outright?

Since nothing calls it, we're paying 857 LOC + 30 test LOC for a layer
that isn't wired. Independently of the MemPalace question, this is dead
code in V1.

**Question:** delete now, or wait to see if dogfood surfaces a need for
working / episodic / lessons / preferences primitives?

### Q3: Should we adopt MemPalace's MCP tools?

Their 29 MCP tools overlap partially with our 14. Adopting means either
wrapping their server (complex) or mapping their tools into our server
shape (moderate).

**Question:** is the additional tool surface worth the integration cost,
or do we keep our 14 and ignore theirs?

## Decision matrix

| Option | personal_brain/ | MemPalace retrieval | MemPalace MCP | Net effect |
|---|---|---|---|---|
| **A: Full adoption** | Delete (857 LOC gone) | Use it | Wrap / map | Most code deleted, biggest external dep |
| **B: Retrieval only** | Delete (857 LOC gone) | Use it | Keep ours | Clean delete + benchmark inherit |
| **C: Delete only** | Delete (857 LOC gone) | Keep ours | Keep ours | Smallest change — just dead-code cleanup |
| **D: Keep everything** | Leave | Keep ours | Keep ours | Status quo |

Previously-assumed "A or B or defer" was too narrow. Option C (plain
dead-code cleanup with no MemPalace) is actually the cheapest and
probably the right first move before we consider retrieval substitution.

## Recommended spike sequence

1. **Delete `personal_brain/` unconditionally.** Independent of MemPalace.
   Dead code in production today. 30 tests go with it. ~857 LOC gone.
2. **Measure whether any dogfood signal surfaces a need** for the working
   / episodic / lessons / preferences primitives we just deleted.
   (Expected: probably not in the first 2 weeks.)
3. **Revisit MemPalace retrieval adoption** (Q1) only if:
   - Dogfood reveals our `HashEmbedder` stub isn't good enough AND
   - A pilot firm provides data we can benchmark against our stub vs
     MemPalace's hybrid search

This keeps the spike bounded, delivers immediate value (dead-code
cleanup), and keeps the MemPalace decision deferrable with a clear
trigger.

## Open questions to resolve before end of spike week

- [ ] Do we have any skill or docs reference to `personal_brain` exports
      that would break on delete? (Check `skills/*.md`, recipes, docs.)
- [ ] Does the forthcoming capability-based connector manifest (P1)
      need a personal-plane writer primitive that `personal_brain/`
      provided? (Prediction: no — connectors write to `staging/`, not
      direct to personal-brain layers.)
- [ ] Is the 30-test suite covering anything we'd regress if removed?
      (Expected: no — the tests cover the layer's internals, which we're
      removing.)
