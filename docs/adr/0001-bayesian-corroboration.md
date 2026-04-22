---
type: ADR
id: "0001"
title: "Bayesian corroboration via Noisy-OR with 0.99 cap"
status: active
date: 2026-04-22
---

## Context

Before Step 13, `promote()` always called `kg.add_triple(...)` for each fact in a proposal. Re-extracting the same fact from a second source produced a second currently-true row with the same `(subject, predicate, object)`. The KG accumulated duplicates instead of aggregating evidence.

Three pressures forced a change:

1. **Provenance integrity** — the system needs to trace every fact to every contributing source. Two separate rows hide the fact that one source independently confirmed another's claim.
2. **Confidence should rise with evidence** — a claim backed by three independent sources is stronger than one. Duplicate rows don't express that; they just multiply the claim.
3. **Federated detection would reinforce the problem** — Step 16's cross-employee aggregation finds patterns where multiple employees assert the same fact. Without corroboration, each federated promotion would create yet another duplicate. The duplicates-per-corroboration ratio would grow linearly with employee count.

The question was: when a new promotion matches an existing currently-true triple, what happens?

## Decision

**Re-extraction of a currently-true fact corroborates (updates confidence + appends source) rather than duplicating. Confidence updates via the Noisy-OR independent-evidence formula, capped at 0.99.**

- `KnowledgeGraph.corroborate(subject, predicate, object, *, confidence, source_closet, source_file)` returns the updated `Triple` on match, `None` on miss.
- Update rule: `new_confidence = min(0.99, 1 - (1 - old) * (1 - new))`.
- A new `triple_sources` table preserves the full per-source history; every corroboration appends a row.
- The promotion pipeline routes every triple-like fact through `_add_or_corroborate()` — if `find_current_triple()` returns a match, corroborate; otherwise add.
- The 0.99 cap applies only to corroboration. Direct `add_triple(confidence=1.0)` callers can still seed at full certainty — that's the explicit human-override path.

## Options considered

- **Option A (chosen): Noisy-OR with 0.99 cap.** Standard independent-evidence combiner. Monotonic, always increases with new corroborating sources, never reaches full certainty through accumulation. Pros: well-understood semantics, tight math, cheap to compute. Cons: assumes independence between sources (violated when three employees share one transcript — mitigated at the promotion layer by the federated detector's source-file threshold, not at the corroboration layer).

- **Option B: Simple average of confidences.** `new = (old + incoming) / 2`. Pros: trivial. Cons: not monotonic — a 0.9 triple corroborated by a 0.5 fact would DROP to 0.7. That's wrong — more evidence should never weaken belief, only change direction.

- **Option C: Weighted average with evidence count.** `new = (old * n + incoming) / (n + 1)`. Pros: monotonic under positive evidence. Cons: converges arithmetically toward the mean; requires tracking `n` separately; handles contradiction poorly.

- **Option D: Replace (last-writer-wins).** Pros: simple. Cons: throws away evidence. Defeats the whole point.

- **Option E: Bayesian with explicit prior.** Full Bayesian update with a stated prior over the claim. Pros: theoretically clean. Cons: requires a meaningful prior per claim, which is infeasible at this stage.

Noisy-OR won on monotonicity + semantics + compute cost.

## Why cap at 0.99 and not 1.0?

The cap is intentional and load-bearing. Accumulated agent-path evidence should never reach certainty — certainty is reserved for explicit human override (e.g., an admin running `kg.add_triple(..., confidence=1.0)` with a deliberate rationale). A chain of 10 corroborations at 0.5 each produces `1 - 0.5^10 ≈ 0.999`, which without the cap would be ≈ 1.0 — indistinguishable from hand-verified truth. That's a silent drift toward unearned certainty. The 0.99 cap keeps the distinction visible: "the agents are very sure" (0.99) vs "a human said this is certain" (1.0).

## Consequences

- **Provenance survives merges, invalidations, and re-extractions.** `triple_sources` is the authoritative per-source history; the main `triples` row carries the first source for backwards compatibility. Audit tools should read `triple_sources`, not `triples.source_closet`.
- **Federated detector composes naturally.** When the detector proposes a firm-plane triple for a fact that's already currently-true in personal plane, promote-time corroboration adds `source_closet='firm'` to the existing triple's provenance instead of creating a parallel row.
- **Step 15 coherence check runs BEFORE corroboration.** A new object on the same `(subject, predicate)` fires a coherence warning; a matching object corroborates. Same code path, different branches.
- **Initial 1.0 confidences lose their head start on corroboration.** A triple added at 1.0 and then corroborated with 0.5 still caps at 0.99. This is correct — the cap says "no automatic certainty," not "preserve whatever confidence the first caller asserted."
- **Eval implications.** The corroboration math is deterministic and scripted-scenario-gradeable. See `docs/EVALS.md` section 2.3 — tolerance `|predicted - expected| < 0.02` on every test case.

## Re-evaluation triggers

- **Time-decay on old confidence.** Not implemented in V1. If a fact was last corroborated 2 years ago, should its weight in a new combination decay? Probably yes. Defer until real staleness data exists.
- **Source independence weighting.** Two corroborations from different employees at the same firm are less independent than two corroborations from unrelated firms. Not modeled today. Revisit if precision drops on real-world data.
- **Contradiction evidence.** "Fact X is NOT true" isn't a first-class concept today. The only way to mark a fact false is `invalidate()`. If contradiction signals become important, a separate `refute()` op that decays confidence might be needed.
- **Cap value.** 0.99 is a round number with no empirical basis. If agents in production are routinely hitting 0.99 on facts that turn out to be wrong, the cap should drop (0.95 or 0.90). The opposite signal (cap too aggressive, human-confirmed facts getting corroborated back down) would argue for raising it — but the whole point of the cap is that agent-path evidence *cannot* raise confidence past this floor for human-only territory.
