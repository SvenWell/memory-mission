---
name: record-ic-decision
version: "2026-04-25"
triggers: ["record IC decision", "log IC outcome", "approve investment", "IC vote complete", "IC decision", "investment committee decided"]
tools: [knowledge_graph, brain_engine, identity_resolver, compile_agent_context, llm, create_proposal, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; running session belongs to an IC member"
  - "venture overlay copied; firm/concepts/venture-constitution.md present with `ic_quorum` field set"
  - "the deal entity exists in the firm KG (resolved via identity_resolver before this skill starts)"
  - "the IC meeting transcript or written-vote source is staged or promoted (the decision needs a citation)"
  - "the deal is currently in `lifecycle_status: ic` — decisions on deals at other stages need explicit operator override"
  - "the host agent is configured to call the firm KG / page / proposal MCP tools (Step 18 surface) against the firm directory — MemPalace-direct alone cannot walk the lifecycle; the firm-plane temporal+stateful state is what makes governance visible"
constraints:
  - "INVARIANT: workflow skills never mutate firm truth directly. This skill creates Proposals + draft events. It MUST NOT call promote(), MUST NOT write to KnowledgeGraph directly, MUST NOT modify pages directly. The review-proposals workflow is the single mutation surface for firm truth."
  - "MUST validate IC quorum against the constitution's `ic_quorum` field — votes below quorum produce a tier=policy (advisory) page, not tier=decision"
  - "MUST validate decision_rights against the constitution — investments above the partner_pair ceiling without ic_full quorum surface as forcing questions"
  - "every IC decision is a Proposal containing a tier=decision page + an UpdateFact (lifecycle_status: ic → decision, valid_from = IC meeting date) + an UpdateFact (ic_status: scheduled → decided, same valid_from) + an `ic_decision` predicate fact + (if invest) `valuation_at_entry` + `closing_date` + `lead_negotiator` facts"
  - "every fact's `valid_from` = the IC meeting date (event time, attested by the transcript), NOT now() / extraction time. The proposal's `created_at` is system time; valid_from on triples is event time."
  - "never auto-promote — review-proposals handles the actual promotion"
  - "the IC decision page MUST cite the IC meeting transcript (or written-vote source) as primary source"
  - "dissenting votes MUST be recorded with attribution (which partner voted no/abstain) — important for downstream coherence checks if the decision turns out poorly"
category: workflow
---

# record-ic-decision — log an IC vote outcome with quorum validation

## What this does

Resolves a deal entity, compiles current deal context, asks the host
LLM to draft a `tier=decision` page capturing the IC outcome, validates
quorum + decision_rights against the constitution, then creates a
Proposal for human review. Never auto-promotes.

Pattern: same as `update-deal-status` but with stricter validation +
generates more facts (the lifecycle transition + the explicit
`ic_decision` predicate + investment-term facts on `invest`).

**Plane discipline:** Firm plane. IC decisions are firm truth.

**Authority discipline:** Only IC members can propose decisions.
Encoded via the `Policy` scope check at create_proposal time
(decision-tier proposals require partner-only or higher scope).

## Workflow

1. **Resolve the deal entity** + load the IC meeting source. If the
   trigger doesn't name a specific deal, ask via `ask_user_question`.

2. **Verify the deal is in `lifecycle_status: ic`.** Other stages need
   explicit operator override (an emergency decision on a deal still
   in `diligence` is unusual but valid; surface as a forcing
   question).

3. **Compile current deal context** via
   `compile_agent_context(role="record-ic-decision",
   task=<phrase>, attendees=[deal_entity_id], kg=kg, engine=engine,
   identity_resolver=resolver, plane="firm", tier_floor="constitution")`.
   The constitution's `ic_quorum` and `decision_rights` are part of
   this context.

4. **Extract vote details from the source.** The host LLM reads the IC
   meeting transcript / written-vote source and produces:
   - Outcome: `invest | pass | defer | follow_up`
   - Vote breakdown: yes_votes, no_votes, abstain_votes
   - Attendees (resolved to person entities)
   - For `invest`: investment_amount, investment_valuation,
     lead_negotiator (resolved to a partner entity), closing_target_date

5. **Validate quorum.** Compare `yes_votes + no_votes` to the
   constitution's `ic_quorum`. Below quorum: produce a `tier=policy`
   page (advisory record of the meeting) instead of a `tier=decision`
   page. The operator can re-run after a quorate follow-up vote.

6. **Validate decision_rights** for `invest` outcomes. Match
   `investment_amount` against the constitution's `decision_rights`
   ceilings:
   - `< partner_solo_ceiling`: any partner can authorize; quorum still
     required for the IC record but the authority bar is lower.
   - `< partner_pair_ceiling`: two partner-yes votes minimum.
   - `≥ partner_pair_ceiling`: ic_full quorum required (already checked
     in step 5).
   Mismatches surface as forcing questions (the operator either
   adjusts the recorded amount or invokes an override).

7. **Draft the IC decision page.** Use
   `overlays/venture/page_templates/ic_decision.md` as the shape;
   fill in:
   - slug: `ic-decision-<deal_slug>-<YYYY-MM-DD>`
   - title: `IC Decision — <Deal Title> — <YYYY-MM-DD>`
   - frontmatter: vote counts, outcome, attendees, investment terms,
     quorum_met (true/false), tier (`decision` if quorate, `policy`
     if not)
   - body: rationale (extracted from transcript), dissenting views
     (attributed to dissenting partners), conditions / next steps

8. **Create the proposal.** Bundle:
   - The IC decision page (a `tier=decision` page with full
     frontmatter)
   - `UpdateFact`: deal_entity, `lifecycle_status`, `decision`,
     supersedes `ic`
   - `RelationshipFact`: deal_entity, `ic_decision`, outcome
     (`invest`/`pass`/`defer`/`follow_up`)
   - For `invest`: `RelationshipFact` × 3 — `valuation_at_entry`,
     `closing_date`, `lead_negotiator`
   - Optional `EventFact`: deal_entity, `next_step`, "term sheet to
     founder" (or similar) with `due_by`
   - All facts cite the IC meeting transcript as `support_quote`
     source.

9. **Log a `DraftEvent`** capturing the proposal id. Do NOT call
   promote. Surface the proposal id to the operator with a summary:
   "IC decision proposed for Acme: invest $4M @ $20M post (lead:
   Alice). Quorum met: 4/3 required. Review via review-proposals."

## What this skill does NOT do

- **Auto-promote.** Promotion is review-proposals' job.
- **Validate the source transcript itself.** If the IC meeting
  transcript is wrong, that's a separate problem (re-run extraction +
  re-record).
- **Modify the deal beyond `lifecycle_status` + `ic_decision`.**
  Investment terms are recorded as separate facts; updates to
  ownership / cap table / governance happen via the
  `update-portfolio-company-state` skill (planned, not yet shipped).
- **Override quorum.** Below-quorum votes get a `tier=policy` page,
  not `tier=decision`. The constitution governs.
- **Override decision_rights ceilings.** Mismatches always surface as
  forcing questions; operators consciously override.
- **Bypass the review gate.** Even with quorum met, the proposal
  reviewer can still reject (e.g. if the meeting transcript was
  garbled and the recorded vote count is wrong).

## On error

- Below-quorum vote: produce `tier=policy` page + warn operator.
- Investment amount > decision_rights ceiling without proper quorum:
  forcing question; operator either adjusts or escalates.
- Cannot resolve deal entity: error.
- Cannot resolve attendees to person entities: warn but proceed (the
  decision page records names as text; identity resolution can be
  improved later).
- Coherence warning at create_proposal time: surface + abort.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Check whether IC decisions consistently fall just-below quorum
   (suggesting `ic_quorum` in the constitution should be lowered
   structurally, not worked around).
2. Check whether `decision_rights` thresholds are being routinely
   overridden (the firm's actual authority practice has drifted from
   the constitution; propose a constitutional amendment).
3. Commit: `skill-update: record-ic-decision, <one-line reason>`.

## Related

- `firm/concepts/venture-constitution.md` — `ic_quorum` +
  `decision_rights` are authoritative.
- `overlays/venture/page_templates/ic_decision.md` — page shape.
- `skills/update-deal-status/SKILL.md` — sibling skill for non-IC
  lifecycle transitions.
- `skills/review-proposals/SKILL.md` — promotes the proposals this
  skill creates.
