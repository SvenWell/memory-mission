---
name: backfill-outlook
version: "2026-04-25"
triggers: ["backfill outlook", "import outlook history", "sync outlook mailbox", "pull historical email outlook", "import m365 mail"]
tools: [outlook_connector, outlook_message_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "outlook connector has a ComposioClient injected (M365 OAuth via Composio)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with an `email` binding (app: outlook)"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by outlook_message_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='personal' with the employee_id — never firm-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
category: ingestion
---

# backfill-outlook — pull historical Microsoft 365 email into personal staging

## What this does

Microsoft 365 / Outlook equivalent of `backfill-gmail`. Pulls the
employee's Outlook mail through the Composio-backed Outlook connector,
normalizes each message to a `NormalizedSourceItem` via
`outlook_message_to_envelope`, and writes the envelope into
`<wiki_root>/staging/personal/<employee_id>/outlook/` via
`StagingWriter.write_envelope`. Each message is a checkpointed step
under a durable run.

**Plane discipline:** Personal source. Each employee pulls their own
mail.

**Visibility discipline:** Outlook carries a built-in `sensitivity`
field (`normal` / `personal` / `private` / `confidential`) surfaced as
`outlook_sensitivity` in the envelope's visibility metadata —
operators typically write `if_field` rules against it (e.g.
`confidential → lp-only`, `private → employee-private`). Outlook
**categories** (the user-assigned tags) surface as `labels` so
`if_label` rules work the same way they do for Gmail labels.

## Workflow

Same shape as `backfill-gmail`:

1. Load the systems manifest from `$MM_WIKI_ROOT/firm/systems.yaml`.
2. Open `observability_scope` for firm + employee.
3. Open `durable_run` named `backfill-outlook-<firm_id>-<employee_id>`.
4. Stand up `make_outlook_connector(client=<ComposioClient>)`.
5. Stand up `StagingWriter(source="outlook", target_plane="personal",
   employee_id=...)`.
6. Pull message ids via `invoke(outlook_connector, "list_messages",
   {"max_results": 200})`. Page through results.
7. Per id: skip if done; fetch via `invoke(outlook_connector,
   "get_message", {"message_id": id})`; convert via
   `outlook_message_to_envelope(result.data, manifest=manifest)`;
   stage via `staging.write_envelope(item)`; mark step done.
8. After full backfill, switch to incremental sync via
   `get_mail_delta` (Outlook's delta-token feed). The skill's
   first run is a full backfill; subsequent runs use the delta token
   to fetch only new/changed messages — much cheaper than re-paging.

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/outlook/.raw/<message_id>.json
<wiki_root>/staging/personal/<employee_id>/outlook/<message_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Frontmatter on each `<message_id>.md` includes the canonical staging
fields plus envelope structural fields (`source_role: email`,
`external_object_type: message`, `target_scope`, `container_id`
(conversation_id), `url` (Outlook web link), `modified_at` (Outlook's
`receivedDateTime`)).

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No live OAuth flow — Composio handles M365 SSO at its layer.
- No firm-plane staging.
- No promotion.

## On crash

The durable run guarantees exactly-once-per-thread processing.
Re-running the same `thread_id` skips already-processed message ids.
The `get_mail_delta` action also lets you resume the incremental sync
from a saved delta token without losing the gap between crashes.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=outlook` from the observability log.
2. If a new failure mode appears (M365 throttling, repeated
   `VisibilityMappingError` from a sensitivity value or category we
   forgot to map, delta-token staleness), append a one-line lesson
   to `KNOWLEDGE.md` next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-outlook, <one-line reason>`.
