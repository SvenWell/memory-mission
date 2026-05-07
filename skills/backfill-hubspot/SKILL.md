---
name: backfill-hubspot
version: "2026-05-05"
triggers: ["backfill hubspot", "import hubspot", "sync hubspot crm", "pull hubspot contacts", "pull hubspot companies", "pull hubspot deals"]
tools: [hubspot_connector, hubspot_record_to_envelope, systems_manifest, durable_run, staging_writer, observability_scope]
preconditions:
  - "hubspot connector has a ComposioClient injected (customer installs should use HubSpot OAuth through Composio)"
  - "$MM_WIKI_ROOT/firm/systems.yaml exists with a `workspace` binding (app: hubspot, target_plane: firm)"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "every staged item is a NormalizedSourceItem produced by hubspot_record_to_envelope - never call StagingWriter.write directly for envelope-shaped items"
  - "if VisibilityMappingError raises, stop and surface - do NOT silently retry with a different scope"
  - "stage under target_plane='firm' - HubSpot is firm-shared CRM data, not personal"
  - "do not invoke an LLM inside this skill - extraction lives in skills/extract-from-staging"
  - "do not write directly to wiki MECE domains; promotion is skills/review-proposals"
  - "do not perform write-side HubSpot mutations during backfill; sync-back uses the connector's write actions separately"
  - "do not store HubSpot private app tokens in Memory Mission config; provision OAuth/static tokens inside Composio"
category: ingestion
---

# backfill-hubspot - pull HubSpot CRM records into firm staging

## What this does

Pulls the firm's HubSpot CRM records through the Composio-backed
HubSpot connector, normalizes each record into a `NormalizedSourceItem`
via `hubspot_record_to_envelope`, and writes the envelope into
`<wiki_root>/staging/firm/hubspot/` via `StagingWriter.write_envelope`.

**Plane discipline:** Firm plane. HubSpot is the customer's shared CRM,
so this is administrator-run only.

**Auth discipline:** Memory Mission never receives or stores a raw
HubSpot token. The host injects a Composio client. Customer-shaped
installs should use HubSpot OAuth through Composio. Static/private
HubSpot tokens are acceptable only for sandbox tests when provisioned
inside Composio.

**Visibility discipline:** HubSpot does not map cleanly to Memory
Mission scopes. The envelope helper exposes list memberships as
`list:<id>` labels, plus `hubspot_object_type`, `hubspot_pipeline`,
`hubspot_dealstage`, `hubspot_owner_id`, and `hubspot_team_id` for
manifest rules. If no rule matches and no `default_visibility` is set,
the item is rejected.

## Workflow

1. **Load the systems manifest** from `$MM_WIKI_ROOT/firm/systems.yaml`.

2. **Open an `observability_scope`** for the firm.

3. **Stand up the HubSpot connector** via
   `make_hubspot_connector(client=<ComposioClient>)`.

4. **Stand up a `StagingWriter`** scoped to `source="hubspot"`,
   `target_plane="firm"`. The writer's source label MUST match the
   manifest binding's app.

5. **Cache schema metadata** before pulling records:
   - `list_properties` for contacts, companies, deals, notes, and any
     custom object types in scope.
   - `list_association_types` for contact-company, deal-company,
     deal-contact, and note associations.
   - Optional: `list_property_groups` so later sync-back can place
     Memory Mission fields in a stable property group.

6. **Backfill standard CRM objects first** in this order:
   companies -> contacts -> deals -> notes. Open a separate
   `durable_run` named `backfill-hubspot-<firm_id>-<object_type>` for
   each object type.

7. **For each record id in the object type:**
   - If the durable run has already marked it done, skip.
   - Fetch the full record via `invoke(connector, "read_object",
     {"object_type": <type>, "object_id": id, "associations": [...]})`
     or the specific `get_contact` / `get_company` / `get_deal`
     action.
   - Convert raw -> envelope:
     `item = hubspot_record_to_envelope(result.data,
     object_type=<type>, manifest=manifest)`.
     - On `VisibilityMappingError`: stop and surface. Operator either
       adds a rule or sets `default_visibility`.
     - On `ValueError` (missing id / timestamp): log and skip.
   - `staged = staging.write_envelope(item)`.
   - Mark the durable step done with state
     `{"record_id": id, "object_type": <type>}`.

8. **Custom objects** are optional. Backfill them after standard
   objects using their HubSpot object type id (`2-...`) or schema name.

## Sync-back note

The HubSpot connector also exposes write-side actions needed by the
separate CRM projection flow: create/update contacts, companies and
deals; create notes; create associations; create custom properties and
property groups; and batch create/update/upsert. Backfill MUST NOT use
those write actions. Sync-back should only write approved Memory Mission
context, should keep idempotency state, and should prefer a custom
unique HubSpot property such as `mm_entity_id`.

## Where the data lands

```
<wiki_root>/staging/firm/hubspot/.raw/<external_id>.json
<wiki_root>/staging/firm/hubspot/<external_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Standard `external_id` values are prefixed by object type:
`company_<id>`, `contact_<id>`, `deal_<id>`, `note_<id>`.

## What this skill does NOT do

- No LLM call.
- No direct connector `invoke()` calls.
- No direct `StagingWriter.write()` for envelope-shaped items.
- No silent visibility fallback.
- No personal-plane staging.
- No promotion.
- No write-side HubSpot mutations.
- No raw HubSpot token storage in repo files, wiki config, or logs.

## On crash

Per-object durable runs guarantee exactly-once-per-thread processing.
Re-running the same `thread_id` skips already-processed records.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=hubspot` from the observability log.
2. If a new failure mode appears (rate-limiting, missing scope,
   repeated `VisibilityMappingError`, property schema drift, association
   direction confusion), append a one-line lesson to `KNOWLEDGE.md`
   next to this file.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: backfill-hubspot, <one-line reason>`.
