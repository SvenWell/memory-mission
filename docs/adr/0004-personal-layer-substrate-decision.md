---
type: ADR
id: "0004"
title: "Personal-layer substrate — MemPalace, custom, or defer"
status: proposed
date: 2026-04-24
---

> **Status: proposed.** Decision gate is a bounded 1-week spike on `SvenWell/mempalace-spike` ending 2026-04-30 (or earlier if acceptance gate hits sooner). ADR moves to `active` once the outcome is recorded.

## Context

Memory Mission's **personal plane** is per-employee private memory. Today it's custom-built: `src/memory_mission/personal_brain/` holds four layers (`working.py` / `episodic.py` / `semantic.py` / `preferences.py` / `lessons.py`), each backed by markdown pages + the shared SQLite KG. Current test count: 707 passing.

The competitive landscape review (2026-04-24) mapped nine open-source memory systems. Relevant to the personal layer:

- **MemPalace** (`MemPalace/mempalace`, **49,332 stars**, Python / SQLite) — verbatim storage + hybrid retrieval, 29 MCP tools, Claude Code auto-save hooks, **96.6% R@5 on LongMemEval raw (zero LLM)**. Our KG was ported from MemPalace (BUILD_LOG Step 6b). Same language, same storage, same mental model as our existing stack.
- **GBrain** (`garrytan/gbrain`, 11k stars, TypeScript) — 28 skills, auto-wiring typed edges. Wrong language for adoption; pattern worth stealing separately.
- **Rowboat** / **Supermemory** — desktop / SaaS products. Wrong shape for substrate adoption.

The question: should the personal plane become a MemPalace instance per employee, or stay custom?

## Options

- **Option A — Adopt MemPalace.** Each employee's personal plane becomes `firm/personal/<employee_id>/mempalace.db` + MemPalace's hooks + MCP tools. Our personal-plane code (`personal_brain/working.py`, `lessons.py`, personal-plane `BrainEngine` / KG wrapping) is deleted or thinned. We inherit MemPalace's 96.6% R@5, community maturity, and continued upstream improvements.

- **Option B — Stay custom.** Keep writing our own personal layer. Accept the benchmark gap vs MemPalace. Apply selective pattern-steals (GBrain's auto-wiring, MemPalace's hook shape) without pulling in their stacks.

- **Option C — Defer.** Ship the capability-based connector + sync-back + venture-pilot chapter without touching the personal layer. Revisit after pilot feedback tells us whether personal-plane friction is real.

## Decision

**DEFERRED — binary at end of 2026-04-30 spike.**

A bounded spike on branch `SvenWell/mempalace-spike` will answer the question with evidence, not speculation. The spike decides A vs B vs C based on a strict acceptance gate.

### Spike acceptance gate (all three must hold to pick Option A)

1. **Coverage.** `MemPalaceAdapter` must cover every current personal-plane call site without `# TODO: MemPalace can't do X` markers. Inventory lives in the spike's Phase 0-B2 notes. Any gap = reject Option A.

2. **Simplification.** Adopting MemPalace must delete ≥500 LOC from `personal_brain/` without losing behavior that existing tests assert. The point of adoption is to stop rebuilding; if the adapter surface ends up bigger than what we're replacing, we're not gaining anything.

3. **Flow preservation.** The extraction → staging → proposal → review → promotion pipeline must stay identical. MemPalace is the personal storage substrate; it is NOT allowed to change how facts become proposals or how the review gate works. Governance invariants are non-negotiable.

### Spike outputs

- `src/memory_mission/personal_brain/mempalace_adapter.py` (on spike branch)
- `src/memory_mission/personal_brain/call_site_inventory.md` (scratch, not committed long-term)
- LOC-delta count: `personal_brain/` LOC before − LOC after adapter integration
- Test run: all 707 tests pass with personal-plane routes delegated

### Decision outcomes

- **Accept Option A** — this ADR moves to `active`, status flips from "proposed" to "adopted". Work rolls into P1+ with MemPalace as the personal substrate. Follow-up ADR records version pinning, upgrade policy, and adapter boundary.
- **Reject Option A → fall to Option B** — this ADR moves to `active`, status `rejected-in-favor-of-custom`. Benchmark gap documented. Personal-layer work is parked; pattern-steals (GBrain auto-wiring) land separately on the firm plane.
- **Reject Option A → fall to Option C** — only valid if the spike reveals ambiguity that can't be resolved in 1 week. ADR moves to `deferred`, lists what we'd need to know to decide, and the question is revisited after pilot feedback.

Neither outcome blocks P1+ (capability-based connectors, sync-back, evidence pack, venture pilot). Personal-layer substrate is orthogonal to the go-to-market work.

## Rationale for running the spike now (rather than later)

1. **MemPalace is our direct lineage.** Our KG was ported from theirs. Rebuilding similar code when they keep shipping improvements is waste we can measure.

2. **The benchmark gap is real and visible.** MemPalace publishes 96.6% R@5; we publish nothing. A pilot customer will compare, and we need either a number to cite or a principled reason the comparison is invalid.

3. **A 1-week spike is cheap insurance.** If Option A works, we delete 500+ LOC and inherit a benchmark. If it fails, we've learned precisely why and we stay custom with conviction.

4. **Deferral has a cost.** Every post-spike phase ships new personal-plane code that adoption would have to unwind. Resolving the question now prevents "adoption would have been easy two months ago" regret.

## Consequences — pending

Filled in at spike end:

- [ ] Final decision: ___ (A / B / C)
- [ ] LOC delta from `personal_brain/`: ___
- [ ] Test-suite result: ___ (all 707 / fewer / with skips)
- [ ] New deps added: ___
- [ ] Follow-up ADRs triggered: ___

## Related decisions

- ADR-0002 — Two-plane split (personal vs firm). Personal layer is the scope of this decision; firm layer is out of scope.
- ADR-0005 — SQLite per firm. MemPalace is also SQLite-backed, so Option A preserves this invariant.
- The firm plane stays ours regardless — nothing in the competitive landscape has our governance shape.
