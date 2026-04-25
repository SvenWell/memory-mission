---
name: backfill-notion
version: "2026-04-25"
triggers: ["backfill notion", "import notion", "sync notion workspace", "pull notion pages", "pull notion databases", "ingest notion wiki"]
tools: [notion_connector, notion_page_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "notion connector has a ComposioClient injected (Notion uses OAuth2 or an Integration API key)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `workspace` binding (app: notion, target_plane: firm)"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by notion_page_to_envelope — never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface — do NOT silently retry with a different scope"
  - "stage under target_plane='firm' — Notion is firm-shared workspace content"
  - "do not invoke an LLM inside this skill — extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "administrator-run only — Notion holds the firm's wiki + structured-DB content"
category: ingestion
---

# backfill-notion — pull workspace pages + database rows into firm staging

## What this does

Pulls the firm's Notion content through the Composio-backed Notion
connector, normalizes each page (or database row) into a
`NormalizedSourceItem` via `notion_page_to_envelope`, and writes the
envelope into `<wiki_root>/staging/firm/notion/` via
`StagingWriter.write_envelope`.

**Plane discipline:** Firm plane. Notion is firm-shared workspace
content — administrator-run only.

**Visibility discipline:** Notion's permission model is per-page (and
inherited from parents). The envelope helper surfaces:

- `notion_parent_type` (`workspace` / `page_id` / `database_id`)
- `notion_parent_id` (the parent's id) — operators typically scope by
  specific parent page or database (e.g.,
  `if_field: notion_parent_id="db-investments" → scope: partner-only`)
- `notion_public_url` — string when the page is share-to-web enabled.
  Use as a strong "public" signal in the manifest if the firm publishes
  anything to web.
- `notion_archived` — bool; archived pages can map to a different
  scope.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm.

3. **Stand up the Notion connector** via `make_notion_connector(
   client=<ComposioClient>)`.

4. **Stand up a `StagingWriter`** scoped to `source="notion"`,
   `target_plane="firm"`. The writer's `source` label MUST match the
   manifest binding's `app` (also `"notion"`).

5. **Discover the page set** to backfill. Two strategies:
   - **Workspace-wide:** call `invoke(connector, "search", {})` and
     paginate to enumerate every page + database the integration has
     access to. Filter by `object: "page"` for pages,
     `object: "database"` for databases.
   - **Database-driven:** call `invoke(connector, "query_database",
     {"database_id": <id>})` for each database the operator listed
     for backfill. Use this when only specific DB rows matter and the
     operator wants to skip arbitrary workspace pages.

6. **For each page id:**
   - If the durable run has already marked it done, skip.
   - Fetch the page metadata via
     `invoke(connector, "get_page", {"page_id": id})`.
   - **If the page has body content worth ingesting**, recursively
     fetch its blocks via `invoke(connector, "get_block_children",
     {"block_id": id})` and flatten the resulting block tree into
     markdown. Inject the result into `raw["block_content"]` so the
     envelope helper picks it up as `body`. (For database rows, the
     properties + parent are usually enough — block content is
     typically empty.)
   - Convert raw → envelope:
     `item = notion_page_to_envelope(raw, manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a parent-id rule or sets `default_visibility`.
     - On `ValueError` (missing id / last_edited_time): log + skip.
   - `staged = staging.write_envelope(item)`. Frontmatter records
     `target_scope`, `source_role: workspace`, `external_object_type:
     notion_page` or `notion_database_row`, `container_id` (parent
     id), `url` (Notion's canonical page URL), `modified_at`
     (Notion's `last_edited_time`).
   - Mark the durable step done with state `{"page_id": id}`.

7. Continue until pagination exhausts. Complete the durable run.

## Block flattening

Notion's blocks are a recursive tree (each block can have children).
For body content, walk the tree depth-first and emit a markdown line
per block respecting type (paragraph → text, heading_2 → `## text`,
bulleted_list_item → `- text`, etc.). Skip database / synced /
embedded children.

The envelope helper does not do the flattening — it expects
`raw["block_content"]` to already be a markdown string when content
is needed. This keeps the helper pure and lets the skill control how
deep to recurse / what block types to render.

## Where the data lands

```
<wiki_root>/staging/firm/notion/.raw/<page_id>.json
<wiki_root>/staging/firm/notion/<page_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No personal-plane staging.
- No promotion.
- No write-side mutations (create_page / update_page / add_block) —
  those route through P5 sync-back.
- No standalone-database ingestion as items (the database object
  itself, not its rows). If the firm needs database schemas as
  items, add a separate `notion_database_to_envelope` helper.

## On crash

The durable run guarantees exactly-once-per-thread processing.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=notion` from the observability log.
2. If a new failure mode appears (rate-limiting, repeated
   `VisibilityMappingError` from a parent_id missing in the manifest,
   block-tree-flattening edge cases, integration-token-scope
   missing-page errors), append a one-line lesson to `KNOWLEDGE.md`
   next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-notion, <one-line reason>`.
