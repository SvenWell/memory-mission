---
name: backfill-slack
version: "2026-04-25"
triggers: ["backfill slack", "import slack", "sync slack workspace", "pull slack history", "ingest slack messages"]
tools: [slack_connector, slack_message_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "slack connector has a ComposioClient injected (Slack OAuth2 / Bearer via Composio with scopes for channels/groups/im/mpim history + users:read)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `chat` binding (app: slack, target_plane: firm)"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts (employee_id needed for DM/MPDM staging)"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by slack_message_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "DMs and MPDMs MUST stage to target_plane='personal' with the employee_id; the envelope helper enforces this structurally — do not override"
  - "internal channels and external-shared channels stage to target_plane='firm'"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "DM rules in the manifest MUST come before is_private rules (DMs are also is_private in Slack); see ADR-0011"
category: ingestion
---

# backfill-slack — pull message history into the right plane per channel-type

## What this does

Pulls Slack message history through the Composio-backed Slack
connector, normalizes each message into a `NormalizedSourceItem` via
`slack_message_to_envelope`, and writes the envelope into the
appropriate staging zone. Each message is a checkpointed step under a
durable run.

**Plane discipline (this skill is special):**

- **DMs** (`is_im`) and **group DMs** (`is_mpim`) → `target_plane:
  personal`, scoped to the running `employee_id`. Other employees
  never see them.
- **Internal channels** (public + private, all members are firm
  employees) → `target_plane: firm`.
- **External shared channels** (`is_ext_shared`) → `target_plane:
  firm` with `external-shared` scope.

The envelope helper enforces the plane override structurally based on
`is_im` / `is_mpim` flags from the channel metadata. There is no
manifest rule that can route a DM to firm staging. See ADR-0011 for
why.

**Visibility discipline:** Slack channel-type flags surface as
top-level metadata fields. The manifest maps them to firm scope.
Critical rule-ordering note: **DM (`is_im` / `is_mpim`) rules MUST
come before `is_private` rules**, because Slack DMs are also
`is_private: true`. Without this ordering the `is_private` rule
fires first and the DM gets the wrong scope (the helper still
overrides plane to personal, but scope is from the rule).

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm + employee.

3. **Stand up the Slack connector** via `make_slack_connector(client=
   <ComposioClient>)`.

4. **Stand up TWO `StagingWriter` instances:**
   - `personal_writer = StagingWriter(source="slack",
     target_plane="personal", employee_id=<this employee>)` for
     DMs/MPDMs.
   - `firm_writer = StagingWriter(source="slack",
     target_plane="firm")` for channels.

5. **Enumerate channels** via `invoke(connector, "list_channels",
   {"types": "public_channel,private_channel,im,mpim", "limit": 1000})`.
   Cache the channel metadata — the envelope helper needs the full
   channel dict for each message it processes.

6. **For each channel**, open a separate `durable_run` named
   `backfill-slack-<firm_id>-<employee_id_or_firm>-<channel_id>` so
   per-channel resume contracts stay clean.

7. **For each channel**, paginate through messages via
   `invoke(connector, "list_messages", {"channel": <id>, "oldest":
   <ts>, "limit": 200})`.

8. **For each message:**
   - If the durable run has already marked it done, skip.
   - Convert raw → envelope:
     `item = slack_message_to_envelope(message, channel=channel_meta,
     manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a rule for the channel-type or sets `default_visibility`.
     - On `ValueError` (missing ts / invalid ts / empty channel):
       log + skip.
   - **Pick the writer based on item.target_plane**:
     - `personal` → `personal_writer.write_envelope(item)`
     - `firm` → `firm_writer.write_envelope(item)`
   - The writer's plane-match assertion will pass because the helper
     computed the plane from is_im/is_mpim — no mismatch possible.
   - Mark the durable step done with state `{"channel_id": <id>,
     "ts": <ts>}`.

9. **Optionally fetch thread replies.** For messages where the helper
   set `slack_thread_ts == ts` in metadata (root messages with a
   thread), fetch replies via `invoke(connector, "get_replies",
   {"channel": <id>, "thread_ts": <ts>})` and process each reply
   as a separate message. The reply's envelope will have
   `slack_thread_ts` set to the root's ts, preserving thread
   structure for the proposal pipeline.

10. Continue per-channel until pagination exhausts. Complete each
    durable run.

## Channel discovery

`list_channels` with `types` set to all four (public + private + im +
mpim) returns everything the bot has access to. For an external
shared channel, `is_shared: true` and `is_ext_shared: true` will both
be set. The helper surfaces these flags directly so the manifest can
distinguish.

## Where the data lands

```
# Channels (firm plane)
<wiki_root>/staging/firm/slack/.raw/<channel_id>_<ts>.json
<wiki_root>/staging/firm/slack/<channel_id>_<ts>.md

# DMs / MPDMs (personal plane, per employee)
<wiki_root>/staging/personal/<employee_id>/slack/.raw/<channel_id>_<ts>.json
<wiki_root>/staging/personal/<employee_id>/slack/<channel_id>_<ts>.md

<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

`external_id` format is `<channel_id>_<ts>` — Slack's canonical
message identity. ts is `epoch.microseconds`.

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- **No DM bypass to firm plane.** The envelope helper's plane
  override is structural; do not try to work around it.
- No promotion.
- No write-side mutations (postMessage / addReaction / archive) —
  those route through P5 sync-back.
- No file-attachment ingestion. Slack file uploads are first-class
  Slack objects (not in messages). Add a separate skill +
  envelope helper if a pilot needs attachments.

## On crash

Per-channel durable runs guarantee exactly-once-per-thread
processing. Re-running the same `thread_id` skips already-processed
messages.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=slack` from the observability log.
2. If a new failure mode appears (rate-limiting, repeated
   `VisibilityMappingError` from a channel-type combo we don't model,
   missing scopes for private/MPIM history), append a one-line lesson
   to `KNOWLEDGE.md` next to this file.
3. If a constraint was violated (DM landed in firm staging, or vice
   versa), escalate as a project memory IMMEDIATELY — that's a
   privacy boundary violation.
4. Commit: `skill-update: backfill-slack, <one-line reason>`.
