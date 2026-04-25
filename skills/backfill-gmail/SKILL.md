---
name: backfill-gmail
version: "2026-04-25"
triggers: ["backfill gmail", "import email history", "sync gmail mailbox", "pull historical email"]
tools: [gmail_connector, gmail_message_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "gmail connector has a ComposioClient injected"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with an `email` binding (app: gmail)"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by gmail_message_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='personal' with the employee_id — never firm-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
category: ingestion
---

# backfill-gmail — pull historical email through the envelope into personal staging

## What this does

Pulls the employee's Gmail history through the Composio-backed Gmail
connector, normalizes each message into a `NormalizedSourceItem` via
`gmail_message_to_envelope`, and writes the envelope into
`<wiki_root>/staging/personal/<employee_id>/gmail/` via
`StagingWriter.write_envelope`. Each message is a checkpointed step
under a durable run so a crash partway through resumes from the last
processed message. No reasoning happens in this skill — it's a
pull → normalize → stage workflow.

**Plane discipline:** Gmail is a personal source. Each employee pulls
their own mail into their own personal staging zone. Nothing lands in
`staging/firm/` from this skill. Firm-level institutional knowledge
comes from `skills/backfill-firm-artefacts`, not from any one
employee's inbox.

**Visibility discipline:** The firm's `systems.yaml` decides which
firm scope each message lands at. The mapping is fail-closed by
default: a message that doesn't match any visibility rule and has no
`default_visibility` fallback is rejected at envelope construction
time (`VisibilityMappingError`). Surface the error; do not work around
it by silently retrying with a different scope.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`
   via `load_systems_manifest(path)`. This is the per-firm capability
   binding + visibility-mapping config.

2. **Open an `observability_scope`** for the firm + employee. Every
   connector invocation logs against this scope.

3. **Open a `durable_run`** named
   `backfill-gmail-<firm_id>-<employee_id>` so all per-message progress
   lives in one resumable thread.

4. **Stand up the Gmail connector** via `make_gmail_connector(client=
   <ComposioClient>)`.

5. **Stand up a `StagingWriter`** scoped to `source="gmail"`,
   `target_plane="personal"`, `employee_id=<this employee>`. The
   writer's `source` label MUST match the manifest binding's `app`
   (also `"gmail"`) — `write_envelope` enforces this.

6. **Pull the list of message ids** in pages (smallest reasonable page
   size, controlled by Gmail's API max).

7. **For each message id:**
   - If the durable run has already marked it done, skip.
   - Fetch the full message through the connector harness:
     `result = invoke(gmail_connector, "get_message", {"message_id":
     id})`. Never call `connector.invoke()` directly. The harness
     writes a `ConnectorInvocationEvent` with PII-scrubbed preview and
     latency.
   - Convert the raw payload to a `NormalizedSourceItem`:
     `item = gmail_message_to_envelope(result.data, manifest=manifest)`.
     - If this raises `VisibilityMappingError`, **log the message id
       and stop the loop**. Do not silently fall back to a permissive
       scope. The operator needs to fix the manifest (add a rule, set
       `default_visibility`, or label the message).
     - If this raises `ValueError` for a missing required field
       (id, internal_date), log and skip.
   - Stage the envelope: `staged = staging.write_envelope(item)`. The
     writer atomically writes the raw JSON sidecar plus a frontmatter-
     headed markdown file. The frontmatter records `target_scope`,
     `source_role`, `external_object_type`, `container_id`, `url`,
     `modified_at` — every downstream stage sees the firm-shaped scope
     without parsing the raw payload again.
   - Mark the durable step done with state `{"message_id": id}` so the
     resumed run after a crash sees an accurate last-processed marker.

8. When the page is exhausted, fetch the next page until Gmail returns
   none. Then complete the durable run.

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/gmail/.raw/<message_id>.json
<wiki_root>/staging/personal/<employee_id>/gmail/<message_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Frontmatter on each `<message_id>.md` includes (canonical fields the
base writer always sets, plus envelope structural fields):

| Field | Source |
|---|---|
| `source` | writer's `source` label (`gmail`) |
| `source_id` | message id |
| `target_plane` | `personal` |
| `employee_id` | the employee |
| `ingested_at` | when staging wrote it |
| `source_role` | `email` (from envelope) |
| `external_object_type` | `message` |
| `target_scope` | from `map_visibility` against the manifest |
| `container_id` | thread id when present |
| `url` | Gmail permalink when present |
| `modified_at` | Gmail's `internal_date` (parsed) |

Nothing under `<wiki_root>/firm/`,
`<wiki_root>/personal/<employee_id>/` (curated), or any MECE domain
directly. The extraction agent (`skills/extract-from-staging`) reads
from personal staging; the promotion pipeline
(`skills/review-proposals`) moves approved claims into the personal or
firm plane — always via review, never by this skill.

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No `MentionTracker` updates. Mention counts are derived from
  extracted entities, not raw email bodies.
- No direct connector `invoke()` calls. The harness is mandatory so
  every fetch is observable + PII-scrubbed.
- No direct `StagingWriter.write()` calls for envelope-shaped items.
  Use `write_envelope(item)` so plane + concrete-app alignment is
  validated and structural frontmatter lands automatically.
- No silent visibility fallback. `VisibilityMappingError` halts the
  loop until an operator either adds a rule, sets
  `default_visibility`, or labels the offending message.
- No live OAuth flow. The Composio client is injected by the caller.
- No firm-plane staging. Use `skills/backfill-firm-artefacts` for
  firm-level content.

## On crash

The durable run guarantees resume-from-last-completed-step semantics.
Re-running the same `thread_id` after a crash skips already-processed
message ids and continues from where the previous run stopped. Each
message is processed exactly once across the lifetime of a thread.
The integration test in
`tests/test_connectors.py` (`test_backfill_loop_resumes_cleanly_after_crash`)
demonstrates the contract.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=gmail` from the observability log.
2. If a new failure mode appears (consistent error, latency spike,
   PII-redaction count anomaly, repeated `VisibilityMappingError`
   from a label that should be added to the manifest), append a
   one-line lesson to `KNOWLEDGE.md` next to this file.
3. If a constraint above was violated (a write landed in firm-plane
   staging, an LLM call snuck in, the raw `write()` path was used
   instead of `write_envelope`), escalate as a project memory so
   future sessions read the boundary correction.
4. Commit: `skill-update: backfill-gmail, <one-line reason>`.
