# Memory Mission — Action Memory (forward-looking sketch)

*Forward layer of operational memory. Sketched 2026-05-05 from Sentra's "Action Memory" essay (`.context/attachments/pasted_text_2026-05-05_12-00-01.txt`). Not yet implemented as a discrete module; the substrate it would build on is shipped (KG, observability events, MemPalace evidence, validity windows, extraction provenance).*

---

## What this doc is

[VISION.md § Three layers of operational memory](VISION.md) introduces action memory as the third layer:

> When a condition changes, who should care, what guardrails apply, whether to act, ask, wait, escalate, or deliberately do nothing.

This doc sketches what the action layer would look like when it lands. Today it is partly emergent — observability events log execution; the KG holds procedural patterns implicitly via `lifecycle_status` predicates and the venture overlay. There is no first-class trigger memory, no threshold rules, no "ask first" gates, no outcome-feedback loop yet.

This is signal-gated. Build only when a pilot needs it (Wealthpoint, etc.) or Hermes asks.

---

## The first principle

**Doing nothing is a first-class action.**

A useful system acts because the context says it should, and stays still when it should not. Most automation systems act because they can. The right move is sometimes to wait, sometimes to ask for approval, sometimes to notify someone without touching the underlying system, and sometimes to stop because the action is technically possible but organizationally wrong. If a Company Brain cannot do nothing on purpose, it cannot be trusted to do anything on purpose.

This is a guardrail, not a feature. Every action-memory primitive should be designed so that "do nothing" is a visible, valid choice — not the absence of a decision.

---

## Four sub-layers

Action memory has four parts, each distinct.

### Procedural memory — how a process is supposed to work

The known paths: onboarding an enterprise customer, issuing a refund, approving pricing, escalating an incident, handling a security review. Most companies pretend these are more standardized than they are.

**What the substrate already supports.** The venture overlay constitution defines `lifecycle_stages` (sourced → screening → diligence → ddq → memo → ic → decision → portfolio | passed) and `decision_rights` (partner_solo / partner_pair / ic_full thresholds). Tier-aware promotion gates encode constitutional doctrine. `ic_status`, `memo_status`, `ddq_status`, etc. are typed state machines for canonical processes (see venture overlay `prompt_examples.md` and `constitution_seed.md`).

**What's not yet built.** Procedural variants per customer / account / deal. The "official flow" vs the "real flow" distinction. Routing logic captured as data, not as prose in skill markdown.

### Trigger memory — when something should happen

A customer mentions churn risk twice. A support ticket sits unresolved for forty-eight hours. A deal crosses a discount threshold. A renewal is thirty days away. A roadmap commitment becomes late. A metric crosses a line. An agent takes an action that needs human review. Each is not just an event; it is a condition that should wake something up.

**What the substrate already supports.** `valid_from` / `valid_to` on triples enables "find triples where X is currently true." The federated detector (`src/memory_mission/federated/detector.py`) watches for cross-employee patterns (N≥3 distinct employees independently extracted the same fact). Coherence detection surfaces conflicts at write time.

**What's not yet built.** First-class trigger primitives: `Trigger(condition, action, guardrail)`. Background condition-watching (the system needs to *notice* changes, not only respond to queries). Threshold-based wake-ups ("alert when commitment overdue by 2+ days"). Cadence-based wake-ups ("weekly portfolio update"). Cross-fact triggers ("if `customer_pain` for org X appears in 3+ accounts, escalate to product").

### Execution memory — what actually happened in a specific case

Who approved the exception. Which step took too long. What workaround was used. Which agent sent the email. Who corrected it. What handoff failed. Which system became the source of confusion.

**What the substrate already supports.** Observability JSONL log captures every promotion event, every connector invocation, every retrieval, every coherence warning. `triple_sources` records who/what corroborated each fact. `ProposalDecidedEvent` captures reviewer + rationale. `record_facts` (`09e4e0d`) appends audit events for source quotes; `invalidate_fact` appends events with invalidation rationale.

**What's not yet built.** Per-action execution traces tied to a `WorkflowInstance` ID. The "agent attempted X, human corrected to Y, here's what changed" feedback structure. Cross-system handoff tracking when an action spans CRM + email + meeting transcript.

### Outcome memory — what happened after the action

The customer renews. The workaround creates technical debt. The escalation reduces risk. The agent action needs human correction. The same issue comes back two weeks later. The result matters because the company should not treat every completed workflow as a successful one.

**What the substrate already supports.** KG temporal validity (was-true → currently-true → invalidated-with-reason) gives a basic outcome shape. `ProjectStatus` and thread `status` enums hold rough outcome state.

**What's not yet built.** First-class `Outcome(action, result, durability, side_effects)` capture. Linking outcomes back to the trigger that caused the action. "This pattern of action consistently produces outcome Y" learning loop.

---

## Canonical primitives (when we build)

Borrowed from SomaOS / TryKosm docs (`TryKosm/somaos-docs`, May 2026; the implementation is private but the design surface is the strongest open spec for governed-action shape we've found). Memory Mission emits the typed state these primitives consume; the action layer either implements these natively or routes them to a downstream governance gateway (Microsoft AGT, AWS Bedrock AgentCore Policy, etc.). Either way, the contract below is the canonical vocabulary when we build.

- **Three-state decision enum.** Every action evaluation returns one of: `allow` (within policy + low risk + no human needed), `review_required` (within policy but trust threshold demands human approval), `blocked` (out of policy — recorded but never executed). Three states, no middle ground, queryable as a fixed enum.

- **`approval_id` as a first-class queryable object.** Approvals are not Slack messages or email threads — they are persistent records with their own lifecycle (`pending` → `approved` | `rejected`), reviewer identity, original action context attached, expiry, and replay capability. Memory Mission's existing `Proposal` already implements this shape for fact promotion; the action-layer extension generalizes it to all reviewable actions.

- **`run_id` (per-step) + `plan_id` (per-multi-step plan).** Multi-step agent work gets one `plan_id` shared across N `run_id`s. Audit + replay can reconstruct a full plan as one query instead of joining unrelated runs. Useful for the granola-extraction-pilot pattern (one ingest plan = ingest + extract + propose + review + promote).

- **`context_hash` binding to prevent replay drift.** Every approval is bound to the exact context at approval time. Reuse with mutated context is rejected. *Already shipped for proposals* via `ProposalIntegrityError` (commit `4655ce5`'s follow-up): `Proposal.expected_proposal_id()` recomputes the SHA-256 hash and `_require_integrity` blocks promote/reject/reopen on a tampered row. Action-layer approvals follow the same pattern.

- **Typed actor namespace** (`agent:hermes` vs `user:sven`). One column instead of two. Routes to different policy paths based on who's attempting the action. Expresses delegation chains cleanly (`agent:hermes` running on behalf of `user:sven`) where Memory Mission's current `proposer_agent_id` + `proposer_employee_id` split can't.

- **Deterministic-default + pluggable `RiskScorer` Protocol.** Ship a typed deterministic default that operates on `(action_category, actor_type, recent_violation_history, context_signals)` — no LLM. Expose a Protocol so production deployments can plug in their own scoring model with the same contract. Same shape as `Connector` + `EmbeddingProvider` + `IdentityResolver`.

- **Blocked attempts persisted as runs.** Forensic-completeness primitive: even denied actions get a `run_id` and event stream, so an SRE can later see exactly which agent tried, when, and with what context. Most governance tools only log approved actions; logging denials is more powerful.

These primitives are NOT shipped today (except `context_hash` via `ProposalIntegrityError`). They are the canonical names + shapes the action layer commits to when it gets built. Trigger conditions for actually building are in "What we should watch for" below.

---

## How the layers compose

An agent with **factual memory** can find the relevant account, ticket, policy, contract, and document.

An agent with **factual + interaction memory** can understand why the work matters, what was promised, what was debated, what assumptions are still fragile.

An agent with **all three layers** can know when something has changed, what workflow should start, who needs to care, what guardrails apply, and whether it should act or escalate. It can draft the follow-up, create the ticket, request approval, notify the owner, update the CRM, escalate the risk, or deliberately do nothing.

That last clause is the difference between an agent that has tools and an agent that can operate inside a company without creating more cleanup work than it saves.

---

## What we are NOT

Borrowed framing from SomaOS docs (which list non-scope by category, not by feature — sharper). Memory Mission's action layer, when built, is **not**:

- **Not an LLM router.** Action layer evaluates whether an action should run; it does not pick which model runs the action.
- **Not an IdP.** Identity resolution lives upstream in `IdentityResolver` (ADR-0014). The action layer consumes resolved actor identity; it doesn't issue or federate it.
- **Not a vector DB or RAG store.** Recall lives in MemPalace (ADR-0004). The action layer consumes typed `compile_agent_context` output as the `context` payload; it doesn't index or retrieve content.
- **Not a generic workflow engine.** n8n / Zapier-shape chaining is the agent's job. The action layer evaluates *whether and when to act*, not *how to chain tool calls*.
- **Not a task scheduler.** Reminders + due dates ride on existing commitment predicates. The action layer does not own scheduling infrastructure.
- **Not an approval UI.** The action layer mints + queries `approval_id` records; rendering them to humans (Slack, email, dashboard) is the host application's job.
- **Not a policy authoring tool.** Policies are typed config the operator brings; the action layer evaluates them deterministically.

The point is to be a **small, well-typed surface that any agent stack can call before doing anything that matters** — same framing as SomaOS — composed with the rest of the substrate, not absorbing it.

---

## What we should watch for

Triggers to actually start building action-memory primitives:

- A pilot firm asks for "remind me when X" or "alert if Y" beyond what `mm_list_*` read tools cover.
- Hermes hand-rolls trigger logic across multiple skills (signal that a primitive is missing).
- The same correction pattern appears 3+ times in observability logs (signal that we should capture it as outcome memory).
- An agent takes an action that should have been deferred — the cleanup cost will be the strongest signal of all.

Until then: the substrate is structurally capable of supporting action memory; the orchestration primitives are not built; the design space is open.
