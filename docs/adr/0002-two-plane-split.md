---
type: ADR
id: "0002"
title: "Two-plane split (personal / firm) with one-way promotion bridge"
status: active
date: 2026-04-21
---

## Context

Before Step 8, Memory Mission had one plane: whatever an employee extracted went into a shared wiki. That shape has obvious problems:

1. **No privacy.** An employee's rough notes, speculations, and half-formed hypotheses become firm memory the moment they hit disk. Employees self-censor or stop extracting.
2. **No governance.** If any extraction can write to firm memory, the firm's believed-truth drifts with every agent invocation. No review gate exists to catch bad data.
3. **No federated signal.** If every extraction goes into one pile, you can't tell the difference between "one employee saw this once" and "five employees independently extracted this from five different sources." The aggregation signal that makes enterprise memory better than any individual's is lost.
4. **No permission model.** Employees see everything or nothing.

Emile's conversation (2026-04-22) framed the fix: **"if a person can't see it, their agent can't. Firm memory gets promoted updates, not raw everything. Bad promotion is worse than missing promotion."** That framing forced a split.

## Decision

**Memory Mission has two planes: `personal/<employee_id>/` and `firm/`. Nothing writes to firm automatically. The only path between the two is the promotion pipeline — `create_proposal` → `review-proposals` skill → `promote()` with required reviewer + rationale.**

Structural consequences:

- **`Plane = Literal["personal", "firm"]`** is a first-class type threaded through every module (`memory.schema`, `promotion.proposals`, `extraction.schema`, `federated.detector`, `synthesis.context`).
- **`PageKey(plane, slug, employee_id)`** lets the same slug exist independently across planes. Alice's `sarah-chen.md`, Bob's `sarah-chen.md`, and the firm's `sarah-chen.md` are three distinct entities by construction. No cross-plane leakage possible without an explicit `promote` call.
- **Personal plane is four-layer** (`working/` / `episodic/` / `semantic/<domain>/` / `preferences/` / `lessons/`). The employee's agent operates freely here.
- **Firm plane is governed** — tier-aware, coherence-checked, permission-gated on read (Move 5 polish) and write (`can_propose`).
- **Staging sits between** (`staging/<plane>/<source>/...`). Connectors write there. Extraction reads from there. Nothing in staging is yet believed by anyone.

## Options considered

- **Option A (chosen): two planes, one-way bridge.** Personal stays private by default; firm receives only promoted facts. Review gate is mandatory. Pros: matches how firms actually operate (individuals have notes; the firm has truth); enables federated aggregation signal; preserves employee privacy. Cons: pays the two-plane retrofit cost upfront (Step 8 was a dedicated migration step); callers have to carry `plane` everywhere; data-lineage arithmetic is slightly more complex.

- **Option B: single plane with per-fact ACLs.** One shared vault, every fact tagged with visibility. Pros: simpler data model; no "which plane" decisions. Cons: privacy is a runtime filter, not a structural property — a bug in the filter leaks everything. Contradicts Emile's "structure before trust" principle. Every query surface must correctly enforce ACLs, which is exactly the kind of distributed invariant that fails in practice.

- **Option C: three planes (personal / draft / firm).** A "draft" shared-but-provisional plane between personal and firm. Pros: lets teams collaborate on a fact before it becomes firm-truth. Cons: three planes triples the surface area for ~15% more expressiveness; drafts without a review gate drift the same way firm memory would without the two-plane separation. If needed, draft-plane semantics can be modeled later as a `Proposal` lifecycle state (`draft → proposed → approved`) without a third plane.

- **Option D: no plane separation; use tiers instead.** Let every fact carry a tier (`decision / policy / doctrine / constitution`) and treat low-tier facts as personal, high-tier as firm. Pros: one axis, not two. Cons: conflates authority (tier) with ownership (plane). A partner's personal `working/` note is not "decision tier" — it's "none of the firm's business yet." Tier is orthogonal to plane, not a replacement for it.

## Consequences

- **Privacy is structural, not policy-dependent.** Even with a broken `can_read`, personal pages can only surface to their owner because the `PageKey` dimension is load-bearing at every retrieval site.
- **Federated detection has a signal.** `detect_firm_candidates` scans personal planes (via `triple_sources.source_closet LIKE 'personal/%'`) and reports patterns worth promoting. One plane would have no "signal worth promoting" concept.
- **Governance has a target.** Review gate is meaningful because approved proposals do something specific: they cross the bridge. Without two planes, a review gate would gate nothing — the fact is already visible.
- **Identity resolution composes cleanly.** Stable person IDs (Step 14) let cross-employee aggregation work: "three employees all refer to `p_abc123`" is a stable-ID query that spans personal planes. Without identity resolution, the two planes would have their own entity-name fragmentation.
- **Coherence checks are scoped to firm.** `check_coherence` (Step 15) runs on firm-plane writes. Personal planes can hold contradicting speculation without triggering warnings — they're scratch space. Firm is the place where contradictions matter.
- **Retrofit was real.** Step 8 was dedicated to migrating existing data into the plane-aware shape. Moving from one plane to two requires passing `plane=` everywhere; the Python codebase was small enough that the migration was a one-session task.

## Re-evaluation triggers

- **Need for a "team" scope below firm.** If firms grow large enough that partners-only, deal-team-only, ops-only scopes become necessary, tier + scope can accommodate without adding a third plane. If they can't, revisit.
- **Shared drafting surface.** If review friction is dominated by "we need a place to co-edit a proposal before submitting it," consider adding a draft lifecycle state on `Proposal` (not a new plane).
- **Cross-firm collaboration.** Intentionally out of scope for V1. When it comes up, the answer is probably federation across firm instances, not a third plane inside one instance.
