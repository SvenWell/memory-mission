---
type: ADR
id: "0004"
title: "Personal-layer substrate — MemPalace, custom, binary decision at end of P1"
status: proposed
date: 2026-04-25
---

> **Status: proposed.** Decision gate is the bounded P1 spike on `SvenWell/mempalace-spike`. ADR moves to `active` once the outcome is recorded. **Defer-again is forbidden** unless a single named blocker with a short follow-up path is documented at decision time.

## Context (revised 2026-04-25)

The personal-plane substrate has been **promoted from "non-blocking optional spike" to "pilot-critical infrastructure."** Reason: every pilot firm needs each employee's agent to carry private memory across email / calendar / transcript interactions from day one. Without that, the agent has no context to ground its drafting / synthesis / pre-meeting briefs in. Personal memory is critical infrastructure, even though it is not the moat (the moat remains the governed firm bridge).

Today the personal plane is partly custom-built: `src/memory_mission/personal_brain/` holds four layers (`working.py` / `episodic.py` / `lessons.py` / `preferences.py`) — but **the call-site inventory (P0-B2) found these have zero production callers**. The personal plane in production today flows through plane-scoped calls on the shared `BrainEngine` + `KnowledgeGraph` with `source_closet="personal/<employee>"` tagging. `personal_brain/` is dead-code-with-tests as of 2026-04-25.

Per P0-C of the revised plan, every personal substrate (whether MemPalace, our own, or any future swap) must implement an explicit Python Protocol — `PersonalMemoryBackend` at `src/memory_mission/personal_brain/backend.py` — with these methods:

- `ingest(NormalizedSourceItem, *, employee_id) -> IngestResult`
- `query(question, *, employee_id, limit) -> list[PersonalHit]`
- `citations(hit_id, *, employee_id) -> list[Citation]`
- `resolve_entity(identifiers, *, employee_id) -> EntityRef`
- `working_context(*, employee_id, task) -> WorkingContext`
- `candidate_facts(*, employee_id, since) -> Iterable[CandidateFact]`

Acceptance scenarios (in `tests/fixtures/pilot_tasks/scenarios.py`) define the four pilot-task shapes the substrate must satisfy:

1. Company / contact recency summary
2. Follow-up commitments
3. Last-meeting deltas
4. Pre-interaction private context

The competitive landscape review identified MemPalace (49,332 stars, Python / SQLite, 96.6% R@5 on LongMemEval raw) as the leading personal-memory substrate. **Our KG was already ported from MemPalace** (BUILD_LOG Step 6b) — same language, same storage, same mental model. P1 tests whether MemPalace fits cleanly behind the `PersonalMemoryBackend` Protocol, and whether adopting it earns its keep.

## Options

- **Option A — Accept MemPalace.** Each employee's personal plane becomes `firm/personal/<employee_id>/mempalace.db` + MemPalace's hooks + MCP tools. The `MemPalaceAdapter` implements `PersonalMemoryBackend`. Dead-code `personal_brain/working.py`/`lessons.py` removed. We inherit MemPalace's 96.6% R@5 retrieval, community maturity, and upstream improvements.

- **Option B — Reject MemPalace and harden current personal layer.** Build a minimal `PersonalMemoryBackend` impl on top of the existing `BrainEngine` + `KnowledgeGraph` with plane scoping. Dead-code `personal_brain/` layers removed (no production callers, no replacement needed). Accept the LongMemEval benchmark gap; firm-coherence eval (which we own) becomes the credibility floor instead.

**Option C — Defer again — is forbidden** unless P1 surfaces a single named blocker with a documented short follow-up path. "Need more dogfood" does not qualify; the synthetic pilot-task harness IS the dogfood per the revised plan.

## Decision

**Pending** — binary at end of P1 spike.

### Acceptance gate (Option A passes only if all five hold)

1. **Protocol coverage.** `MemPalaceAdapter` implements every method on `PersonalMemoryBackend` without `# TODO: MemPalace can't do X` markers.

2. **Acceptance scenarios.** All four pilot-task scenarios in `tests/fixtures/pilot_tasks/scenarios.py` pass when the contract test runs against `MemPalaceAdapter` (parametrized parametrization in `tests/test_personal_backend_contract.py`).

3. **Employee-private isolation.** The multi-employee fixture asserts that data ingested under `alice@vc.example` is never returned by queries / citations / candidate_facts under `bob@vc.example`. Structural enforcement, not convention.

4. **Bridge integrity.** `candidate_facts()` produces `CandidateFact.payload` shapes that the existing extraction → proposal pipeline consumes without converters. Specifically: `payload["kind"]` is one of `identity` / `relationship` / `preference` / `event` / `update` / `open_question`.

5. **Net complexity reduction or wash.** Adopting MemPalace + adapter must NOT make the system harder to reason about. Specifically: the adapter file size + any helper code added must be ≤ the LOC removed from `personal_brain/` + any redundant in-house personal-plane wrapping in the engine. If we end up with more code, MemPalace isn't earning its keep.

Thin compatibility shims for working-state are acceptable. A perfect 1:1 replacement of every prior helper is NOT required.

### Decision outcomes

- **Accept Option A** — ADR moves to `active`, status `adopted`. Work rolls into P3 with MemPalace as the personal substrate. Spike branch merged. Follow-up ADR records version pinning + upgrade policy + adapter boundary.

- **Reject Option A → Option B** — ADR moves to `active`, status `rejected-in-favor-of-custom`. Spike branch discarded. Build the minimal `PersonalMemoryBackend` impl on existing primitives. `personal_brain/` dead code still gets cleaned (it's unwired regardless of substrate choice). LongMemEval benchmark gap accepted; document why our priority is firm-coherence eval instead.

- **Forbidden: Option C — defer again** — only valid if a single named blocker is documented at decision time with a short follow-up path. Otherwise the decision is forced; defaulting to Option B is preferable to deferral.

## Rationale for the binary gate

1. **Personal substrate is pilot-critical.** Without an answer, the personal-source ingestion in P3 (email/calendar/transcripts) has no destination — and P3 is the demo milestone where employees actually start using the agent. Punting blocks the most visible pilot deliverable.

2. **Defer-again has compounding cost.** Every post-spike phase that ships new personal-plane code becomes adoption work to unwind. The acceptance gate exists to FORCE the answer, not delay it.

3. **The synthetic pilot-task harness is the validation.** P0-C ships four acceptance scenarios in `tests/fixtures/pilot_tasks/`. These are venture-shaped synthetic corpora — small, realistic, reproducible. We're not waiting for "real dogfood data" because Sven explicitly is not personally dogfooding (revised-plan assumption).

4. **Both outcomes are reasonable.** Accept gives us 49k-star credibility + 96.6% R@5; Reject gives us full ownership and a thinner stack. The wrong move is to defer and ship around the question.

## Consequences — pending (filled in at decision time)

- [ ] Final decision: ___ (A / B / blocker-deferred)
- [ ] All five acceptance gate items: ___ (pass/fail count)
- [ ] LOC delta — `personal_brain/` removed: ___; adapter / shim code added: ___; net: ___
- [ ] Test-suite result: ___ (full count / contract count / acceptance scenarios passing)
- [ ] New deps added: ___ (`mempalace>=3.3,<4.0` if A; nothing new if B)
- [ ] Follow-up ADRs triggered: ___

## Related decisions

- ADR-0002 — Two-plane split (personal vs firm). Personal-substrate decision is scoped to the personal plane; firm plane stays ours regardless of outcome.
- ADR-0005 — SQLite per firm. MemPalace is also SQLite-backed, so Option A preserves the invariant. Option B uses our existing SQLite KG.
- ADR-0007 (pending P2) — Capability-based connector roles. The connector layer feeds either substrate via the same `NormalizedSourceItem` envelope; substrate decision is independent of connector work.
