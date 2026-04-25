---
name: backfill-granola
version: "2026-04-25"
triggers: ["backfill granola", "import meeting transcripts", "sync granola transcripts", "pull historical meetings"]
tools: [granola_connector, granola_transcript_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "granola connector has a ComposioClient injected"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `transcript` binding (app: granola)"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by granola_transcript_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='personal' with the employee_id — never firm-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
category: ingestion
---

# backfill-granola — pull meeting transcripts through the envelope into personal staging

## What this does

Pulls the employee's Granola meeting transcripts through the
Composio-backed Granola connector, normalizes each transcript into a
`NormalizedSourceItem` via `granola_transcript_to_envelope`, and writes
the envelope into
`<wiki_root>/staging/personal/<employee_id>/granola/` via
`StagingWriter.write_envelope`. Each transcript is a checkpointed step
under a durable run.

**Plane discipline:** Granola is a personal source — the transcripts
belong to the employee whose calls they recorded. Each employee
backfills their own transcripts into their own personal staging zone.
Nothing lands in `staging/firm/` from this skill.

**Visibility discipline:** The firm's `systems.yaml` decides which firm
scope each transcript lands at. Granola transcripts rarely carry rich
visibility metadata, so most firms set a sensible
`default_visibility` (e.g. `partner-only` or `employee-private`) on
the `transcript` binding. The mapping is still fail-closed by default
— a binding without `default_visibility` rejects every transcript.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`
   via `load_systems_manifest(path)`.

2. **Open an `observability_scope`** for the firm + employee.

3. **Open a `durable_run`** named
   `backfill-granola-<firm_id>-<employee_id>`.

4. **Stand up the Granola connector** via
   `make_granola_connector(client=<ComposioClient>)`.

5. **Stand up a `StagingWriter`** scoped to `source="granola"`,
   `target_plane="personal"`, `employee_id=<this employee>`. The
   writer's `source` label MUST match the manifest binding's `app`
   (also `"granola"`) — `write_envelope` enforces this.

6. **Pull the list of transcript ids** (paginated by Granola's API
   max).

7. **For each transcript id:**
   - If the durable run has already marked it done, skip.
   - Fetch through the harness: `result = invoke(granola_connector,
     "get_transcript", {"transcript_id": id})`.
   - Convert raw → envelope: `item = granola_transcript_to_envelope(
     result.data, manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       sets `default_visibility` on the `transcript` binding or adds a
       rule.
     - On `ValueError` (missing id / created_at): log and skip.
   - `staged = staging.write_envelope(item)` writes the raw JSON
     sidecar + frontmatter-headed markdown with the envelope's
     `target_scope`, `source_role`, `external_object_type`,
     `container_id` (meeting_id), `url`, and `modified_at`.
   - Mark the durable step done with state `{"transcript_id": id}`.

8. Continue until Granola returns no more transcripts. Complete the
   durable run.

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/granola/.raw/<transcript_id>.json
<wiki_root>/staging/personal/<employee_id>/granola/<transcript_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Envelope-derived frontmatter on each `<transcript_id>.md` includes
`source_role: transcript`, `external_object_type: transcript`,
`target_scope` (from the manifest), `container_id` (meeting_id when
present), `url`, and `modified_at` (Granola's `created_at`, parsed).

Nothing under `<wiki_root>/firm/`,
`<wiki_root>/personal/<employee_id>/` (curated), or any MECE domain
directly.

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No `MentionTracker` updates.
- No direct connector `invoke()` calls — the harness is mandatory.
- No direct `StagingWriter.write()` for envelope-shaped items —
  use `write_envelope(item)`.
- No silent visibility fallback.
- No live OAuth flow — Composio client is injected.
- No firm-plane staging.
- No promotion — that's `skills/review-proposals`.

## On crash

Same shape as `backfill-gmail`: the durable run guarantees
exactly-once-per-thread processing across crashes. Re-running the same
`thread_id` skips already-processed transcripts.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=granola` from the observability log.
2. If a new failure mode appears (consistent error, latency spike,
   PII-redaction count anomaly, missing transcript bodies, repeated
   `VisibilityMappingError`), append a one-line lesson to
   `KNOWLEDGE.md` next to this file.
3. If a constraint above was violated (firm-plane write, LLM inside
   the loop, raw `write()` path used), escalate as a project memory.
4. Commit: `skill-update: backfill-granola, <one-line reason>`.
