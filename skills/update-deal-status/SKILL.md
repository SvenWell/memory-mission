---
name: update-deal-status
version: "2026-04-25"
triggers: ["update deal", "move deal to", "advance deal", "deal status", "ddq complete", "ddq sent", "memo drafted", "deal passed", "deal closed"]
tools: [knowledge_graph, brain_engine, identity_resolver, compile_agent_context, llm, create_proposal, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; running session is a partner / principal / associate authorized to advance deals"
  - "venture overlay copied into the firm directory (firm/concepts/venture-constitution.md present and tier=constitution)"
  - "the deal entity exists in the firm KG — resolve via identity_resolver before calling this skill"
  - "the source artefact driving the transition is staged or already promoted (the transition needs a citation)"
  - "the host agent (Hermes / Codex / Cowork / Claude Code) is configured to call the firm KG / page / proposal MCP tools (Step 18 surface) against the firm directory — MemPalace-direct alone cannot walk the lifecycle; the firm-plane temporal+stateful state is what makes governance visible"
constraints:
  - "INVARIANT: workflow skills never mutate firm truth directly. This skill creates Proposals + draft events. It MUST NOT call promote(), MUST NOT write to KnowledgeGraph directly, MUST NOT modify pages directly. The review-proposals workflow is the single mutation surface for firm truth."
  - "never auto-promote — every transition is a Proposal that goes through review-proposals"
  - "every UpdateFact carries a non-empty support_quote citing the source artefact"
  - "every UpdateFact's `valid_from` is the source-attested event date (when the transition actually happened per the source), NOT now() / extraction time. The proposal's `created_at` is system time; valid_from on the triple is event time. Timeline queries must remain correct (e.g., 'what was status on Monday?' returns the answer that was true on Monday, not the answer learned on Wednesday)."
  - "lifecycle predicate enum is bounded by firm/concepts/venture-constitution.md `lifecycle_stages` — values outside the list raise a coherence warning at promote time"
  - "sub-state predicates (`ddq_status`, `memo_status`, `ic_status`, `closing_status`, `portfolio_status`) are bounded by their respective constitution vocabulary lists (`ddq_statuses`, `memo_statuses`, etc.)"
  - "single source events commonly emit multi-fact proposals: a sub-state UpdateFact + an EventFact + optionally an open_question RECOMMENDING (not applying) a high-level lifecycle_status change. The reviewer decides whether to advance the high-level stage."
  - "do NOT skip lifecycle stages without rationale — sourced → memo (skipping screening + diligence + ddq) requires explicit reviewer override"
  - "diligence → memo transitions check `diligence_required_artefacts` from the constitution; missing artefacts surface as forcing questions to the reviewer"
  - "decision-stage transitions are administrator-gated — only IC members can propose `lifecycle_status: decision` (use record-ic-decision skill instead)"
category: workflow
---

# update-deal-status — advance a deal through the venture lifecycle

## What this does

Resolves a deal entity, compiles current deal context, asks the host
LLM to propose the next-stage transition + rationale, then creates a
Proposal containing an `UpdateFact` (lifecycle_status: old → new) and
optional `EventFact` (transition_date) for human review. Never
auto-promotes. The review-proposals skill handles the actual
promotion.

Pattern mirrors `meeting-prep` but writes proposals instead of just
drafts. The constitution governs the lifecycle vocabulary; this skill
enforces the structural envelope.

**Plane discipline:** Firm plane. Deal lifecycle is firm truth, not
personal cognition.

**Authority discipline:** Partners + principals + associates can
propose transitions through `lifecycle_status: ic` (advancing into IC
review). The transition into `lifecycle_status: decision` is
record-ic-decision's job — it validates IC quorum against the
constitution before proposing.

## Workflow

1. **Resolve the deal entity** via `identity_resolver.resolve(...)`.
   The trigger phrase typically names the deal (e.g. "update Acme to
   diligence"); resolve "Acme" → `o_acme_<id>`. If multiple matches,
   `ask_user_question` to disambiguate.

2. **Compile current deal context** via
   `compile_agent_context(role="update-deal-status", task=<phrase>,
   attendees=[deal_entity_id], kg=kg, engine=engine,
   identity_resolver=resolver, plane="firm",
   tier_floor="doctrine")`. This pulls:
   - The deal's current `lifecycle_status` (currently-true triple)
   - Recent `next_step` commitments
   - The diligence artefacts already linked to the deal
   - The constitution's `lifecycle_stages` + `diligence_required_artefacts`
   - Recent events (last DDQ status change, last partner sentiment
     update, etc.)

3. **Determine the proposed new stage.** The trigger phrase usually
   names it explicitly ("move Acme to diligence", "DDQ complete"). If
   ambiguous, the host LLM proposes the next stage from the
   constitution's lifecycle list. If the proposed stage skips intermediate
   stages (e.g. `sourced → memo`), the LLM MUST surface this as a
   forcing question — skipping requires explicit operator override.

4. **Check stage-specific gates.** For `diligence → memo`: validate
   that all `diligence_required_artefacts` from the constitution are
   linked to the deal. Missing artefacts become an `ask_user_question`
   forcing question; the operator either provides the missing
   artefact or explicitly waives.

5. **Block decision-stage transitions.** If the proposed new stage is
   `decision`, surface an error pointing the operator at the
   `record-ic-decision` skill. The lifecycle transition into
   `decision` requires IC quorum validation that is not part of this
   skill's surface.

6. **Host LLM drafts the proposal — multi-fact pattern.** Inputs:
   current context (from step 2), proposed transition (from step 3),
   source artefact (from the trigger phrase or operator-supplied
   evidence). Output is commonly a *bundle* of facts capturing the
   full multi-axis impact of one event:

   - **Sub-state `UpdateFact`(s)** for whichever sub-states the source
     attests (e.g., source says "DDQ received" → emit
     `UpdateFact(ddq_status: sent → complete)`; source says "memo
     drafted" → emit `UpdateFact(memo_status: not_started → draft)`).
     `valid_from` = the date the source attests the transition (NOT
     now). `supersedes_object` = the previous sub-state value.
   - **High-level `UpdateFact(lifecycle_status)` ONLY when the source
     unambiguously attests it**, OR when the operator is explicitly
     requesting the high-level transition. Do not auto-derive it from
     a sub-state event — emit an `OpenQuestion` recommending it
     instead and let the reviewer decide.
   - **`EventFact`** capturing the source-attested event date with
     predicate `lifecycle_transitioned_at` or sub-state-specific
     (`ddq_received_at`, `memo_drafted_at`, etc.).
   - **`next_step` facts** for follow-up commitments the source
     names (with `due_by`).

   Every fact in the bundle carries `valid_from` = source-attested
   event date and a non-empty `support_quote` citing the source.

7. **Create the proposal** via `create_proposal(facts=<facts>,
   reviewer_required=True, source_file=<artefact_path>,
   target_scope=<deal's current scope>)`. Log a `DraftEvent` for the
   draft itself. Do NOT call promote.

8. **Surface the proposal id** to the operator with a one-line
   summary: "Proposed: Acme → diligence (cited from
   first-call-2026-04-15.md). Review via review-proposals."

## What this skill does NOT do

- **Promote anything.** Promotion is review-proposals' job. This skill
  only creates proposals.
- **Write to the firm KG directly.** All writes go through the
  proposal gate.
- **Bypass the constitution's lifecycle vocabulary.** Values outside
  the constitution's `lifecycle_stages` list will coherence-warn at
  promote time; the skill catches this earlier and refuses to draft
  the proposal.
- **Validate IC quorum.** Use `record-ic-decision` for decision-stage
  transitions.
- **Modify the deal's other frontmatter fields.** This skill changes
  `lifecycle_status` and (optionally) appends to the timeline. Other
  fields (deal_owner, valuation_at_entry, etc.) are updated via
  separate proposals from the relevant workflow.
- **Auto-resolve missing artefacts.** Diligence-required artefacts
  must be linked manually before transition; the skill surfaces
  missing ones as forcing questions, doesn't fetch them.

## On error

- Stage outside constitution's `lifecycle_stages`: error + suggest the
  closest valid stage.
- Missing required diligence artefacts: forcing question via
  `ask_user_question`; operator supplies or waives.
- Decision-stage transition: hard error pointing at
  `record-ic-decision`.
- Coherence warning at create_proposal time (rare, since the skill
  pre-validates): surface the warning + abort. Do NOT silently override.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Check whether reviewers consistently accept or reject this skill's
   proposals. Acceptance < 70% suggests the LLM's stage-transition
   judgment is off; tune `prompt_examples.md` with the rejection
   patterns.
2. Check whether `ddq → memo` transitions consistently miss the same
   artefact type; add to `diligence_required_artefacts` in the
   constitution if so.
3. Commit: `skill-update: update-deal-status, <one-line reason>`.

## Related

- `firm/concepts/venture-constitution.md` — authoritative
  `lifecycle_stages` + `diligence_required_artefacts` + `decision_rights`.
- `skills/record-ic-decision/SKILL.md` — sibling skill for
  IC-quorum-validated decision-stage transitions.
- `skills/review-proposals/SKILL.md` — handles the actual promotion
  of the proposals this skill creates.
- `overlays/venture/prompt_examples.md` — predicate vocabulary the
  host LLM uses when drafting `UpdateFact` shapes.
