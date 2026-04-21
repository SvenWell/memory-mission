---
name: backfill-gmail
version: "2026-04-21"
triggers: ["backfill gmail", "import email history", "sync gmail mailbox", "pull historical email"]
tools: [gmail_connector, durable_run, staging_writer, observability_scope]
preconditions:
  - "gmail connector has a ComposioClient injected"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "write to staging only, never directly to wiki MECE domains"
  - "do not invoke an LLM inside this skill — extraction lives in Step 8"
category: ingestion
---

# backfill-gmail — pull historical email into the staging area

## What this does

Pulls the employee's Gmail history through the Composio-backed Gmail
connector into `<wiki_root>/staging/gmail/` for the extraction agent
(Step 8) to consume. Idempotent across re-runs: each message is a
checkpointed step under a durable run, so a crash partway through
resumes from the last processed message. No reasoning happens in this
skill — it's a pull-and-stage workflow.

## Workflow

Open an observability scope for the firm + employee. Inside it, open a
durable run named `backfill-gmail-<firm_id>-<employee_id>` so all per-
message progress lives in one resumable thread. Stand up a Gmail
connector via the factory and a staging writer scoped to source
`gmail`.

Pull the list of message ids in pages (smallest reasonable page size,
controlled by Gmail's API max). For each id:

- If the durable run has already marked it done, skip.
- Otherwise, fetch the message through the connector harness — never
  the connector's `invoke()` directly. The harness writes a
  `ConnectorInvocationEvent` with PII-scrubbed preview and latency.
- Hand the raw payload to the staging writer with the source id, and
  pass through any caller-relevant frontmatter extras (sender, subject,
  thread id, gmail labels). The writer atomically writes the raw JSON
  sidecar plus a frontmatter-headed markdown file.
- Mark the durable step done with state `{"message_id": id}` so the
  resumed run after a crash sees an accurate last-processed marker.

When the page is exhausted, fetch the next page until Gmail returns
none. Then complete the durable run.

## Where the data lands

```
<wiki_root>/staging/gmail/.raw/<message_id>.json    # connector payload, verbatim
<wiki_root>/staging/gmail/<message_id>.md           # frontmatter + body for review
<observability_root>/<firm_id>/events.jsonl         # one event per fetch
<durable_db_path>                                   # checkpointed run state
```

Nothing under `<wiki_root>/people/`, `<wiki_root>/companies/`, or any
other MECE domain. The extraction agent (Step 8) is the component that
proposes promotions out of staging into curated pages — and even that
goes through the promotion pipeline (Step 9), not directly into truth.

## What this skill does NOT do

- No LLM call. Extraction (deciding what facts an email contains) is
  a separate agent because LLM cost + latency don't belong inside the
  pull loop.
- No `MentionTracker` updates. Mention counts are derived from
  extracted entities, not raw email bodies. Wired in Step 8.
- No direct connector `invoke()` calls. The harness is mandatory so
  every fetch is observable + PII-scrubbed.
- No live OAuth flow. The Composio client is injected by the caller;
  if it's missing, the connector raises and this skill should surface
  the error rather than try to bootstrap auth.

## On crash

The durable run guarantees resume-from-last-completed-step semantics.
Re-running the same `thread_id` after a crash skips already-processed
message ids and continues from where the previous run stopped. Each
message is processed exactly once across the lifetime of a thread,
even across multiple crashes. The integration test in
`tests/test_connectors.py` (`test_backfill_loop_resumes_cleanly_after_crash`)
demonstrates the contract.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for `connector_name=gmail`
   from the observability log.
2. If a new failure mode appears (consistent error, latency spike,
   PII-redaction count anomaly), append a one-line lesson to
   `KNOWLEDGE.md` next to this file.
3. If a constraint above was violated (e.g., a write landed outside
   `staging/`, or an LLM call snuck in), escalate as a project memory
   so future sessions read the boundary correction.
4. Commit: `skill-update: backfill-gmail, <one-line reason>`.
