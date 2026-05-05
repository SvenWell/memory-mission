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

## How the layers compose

An agent with **factual memory** can find the relevant account, ticket, policy, contract, and document.

An agent with **factual + interaction memory** can understand why the work matters, what was promised, what was debated, what assumptions are still fragile.

An agent with **all three layers** can know when something has changed, what workflow should start, who needs to care, what guardrails apply, and whether it should act or escalate. It can draft the follow-up, create the ticket, request approval, notify the owner, update the CRM, escalate the risk, or deliberately do nothing.

That last clause is the difference between an agent that has tools and an agent that can operate inside a company without creating more cleanup work than it saves.

---

## What we are NOT building yet

- **Generic workflow engines** (n8n, Zapier shape). Action memory is about *when and whether to act*, not about *how to chain tool calls*. The chaining is the agent's job.
- **"Skills marketplace"** (action recipes you can install). Each firm's action rules are firm-specific by definition.
- **Rule DSL** (`if commitment_overdue then notify_owner`). Premature; the substrate isn't there yet.
- **Outcome dashboards.** Premature; we don't have outcome capture.

---

## What we should watch for

Triggers to actually start building action-memory primitives:

- A pilot firm asks for "remind me when X" or "alert if Y" beyond what `mm_list_*` read tools cover.
- Hermes hand-rolls trigger logic across multiple skills (signal that a primitive is missing).
- The same correction pattern appears 3+ times in observability logs (signal that we should capture it as outcome memory).
- An agent takes an action that should have been deferred — the cleanup cost will be the strongest signal of all.

Until then: the substrate is structurally capable of supporting action memory; the orchestration primitives are not built; the design space is open.
