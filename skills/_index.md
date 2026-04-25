# Skill Registry

Read this file first. Full `SKILL.md` contents load only when a skill's
triggers match the current task. Machine-readable equivalent:
`skills/_manifest.jsonl`. Conventions: `skills/_writing-skills.md`.

**8 skills shipped** as of 2026-04-25. The three personal-source
backfills (gmail, granola, calendar) all route through P2's envelope
path: load `firm/systems.yaml`, call the per-app envelope helper, and
write via `StagingWriter.write_envelope`. Visibility maps to firm
scope per the manifest — fail-closed by default (ADR-0007).

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
