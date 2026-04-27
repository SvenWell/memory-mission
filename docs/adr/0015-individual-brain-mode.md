---
type: ADR
id: "0015"
title: "Individual brain mode + simple write policy"
status: active
date: 2026-04-27
---

## Context

The substrate that ships through Step 18 is built around a multi-employee
firm: scope on every triple, tier on every page, two-plane split, review-
gated promotion, federated cross-employee detector, fail-closed visibility.
That's load-bearing for regulated firms — and exactly the wrong shape for a
single human dogfooding their own personal agent.

Sven's Hermes agent (a personal operating runtime) reviewed the substrate
on 2026-04-27 and produced a critique with a clean architectural reframe
(see project memory `project_individual_brain_architecture.md` and the
ongoing `project_hermes_feedback_log.md`):

1. The next product shape is **Memory Mission Individual** — durable
   backend for one agent serving one person.
2. **Operating memory** (current truth, threads, commitments, project
   pages, identity) and **evidence memory** (MemPalace recall + citations)
   are different roles. Don't replace MemPalace; wrap it.
3. Memory should be an **agent boot substrate**, not a tool. Inject a
   compact, task-hint-aware context at agent spin-up — don't make the
   model discover memory ad-hoc through tool calls.
4. **Skills (Hermes) and state (Memory Mission) are symbiotic.** Hermes
   owns procedures. Memory Mission owns "what is true / current /
   relevant." Three integration points: skill grounding, skill outcome
   capture, skill evolution signal. Don't build a second skill engine
   inside Memory Mission.

Two facts make this immediately implementable:

- **The substrate already ships everything Individual needs.**
  `PersonalMemoryBackend` Protocol (ADR-0004), per-employee KG
  (ADR-0013), per-employee identity resolver, MemPalaceAdapter — all
  exist. Single-employee operation is a runtime mode, not a fork.
- **The firm-plane review gate is the wrong write policy for an
  individual loop.** A user saying "remember this" or correcting their
  agent shouldn't go through `create_proposal` → `review-proposals`. The
  proposal pipeline is governance for *firm* truth; for *personal*
  truth, the conversational act itself is the gate.

This ADR names the runtime mode, the surface that makes it the agent's
boot substrate, and the personal-plane write policy that drops the
proposal gate while keeping provenance mandatory.

## Decision

**Memory Mission supports an Individual mode that runs the existing
substrate in single-employee form, with a separate write policy on the
personal plane and a new boot-context surface designed for agent
launch-time injection.**

Concretely:

### 1. Single-employee runtime mode

Same package, runtime config:

- Two new env vars: `MM_PROFILE` (the user / employee id) and
  `MM_ROOT` (the on-disk root, default `~/.memory-mission`).
- When `MM_PROFILE` is set without an explicit firm, the runtime
  defaults `firm_id = MM_PROFILE` and exposes only personal-plane
  surfaces.
- Firm-only features (federated detector, review-proposals workflow,
  firm-plane MCP tools) are **not loaded** in Individual mode. They're
  not removed — they're dormant. A future "promote to firm" path is
  preserved by keeping the substrate identical.

### 2. Boot-context primitive

New module `src/memory_mission/synthesis/individual_boot.py`:

```python
def compile_individual_boot_context(
    *,
    user_id: str,
    agent_id: str,
    backend: PersonalMemoryBackend,
    kg: PersonalKnowledgeGraph,
    engine: BrainEngine | None = None,
    identity_resolver: IdentityResolver | None = None,
    task_hint: str | None = None,
    token_budget: int = 4000,
    as_of: datetime | None = None,
) -> IndividualBootContext:
    ...
```

Returns a frozen `IndividualBootContext` aggregating:

- **`active_threads`** — currently-true threads in `working` /
  `in_progress` state, sorted by recency.
- **`commitments`** — open commitments with `due_by` / `status` /
  `source`.
- **`preferences`** — durable preferences (reply style, tooling
  defaults, no-go lists).
- **`recent_decisions`** — last N tier=`decision` pages within a
  recency window, with the operator action recorded.
- **`relevant_entity_state`** — top-k entities ranked by mention
  frequency × recency, biased by `task_hint` when provided.
- **`project_status`** — currently-true `(project, status, *)`
  triples per project page.

`render()` returns markdown structured for system-prompt injection.
Token-budgeted: oldest / least-recent / lowest-confidence drops first
when the budget is tight. Default-on at every agent spin-up.

This is **distinct from** `synthesis/compile.py::compile_agent_context`,
which compiles a per-task briefing for one workflow. The boot version
is multi-aspect and lifecycle-aware (boot, not call-time).

### 3. Working-memory pages restored

Add `personal_brain/working/` back as first-class structured pages.
Frontmatter uses `domain: concepts` (a CORE_DOMAINS member — the
substrate's MECE registry is intentionally locked) plus an `extra`
discriminator `type: working_memory` (or `type: project`,
`type: thread`, etc. for narrower variants). This is the same
extras-based convention the venture overlay uses for
`type: portfolio_company` and `type: deal`.

The pages round-trip through `parse_page` / `render_page`, carry
provenance, support temporal validity, and live in the same
markdown-with-frontmatter format every other Memory Mission page
uses. Grep-able. Editable in Obsidian.

The boot-context compiler keys on these `extra` markers — e.g.
project-status aggregation walks pages where `domain=concepts` and
`extra.type=project` and joins the currently-true `status` triple
on the slug.

### 4. Hermes-scoped MCP surface (subset of Step 18)

A separate FastMCP server config exposes only the individual-loop
tools:

- `memory.search_recall(query)` → MemPalace hybrid recall + citations.
- `memory.get_working_context(task_hint?)` → on-demand boot-context
  recompile.
- `memory.list_active_threads(filter?)` / `memory.upsert_thread_status(...)`.
- `memory.record_preference(preference, source)` /
  `memory.record_commitment(commitment, due_by?, source)`.
- `memory.resolve_entity(name_or_identifier)` /
  `memory.query_entity(entity, as_of?)`.

No `create_proposal`. No `review-proposals`. No firm-plane tools. The
existing Step 18 server stays untouched for firm deployments; the
Individual server is a new entrypoint that reuses the same substrate
bindings.

### 5. Personal-plane simple write policy

Personal-plane writes drop the proposal gate:

- **Triggers that allow direct write:** user explicitly says
  "remember this" / "note that"; user corrects a prior fact; agent
  records a decision after operator confirmation; agent records the
  outcome of a completed durable workflow.
- **Provenance still mandatory.** Every triple carries `source_closet`
  + `source_file` (or an explicit `conversational:<session_id>` marker
  when the source is the chat itself). Every page records the
  operator-confirmed event that produced it.
- **Bayesian corroboration still applies.** Repeated assertions of the
  same fact still corroborate via Noisy-OR.
- **Coherence checks still apply.** Conflicting writes (predicate,
  object) still surface a `CoherenceWarning`, but in Individual mode
  the warning is presented to the user inline (in-conversation),
  not routed to a separate review queue.

Firm-plane writes continue to require the proposal gate (Step 10
unchanged). Mixed deployments (an Individual user who *also* belongs
to a firm) get both behaviors based on `target_plane`.

### 6. Hermes-seed migration adapter

A one-shot migration utility that reads existing Hermes memory dumps
and writes the stable subset into the per-user personal KG + project
pages, with provenance pointing at a `hermes-seed-<YYYY-MM-DD>`
closet. Native Hermes memory is **demoted to a bootstrap card** —
just enough to point future agent launches at the Individual root;
the operating truth lives in Memory Mission.

## Consequences

**Positive:**

- **Dogfood loop ships immediately.** Sven points his Hermes at the
  new backend; the eval Hermes named (resume threads without
  re-explanation, distinguish active vs parked, exact preferences
  without overfitting, project state across surfaces, "what were we
  doing with X?" with provenance, prep before touching a repo)
  becomes the scorecard.
- **No fork.** Same `memory_mission` package. Same tests. Future
  promote-to-firm path preserved.
- **MCP surface is plane-correct.** Individual server can't
  accidentally expose firm tools to a user without firm context.
- **Boot substrate framing aligns with the agent+terminal thesis**
  (`project_agent_terminal_thesis.md`): inject structure once at
  boot, then let the agent grep + read directly.

**Negative / accepted:**

- **Two MCP servers to maintain** (firm via Step 18; individual via
  the new entrypoint). Acceptable: the Individual server is a thin
  re-export that selects a tool subset.
- **Two write policies in one substrate.** Personal-plane writes go
  direct; firm-plane writes go through the proposal pipeline. Code
  paths are clearly separated by plane; the risk is human confusion,
  not structural ambiguity.
- **Working-memory pages re-introduce a layer that ADR-0004 deleted.**
  The deletion was correct *as cleanup of dead code*; this restoration
  is a *first-class* re-introduction with tests + provenance + page
  parser round-trip. Different shape, same name space.

**Neutral:**

- **MemPalace public-API hardening is still tech debt** — the adapter
  uses `palace.get_collection`, `build_closet_lines`, and an
  `MM_SOURCE_ID:` marker. Hermes flagged this. Not a blocker for
  Individual mode; address as the adapter matures.

## Alternatives considered

- **Fork into `memory_mission_individual`.** Rejected. Doubles
  maintenance, breaks the "same substrate, different scope" invariant
  that's been load-bearing since ADR-0002 (two-plane split). The
  promote-to-firm path becomes harder.
- **Keep the proposal gate for personal-plane writes.** Rejected. The
  review gate is governance for *firm* truth — multiple humans with
  different scopes deciding what becomes shared. For an individual
  loop the gate is friction without upside; the conversational act
  is the review.
- **Keep memory access tool-only (no boot-context injection).**
  Rejected. Hermes' point lands: the agent shouldn't have to discover
  memory mid-session via ad-hoc tool calls when a compact boot
  context would prime the right moves up front. Keep the tools — they
  remain useful for mid-session lookups — but make boot-context the
  default contract.
- **Build an Individual-specific skill engine.** Rejected. Hermes (the
  agent runtime) owns procedures; Memory Mission owns state. Three
  integration points (grounding, outcome capture, evolution signal)
  let Hermes consume Memory Mission without Memory Mission becoming
  an agent runtime itself.

## Verification

- Plain `make check` passes; new module ships with tests covering
  empty boot context, token-budget eviction, task-hint biasing,
  multi-aspect aggregation.
- A Hermes spin-up against a synthetic Sven-shaped corpus produces a
  bounded `IndividualBootContext` with all six aspects populated.
- The Hermes-scoped MCP surface lists exactly the individual tools
  (no firm tools enumerated) when the server is launched in
  Individual mode.
- Personal-plane writes via the simple-write-policy path produce
  triples with provenance set and no proposal-store entry.
- Migration adapter against a synthetic Hermes seed produces personal
  KG triples + project pages whose provenance points at the
  `hermes-seed-<date>` closet.

## Related

- `project_individual_brain_architecture.md` — the framing memory
  driving this ADR.
- `project_hermes_feedback_log.md` — ongoing log of Hermes critiques;
  the 2026-04-27 entry sourced this work.
- ADR-0002 (two-plane split) — Individual mode preserves the split
  invariant; firm tools just stay dormant.
- ADR-0004 (MemPalace as personal substrate) — extended, not
  superseded. MemPalace stays as evidence memory; this ADR adds the
  operating-memory layer above it.
- ADR-0013 (personal-plane temporal KG) — the structural primitive
  Individual mode runs on top of.
- ADR-0014 — agent+terminal compatibility constraint (planned). The
  boot-substrate framing here is consistent with that thesis: inject
  structure once at boot, let the agent grep + read directly
  thereafter.
