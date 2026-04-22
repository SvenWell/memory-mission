---
name: review-proposals
version: "2026-04-22"
triggers: ["review proposals", "pending reviews", "what's in the queue", "approve proposals", "review pending promotions"]
tools: [proposal_store, knowledge_graph, permissions_policy, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved and reviewer identity known (usually the current human user)"
  - "ProposalStore available for this firm"
  - "KnowledgeGraph available for this firm"
  - "optional: permissions Policy loaded for scope checks"
constraints:
  - "every approve / reject / reopen call requires a non-empty rationale — no rubber-stamping"
  - "never auto-approve, regardless of confidence or tier"
  - "surface one proposal at a time to the human — batch approval breaks the review contract"
  - "honor the permissions Policy — skip proposals the reviewer can't propose (no-escalation rule)"
  - "on error during promote, stop and surface the error — do NOT attempt the next proposal"
category: governance
---

# review-proposals — PR-model promotion with tick-the-box approval

## What this does

Reads pending proposals from the `ProposalStore`, ranks them by the
signals that matter (tier crossings, rejection count, confidence,
recency), surfaces each one to the human via the host agent's
question interface, and calls `promote` / `reject` / `reopen` based
on the human's decision. Rationale is required on every decision —
the pipeline itself enforces this; the skill captures the reasoning
in the human's own words so `decision_history` stays meaningful.

This is Memory Mission's V1 centerpiece. Bad promotion is worse than
missing promotion; the gate runs one proposal at a time and waits
for an explicit human call.

## Workflow

Open an observability scope for the firm + reviewer (the reviewer
may or may not be an "employee" in the usual sense — the
`reviewer_id` is whoever is answering the chat, typically a partner
or admin). Open the `ProposalStore` and the firm's `KnowledgeGraph`.
Optionally load the firm's permissions Policy so you can skip
proposals the reviewer isn't allowed to decide.

Loop:

1. **List pending proposals.** `store.list(status="pending")`. If the
   queue is empty, say so and stop.
2. **Rank for review priority.**
   - First: proposals whose `rejection_count > 0` (recurring churn
     deserves attention).
   - Then: proposals where the target entity recently crossed an
     enrichment tier (surface high-signal entities first).
   - Then: by `created_at` ascending (oldest pending first).
3. **Permission pre-check.** If a permissions Policy is loaded, use
   `can_propose(policy, reviewer_id, proposal.target_scope)` before
   presenting. A reviewer who can't propose into that scope also
   can't approve into it — skip with a one-line note.
4. **Render one proposal.** Show the human:
   - Target entity + target plane + target scope
   - Proposer (agent + employee)
   - Source report path (provenance)
   - Each fact with its kind, confidence, and support_quote
   - If `rejection_count > 0`: the previous rejection rationales
     (from `decision_history`)
5. **Ask the human.** Present three options plus a rationale prompt:
   - **Approve** — proposal's facts land in the KG
   - **Reject** — proposal marked rejected, returns to the queue
     only if someone explicitly reopens it
   - **Skip** — leave pending, come back next review
6. **Execute the decision.**
   - Approve → call `promote(store, kg, proposal.proposal_id,
     reviewer_id=..., rationale=<human text>)`. If the call raises,
     surface the error and stop.
   - Reject → call `reject(...)` with the human's rationale.
   - Skip → do nothing; move on.
7. **Repeat** until the human says "done" or the queue is empty.

## Forcing questions (never guess)

- **Contradiction with existing firm truth:** "Proposal says Sarah
  works at Beta Fund. Firm KG says she works at Acme. Approve as
  supersession, reject as conflict, or flag as open question?"
  (The proposal's `UpdateFact` should already carry
  `supersedes_object`; if it doesn't, surface the mismatch.)
- **Coherence warning (Step 15):** Before presenting a proposal,
  read the observability log for recent `CoherenceWarningEvent`
  rows on the current `trace_id` / proposal id. If any surface,
  include the conflicting fact + its tier (constitution / doctrine
  / policy / decision) in the summary you show the reviewer. Sample
  phrasing: "This proposal says `firm thesis = buy-momentum`. A
  currently-true `doctrine`-tier fact says `firm thesis =
  buy-durable-compounders`. Approve as supersession (new doctrine),
  approve as decision-level addition, reject, or request a
  supersession update?" If the firm is in `constitutional_mode`,
  `promote()` will block on this conflict — the reviewer MUST
  resolve the conflict explicitly before the proposal can land.
- **Permission uplift warning:** "Source was personal plane; this
  proposal targets firm + scope `partner-only`. Reviewer, confirm
  you want to expose this to the `partner-only` audience?"
- **Recurring rejection:** "This exact proposal was rejected on
  <date> for `<prior rationale>`. New evidence, or same call?"
- **Low-confidence bundle:** "5 of 7 facts have confidence < 0.6.
  Approve all, approve high-confidence only, reject whole bundle?"

Surface these as `QUESTION:` lines in your instruction output. The
host agent's question mechanism (AskUserQuestion, chat) presents
them; never guess on the human's behalf.

## Where state changes

Approve:
- Facts land in `<firm_kg.sqlite3>` with provenance (source_closet +
  source_file)
- Proposal status → `approved`, rationale + reviewer_id recorded
- `ProposalDecidedEvent` logged to observability
- If any coherence conflicts were detected, one
  `CoherenceWarningEvent` per conflict lands too (advisory by
  default; `blocked=True` and `CoherenceBlockedError` raised when the
  firm is in `constitutional_mode`)

Reject:
- Proposal status → `rejected`, `rejection_count` incremented
- Decision history appended with the rationale
- `ProposalDecidedEvent` logged

Reopen (only valid on rejected):
- Proposal status → `pending`
- Decision history appended with reason for reopening
- `ProposalDecidedEvent` logged

Nothing else moves. Curated page rewrites (compiled-truth
regeneration) are a separate workflow step later — this skill is
strictly the merge gate.

## What this skill does NOT do

- No LLM call. The human is the reviewer. If you use an LLM to help
  summarize a proposal for the human, that's fine — but the
  decision comes from the human, always.
- No auto-approve on any signal. Confidence thresholds and tier
  crossings INFORM which proposal to surface next; they do not
  override the human gate.
- No batch operations. One proposal, one decision, one rationale.
- No direct KG writes — the pipeline function (`promote`) handles
  the KG writes atomically with the status change.
- No edits to approved proposals. If a fact needs revision, create
  a new proposal with an `UpdateFact`; the prior approved proposal
  stays in the audit record unchanged.

## On crash / resume

The `ProposalStore` persists across sessions. Interrupting mid-review
is safe: proposals you didn't decide on stay pending. Resume by
re-running the skill — it'll re-list pending proposals and continue.
`create_proposal` is idempotent on the extraction side too, so
re-running extraction won't duplicate proposals in the queue.

## Self-rewrite hook

After every 5 review sessions OR on any failure:

1. Read the last 5 `ProposalDecidedEvent` rows from the observability
   log. If one decision type dominates (e.g., 80% rejected), check
   whether the ranking is surfacing the right proposals.
2. If a single rationale pattern keeps showing up in rejections
   ("source stale", "low confidence"), append a one-line lesson to
   `KNOWLEDGE.md` next to this file and consider proposing an
   upstream fix (extraction prompt tightening, tier threshold tune).
3. If an approve / reject call raised (e.g., wrong-status
   `ProposalStateError`), escalate as a project memory so the cause
   is traced.
4. Commit: `skill-update: review-proposals, <one-line reason>`.
