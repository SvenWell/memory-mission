---
type: ADR
id: "0007"
title: "Capability-based connector roles + fail-closed visibility mapping"
status: active
date: 2026-04-25
---

## Context

Through P0–P1 every connector emitted its own raw payload shape. Gmail
returned `{id, subject, body, labels, ...}`; Granola returned
`{id, title, transcript, attendees, ...}`; Drive returned `{id, name,
content, permissions, ...}`. The downstream pipeline (extraction →
proposal → personal substrate → optional sync-back) had no single typed
input — every consumer had to know each connector's raw shape.

Two specific problems followed:

1. **No explicit firm scope at ingestion time.** A Gmail message tagged
   `lp-only` and a Drive doc shared with `anyone` both flowed into
   staging without a firm-shaped scope on the envelope. Scope only
   appeared later, on the `Triple`, after the proposal pipeline had
   already touched the data. Reviewers had no fail-closed gate on
   visibility.

2. **Concrete-app bindings were implicit.** A firm using Outlook instead
   of Gmail (still the email role) had to swap connector imports
   throughout the pipeline. The capability ("email_system") and the
   concrete app ("gmail") were collapsed.

P0 Track C landed `ConnectorRole` + `NormalizedSourceItem` (the
envelope) in `src/memory_mission/ingestion/roles.py` so the
`PersonalMemoryBackend` Protocol had a typed input shape from day one.
P2 closes the loop: per-firm config that binds roles to apps + maps
external visibility annotations to firm scopes, plus the per-app
helpers that emit the envelope, plus a `StagingWriter.write_envelope`
entry point so envelopes are the single supported staging path.

## Decision

**Per-firm `firm/systems.yaml` is the single source of truth for
capability-app bindings and visibility-to-scope mappings. External
visibility that does not map to a firm scope is rejected at ingestion
time (fail-closed). Operators opt into a per-role fallback explicitly;
there is no implicit default.**

The system is composed of:

1. **`SystemsManifest` (Pydantic, frozen).** Loads `firm/systems.yaml`.
   Maps `ConnectorRole → RoleBinding`. Each binding declares
   `app`, `target_plane`, an ordered list of `VisibilityRule`s, and an
   optional `default_visibility`. `default_visibility = None` is fail-
   closed; setting it to a scope name is the operator's explicit opt-in
   to a fallback.

2. **`map_visibility(visibility_metadata, *, role, manifest) -> str`.**
   Evaluates rules in order; first match wins. On no match: returns
   `default_visibility` if set, else raises `VisibilityMappingError`.

3. **Per-app envelope helpers** (`gmail_message_to_envelope`,
   `granola_transcript_to_envelope`, `drive_file_to_envelope`). Pure
   functions that take a raw connector payload + manifest and return a
   `NormalizedSourceItem`. They extract the per-app visibility surface,
   call `map_visibility`, and assemble the envelope. Helpers refuse to
   run against a manifest binding that names a different concrete app
   (`gmail_message_to_envelope` requires `binding.app == "gmail"`).

4. **`StagingWriter.write_envelope(item)`.** Higher-level write path
   that takes a `NormalizedSourceItem`, validates plane + concrete-app
   alignment with the writer's scope, and persists raw + markdown +
   structural frontmatter (including `target_scope`, `source_role`,
   `external_object_type`, `modified_at`) into the staging zone.

The connector layer (`Connector` Protocol + `invoke()` harness +
`ComposioConnector`) stays purely about transport, auth, and PII-
scrubbed audit logging. The envelope helpers are one layer up: the
firm-shaped contract that downstream code consumes.

## Options considered

- **Option A (chosen): manifest-driven binding + envelope helpers as a
  separate layer above connectors.** Connectors return raw; envelope
  helpers normalize per-app raw → typed envelope per firm config.
  Downstream code only sees `NormalizedSourceItem`. Visibility mapping
  is one function with declarative rules.

- **Option B: connectors emit envelopes directly.** Add an
  `to_envelope` callable to `ComposioConnector`. Pro: fewer files. Con:
  conflates transport concerns with firm-shaped contracts; testing the
  envelope shape requires standing up a connector instance; per-firm
  visibility config has to be threaded through the connector
  constructor.

- **Option C: defer the manifest, hardcode role→app binding in code.**
  Cheapest. Pro: ships faster. Con: every new firm requires a code
  change; the implicit binding can drift from the actual deployment;
  no fail-closed visibility surface.

- **Option D: pull visibility mapping into the proposal pipeline (not
  ingestion).** Wait until promotion to assign scope. Pro: consolidates
  policy into the existing review path. Con: staged items live without
  a scope, which means reviewers can't filter by scope, the personal
  substrate writes happen before scope is known (they happen at
  staging), and an attacker who can control staging can bypass scope
  altogether.

## Rationale

1. **Fail-closed visibility is the single biggest correctness win.**
   The prior implicit-default behavior meant a Drive file with no
   recognized permission shape silently became `public`. The plan called
   this out as a P2 explicit requirement. Default-deny is the only
   safe operator-facing surface for visibility; explicit opt-in to a
   fallback keeps configurability without losing the default.

2. **Capability-app split matches operator reality.** Different firms
   use different concrete apps for the same role. Outlook fulfils
   `email`; Notion fulfils `document` or `workspace`; Affinity, Attio,
   Salesforce, Monday all fulfil `workspace`. The manifest is the seam
   that lets a single codebase serve them all without per-firm
   forks.

3. **Envelope-as-contract simplifies every downstream stage.**
   Extraction, proposal review, personal substrate ingestion, federated
   detection, and (P5) sync-back all consume the same typed shape.
   Per-connector special cases are eliminated.

4. **Helpers as pure functions enable trivial testing.** No
   Composio client, no HTTP mocks, no live credentials. A fake raw dict
   shaped like the documented Composio response is enough.

5. **Helper-vs-binding mismatch is caller error worth catching.**
   Calling `gmail_message_to_envelope` against an Outlook-bound firm is
   always a logic bug. Raising explicitly beats silently producing a
   misshapen envelope.

## Consequences

- New required surface for any future role: declare a binding in
  `firm/systems.yaml`, write a per-app envelope helper. No changes to
  extraction / proposal / personal substrate code.
- Operators must define visibility rules (or set `default_visibility`)
  per role. A binding that omits both rejects every item — loud, not
  silent. This is the intended behavior.
- The `StagingWriter.write` method remains for ad-hoc / non-envelope
  writes (e.g. Composio invocation logs, free-form ingestion). The
  envelope path is the canonical entry point for connector-emitted
  source items.
- ADR-0008 (typed sync-back, P5) inherits the same envelope shape: an
  approved fact's provenance trail starts at the `NormalizedSourceItem`
  it was extracted from. Sync-back per-app `allowed_mutation_kinds`
  will live in the same `firm/systems.yaml`.

## Follow-ups

- **Calendar role** — the `CALENDAR` value in `ConnectorRole` exists in
  `roles.py` but no connector or envelope helper ships in P2 (no
  Calendar Composio connector exists yet). P3 lands the connector +
  helper.
- **Workspace role** — Notion / Attio / Affinity / Monday / Salesforce
  envelope helpers all land in P3/P4 once concrete connectors ship.
- **Skill updates** — `skills/backfill-gmail/`, `skills/backfill-granola/`,
  `skills/backfill-firm-artefacts/` will be updated in P3 to invoke
  the envelope helpers + `write_envelope`. The current SKILL markdowns
  describe the raw-write path.

## Related decisions

- **ADR-0002 (two planes, one-way bridge)** — envelope's `target_plane`
  honors the personal/firm split.
- **ADR-0004 (MemPalace personal substrate)** — `MemPalaceAdapter.ingest`
  consumes the envelope directly.
- **ADR-0005 (SQLite per firm)** — manifest is per firm; no shared
  cross-firm config.
- **ADR-0008 (planned, P5)** — typed sync-back will reuse the envelope
  shape as the inbound provenance trail.
