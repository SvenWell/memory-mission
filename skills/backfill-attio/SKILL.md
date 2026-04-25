---
name: backfill-attio
version: "2026-04-25"
triggers: ["backfill attio", "import attio", "sync attio crm", "pull attio records", "pull attio companies", "pull attio people", "pull attio deals"]
tools: [attio_connector, attio_record_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "attio connector has a ComposioClient injected (Attio uses OAuth2)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `workspace` binding (app: attio, target_plane: firm)"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by attio_record_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='firm' — Attio is firm-shared CRM data, not personal"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "administrator-run only — Attio holds the firm's CRM truth"
category: ingestion
---

# backfill-attio — pull schema-flexible CRM records into firm staging

## What this does

Pulls the firm's Attio records through the Composio-backed Attio
connector, normalizes each record into a `NormalizedSourceItem` via
`attio_record_to_envelope`, and writes the envelope into
`<wiki_root>/staging/firm/attio/` via `StagingWriter.write_envelope`.

**Plane discipline:** Firm plane. Attio is firm-shared CRM —
administrator-run only.

**Visibility discipline:** Attio uses **Lists** (saved views /
collections) as the primary classification mechanism. The envelope
helper surfaces each list-membership as a `list:<list_id>` label so
visibility rules match like `if_label: list:pipeline → scope:
partner-only`. The record's `attio_object_slug` is also surfaced as
a top-level metadata field so rules can scope by object type
(e.g., `if_field: attio_object_slug=deals → scope: partner-only`).

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm.

3. **Stand up the Attio connector** via `make_attio_connector(client=
   <ComposioClient>)`.

4. **Stand up a `StagingWriter`** scoped to `source="attio"`,
   `target_plane="firm"`. The writer's `source` label MUST match the
   manifest binding's `app` (also `"attio"`).

5. **First, enumerate the workspace's objects** via `invoke(connector,
   "list_objects", {})`. Cache the object slugs (typically `people`,
   `companies`, `deals`, plus any custom objects the firm has
   defined).

6. **Optionally enumerate lists** via `list_lists` so the operator
   can see which `list:<id>` rules they need in the manifest.

7. **For each object slug**, open a separate `durable_run` named
   `backfill-attio-<firm_id>-<object_slug>`. Per-object resume
   contracts stay clean.

8. **For each record id in the object:**
   - If the durable run has already marked it done, skip.
   - Fetch the full record via `invoke(connector, "find_record",
     {"object": <slug>, "record_id": id})`. The list_records action's
     payload is sometimes thinner; prefer find_record for the
     complete attribute set.
   - Convert raw → envelope:
     `item = attio_record_to_envelope(result.data, object_slug=<slug>,
     manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a list-id rule or sets `default_visibility`.
     - On `ValueError` (missing record_id / updated_at): log + skip.
   - `staged = staging.write_envelope(item)`. Frontmatter records
     `target_scope`, `source_role: workspace`, `external_object_type:
     <slug>` (e.g., `people` / `companies` / `deals` / custom),
     `container_id: <workspace_id>`, `modified_at` (Attio's
     `updated_at`).
   - Mark the durable step done with state `{"record_id": id,
     "object": <slug>}`.

9. Continue until pagination exhausts per object. Complete each
   durable run.

## Object ordering

Recommended: **system objects first** (`people`, `companies`,
`deals`), then custom objects. People typically reference companies;
deals reference both. Backfilling system objects first lets identity
resolution canonicalize entities before custom-object records link
to them.

## Where the data lands

```
<wiki_root>/staging/firm/attio/.raw/<external_id>.json
<wiki_root>/staging/firm/attio/<external_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

`external_id` is slug-prefixed: `companies_<record_id>`,
`people_<record_id>`, etc., to avoid id collision across object
types.

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No personal-plane staging.
- No promotion.
- No write-side mutations (create_record / update_record /
  create_note / delete_*) — those route through P5 sync-back.
- No URL synthesis. Attio URLs are workspace-specific
  (`https://app.attio.com/<workspace>/...`); the envelope's `url`
  stays `None` unless the raw payload includes one explicitly.

## On crash

Per-object durable runs guarantee exactly-once-per-thread processing.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=attio` from the observability log.
2. If a new failure mode appears (rate-limiting, repeated
   `VisibilityMappingError` from a list id missing in the manifest,
   schema drift on custom objects, attribute-shape changes), append
   a one-line lesson to `KNOWLEDGE.md` next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-attio, <one-line reason>`.
