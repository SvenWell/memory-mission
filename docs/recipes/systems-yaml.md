# `firm/systems.yaml` — capability bindings + visibility mapping

The systems manifest is **per-firm config**. It tells Memory Mission
which concrete app fulfils each `ConnectorRole` and how external
visibility annotations map to firm scopes. This file is loaded once at
backfill time via `load_systems_manifest()`; every connector emits a
`NormalizedSourceItem` whose `target_scope` is set by `map_visibility()`
against this manifest.

**The mapping is fail-closed by default.** If a binding has no
`default_visibility` and no rule matches an item's
`visibility_metadata`, the call raises `VisibilityMappingError` and the
item is rejected. Operators opt into a fallback explicitly. There is no
implicit default.

## Where to put it

```
$MM_WIKI_ROOT/firm/systems.yaml
```

Per-firm — each firm directory has its own. Not committed to this repo
(every firm's bindings are different).

## Minimal example — one-person personal-test firm

Use this as a starting point if you want to fire your own personal
agent at the backfill flow against your own Gmail / Granola / Calendar
data. Everything routes to `target_plane: personal` with a
fail-closed-by-default-but-employee-private-fallback posture.

```yaml
firm_id: sven-personal
bindings:
  email:
    app: gmail
    target_plane: personal
    visibility_rules:
      # Operator-controlled labels you set in Gmail to flag items
      # that should land at a more permissive scope. Not required.
      - if_label: external-shared
        scope: external-shared
      - if_label: lp-only
        scope: lp-only
    default_visibility: employee-private

  calendar:
    app: gcal
    target_plane: personal
    visibility_rules:
      - if_field: { gcal_visibility: public }
        scope: external-shared
      - if_field: { gcal_visibility: private }
        scope: employee-private
    default_visibility: employee-private

  transcript:
    app: granola
    target_plane: personal
    # Granola transcripts rarely carry rich visibility metadata —
    # just default everything to employee-private.
    default_visibility: employee-private
```

## Fail-closed example — strict-by-default firm

For a real firm where every email must explicitly carry a label or get
rejected. Useful for a compliance-mode pilot.

```yaml
firm_id: northpoint
bindings:
  email:
    app: gmail
    target_plane: personal
    visibility_rules:
      - if_label: lp-only
        scope: lp-only
      - if_label: portfolio-shared
        scope: partner-only
      - if_label: external-shared
        scope: external-shared
    # default_visibility omitted -> fail-closed.
    # Items without a recognized label are rejected at ingestion.
```

## M365 firm example — Outlook + OneDrive/SharePoint stack

For firms on the Microsoft 365 stack (instead of Google Workspace).
Outlook fulfils `email`; OneDrive (which Composio's toolkit also uses
for SharePoint document libraries) fulfils `document`.

```yaml
firm_id: northpoint
bindings:
  email:
    app: outlook
    target_plane: personal
    visibility_rules:
      # Outlook's built-in sensitivity field is the strongest signal.
      - if_field: { outlook_sensitivity: confidential }
        scope: lp-only
      - if_field: { outlook_sensitivity: private }
        scope: employee-private
      # User-assigned Outlook categories surface as `labels`.
      - if_label: external-shared
        scope: external-shared
    default_visibility: employee-private

  document:
    app: one_drive
    target_plane: firm
    visibility_rules:
      # OneDrive items shared via an anonymous link are public.
      - if_field: { drive_anyone: true }
        scope: public
      # Items shared via an organization link stay firm-internal.
      - if_field: { drive_organization_link: true }
        scope: firm-internal
      # SharePoint items can be scoped per-site (the helper synthesizes
      # `sharepoint_site_id` from parentReference.siteId).
      - if_field: { sharepoint_site_id: "abc-partner-site-id" }
        scope: partner-only
    default_visibility: client-confidential
```

Auth: OAuth2 via Composio (M365 enterprise SSO is handled at the
Composio layer; the firm provisions per-firm OAuth config in
Composio's dashboard). The OneDrive toolkit covers BOTH personal
OneDrive AND SharePoint document libraries. SharePoint pages and list
items have different shapes — separate helpers will land when a
pilot needs them.

## Slack example — `chat` role with plane override

Slack messages mix planes within a single binding: DMs and group DMs
are personal-plane (employee-private), regular channels are firm-plane.
The envelope helper structurally overrides `target_plane` to
`personal` when `slack_is_im` or `slack_is_mpim` is true; the manifest
binding's `target_plane: firm` is the default for non-DM messages.
See ADR-0011.

```yaml
firm_id: northpoint
bindings:
  chat:
    app: slack
    target_plane: firm  # default for non-DM; helper overrides for is_im/is_mpim
    # CRITICAL ORDERING: DMs in Slack are also is_private. The is_im /
    # is_mpim rules MUST fire before the is_private rule, otherwise
    # DMs get scoped as partner-only instead of employee-private.
    visibility_rules:
      - if_field: { slack_is_im: true }
        scope: employee-private
      - if_field: { slack_is_mpim: true }
        scope: employee-private
      - if_field: { slack_is_ext_shared: true }
        scope: external-shared
      - if_field: { slack_is_private: true }
        scope: partner-only
    default_visibility: firm-internal
```

Auth: OAuth2 / Bearer token via Composio. The Slack token must have
the appropriate scopes for each channel type
(`channels:history` / `groups:history` / `im:history` /
`mpim:history`). The `backfill-slack` skill maintains two
`StagingWriter` instances (one personal, one firm) and picks the
right one per `item.target_plane` — the writer's plane-match
assertion guarantees no DM ever lands in firm staging.

## Notion example — workspace wiki + structured databases

Notion fits the `workspace` role for firms that use it as their wiki +
project database. The same connector can also fulfil `document` if a
firm wants to model individual Notion pages as document artefacts —
the binding is your choice. The most common pattern is `workspace`
only.

```yaml
firm_id: northpoint
bindings:
  workspace:
    app: notion
    target_plane: firm
    visibility_rules:
      # Scope by parent database — every row in the Investments DB is
      # partner-only, every row in the Public Memos DB is external-shared.
      - if_field: { notion_parent_id: "db-investments" }
        scope: partner-only
      - if_field: { notion_parent_id: "db-public-memos" }
        scope: external-shared
      # Pages parented under specific high-level wiki sections.
      - if_field: { notion_parent_id: "page-partner-only-wiki" }
        scope: partner-only
    default_visibility: firm-internal
```

Auth: OAuth2 (typical) or an Integration API key via Composio. The
backfill skill fetches `get_page` plus `get_block_children`
recursively, flattens the block tree into markdown, and stuffs it into
`raw["block_content"]` before calling the envelope helper — the
helper itself stays pure and offline. Backfill is administrator-run.

## Schema-flexible CRM example — Attio

Attio is a customizable CRM with both system objects (people,
companies, deals) and user-defined custom objects. Records belong to
**Lists** (saved views / collections); list membership is the typical
visibility signal, with object-type scoping as a secondary axis.

```yaml
firm_id: northpoint
bindings:
  workspace:
    app: attio
    target_plane: firm
    visibility_rules:
      # Lists in Attio are saved-view ids (string slugs or UUIDs).
      # Run `list_lists` once to discover yours.
      - if_label: list:pipeline
        scope: partner-only
      - if_label: list:portfolio
        scope: firm-internal
      # Object-level scoping — treat all `deals` records as partner-only
      # regardless of list membership.
      - if_field: { attio_object_slug: deals }
        scope: partner-only
    default_visibility: firm-internal
```

Auth: OAuth2 via Composio. Backfill is administrator-run only —
Attio holds the firm's CRM truth.

## Venture-CRM example — Affinity as the firm workspace

Affinity is the dominant venture-fund CRM. Records (organizations,
persons, opportunities) belong to one or more **Lists** — the firm's
deal pipelines, portfolio tracker, LP network, etc. List membership
is the primary visibility signal: a "Pipeline" list might be
partner-only; a "Portfolio" list firm-wide; an "LP Network" list
partner-only.

```yaml
firm_id: northpoint
bindings:
  workspace:
    app: affinity
    target_plane: firm
    visibility_rules:
      # Each list_id below is the integer Affinity assigns to a List.
      # Run `list_lists` once to discover yours; the envelope helper
      # surfaces each membership as `list:<list_id>` label.
      - if_label: list:42        # Active Pipeline
        scope: partner-only
      - if_label: list:91        # Portfolio Companies
        scope: firm-internal
      - if_label: list:104       # LP Network
        scope: partner-only
      # Affinity's `global: true` flag means a globally-known company
      # (anyone with Affinity sees it, not firm-private). Maps to
      # external-shared by default.
      - if_label: global
        scope: external-shared
    default_visibility: firm-internal
```

Affinity uses **API-key auth** at the Composio layer (not OAuth2). The
firm provisions a per-firm Affinity API key in Composio's dashboard
and the connector picks it up automatically. Backfill is
administrator-run only — Affinity holds the firm's relationship +
deal data.

## Firm-plane example — Drive as the firm document substrate

```yaml
firm_id: northpoint
bindings:
  document:
    app: drive
    target_plane: firm
    visibility_rules:
      # Drive files shared with "anyone" go straight to public scope.
      - if_field: { drive_anyone: true }
        scope: public
    # Anything else — internal to the firm by default.
    default_visibility: client-confidential
```

## Reference — schema

Every binding entry has these keys:

| Field | Type | Required | Notes |
|---|---|---|---|
| `app` | string | yes | Concrete app name. The envelope helper checks this matches its expected app (Gmail helper requires `app: gmail`). |
| `target_plane` | `personal` \| `firm` | yes | Where items from this binding land. |
| `visibility_rules` | list | no | Ordered. First match wins. |
| `default_visibility` | string \| `null` | no (defaults to `null`) | When `null`, no rule match raises `VisibilityMappingError`. When a string, that scope is the fallback. |

Each `VisibilityRule` has:

| Field | Type | Required | Notes |
|---|---|---|---|
| `if_label` | string | conditional | Matches if `metadata["labels"]` is a list and contains this string. |
| `if_field` | mapping | conditional | Matches if every `key: value` pair equals `metadata[key]`. |
| `scope` | string | yes | Non-empty. The firm scope the item gets if this rule matches. |

A rule must have at least one of `if_label` or `if_field`. A rule with
both requires both to match.

## Per-app `visibility_metadata` shapes

Each envelope helper extracts a per-app visibility surface from the raw
connector payload before `map_visibility` is called. Knowing the shape
lets you write rules that target the right keys.

| Helper | Surface |
|---|---|
| `gmail_message_to_envelope` | `labels` (list[str]), `to` (list[str]), `cc` (list[str]) |
| `granola_transcript_to_envelope` | `attendees` (list[str]), `labels` (list[str]) |
| `calendar_event_to_envelope` | `gcal_visibility` (str: `default` / `public` / `private` / `confidential`), `attendees` (list[str]), `labels` (list[str]) |
| `drive_file_to_envelope` | `permissions` (list[dict]), `owners` (list[str]), `drive_anyone` (bool — synthesized: True iff any permission grants `type: anyone`), `labels` (list[str]) |
| `affinity_record_to_envelope` | `labels` (list[str]: one `list:<list_id>` per Affinity list the record sits in, plus `global` when Affinity flags the record as global), `affinity_object_type` (`organization` / `person` / `opportunity`), `affinity_owner_id` (int or null) |
| `attio_record_to_envelope` | `labels` (list[str]: one `list:<list_id>` per Attio list the record sits in), `attio_object_slug` (str: object identifier — `people` / `companies` / `deals` / custom), `attio_workspace_id` (str or null) |
| `notion_page_to_envelope` | `labels` (list[str]: empty by default), `notion_parent_type` (str: `workspace` / `page_id` / `database_id`), `notion_parent_id` (str or null), `notion_public_url` (str or null — non-null when share-to-web enabled), `notion_archived` (bool) |
| `slack_message_to_envelope` | `labels` (list[str]: empty by default), `slack_channel_id` (str), `slack_channel_name` (str), `slack_is_im` / `slack_is_mpim` / `slack_is_private` / `slack_is_shared` / `slack_is_ext_shared` (all bool), `slack_member_count` (int or null), `slack_thread_ts` (str or null — present when message is a reply). NOTE: helper structurally overrides `target_plane` to `personal` when `is_im` or `is_mpim`; this is the only role with per-item plane variation (ADR-0011). |
| `outlook_message_to_envelope` | `outlook_sensitivity` (str: `normal` / `personal` / `private` / `confidential` — Outlook's built-in field), `labels` (list[str]: Outlook user-assigned categories), `to` (list[str]), `cc` (list[str]) |
| `onedrive_item_to_envelope` | `permissions` (list[dict]: Microsoft Graph grants), `owners` (list[str]: display names), `drive_anyone` (bool — synthesized: True iff any permission grants `link.scope == "anonymous"`), `drive_organization_link` (bool — synthesized: True iff any link is `scope == "organization"`), `is_sharepoint` (bool — True when item lives in a SharePoint document library), `sharepoint_site_id` (str or null), `labels` (list[str]) |

## Loader API

```python
from pathlib import Path
from memory_mission.ingestion import load_systems_manifest, map_visibility

manifest = load_systems_manifest(Path("firm/systems.yaml"))
binding = manifest.binding(ConnectorRole.EMAIL)
# binding.app == "gmail", binding.target_plane == "personal", ...
```

The loader raises `pydantic.ValidationError` for structural problems
(missing fields, wrong types, unknown role keys, unknown
`target_plane`) and `ValueError` for non-mapping top-level YAML.

## Why fail-closed

A Drive file with no recognized permission shape silently becoming
`public` is the kind of bug that doesn't surface until a customer asks
why their LP-private memo ended up in a public-scope query. The
`default_visibility = null` posture forces the operator to make the
tradeoff explicit. Setting it to a permissive scope is fine — but it's
on the operator, not on Memory Mission's defaults.

## Related

- ADR-0007 — capability-based connectors + fail-closed visibility
  (architectural rationale).
- `src/memory_mission/ingestion/systems_manifest.py` — types + loader.
- `src/memory_mission/ingestion/envelopes.py` — per-app helpers.
- `src/memory_mission/ingestion/staging.py` — `StagingWriter.write_envelope`.
