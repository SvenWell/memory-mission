---
name: backfill-onedrive
version: "2026-04-25"
triggers: ["backfill onedrive", "backfill sharepoint", "import sharepoint", "sync onedrive", "pull historical documents m365", "import m365 documents"]
tools: [onedrive_connector, onedrive_item_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "onedrive connector has a ComposioClient injected (M365 OAuth via Composio)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `document` binding (app: one_drive)"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator (SharePoint backfill is firm-scoped)"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by onedrive_item_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='firm' — SharePoint document libraries are firm-shared institutional content"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "administrator-run only — SharePoint backfill seeds firm-plane truth and should be reviewed by a designated reviewer"
category: ingestion
---

# backfill-onedrive — pull OneDrive + SharePoint documents into firm staging

## What this does

Microsoft 365 / OneDrive + SharePoint equivalent of
`backfill-firm-artefacts` (Drive). Pulls firm documents through the
Composio-backed OneDrive connector — which **also covers SharePoint
document libraries** through the same Microsoft Graph drive-item API.
Each item normalizes to a `NormalizedSourceItem` via
`onedrive_item_to_envelope`, then writes to
`<wiki_root>/staging/firm/onedrive/` via
`StagingWriter.write_envelope`.

**Plane discipline:** Firm plane. SharePoint document libraries are
firm-shared institutional content — administrator-run only.

**Visibility discipline:** OneDrive's permission model (Microsoft
Graph) maps to firm scope:

- Items shared via an `anonymous` link → `drive_anyone: true` in
  visibility metadata. Operators typically map this to `public` scope.
- Items shared via an `organization` link → `drive_organization_link:
  true`. Typically maps to `firm-internal`.
- Items in a SharePoint site → `is_sharepoint: true` plus
  `sharepoint_site_id`. Operators can map per-site visibility (e.g.
  `if_field: sharepoint_site_id: "abc-partner-site" → scope:
  partner-only`).
- Otherwise the manifest's `default_visibility` applies (typically
  `client-confidential` or stricter).

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm. (No `employee_id`.)

3. **For each top-level scope** (personal OneDrive root + each
   SharePoint site the firm wants ingested), open a separate
   `durable_run` named `backfill-onedrive-<firm_id>-<scope>` so resume
   contracts stay clean per scope.

4. **Stand up the OneDrive connector** via `make_onedrive_connector(
   client=<ComposioClient>)`.

5. **Stand up a `StagingWriter`** scoped to `source="one_drive"`,
   `target_plane="firm"`. The writer's `source` label MUST match the
   manifest binding's `app` (also `"one_drive"`).

6. **Enumerate items** per scope:
   - For OneDrive root: `invoke(connector, "list_drive_items",
     {"drive_id": <root>})`. Page through.
   - For SharePoint sites: first `list_site_subsites` to discover
     children, then `list_drive_items` for each site's document
     library.

7. **For each item id:**
   - If the durable run has already marked it done, skip.
   - Fetch full metadata via `invoke(connector, "get_item",
     {"item_id": id, "drive_id": <id>})` and permissions via
     `invoke(connector, "get_item_permissions", {"item_id": id})`.
     Merge the permission grants into the raw payload before passing
     to the envelope helper (the helper reads `permissions` to
     synthesize `drive_anyone` / `drive_organization_link`).
   - Convert raw → envelope:
     `item = onedrive_item_to_envelope(merged_raw, manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a rule for the item's permission shape or sets
       `default_visibility`.
     - On `ValueError` (missing id / lastModifiedDateTime): log + skip.
   - `staged = staging.write_envelope(item)`. Frontmatter records
     `target_scope`, `source_role: document`, `external_object_type`
     (mime type or `folder`), `container_id` (drive id),
     `modified_at`. The raw sidecar preserves the full Microsoft
     Graph payload (parentReference, file, fileSystemInfo,
     permissions, etc) verbatim.
   - Mark the durable step done with state `{"item_id": id, "scope":
     <scope>}`.

8. Continue per scope until pagination exhausts. Complete each
   durable run.

## SharePoint pages and list items

This skill covers OneDrive personal/business AND SharePoint document
libraries (all of which are drive items in Microsoft Graph).
**SharePoint pages and list items have different shapes** and aren't
covered by `onedrive_item_to_envelope` — they're follow-on work via
`get_sharepoint_site_page_content` / `get_sharepoint_list_items` plus
new envelope helpers (`sharepoint_page_to_envelope`,
`sharepoint_list_item_to_envelope`). Add when a pilot needs them.

## Where the data lands

```
<wiki_root>/staging/firm/onedrive/.raw/<item_id>.json
<wiki_root>/staging/firm/onedrive/<item_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No personal-plane staging.
- No SharePoint pages or list items (separate helpers needed).
- No promotion.

## On crash

Per-scope durable runs guarantee exactly-once-per-thread processing.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=one_drive` from the observability log.
2. If a new failure mode appears (M365 throttling, repeated
   `VisibilityMappingError` from a permission shape we don't model,
   site-scoped items missing `siteId`), append a one-line lesson to
   `KNOWLEDGE.md` next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-onedrive, <one-line reason>`.
