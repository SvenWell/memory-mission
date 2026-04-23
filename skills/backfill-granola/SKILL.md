---
name: backfill-granola
version: "2026-04-22"
triggers: ["backfill granola", "import meeting transcripts", "sync granola transcripts", "pull historical meetings"]
tools: [granola_connector, durable_run, staging_writer, observability_scope]
preconditions:
  - "granola connector has a ComposioClient injected"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "stage under target_plane='personal' with the employee_id — never firm-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in Step 9"
  - "do not write directly to wiki MECE domains; promotion is Step 10"
category: ingestion
---

# backfill-granola — pull meeting transcripts into the personal staging plane

## What this does

Pulls the employee's Granola meeting transcripts through the
Composio-backed Granola connector into
`<wiki_root>/staging/personal/<employee_id>/granola/` for the extraction
agent (Step 9) to consume. Idempotent across re-runs: each transcript is
a checkpointed step under a durable run, so a crash partway through
resumes from the last processed transcript.

**Plane discipline:** Granola is a personal source — the transcripts
belong to the employee whose calls they recorded. Each employee
backfills their own transcripts into their own personal staging zone.
Nothing lands in `staging/firm/` from this skill. Firm-level
institutional knowledge comes from `skills/backfill-firm-artefacts`,
not from any one employee's meeting history.

## Workflow

Open an observability scope for the firm + employee. Inside it, open a
durable run named `backfill-granola-<firm_id>-<employee_id>` so all
per-transcript progress lives in one resumable thread. Stand up a
Granola connector via `make_granola_connector` and a staging writer
scoped to source `granola`, `target_plane="personal"`,
`employee_id=<this employee>`.

Pull the list of transcript ids (paginated by Granola's API max). For
each id:

- If the durable run has already marked it done, skip.
- Otherwise, fetch the transcript through the connector harness — never
  the connector's `invoke()` directly. The harness writes a
  `ConnectorInvocationEvent` with PII-scrubbed preview and latency.
- Hand the raw payload to the staging writer with the transcript id, and
  pass through any caller-relevant frontmatter extras (meeting title,
  participants, recorded_at, granola tags). The writer atomically
  writes the raw JSON sidecar plus a frontmatter-headed markdown file.
  The frontmatter records `target_plane: personal` and `employee_id:
  <id>` so the extraction agent sees where this item belongs.
- Mark the durable step done with state `{"transcript_id": id}` so the
  resumed run after a crash sees an accurate last-processed marker.

Continue until Granola returns no more transcripts. Then complete the
durable run.

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/granola/.raw/<transcript_id>.json
<wiki_root>/staging/personal/<employee_id>/granola/<transcript_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Nothing under `<wiki_root>/firm/`, `<wiki_root>/personal/<employee_id>/`
(curated), or any MECE domain directly. The extraction agent (Step 9)
reads from personal staging; the promotion pipeline (Step 10) reviews
proposals before any fact lands in the personal or firm plane.

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No `MentionTracker` updates. Mention counts are derived from
  extracted entities, not raw transcripts.
- No direct connector `invoke()` calls. The harness is mandatory so
  every fetch is observable + PII-scrubbed.
- No live OAuth flow. The Composio client is injected by the caller;
  if it's missing, the connector raises `NotImplementedError` and this
  skill should surface the error rather than try to bootstrap auth.
- No firm-plane staging. This skill is employee-personal; firm-level
  content comes from `skills/backfill-firm-artefacts`.
- No promotion. That's `skills/review-proposals`.

## On crash

The durable run guarantees resume-from-last-completed-step semantics.
Re-running the same `thread_id` after a crash skips already-processed
transcripts and continues from where the previous run stopped. Each
transcript is processed exactly once across the lifetime of a thread,
even across multiple crashes. The integration test in
`tests/test_connectors.py` (`test_backfill_loop_resumes_cleanly_after_crash`)
demonstrates the contract on the Gmail connector; same shape applies
here.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=granola` from the observability log.
2. If a new failure mode appears (consistent error, latency spike,
   PII-redaction count anomaly, missing transcript bodies), append a
   one-line lesson to `KNOWLEDGE.md` next to this file.
3. If a constraint above was violated (write landed in firm-plane
   staging, an LLM call snuck in), escalate as a project memory so
   future sessions read the boundary correction.
4. Commit: `skill-update: backfill-granola, <one-line reason>`.
