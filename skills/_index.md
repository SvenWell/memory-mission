# Skill Registry

Read this file first. Full `SKILL.md` contents load only when a skill's
triggers match the current task. Machine-readable equivalent:
`skills/_manifest.jsonl`. Conventions: `skills/_writing-skills.md`.

## backfill-gmail

Pull historical email through the Gmail connector (Composio-backed) into
the **employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/gmail/`) for the extraction
agent (Step 9) to consume. Each message becomes a checkpointed step
under a durable run, so a crash mid-loop resumes from the last processed
message. No LLM calls, no extraction, no firm-plane writes.

Triggers: "backfill gmail", "import email history", "sync gmail mailbox",
"pull historical email"

Constraints: personal plane only (never firm staging), no writes to
curated wiki pages, no LLM inside the loop, every fetch flows through
the connector harness.

## extract-from-staging

Read source items from staging (Gmail message, Granola transcript, Drive
memo), run the host agent's LLM with the `EXTRACTION_PROMPT`, parse the
response into an `ExtractionReport` (six fact kinds: identity /
relationship / preference / event / update / open_question), and write
to fact staging via `ingest_facts()`. No direct writes to the knowledge
graph — promotion pipeline (Step 10) reviews proposals first.

Triggers: "extract from staging", "extract facts", "run extraction",
"process staged items"

Constraints: Memory Mission imports no LLM SDK (host agent runs the
LLM), every fact must carry a `support_quote` from the source, low
confidence routes to `open_question`, extracted target_plane must
match source target_plane.

## backfill-granola

Pull historical meeting transcripts through the Granola connector
(Composio-backed) into the **employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/granola/`) for the
extraction agent (Step 9) to consume. Same shape as backfill-gmail,
different source. Each transcript is a checkpointed step.

Triggers: "backfill granola", "import meeting transcripts",
"sync granola transcripts", "pull historical meetings"

Constraints: personal plane only, every fetch through the harness,
no LLM, no firm-plane writes.

## backfill-firm-artefacts

Cold-start the firm plane from firm-authored documents (memos,
decks, training docs, quarterly updates, board material) via the
Drive connector. **Administrator-run only.** Stages under
`<wiki_root>/staging/firm/drive/`; resulting proposals go through
`skills/review-proposals` for merge gate. Solves Emile's authority
problem — firm truth comes from firm-authored content, not one
employee agent's extracted opinions.

Triggers: "backfill firm artefacts", "cold-start firm knowledge",
"import firm documents", "seed firm wiki", "ingest drive folder"

Constraints: firm plane only (no employee_id), administrator-run,
reviewer at the merge gate is separate from the administrator who
pulled the source, every fetch through the harness, no LLM.

## review-proposals

PR-model promotion review: the V1 centerpiece. Surface pending
proposals from the `ProposalStore` one at a time to a human reviewer,
capture the decision with required rationale, call `promote()` /
`reject()` / `reopen()`. No auto-approve on any signal. Every approve
atomically applies the proposal's facts to the firm's
`KnowledgeGraph` with full provenance.

Triggers: "review proposals", "pending reviews", "what's in the queue",
"approve proposals", "review pending promotions"

Constraints: rationale required on every decision (rubber-stamping
structurally blocked), one proposal at a time (no batch approval),
honor the permissions policy (skip proposals the reviewer can't
decide), stop on error during promote (don't cascade failures).

## detect-firm-candidates

Federated cross-employee pattern detector: scan the firm's
`KnowledgeGraph` for personal-plane triples that appear across
N≥3 employees via N≥3 distinct source documents, and stage a
pending firm-plane `Proposal` for each qualifying pattern. The
`review-proposals` skill then surfaces each proposal to a human
reviewer. Independence enforced by distinct-source-file threshold
— three employees sharing one Granola transcript does NOT fire.

Triggers: "detect firm candidates", "find cross-employee patterns",
"federated detection", "what do employees agree on",
"scan for firm truth"

Constraints: administrator-run only, no direct KG writes (proposals
only), no auto-promotion, independence check must pass, stop on
error (don't cascade).

## meeting-prep

Compile a distilled context package (doctrine + per-attendee
outgoing / incoming / events / preferences / related pages) for a
specific meeting or task, render it as markdown, and hand it to the
host-agent LLM for drafting. First workflow-level skill in Memory
Mission; reuses `compile_agent_context` primitive which other
workflow skills (email-draft, CRM-update, deal-memo) can share.
Reads-only: never writes to KG or pages.

Triggers: "prep meeting", "prep for", "brief on", "who is",
"what do we know about", "meeting prep", "meeting with"

Constraints: no LLM call inside the skill (host owns it), must not
include superseded facts, every fact cites source_closet /
source_file, no auto-promotion of observations, respect tier_floor
in constitutional-mode firms.
