---
name: extract-from-staging
version: "2026-04-22"
triggers: ["extract from staging", "extract facts", "run extraction", "process staged items"]
tools: [staging_reader, llm, ingest_facts, mention_tracker, observability_scope]
preconditions:
  - "host agent has an LLM available (Claude, GPT, Gemini, or local model)"
  - "staging has at least one pending item for the requested source"
  - "firm_id and employee_id resolved before the skill starts"
  - "wiki_root + observability_root configured"
constraints:
  - "Memory Mission must not import any LLM SDK — host agent runs the LLM"
  - "every fact must carry support_quote from the source text — no quote, no fact"
  - "write extracted reports to fact staging only, never directly to the knowledge graph"
  - "low-confidence facts (< 0.3) must be dropped; 0.3-0.6 land as open_question"
  - "target_plane of extracted facts must match the source item's target_plane"
category: ingestion
---

# extract-from-staging — turn staged source items into reviewable claims

## What this does

Reads items from source staging (emails from Gmail, transcripts from
Granola, memos from Drive), runs the host agent's LLM with the
`EXTRACTION_PROMPT` template, and writes the parsed output as an
`ExtractionReport` to fact staging. Fact staging is the input to the
promotion pipeline (Step 10) — no fact lands in the knowledge graph or
on a curated page until a human reviews and approves the proposal.

For pilot workflows that need a reviewable preview before fact staging
(for example `granola-extraction-pilot`), use
`src/memory_mission/extraction/dry_run.py`: `select_staged_items`
chooses the narrow source slice and `write_extraction_dry_run` writes
`staging/<plane>/<source>/.dry_run/<run_id>.jsonl` from host-produced
`ExtractionReport` objects. That dry-run path never calls
`ingest_facts` and never mutates KG / pages / proposals.

## Workflow

Open an observability scope for the firm + employee. Open the
`MentionTracker` for this firm. For each pending source item in the
staging zone matching the requested `target_plane` and `source`:

1. **Skip if already extracted.** Check fact staging via
   `ExtractionWriter.read(source_id)`; if a report exists, move on.
2. **Load the source body** from `StagingWriter.get(source_id)` — the
   markdown file's body minus its frontmatter.
3. **Call the host agent's LLM** with the `EXTRACTION_PROMPT` and the
   source body. Ask for a JSON object matching `ExtractionReport`.
4. **Validate the response** with
   `ExtractionReport.model_validate_json(...)`. If validation fails,
   surface the parse error as a forcing question — don't guess.
5. **Call `ingest_facts(report, wiki_root=..., mention_tracker=...)`**.
   The ingest function persists the report and records entity mentions.
   It returns `TierCrossing` events for every entity whose tier
   advanced; surface high-signal crossings (e.g. `is_promotion` True
   on entities crossing into `enrich` or `full`) via a forcing question
   so the human knows what's about to get proposed to the firm plane.
6. **Mark the source item done** if your pipeline uses a durable run
   for extraction batches; otherwise the fact-staging existence check
   in step 1 is the idempotency guard.

## Forcing questions

Ask these of the user when they surface, not guess:

- **Entity-match ambiguity:** "The source mentions 'Acme' — is this
  `acme-corp` (existing firm-plane page) or a new `acme-ventures` you
  want to create?"
- **Low-confidence open question:** "The LLM flagged: 'Is Mark Thompson
  still CFO at Acme Corp?' Want to promote as an open question or
  drop?"
- **Plane-escalation check on tier crossing:** "`acme-corp` just
  crossed into the `enrich` tier (3 mentions across your inbox). Want
  to draft a firm-plane proposal now, or keep staging personal-only?"
- **Validation failure:** "LLM returned invalid JSON: <error>. Want
  me to retry with a stricter prompt or skip this item for now?"

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/.facts/<source>/<source_id>.json
<wiki_root>/staging/firm/.facts/<source>/<source_id>.json
<observability_root>/<firm_id>/events.jsonl
<mention_tracker_db>
```

Each `<source_id>.json` is an `ExtractionReport` with all facts from
that source item. The promotion pipeline (Step 10) reads these,
bundles them into `Proposal` objects grouped by entity, and surfaces
them for review.

## What this skill does NOT do

- No direct writes to `KnowledgeGraph` — the promotion pipeline does
  that after a human approves.
- No direct writes to curated pages in `personal/` or `firm/`
  (non-staging zones).
- No LLM SDK imports in Memory Mission code — your host agent runs
  the LLM. This skill composes what Memory Mission ships (prompt +
  schema + ingest function).
- No auto-merge of contradictions — `update` facts record the
  supersession intent; actual invalidate + add_triple happens at
  promotion time.

## On crash / resume

This skill's idempotency guard is fact-staging existence: if
`<source_id>.json` already exists, the source item has been extracted.
Re-running the skill safely skips already-extracted items. For
long-running batches across many items, wrap the loop in a
`durable_run` named `extract-<firm_id>-<employee_id>-<source>` so
per-item LLM calls become checkpointed steps.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 extraction reports for this source from fact
   staging.
2. If a recurring pattern appears (LLM consistently mis-classifying a
   kind, or always missing a predicate), append a one-line lesson to
   `KNOWLEDGE.md` next to this file and consider proposing a
   `EXTRACTION_PROMPT` refinement via a plan.
3. If a constraint above was violated (direct KG writes, no
   support_quote, plane mismatch), escalate as a project memory.
4. Commit: `skill-update: extract-from-staging, <one-line reason>`.
