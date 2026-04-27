---
type: ADR
id: "0013"
title: "Personal-plane temporal KG alongside MemPalace"
status: active
date: 2026-04-26
---

## Context

ADR-0004 adopted MemPalace as the personal-substrate retrieval layer
behind `PersonalMemoryBackend` and deleted four unwired
`personal_brain/` layers (`working/` / `episodic/` / `lessons/` /
`preferences/`) that had zero production callers. That decision was
correct *implementation cleanup* — the layers really were dead code —
but it implicitly *also deleted the architectural vision*: that
personal memory should be a **temporal+stateful per-employee brain**
in the same architectural shape as the firm KG, just at a different
scope.

What MemPalace gives us today (per ADR-0004): per-employee vector
search + citations + candidate-fact surfacing. Useful for "did I see
something about X recently?" recall.

What MemPalace doesn't give us:

- **Entity-centric view.** Sarah Chen mentioned in an email + a
  Telegram message + a phone call appears in MemPalace as three
  documents. There's no rolled-up "Sarah Chen entity with three
  source-types of evidence" view at the personal layer.
- **Temporal state on personal entities.** "What's my current
  relationship state with Sarah?" — MemPalace returns relevant
  documents, not a current-true triple. There's no
  `valid_from`/`valid_to` on personal facts, no Bayesian
  corroboration, no `find_current_triple` semantic.
- **Structured working memory for the individual.** Current tasks,
  open commitments, preferences, lessons — the agent-as-personal-
  assistant case, not just the feeding-the-firm-pipeline case.
  MemPalace has documents; it doesn't model the individual's
  operating model.
- **Identity continuity inside the personal plane.** Personal items
  are document-shaped in MemPalace; the firm-wide IdentityResolver
  (which canonicalizes `p_<id>` / `o_<id>`) only kicks in when items
  cross to firm via the proposal pipeline. Inside personal, "Sarah"
  in three different documents stays three different documents.

The original `personal_brain/` four-layer structure was a (different)
attempt at solving this, but it was never wired in production and
the deletion was warranted. The architectural lesson stuck: personal
memory and firm memory are the same architectural shape (temporal
KG + identity-resolved entities + structured pages + provenance),
just at different scopes. Personal scope = `employee_<id>`; firm
scope = a firm-policy-defined string (`public`, `partner-only`, etc.).
The bridge between them is the proposal pipeline (which already
exists).

## Decision

**Per-employee `KnowledgeGraph` instance, scoped to
`employee_<id>`, persisted at
`<firm_root>/personal/<employee_id>/personal_kg.db`. MemPalace
remains as the retrieval substrate underneath; the per-employee KG
holds personal entity-state.**

A new wrapper class `PersonalKnowledgeGraph`
(`src/memory_mission/personal_brain/personal_kg.py`) constructs the
per-employee `KnowledgeGraph` instance, validates the `employee_id`
via `validate_employee_id` (ADR-0004 hardening), and auto-applies
`scope=f"employee_{employee_id}"` on every write + auto-applies
`viewer_scopes={f"employee_{employee_id}"}` on every read. This makes
cross-employee leak *structurally impossible at the wrapper level* —
even before the firm-side `viewer_scopes` filter that exists today.

The `PersonalMemoryBackend` Protocol gains methods for personal-KG
access. The `MemPalaceAdapter` implements them by maintaining a
per-employee `PersonalKnowledgeGraph` instance cache (one KG per
employee, lazily constructed). Existing `ingest()` / `query()` /
`citations()` / `resolve_entity()` / `working_context()` /
`candidate_facts()` Protocol methods are unchanged — MemPalace
stays as the retrieval substrate; the new methods add a parallel
state surface alongside it.

## What lands in this ADR's MVP scope

1. **`PersonalKnowledgeGraph`** wrapper class — instance per
   employee, scope auto-applied, identity-resolver bridge, full
   read/write surface (add_triple, corroborate, find_current_triple,
   query_entity, query_relationship, timeline, invalidate).
2. **`PersonalMemoryBackend` Protocol extension** — new method
   `personal_kg(employee_id)` returns a `PersonalKnowledgeGraph` for
   that employee. Existing methods unchanged.
3. **`MemPalaceAdapter` wiring** — caches one
   `PersonalKnowledgeGraph` per employee (analogous to its existing
   `_PerEmployeeInstance` cache for the MemPalace palace).
4. **Tests** — per-employee isolation; scope auto-application on
   writes; viewer-scope filter on reads; temporal semantics
   (`valid_from`, `valid_to`, `find_current_triple`); identity-
   resolver bridge so personal `p_<id>` / `o_<id>` are the same
   stable IDs the firm KG references.
5. **ABSTRACTIONS.md** gains a `PersonalKnowledgeGraph` section.

## What's deferred to later phases

- **Ingest-time entity rollup.** MemPalaceAdapter could extract
  entity references from a `NormalizedSourceItem` at ingest time and
  auto-write `(entity, mentioned_in, source_external_id)` triples to
  the per-employee KG. That requires either a NER pass or LLM-side
  entity extraction. Deferred until extraction-side wiring lands.
  For MVP, the per-employee KG is empty by default; the personal
  agent or the extraction skill writes triples explicitly.
- **Working-memory pages.** Structured per-employee `Page` instances
  (tier=working, domain=working_memory) for current tasks, open
  commitments, preferences, lessons. Different shape from the
  deleted `working/WORKSPACE.md`; this time first-class with
  provenance + temporal validity. Deferred to a follow-up commit.
- **Bridge into firm-promotion.** When a personal triple graduates
  to firm via `create_proposal`, the personal-side triple should be
  invalidated (or marked with a `promoted_to_firm` annotation) so
  the personal KG doesn't drift from firm truth. The firm-side
  promotion already happens via the existing pipeline; the
  personal-side bookkeeping is a follow-up.
- **Personal MCP surface.** A new MCP scope (e.g. `personal_kg`)
  that exposes per-employee KG queries to the host agent. Deferred
  until the personal agent has clear use cases driving the surface.

## Options considered

- **Option A (chosen):** New `PersonalKnowledgeGraph` wrapper
  per-employee, reusing the existing `KnowledgeGraph` substrate.
  Auto-scope to `employee_<id>`. Smallest delta; reuses everything.
- **Option B:** Extend `KnowledgeGraph` itself with a multi-tenant
  mode where `scope` is the tenant key and the same DB file holds
  multiple employees. Smaller storage cost but mixes employees in
  one file (privacy-adjacent risk + harder backup story per
  employee). Rejected.
- **Option C:** Build a fresh per-employee KG class from scratch
  (different schema, different SQL). Maximum flexibility but
  duplicates ~1000 LOC of well-tested temporal KG code. Rejected.
- **Option D:** Add temporal/stateful semantics directly to
  MemPalace (modify upstream). Out of our control; long upstream
  cycle. Rejected.

## Rationale

1. **Architectural symmetry.** Personal memory and firm memory now
   have the same shape (temporal KG + identity-resolved entities +
   provenance) at different scopes. The bridge (proposal pipeline)
   already exists. ADR-0002's "two planes, one-way bridge" finally
   has matching planes on both sides.

2. **MemPalace stays useful.** Vector search + citations are
   genuinely useful for recall. The new per-employee KG doesn't
   replace MemPalace; it sits alongside it. MemPalace = recall
   substrate; PersonalKnowledgeGraph = state.

3. **Reuses well-tested code.** `KnowledgeGraph` has 100+ tests
   covering temporal validity, scope filtering, corroboration,
   migrations, WAL semantics, identity merges. Reusing it
   per-employee is ~150 LOC of wrapper, not ~1000 LOC of
   reimplementation.

4. **Cross-employee leak is structurally blocked at two layers.**
   (a) The wrapper auto-scopes every write to `employee_<id>` and
   every read to `viewer_scopes={employee_<id>}`. (b) Each employee
   has their own DB file at a separate path, validated via
   `validate_employee_id`. Even if (a) had a bug, (b) would block
   the leak (path isolation). Defense in depth.

5. **Identity continuity inside personal.** Sharing the firm-wide
   `IdentityResolver` means personal "Sarah Chen" entities resolve
   to the same `p_sarah_chen_abc123` the firm KG would use. When
   personal facts graduate to firm, the entity ID is already
   correct; no re-canonicalization at the bridge.

## Consequences

- **One additional SQLite file per employee** at
  `firm/personal/<employee_id>/personal_kg.db`. Storage is cheap;
  ~10MB per employee for typical usage even at scale.
- **The `PersonalMemoryBackend` Protocol grows by one method**
  (`personal_kg(employee_id)`). Existing implementations need to
  add it; the in-house fake backend in
  `tests/test_personal_backend_contract.py` gets a small extension.
- **Cross-employee queries are not supported on the personal plane**
  and never will be. That's correct — only the firm KG (with
  `firm/`-scoped triples + the federated detector) does
  cross-employee queries, and it does them with explicit governance.
  The personal plane is structurally per-employee.
- **The `MemPalaceAdapter._instances` cache pattern extends
  naturally** — same shape, two caches (one for the MemPalace
  palace, one for the PersonalKnowledgeGraph). Both keyed on the
  validated `employee_id`.

## Relationship to ADR-0004

This ADR **extends** ADR-0004; it does not supersede it. ADR-0004
chose MemPalace as the personal substrate, which remains correct.
This ADR adds a parallel state layer on top. Both documents stay
active. The combined architecture:

```
┌─────────────────────────────────────────────────────────┐
│  PersonalMemoryBackend (Protocol, ADR-0004 + ADR-0013)  │
└─────────────────────────────────────────────────────────┘
              │                              │
              ▼                              ▼
┌────────────────────────┐    ┌─────────────────────────────┐
│   MemPalace            │    │  PersonalKnowledgeGraph      │
│   (ADR-0004)           │    │  (ADR-0013)                 │
│   ─────────────        │    │  ─────────────────          │
│   Vector retrieval     │    │  Temporal entity state      │
│   Citations            │    │  Bayesian corroboration     │
│   Per-employee palace  │    │  Per-employee KG file       │
│   ChromaDB-backed      │    │  SQLite-backed (reuses      │
│                        │    │  KnowledgeGraph)            │
└────────────────────────┘    └─────────────────────────────┘
```

Both serve the same employee. MemPalace handles "did I see
something about X?". PersonalKnowledgeGraph handles "what do I
currently believe about X, when did it become true, and what
evidence supports it?".

## Related decisions

- **ADR-0002** — two-plane split. Personal plane now has the same
  temporal+stateful shape as firm plane.
- **ADR-0004** — MemPalace adoption. Stays active. This ADR extends.
- **ADR-0007** — capability-based connector roles + envelope. The
  envelope's `target_plane=personal` items can now optionally
  populate the per-employee KG with entity rollups (when the
  ingest-time extraction is wired in a follow-up).
- **ADR-0011** — `chat_system` role with helper-side plane override.
  DM messages routed to personal plane will land in the per-employee
  MemPalace + (eventually) the per-employee KG.

## Follow-ups

- Working-memory pages — separate commit + ADR if it becomes load-
  bearing (most likely just doc work + a `tier: working` value
  added to `tiers.py`).
- Ingest-time entity rollup — separate commit; depends on extraction
  pipeline integration.
- Personal MCP surface — separate ADR if it ships.
- Personal-side invalidation on firm-promotion — bookkeeping
  follow-up after the bridge has real traffic.
