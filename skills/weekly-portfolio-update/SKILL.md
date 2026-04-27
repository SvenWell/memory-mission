---
name: weekly-portfolio-update
version: "2026-04-27"
triggers: ["weekly portfolio update", "portfolio digest", "portfolio review", "weekly portfolio brief", "portfolio sync", "portfolio status this week"]
tools: [knowledge_graph, brain_engine, identity_resolver, compile_agent_context, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; running session is a partner / principal authorized to read portfolio state"
  - "venture overlay copied; firm/concepts/venture-constitution.md present and tier=constitution"
  - "KnowledgeGraph available for this firm (an empty portfolio set is a valid state and triggers the empty-portfolio forcing question rather than blocking the skill)"
  - "BrainEngine recommended (used for per-company curated page retrieval)"
  - "IdentityResolver recommended (canonical names attached to stable o_<id> entities)"
  - "the host agent is configured to call the firm KG / page MCP tools (Step 18 surface) — MemPalace-direct alone cannot walk portfolio state"
category: workflow
constraints:
  - "INVARIANT: workflow skills never mutate firm truth directly. This skill is READ-ONLY — it produces a digest + DraftEvent. It MUST NOT call promote(), MUST NOT write to KnowledgeGraph directly, MUST NOT modify pages directly, MUST NOT create proposals. The review-proposals workflow is the single mutation surface for firm truth."
  - "if the operator confirms a real state change during digest review (e.g. 'yes, Acme exited last week'), route them to `update-deal-status` or `record-ic-decision` — do NOT draft proposals from this skill"
  - "no LLM call inside the skill — `compile_agent_context` is pure Python; the host agent LLM consumes the rendered digest"
  - "must not include superseded facts (valid_to set) — `compile_agent_context` drops them; the digest must inherit that filter"
  - "every fact in the digest cites source_closet / source_file — preserve citations end-to-end"
  - "tier_floor defaults to `policy` (partner-internal context); raise to `doctrine`/`constitution` only if the operator explicitly asks"
  - "firm plane only — portfolio state is firm truth, not personal cognition"
  - "stale-detection threshold reads each company's frontmatter `quarterly_update_cadence_days` (per-company, not constitution-level); when missing, surface a forcing question rather than guessing"
---

# weekly-portfolio-update — partner-ready portfolio digest

## What this does

Compile a portfolio-wide weekly digest. For every deal currently in
`lifecycle_status: portfolio`, gather currently-true state (portfolio
sub-status, board cadence, last quarterly update, recent events, open
questions) into a per-company snapshot, aggregate into a partner-ready
markdown brief, and hand off to the host LLM for narrative shaping.

Read-only by contract. The digest *surfaces* attention items
(stale companies, missing artefacts, recent state changes); it does
NOT propose state changes. When the operator wants to act on a surfaced
item, they invoke `update-deal-status` or `record-ic-decision` with
fresh source evidence.

Pattern: same shape as `meeting-prep` but aggregated across portfolio
scope rather than per-meeting. Reuses `compile_agent_context` once per
company.

**Plane discipline:** Firm plane. Portfolio state is firm truth.

**Authority discipline:** Any portfolio-authorized employee can run
the digest. The digest contains only facts already promoted to firm
truth — nothing under review surfaces here (proposals live in the
review-proposals queue, not the portfolio brief).

## Workflow

1. **Open observability scope** for the firm + employee. Open
   `KnowledgeGraph`, `BrainEngine`, and `IdentityResolver`.

2. **Resolve the active portfolio set.** Query the KG for every entity
   where the currently-true `lifecycle_status` triple equals
   `portfolio`. Partition by `portfolio_status` (currently-true sub-state
   triple): `active` first, then `exited`/`written_off` only if the
   sub-state changed in the last 7 days (otherwise drop — they're
   archive material, not weekly attention).

3. **Per-company snapshot.** For each portfolio entity in scope, call
   `compile_agent_context(role="weekly-portfolio-update",
   task=<digest phrase>, attendees=[entity_id], kg=kg, engine=engine,
   identity_resolver=resolver, plane="firm", tier_floor="policy")`.
   That pulls: currently-true triples on the company, recent events,
   open questions, related promoted pages.

4. **Compute staleness flags.** For each company, read its page's
   frontmatter `quarterly_update_cadence_days` and compare to the
   timestamp of the most recent quarterly-update artefact. If the gap
   exceeds the cadence, flag as `attention: stale_quarterly_update`.
   If the page lacks `quarterly_update_cadence_days`, surface a
   forcing question — do not pick a default.

5. **Aggregate to a digest.** Render markdown sections in this order:
   `## Active portfolio` (per-company micro-brief, oldest stale first,
   then by entity name), `## Recent state changes` (sub-state
   transitions in the last 7 days), `## Needs attention` (stale
   companies + missing artefacts), `## Archive deltas` (exits /
   write-offs in the last 7 days). Every fact carries its citation.

6. **Hand off to host LLM.** Pass the rendered digest with a prompt
   like: "Turn this into a 1-page partner-ready portfolio brief for
   the Monday review. Use only the facts provided. Cite every claim.
   Lead with `Needs attention` if non-empty."

7. **Log a `DraftEvent`** with: portfolio company count, stale count,
   recent-change count, render length. Keep `user_action="pending"`
   until the operator confirms / edits / sends.

8. **Never auto-write.** If the operator says during review "Acme
   actually exited last Wednesday," respond with: "Run
   `update-deal-status` with the source artefact. This skill is
   read-only — it surfaces signals; `update-deal-status` records
   transitions through the review gate."

## Forcing questions (never guess)

- **Empty portfolio set:** "We resolved zero deals currently in
  `lifecycle_status: portfolio`. Is that correct, or should we widen
  the search (e.g. include `decision`-stage deals awaiting closing)?"
- **Missing `quarterly_update_cadence_days`:** "<Company> has no
  cadence on its page. Skip staleness check for it, or use the
  constitution's `fund_thesis_review_cadence` as a fallback?"
- **Operator volunteers a state change:** "Sounds like a transition.
  Which source attests it? Run `update-deal-status` with that source
  rather than recording it inline here."
- **Tier_floor escalation:** "Raise to `doctrine` (include thesis
  framing in each company brief) or stay at `policy` (partner-
  internal facts only)?"

## Where state changes

Nothing writes to the KG or pages. The skill:
- Reads currently-true triples from `KnowledgeGraph`.
- Reads pages from `BrainEngine` at the chosen tier floor.
- Reads canonical names from `IdentityResolver`.
- Logs a `DraftEvent` (manually) and inherits `RetrievalEvent`s from
  `BrainEngine.query`. No `KGUpdateEvent`, no proposals.

## What this skill does NOT do

- **Propose anything.** No `create_proposal`. State changes flow
  through `update-deal-status` / `record-ic-decision`.
- **Cross firm boundary.** One firm, one portfolio digest. No
  cross-firm aggregation.
- **Auto-schedule.** This skill produces the digest; cadence is the
  caller's problem (cron / `/loop` / the operator runs it).
- **Coverage analytics.** That's `synthesis/coverage.py`'s job
  (Context Farmer surface, ADR-0012). The digest is partner-ready
  prose; coverage is structural debt.
- **Heuristic resolution.** If a company entity doesn't resolve to a
  stable ID, surface a forcing question; don't fall back to
  string-matching.

## On crash / resume

Pure read. Idempotent. If interrupted mid-render, re-running the skill
produces the same digest given the same inputs (modulo `generated_at`).

## Self-rewrite hook

After every 5 digests OR on any operator-reported failure:

1. Read the last 5 `DraftEvent` rows for this workflow. Check the ratio
   of `user_action="sent"` vs `"edited"` / `"discarded"`. If edits
   dominate, look for common edit patterns — sections may be in the
   wrong order, or the LLM prompt may be over-narrating.
2. Track which companies routinely surface as `attention: stale_*` but
   the operator never acts on them — those companies' cadences may be
   wrong (revisit `quarterly_update_cadence_days` on the page).
3. Track operator volunteer-state-change frequency. If high, the
   firm's source-artefact discipline has gaps; flag for the
   review-proposals reviewer.
4. Commit: `skill-update: weekly-portfolio-update, <one-line reason>`.

## Related

- `firm/concepts/venture-constitution.md` — `portfolio_statuses` +
  `fund_thesis_review_cadence` (advisory fallback for
  cadence-missing companies).
- `overlays/venture/page_templates/portfolio_company.md` — defines
  `quarterly_update_cadence_days` per-company.
- `skills/meeting-prep/SKILL.md` — sibling read-only briefing skill;
  same `compile_agent_context` primitive, different scope.
- `skills/update-deal-status/SKILL.md` — sibling write-side skill;
  the digest routes the operator here when state needs changing.
- `skills/record-ic-decision/SKILL.md` — sibling write-side skill for
  IC-quorum-validated decisions.
- `src/memory_mission/synthesis/compile.py` — `compile_agent_context`
  primitive consumed once per portfolio entity.
