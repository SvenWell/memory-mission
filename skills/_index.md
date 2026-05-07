# Skill Registry

Read this file first. Full `SKILL.md` contents load only when a skill's
triggers match the current task. Machine-readable equivalent:
`skills/_manifest.jsonl`. Conventions: `skills/_writing-skills.md`.

**20 skills shipped** as of 2026-05-05. Backfill skills (gmail, outlook,
granola, calendar, drive/firm-artefacts, onedrive, affinity, attio,
hubspot, notion, slack) route through P2's envelope path: load
`firm/systems.yaml`, call the per-app envelope helper, and write via
`StagingWriter.write_envelope`. Visibility maps to firm scope per the
manifest — fail-closed by default (ADR-0007).

The four venture-overlay workflow skills (`update-deal-status`,
`record-ic-decision`, `onboard-venture-firm`, `weekly-portfolio-update`)
are part of P7-A and ride on the venture overlay (`overlays/venture/`).
The first three consume the constitution's authoritative vocabulary
(`lifecycle_stages`, `ic_quorum`, `decision_rights`) and write
`UpdateFact` / `RelationshipFact` / page proposals through the standard
review-proposals gate — never auto-promote. The fourth
(`weekly-portfolio-update`) is read-only by contract: it produces a
partner-ready portfolio digest from currently-true firm-plane state,
surfaces stale companies as forcing questions, and routes the operator
to `update-deal-status` / `record-ic-decision` when state actually
needs changing.

## backfill-gmail

Pull historical email through the Gmail connector (Composio-backed),
normalize each message to a `NormalizedSourceItem` via
`gmail_message_to_envelope`, and write the envelope into the
**employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/gmail/`) via
`StagingWriter.write_envelope`. Each message becomes a checkpointed
step under a durable run. Visibility maps to firm scope per
`firm/systems.yaml` — fail-closed by default (ADR-0007). No LLM calls,
no extraction, no firm-plane writes.

Triggers: "backfill gmail", "import email history", "sync gmail mailbox",
"pull historical email"

Constraints: personal plane only (never firm staging), envelope path
only (`write_envelope`, never raw `write` for envelope-shaped items),
`VisibilityMappingError` halts the loop (no silent fallback), every
fetch flows through the connector harness, no LLM inside the loop.

## extract-from-staging

Read source items from staging (Gmail message, Granola transcript, Drive
memo), run the host agent's LLM with the `EXTRACTION_PROMPT`, parse the
response into an `ExtractionReport` (six fact kinds: identity /
relationship / preference / event / update / open_question), and write
to fact staging via `ingest_facts()`. No direct writes to the knowledge
graph — promotion pipeline (Step 10) reviews proposals first.

Triggers: "extract from staging", "extract facts", "run extraction",
"process staged items"

Constraints: Memory Mission imports no LLM SDK (host agent runs the
LLM), every fact must carry a `support_quote` from the source, low
confidence routes to `open_question`, extracted target_plane must
match source target_plane.

## backfill-granola

Pull historical meeting transcripts through the Granola connector
(Composio-backed), normalize via `granola_transcript_to_envelope`, and
write the envelope into the **employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/granola/`) via
`StagingWriter.write_envelope`. Same shape as backfill-gmail, different
source. Each transcript is a checkpointed step. Visibility maps to firm
scope per `firm/systems.yaml` — most firms set `default_visibility` on
the `transcript` binding since transcripts rarely carry rich metadata.

Triggers: "backfill granola", "import meeting transcripts",
"sync granola transcripts", "pull historical meetings"

Constraints: personal plane only, envelope path only (`write_envelope`),
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM.

## backfill-calendar

Pull historical Google Calendar events through the Calendar connector
(Composio-backed), normalize via `calendar_event_to_envelope`, and
write the envelope into the **employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/gcal/`) via
`StagingWriter.write_envelope`. Each event is a checkpointed step.
Visibility maps from Google Calendar's built-in `visibility` field
(`default` / `public` / `private` / `confidential`) to firm scope per
`firm/systems.yaml`. Recurring event instances are processed
individually; non-primary calendars use a separate durable run per
`calendar_id`.

Triggers: "backfill calendar", "import calendar history", "sync gcal",
"pull historical events", "import meetings"

Constraints: personal plane only, envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM.

## backfill-outlook

Pull historical Microsoft 365 / Outlook email through Composio (OAuth2),
normalize via `outlook_message_to_envelope`, write to the **employee's
personal staging plane** (`<wiki_root>/staging/personal/<employee_id>/
outlook/`) via `StagingWriter.write_envelope`. M365 equivalent of
`backfill-gmail`. Visibility maps from Outlook's built-in `sensitivity`
field (`normal` / `personal` / `private` / `confidential`) plus
user-assigned `categories` (which surface as `labels` for `if_label`
rules). Incremental sync via `get_mail_delta` after the first full
backfill.

Triggers: "backfill outlook", "import outlook history",
"sync outlook mailbox", "pull historical email outlook",
"import m365 mail"

Constraints: personal plane only, envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM.

## backfill-onedrive

Pull OneDrive + SharePoint document libraries through Composio
(OAuth2). Single skill covers both — Microsoft Graph treats SharePoint
document libraries as drives. Normalize via
`onedrive_item_to_envelope`, write to the **firm staging plane**
(`<wiki_root>/staging/firm/onedrive/`) via
`StagingWriter.write_envelope`. Per-scope durable runs (one per
SharePoint site / OneDrive root). M365 equivalent of
`backfill-firm-artefacts` (Drive). Visibility maps from
permission-link scope (`anonymous` → public, `organization` →
firm-internal) plus per-site rules (`is_sharepoint` + `sharepoint_site_id`).

Triggers: "backfill onedrive", "backfill sharepoint",
"import sharepoint", "sync onedrive",
"pull historical documents m365", "import m365 documents"

Constraints: firm plane only (administrator-run), envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM. SharePoint pages and list items have different
shapes — separate helpers needed (not in V1).

## backfill-slack

Pull Slack message history through Composio (OAuth2 / Bearer),
normalize via `slack_message_to_envelope`, write to the **right
plane per channel-type**: DMs and group DMs land in
`<wiki_root>/staging/personal/<employee_id>/slack/`; internal channels
and external-shared channels land in `<wiki_root>/staging/firm/slack/`.
The envelope helper enforces the plane override structurally based on
`is_im` / `is_mpim` flags — no manifest rule can route a DM to firm
staging. See ADR-0011.

Per-message envelope (atomic unit, mirrors Gmail). The skill
maintains TWO StagingWriter instances (one personal, one firm) and
picks the right one per `item.target_plane`. Per-channel durable runs
(one thread per `channel_id`) so resume contracts stay clean.

Visibility surface: `slack_channel_id`, `slack_channel_name`,
`slack_is_im` / `is_mpim` / `is_private` / `is_shared` /
`is_ext_shared`, `slack_member_count`, `slack_thread_ts`. Critical:
DM rules MUST come before is_private rules in the manifest (DMs are
also is_private in Slack).

Triggers: "backfill slack", "import slack", "sync slack workspace",
"pull slack history", "ingest slack messages"

Constraints: DMs/MPDMs personal plane only (structurally enforced),
channels firm plane, envelope path only, `VisibilityMappingError`
halts the loop, every fetch through the harness, no LLM, no
write-side mutations (sync-back is P5), no Slack-file ingestion
(separate helper needed).

## backfill-notion

Pull Notion workspace pages and database rows through Composio
(OAuth2 or Integration API key), normalize via
`notion_page_to_envelope`, write to the **firm staging plane**
(`<wiki_root>/staging/firm/notion/`) via
`StagingWriter.write_envelope`. Notion's API treats database rows as
pages with a `database_id` parent, so one helper handles both;
`external_object_type` is `notion_page` or `notion_database_row`
depending on the parent type.

Visibility maps from `notion_parent_type` / `notion_parent_id`
(typical: scope by parent database id), `notion_public_url`
(share-to-web pages), and `notion_archived`. The skill is
responsible for fetching `get_block_children` recursively and
flattening into markdown when body content matters; the envelope
helper picks up `raw["block_content"]` if pre-populated.

Triggers: "backfill notion", "import notion", "sync notion workspace",
"pull notion pages", "pull notion databases", "ingest notion wiki"

Constraints: firm plane only (administrator-run), envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM, no write-side mutations (sync-back is P5).

## backfill-attio

Pull schema-flexible CRM records (people, companies, deals, and any
custom user-defined objects) from Attio through Composio (OAuth2),
normalize via `attio_record_to_envelope`, write to the **firm staging
plane** (`<wiki_root>/staging/firm/attio/`) via
`StagingWriter.write_envelope`. Per-object durable runs (one per
object slug). Recommended order: system objects first (people →
companies → deals), then custom objects.

Visibility maps from list-membership (each Attio list as
`list:<list_id>` label) plus per-object scoping (`if_field:
attio_object_slug=deals → scope: partner-only`). Workspace-wide
records get the manifest's `default_visibility` fallback.

Triggers: "backfill attio", "import attio", "sync attio crm",
"pull attio records", "pull attio companies", "pull attio people",
"pull attio deals"

Constraints: firm plane only (administrator-run), envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM, no write-side mutations (sync-back is P5).

## backfill-affinity

Pull venture-CRM records (organizations, persons, opportunities) from
Affinity through Composio (API-key auth), normalize via
`affinity_record_to_envelope`, and write the envelope into the **firm
staging plane** (`<wiki_root>/staging/firm/affinity/`) via
`StagingWriter.write_envelope`. Three-pass strategy: organizations →
persons → opportunities, so identity resolution canonicalizes orgs
before persons/opps link to them. Per-type durable runs.

Visibility maps from list-membership: each Affinity list (Pipeline /
Portfolio / LP Network / etc.) becomes a `list:<id>` label that the
manifest maps to a firm scope. Globally-known companies get a `global`
label that typically maps to `external-shared`.

Triggers: "backfill affinity", "import affinity", "sync crm",
"pull affinity organizations", "pull affinity persons",
"pull affinity opportunities"

Constraints: firm plane only (administrator-run), envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM. Affinity's pagination yields basic info — always
`get_*` per id for full field data.

## backfill-hubspot

Pull HubSpot CRM records (companies, contacts, deals, notes, and
optional custom objects) through Composio, normalize via
`hubspot_record_to_envelope`, and write to the **firm staging plane**
(`<wiki_root>/staging/firm/hubspot/`) via
`StagingWriter.write_envelope`. Standard object order: companies →
contacts → deals → notes; custom objects after standard objects.

Visibility maps from HubSpot list memberships (`list:<id>` labels),
object type, pipeline, deal stage, owner id, and team id. Customer
installs should use HubSpot OAuth through Composio. Private/static
tokens are acceptable for sandbox tests only when provisioned inside
Composio, never stored in Memory Mission config.

Triggers: "backfill hubspot", "import hubspot", "sync hubspot crm",
"pull hubspot contacts", "pull hubspot companies",
"pull hubspot deals"

Constraints: firm plane only (administrator-run), envelope path only,
`VisibilityMappingError` halts the loop, every fetch through the
harness, no LLM, no write-side HubSpot mutations during backfill.
The connector exposes write actions for the separate approved-context
sync-back flow.

## backfill-firm-artefacts

Cold-start the firm plane from firm-authored documents (memos,
decks, training docs, quarterly updates, board material) via the
Drive connector. **Administrator-run only.** Stages under
`<wiki_root>/staging/firm/drive/`; resulting proposals go through
`skills/review-proposals` for merge gate. Solves Emile's authority
problem — firm truth comes from firm-authored content, not one
employee agent's extracted opinions.

Triggers: "backfill firm artefacts", "cold-start firm knowledge",
"import firm documents", "seed firm wiki", "ingest drive folder"

Constraints: firm plane only (no employee_id), administrator-run,
reviewer at the merge gate is separate from the administrator who
pulled the source, every fetch through the harness, no LLM.

## review-proposals

PR-model promotion review: the V1 centerpiece. Surface pending
proposals from the `ProposalStore` one at a time to a human reviewer,
capture the decision with required rationale, call `promote()` /
`reject()` / `reopen()`. No auto-approve on any signal. Every approve
atomically applies the proposal's facts to the firm's
`KnowledgeGraph` with full provenance.

Triggers: "review proposals", "pending reviews", "what's in the queue",
"approve proposals", "review pending promotions"

Constraints: rationale required on every decision (rubber-stamping
structurally blocked), one proposal at a time (no batch approval),
honor the permissions policy (skip proposals the reviewer can't
decide), stop on error during promote (don't cascade failures).

## detect-firm-candidates

Federated cross-employee pattern detector: scan the firm's
`KnowledgeGraph` for personal-plane triples that appear across
N≥3 employees via N≥3 distinct source documents, and stage a
pending firm-plane `Proposal` for each qualifying pattern. The
`review-proposals` skill then surfaces each proposal to a human
reviewer. Independence enforced by distinct-source-file threshold
— three employees sharing one Granola transcript does NOT fire.

Triggers: "detect firm candidates", "find cross-employee patterns",
"federated detection", "what do employees agree on",
"scan for firm truth"

Constraints: administrator-run only, no direct KG writes (proposals
only), no auto-promotion, independence check must pass, stop on
error (don't cascade).

## meeting-prep

Compile a distilled context package (doctrine + per-attendee
outgoing / incoming / events / preferences / related pages) for a
specific meeting or task, render it as markdown, and hand it to the
host-agent LLM for drafting. First workflow-level skill in Memory
Mission; reuses `compile_agent_context` primitive which other
workflow skills (email-draft, CRM-update, deal-memo) can share.
Reads-only: never writes to KG or pages.

Triggers: "prep meeting", "prep for", "brief on", "who is",
"what do we know about", "meeting prep", "meeting with"

Constraints: no LLM call inside the skill (host owns it), must not
include superseded facts, every fact cites source_closet /
source_file, no auto-promotion of observations, respect tier_floor
in constitutional-mode firms.

## update-deal-status

Workflow skill on the venture overlay (P7-A). Resolves a deal entity,
compiles current deal context, asks the host LLM to propose the
next-stage lifecycle transition + rationale, then creates a Proposal
containing an `UpdateFact` (lifecycle_status: old → new) + optional
`EventFact` for human review through review-proposals. Never
auto-promotes. Validates the proposed stage against the constitution's
`lifecycle_stages` vocabulary. For `diligence → memo` transitions,
checks `diligence_required_artefacts` coverage; missing artefacts
surface as forcing questions. Decision-stage transitions are blocked
— use `record-ic-decision` instead.

Triggers: "update deal", "move deal to", "advance deal", "deal
status", "ddq complete", "ddq sent", "memo drafted", "deal passed",
"deal closed"

Constraints: never auto-promote, every transition cites source,
lifecycle vocabulary bounded by constitution, decision-stage
transitions are record-ic-decision's job.

## record-ic-decision

Workflow skill on the venture overlay (P7-A). Resolves a deal
entity in `lifecycle_status: ic`, extracts vote details from the IC
meeting transcript, validates IC quorum against the constitution's
`ic_quorum` field (below-quorum votes produce a `tier=policy` page
instead of `tier=decision`), validates `decision_rights` ceilings,
then drafts a `tier=decision` page bundled with `UpdateFact`
(lifecycle_status: ic → decision) + `ic_decision` predicate fact + (if
`invest`) investment-term facts. Creates a Proposal — never
auto-promotes. Dissenting votes recorded with attribution.

Triggers: "record IC decision", "log IC outcome", "approve
investment", "IC vote complete", "IC decision", "investment committee
decided"

Constraints: IC quorum validated structurally, decision_rights
ceilings checked, every IC decision cites the meeting transcript,
dissenting votes attributed by partner.

## onboard-venture-firm

Administrator skill on the venture overlay (P7-A). Scaffolds a new
venture firm by copying `overlays/venture/firm_template.yaml` →
`firm/systems.yaml`, `overlays/venture/permissions_preset.md` →
`firm/protocols/permissions.md`, page templates → `firm/_templates/venture/`,
and **proposes** the constitution as a `tier=constitution` page through
review-proposals (the firm's partners review + ratify; never
auto-promoted). Optionally runs an initial `backfill-firm-artefacts`
against the firm's investment-thesis Drive folder. Writes a
setup-confirmation page summarizing what was copied and what
placeholders need replacement.

Triggers: "onboard venture firm", "set up venture pilot", "scaffold
venture overlay", "initialize venture firm", "bootstrap venture firm"

Constraints: administrator-run only, never overwrites existing firm
config without explicit confirmation, constitution goes through
review-proposals (no auto-promotion), Drive backfill is optional.

## weekly-portfolio-update

Workflow skill on the venture overlay (P7-A). Compiles a partner-ready
weekly portfolio digest. Resolves every deal currently in
`lifecycle_status: portfolio`, gathers per-company snapshots via
`compile_agent_context` (currently-true triples + recent events + open
questions + related pages), partitions by `portfolio_status`
(`active` first; `exited` / `written_off` only when the sub-state
changed in the last 7 days), computes per-company staleness against
each page's `quarterly_update_cadence_days` frontmatter, and renders a
markdown digest organized as Active portfolio / Recent state changes /
Needs attention / Archive deltas. Hands the render to the host LLM for
narrative shaping. Never proposes — when the operator confirms a real
state change during review, the skill routes them to
`update-deal-status` or `record-ic-decision` with the source artefact.

Triggers: "weekly portfolio update", "portfolio digest", "portfolio
review", "weekly portfolio brief", "portfolio sync", "portfolio status
this week"

Constraints: read-only by contract (no `create_proposal`, no KG
writes, no page mutations), no LLM call inside the skill, must not
include superseded facts, every fact cites source_closet /
source_file, tier_floor defaults to `policy`, firm plane only,
stale-detection threshold reads each company's frontmatter (no
constitution-level fallback without explicit operator ask).

## granola-extraction-pilot

Narrow-slice 3-layer Granola transcript import pipeline. Runs the
operator-supplied filter (tag / date range / explicit meeting IDs)
against the staged Granola corpus, asks the host LLM to extract
typed facts with provenance, writes a dry-run JSONL the operator
inspects BEFORE any KG write, then promotes only the marked
high-signal subset through `review-proposals`. Encodes Hermes'
2026-04-27 framing: Memory Mission stores current truth + structured
history; Granola remains the dated evidence base; this skill is the
controlled bridge between the two. Refuses full-corpus extraction
by design — surfaces a forcing question if the operator asks.

Triggers: "granola pilot", "narrow-slice extraction", "wealth-ai
backfill", "extract granola pilot", "granola extraction pilot"

Constraints: INVARIANT 3-layer split (evidence stays in staging,
extraction emits dry-run JSONL, curated promotion via
review-proposals — never auto-promote); narrow-slice only; every
fact carries source_closet=granola + source_file + source_quote
(no quote, no fact); low-confidence (<0.6) facts dropped, not
promoted as open_questions; entity merges go through
identity_resolver, not extraction.
