---
name: meeting-prep
version: "2026-04-22"
triggers: ["prep meeting", "prep for", "brief on", "who is", "what do we know about", "meeting prep", "meeting with"]
tools: [knowledge_graph, brain_engine, identity_resolver, compile_agent_context, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; employee_id known (the person the agent is briefing)"
  - "KnowledgeGraph available for this firm"
  - "BrainEngine (optional but recommended — needed for curated page retrieval)"
  - "IdentityResolver (optional — used to attach canonical names to stable entity IDs)"
  - "attendees resolved to stable IDs (p_<id> / o_<id>) OR raw entity names the KG already knows"
constraints:
  - "no direct LLM call inside this skill — compile_agent_context is pure Python; the host agent LLM consumes the render"
  - "must not include superseded facts (valid_to set) — compile_agent_context drops them; DO NOT fall back to query_entity without the same filter"
  - "must cite every fact with its source_closet / source_file — compile_agent_context handles this in render(); the draft should preserve citations"
  - "never auto-promote anything observed during prep to the firm plane; promotion requires the review gate"
  - "if the firm is in constitutional_mode, respect tier_floor on the context query so sensitive doctrine does not leak into every briefing"
category: workflow
---

# meeting-prep — distilled-context briefing for one workflow

## What this does

Given a list of attendees + a task description, compile a structured
context package (doctrine pages at or above the requested tier,
per-attendee outgoing / incoming / events / preferences / related
pages), render it to markdown, and hand it to the host-agent LLM to
turn into a briefing. Consumes the full stack: extraction →
promotion → corroboration → identity → tier → federated detection
all contribute to what lands in the context.

This is the first workflow-level consumer in Memory Mission. Later
skills (email-draft, CRM-update, deal-memo) reuse
`compile_agent_context` with different `role` values — the
primitive is deliberately general.

## Workflow

Open an observability scope for the firm + employee. Open the
`KnowledgeGraph`, `BrainEngine`, and (if available) `IdentityResolver`.

1. **Resolve attendees.** The caller should already have stable
   entity IDs. If the user types "Sarah Chen" and the resolver
   doesn't find that exact name, surface a `QUESTION:` — "Did you
   mean `p_abc123` (Sarah Chen, works at Acme) or `p_xyz789`
   (Sarah Chen-Peterson at Beta Fund)?" Do not guess.
2. **Pick the tier floor.** If the firm has a `constitutional_mode`
   policy or the task is sensitive, default to `tier_floor="policy"`.
   For exploratory meetings with new people, leave `tier_floor=None`
   (doctrine section omitted). When in doubt, ask.
3. **Call `compile_agent_context`** with `role="meeting-prep"`,
   the task string, attendee IDs, kg, engine, optional
   identity_resolver, plane (typically `"firm"` for shared briefings,
   `"personal"` for private prep), tier_floor, and optional `as_of`
   for historical prep.
4. **Render and hand off.** Call `ctx.render()` to get the markdown.
   Feed it to the host-agent LLM with a task-specific prompt
   ("Draft a 2-paragraph briefing for the Q3 review meeting tomorrow.
   Cite every claim. Flag any superseded facts the user should
   double-check.").
5. **Log a `DraftEvent`** with the workflow name, context pages
   involved, and the length of the output. Keep
   `user_action="pending"` until the user confirms / edits / sends.
6. **Never auto-promote.** If the briefing surfaces a new fact the
   agent discovered during drafting, the flow is: draft → user
   confirms → user runs extract-from-staging or adds a manual note →
   review-proposals gate. Meeting-prep does not write to the KG.

## Forcing questions (never guess)

- **Ambiguous attendee name:** "You said 'Sarah' — we have three
  stable IDs matching that first name. Which one?"
- **Empty context:** "We have zero facts on any of these attendees.
  Is this the correct firm / plane? Or is this a new relationship
  with no prior interactions captured?"
- **Superseded facts:** `compile_agent_context` drops them, but if
  the user explicitly asks "what did we know about X last year?",
  offer `as_of` time-travel. "Do you want the current view, or the
  view as of a specific date?"
- **Tier floor choice:** For sensitive briefings with external
  parties, "Should doctrine/constitution content be included, or
  keep to decision-tier facts only?" If no clear answer, default to
  `tier_floor=None` (no doctrine section) to minimize exposure.
- **Personal vs firm plane:** "Is this for your own prep notes
  (personal plane — only your extractions contribute) or for a
  shared meeting brief (firm plane — everything the firm knows)?"

Surface these as `QUESTION:` lines. The host agent's question
mechanism presents them.

## Where state changes

Nothing writes to the KG or pages. The skill:

- Reads from `KnowledgeGraph` (triples, possibly via `sql_query` for
  richer aggregations).
- Reads from `BrainEngine` (pages at tier floor, attendee-slug
  lookups).
- Reads from `IdentityResolver` (canonical name per attendee).
- Logs a `RetrievalEvent` (automatically by `BrainEngine.query`) and
  a `DraftEvent` (manually, after the LLM responds).

## Integration points

- `compile_agent_context(role, task, attendees, kg, engine=?,
  plane=?, tier_floor=?, as_of=?, identity_resolver=?)` — the
  primitive.
- `AgentContext` — the structured output. `.render()` → markdown
  string, `.attendees` / `.doctrine` / `.fact_count` /
  `.attendee_ids` for programmatic inspection (eval harness reads
  this directly per `docs/EVALS.md` section 2.8).

## What this skill does NOT do

- No LLM call inside Python. Memory Mission ships the context; the
  host agent's LLM does the drafting.
- No direct KG writes. Prep is read-only.
- No auto-promotion of observations. New facts flow through
  extract-from-staging → review-proposals.
- No cross-firm queries. Everything is per-firm by KG path.
- No heuristic name resolution. If the attendee ID isn't a stable
  ID or an exact entity name, surface a forcing question.

## On crash / resume

Pure read. Idempotent. If the skill is interrupted mid-draft, the
host agent can re-run it — `compile_agent_context` returns the same
structured package given the same inputs (modulo `generated_at`
timestamp).

## Self-rewrite hook

After every 5 briefings OR on any user-reported failure:

1. Read the last 5 `DraftEvent` rows for this workflow. Check the
   ratio of `user_action="sent"` vs `user_action="edited"` /
   `"discarded"`. If edits dominate, look for common edit patterns
   in the output preview — the rendering may need to change (e.g.,
   surface relationship-strength differently, include/exclude
   source citations by default).
2. Look at the distribution of `fact_count` across briefings. If
   users routinely ask for facts the skill didn't surface, the
   `compile_agent_context` scope is wrong — flag in
   `KNOWLEDGE.md` next to this file.
3. If the skill raised, escalate as a project memory with the
   input and the stack trace.
4. Commit: `skill-update: meeting-prep, <one-line reason>`.
