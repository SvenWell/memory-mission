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
