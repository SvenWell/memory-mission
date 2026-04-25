---
name: backfill-calendar
version: "2026-04-25"
triggers: ["backfill calendar", "import calendar history", "sync gcal", "pull historical events", "import meetings"]
tools: [calendar_connector, calendar_event_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "calendar connector has a ComposioClient injected"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `calendar` binding (app: gcal)"
  - "wiki_root and observability_root are configured"
  - "firm_id and employee_id are resolved before the skill starts"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by calendar_event_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='personal' with the employee_id — never firm-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
category: ingestion
---

# backfill-calendar — pull Google Calendar events through the envelope into personal staging

## What this does

Pulls the employee's Google Calendar events through the Composio-backed
Calendar connector, normalizes each event into a `NormalizedSourceItem`
via `calendar_event_to_envelope`, and writes the envelope into
`<wiki_root>/staging/personal/<employee_id>/gcal/` via
`StagingWriter.write_envelope`. Each event is a checkpointed step under
a durable run.

**Plane discipline:** Calendar is a personal source — events belong to
the employee whose calendar holds them. Each employee backfills their
own events into their own personal staging zone. Nothing lands in
`staging/firm/` from this skill.

**Visibility discipline:** Google Calendar events carry a built-in
`visibility` field (`default` / `public` / `private` / `confidential`)
which the envelope helper surfaces as `gcal_visibility` in
`visibility_metadata`. A typical firm binding maps:

```yaml
calendar:
  app: gcal
  target_plane: personal
  visibility_rules:
    - if_field: { gcal_visibility: public }
      scope: external-shared
    - if_field: { gcal_visibility: private }
      scope: employee-private
  default_visibility: employee-private
```

Events with `visibility: default` (the most common case) inherit the
manifest's `default_visibility`. The mapping is fail-closed if neither
a rule nor a default match.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`
   via `load_systems_manifest(path)`.

2. **Open an `observability_scope`** for the firm + employee.

3. **Open a `durable_run`** named
   `backfill-calendar-<firm_id>-<employee_id>`.

4. **Stand up the Calendar connector** via
   `make_calendar_connector(client=<ComposioClient>)`. The connector's
   internal name is `gcal` (matches the manifest binding's `app`).

5. **Stand up a `StagingWriter`** scoped to `source="gcal"`,
   `target_plane="personal"`, `employee_id=<this employee>`. The
   writer's `source` label MUST match the manifest binding's `app`
   (also `"gcal"`) — `write_envelope` enforces this.

6. **Pull events** via `invoke(calendar_connector, "list_events",
   {"calendar_id": "primary", "time_min": ..., "time_max": ...,
   "max_results": 250})`. Page through results; the connector's
   `list_events` action accepts `page_token` for pagination.

7. **For each event id:**
   - If the durable run has already marked it done, skip.
   - Fetch the full event:
     `result = invoke(calendar_connector, "get_event",
     {"calendar_id": "primary", "event_id": id})`. Need the full
     payload (attendees, description, organizer) — `list_events`
     returns summaries.
   - Convert raw → envelope:
     `item = calendar_event_to_envelope(result.data, manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface.
     - On `ValueError` (missing id / updated): log and skip.
   - `staged = staging.write_envelope(item)` writes the raw JSON
     sidecar + frontmatter-headed markdown. Frontmatter records
     `target_scope`, `source_role: calendar`, `external_object_type:
     event`, `container_id: primary` (or whatever calendar id), `url`
     (htmlLink), and `modified_at` (Google's `updated`).
   - Mark the durable step done with state `{"event_id": id}`.

8. Continue until pagination exhausts. Complete the durable run.

## Non-primary calendars

If the employee subscribes to additional calendars (shared partner
calendar, portfolio company calendar), repeat the pull loop per
`calendar_id`. Open a separate durable run per calendar id so the
resume contract stays clean. The envelope's `container_id` carries the
calendar id so reviewers can tell which calendar an event came from.

## Recurring events

Recurring event instances each have their own `id` — Google Calendar
materializes them on demand. The backfill processes each instance as a
distinct item. The recurring root event id (when set) appears under
the raw payload's `recurringEventId`; downstream extraction can use it
to detect series membership without this skill having to know.

## Where the data lands

```
<wiki_root>/staging/personal/<employee_id>/gcal/.raw/<event_id>.json
<wiki_root>/staging/personal/<employee_id>/gcal/<event_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Envelope-derived frontmatter on each `<event_id>.md` includes
`source_role: calendar`, `external_object_type: event`, `target_scope`
(from the manifest), `container_id` (calendar id), `url` (htmlLink),
and `modified_at` (`updated` parsed). The raw JSON sidecar preserves
`start`, `end`, `attendees`, `organizer`, `recurringEventId`,
`location`, etc verbatim — extraction reads from there.

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No `MentionTracker` updates.
- No direct connector `invoke()` calls — the harness is mandatory.
- No direct `StagingWriter.write()` for envelope-shaped items —
  use `write_envelope(item)`.
- No silent visibility fallback. `VisibilityMappingError` halts the
  loop until an operator either adds a rule, sets
  `default_visibility`, or marks the offending event public/private.
- No live OAuth flow — Composio client is injected.
- No firm-plane staging.
- No conflict resolution between calendar events and email items
  about the same meeting — that's the extraction agent's job.

## On crash

Same shape as `backfill-gmail` and `backfill-granola`: the durable run
guarantees exactly-once-per-thread processing across crashes.
Re-running the same `thread_id` skips already-processed event ids.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=gcal` from the observability log.
2. If a new failure mode appears (rate-limiting, repeated
   `VisibilityMappingError`, attendee parsing failures, all-day-event
   datetime weirdness), append a one-line lesson to `KNOWLEDGE.md`
   next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-calendar, <one-line reason>`.
