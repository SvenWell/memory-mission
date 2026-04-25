---
name: backfill-affinity
version: "2026-04-25"
triggers: ["backfill affinity", "import affinity", "sync crm", "pull affinity organizations", "pull affinity persons", "pull affinity opportunities"]
tools: [affinity_connector, affinity_record_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "affinity connector has a ComposioClient injected (Affinity uses API-key auth — the firm provisions a per-firm key in Composio's dashboard)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `workspace` binding (app: affinity, target_plane: firm)"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by affinity_record_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='firm' — Affinity is firm-shared CRM data, not personal"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "administrator-run only — Affinity holds the firm's deal pipeline + portfolio + LP relationships"
category: ingestion
---

# backfill-affinity — pull venture-CRM records into the firm staging plane

## What this does

Pulls the firm's Affinity records (organizations, persons, opportunities)
through the Composio-backed Affinity connector, normalizes each record
into a `NormalizedSourceItem` via `affinity_record_to_envelope`, and
writes the envelope into `<wiki_root>/staging/firm/affinity/` via
`StagingWriter.write_envelope`. Each record is a checkpointed step
under a durable run.

**Plane discipline:** Affinity holds the firm's relationship + deal +
portfolio data — it is firm-shared, not personal. Records land in
`staging/firm/affinity/`. The promotion pipeline then reviews each
record before it becomes firm truth.

**Visibility discipline:** Affinity records belong to one or more
**Lists** (the firm's deal pipelines, portfolio, LP network, …). List
membership is the primary visibility signal. The envelope helper
surfaces each list as a `list:<list_id>` label so visibility rules
match like `if_label: list:42 → scope: partner-only`. Globally-known
companies (Affinity's `global: true` flag) get a `global` label that
typically maps to `external-shared`.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm. (No `employee_id` —
   firm-plane scope.)

3. **Stand up the Affinity connector** via `make_affinity_connector(
   client=<ComposioClient>)`.

4. **Stand up a `StagingWriter`** scoped to `source="affinity"`,
   `target_plane="firm"`. The writer's `source` label MUST match the
   manifest binding's `app` (also `"affinity"`) — `write_envelope`
   enforces this.

5. **First, enumerate the firm's lists** via `invoke(connector,
   "list_lists", {})`. Cache the list ids — visibility rules in the
   manifest reference them.

6. **For each object type** (organizations, persons, opportunities),
   open a separate `durable_run` named
   `backfill-affinity-<firm_id>-<object_type>` so resume contracts
   stay clean per type.

7. **For each record id in the type:**
   - If the durable run has already marked it done, skip.
   - Fetch the full record via `invoke(connector, "get_organization",
     {"organization_id": id})` (etc per type). Don't use the
     `list_*` action's payloads directly — Affinity's pagination
     yields basic info but excludes field data.
   - Convert raw → envelope:
     `item = affinity_record_to_envelope(result.data,
     object_type="organization", manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a list-id rule or sets `default_visibility`.
     - On `ValueError` (missing id / dates): log and skip.
   - `staged = staging.write_envelope(item)` writes the raw JSON
     sidecar + frontmatter-headed markdown with `target_scope`,
     `source_role: workspace`, `external_object_type:
     organization|person|opportunity`, `container_id: list_<first_id>`,
     and `modified_at` (last interaction date when present).
   - Mark the durable step done with state `{"object_id": id, "type":
     <type>}`.

8. Continue until pagination exhausts per type. Complete each durable
   run.

## Three-pass strategy

Recommended order: **organizations → persons → opportunities.** Persons
typically reference organization ids (`organization_ids` field), and
opportunities reference both. Backfilling in this order ensures the
KG identity resolver has organizations canonicalized before persons /
opportunities link to them.

## Where the data lands

```
<wiki_root>/staging/firm/affinity/.raw/<external_id>.json
<wiki_root>/staging/firm/affinity/<external_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

`external_id` is type-prefixed: `org_<id>`, `person_<id>`, `opp_<id>`
to avoid id collision across types.

Frontmatter on each `<external_id>.md` includes:

| Field | Source |
|---|---|
| `source_role` | `workspace` |
| `external_object_type` | `organization` / `person` / `opportunity` |
| `target_scope` | from the list-membership-driven manifest mapping |
| `container_id` | first list id (`list_<id>`) the record sits in |
| `modified_at` | Affinity's `interaction_dates_last_interaction_date` if present, else `dates_modified_date`, else `dates_created_date` |

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No direct connector `invoke()` calls — the harness is mandatory.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No personal-plane staging — Affinity is firm-shared.
- No promotion — that's `skills/review-proposals`.
- No URL synthesis. Affinity URLs use `https://<workspace>.affinity.co/...`
  but workspace is firm-specific. The envelope's `url` stays `None`
  unless the raw payload includes one explicitly.

## On crash

Per-type durable runs guarantee exactly-once-per-thread processing.
Re-running the same `thread_id` skips already-processed records.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=affinity` from the observability log.
2. If a new failure mode appears (rate-limiting, repeated
   `VisibilityMappingError` from a list id missing in the manifest,
   field-data-missing on opportunities, attendee parsing failures),
   append a one-line lesson to `KNOWLEDGE.md` next to this file.
3. If a constraint was violated (firm-plane bypass, LLM inside loop,
   raw `write()` used), escalate as a project memory.
4. Commit: `skill-update: backfill-affinity, <one-line reason>`.
