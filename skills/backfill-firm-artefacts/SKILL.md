---
name: backfill-firm-artefacts
version: "2026-04-22"
triggers: ["backfill firm artefacts", "cold-start firm knowledge", "import firm documents", "seed firm wiki", "ingest drive folder"]
tools: [drive_connector, durable_run, staging_writer, observability_scope]
preconditions:
  - "drive connector has a ComposioClient injected"
  - "wiki_root and observability_root are configured"
  - "firm_id resolved; running session belongs to a firm administrator"
  - "the selected Drive folder(s) hold firm-authored material the firm intends to make institutional truth"
constraints:
  - "every fetch flows through the connector harness, never connector.invoke() directly"
  - "wrap the loop in durable_run so crashes resume cleanly"
  - "stage under target_plane='firm' — never personal-plane staging"
  - "do not invoke an LLM inside this skill — extraction lives in Step 9"
  - "do not write directly to wiki MECE domains; promotion is Step 10"
  - "intended for firm administrators only; the resulting proposals seed firm truth and should be reviewed by a designated reviewer before approval"
category: ingestion
---

# backfill-firm-artefacts — cold-start the firm plane from authored documents

## What this does

Pulls firm-authored documents (memos, decks, training docs, quarterly
updates, board material, LP letters, investment thesis docs) through
the Composio-backed Drive connector into
`<wiki_root>/staging/firm/drive/` for the extraction agent (Step 9) to
consume and the review-proposals skill (Step 10) to merge into the
firm plane.

**Why this skill exists:** Emile's authority problem. Firm-level
institutional knowledge cannot be defined by one employee agent's
extracted opinions — that's how a thesis gets distorted by whichever
employee happened to backfill first. The right cold-start path is to
seed firm knowledge from documents the firm itself authored:
investment memos, the pitch deck, training materials, quarterly
updates. A designated reviewer (typically a partner or admin)
approves the resulting proposals through `skills/review-proposals`
before any fact lands in the firm plane.

## Workflow

Open an observability scope for the firm + administrator (the
"employee_id" in the scope is whoever is running the backfill, but the
target plane is firm). Inside it, open a durable run named
`backfill-firm-artefacts-<firm_id>-<run_label>` — `run_label` is
typically the Drive folder being backfilled, so multiple parallel
backfills don't collide. Stand up a Drive connector via
`make_drive_connector` and a staging writer scoped to source `drive`,
`target_plane="firm"` (no `employee_id` for firm-plane staging).

Pull the list of file ids from the requested Drive folder (paginated by
Drive's API max). For each id:

- If the durable run has already marked it done, skip.
- Otherwise, fetch the file through the connector harness — never the
  connector's `invoke()` directly. The harness writes a
  `ConnectorInvocationEvent` with PII-scrubbed preview and latency.
  Composio handles Google Docs export to markdown server-side.
- Hand the raw payload to the staging writer with the file id, and pass
  through any caller-relevant frontmatter extras (file name, mime type,
  modified_time, drive folder id, author). The writer atomically
  writes the raw JSON sidecar plus a frontmatter-headed markdown file.
  The frontmatter records `target_plane: firm` (no `employee_id`) so
  the extraction agent knows where this item belongs.
- Mark the durable step done with state `{"file_id": id}` so the
  resumed run after a crash sees an accurate last-processed marker.

Continue until Drive returns no more files. Then complete the durable
run.

## Where the data lands

```
<wiki_root>/staging/firm/drive/.raw/<file_id>.json
<wiki_root>/staging/firm/drive/<file_id>.md
<observability_root>/<firm_id>/events.jsonl
<durable_db_path>
```

Nothing under `<wiki_root>/firm/` (curated), `<wiki_root>/personal/`,
or any MECE domain directly. Once these stage, the extraction agent
(Step 9) produces facts; the promotion pipeline (Step 10) bundles
them into proposals targeting `target_plane="firm"`; a designated
reviewer approves via `skills/review-proposals` before facts land in
the firm KG.

## Authority + governance

This skill seeds the firm's institutional truth. Three guardrails:

1. **Administrator-only.** A regular employee should not run this
   against arbitrary Drive folders — the resulting proposals would
   crowd the review queue with unvetted material. Restrict access at
   the host-agent level (skill availability, OAuth scope).
2. **Reviewer at the merge gate.** Every proposal still goes through
   `skills/review-proposals`. The administrator running this skill
   does NOT auto-approve their own proposals — separation between
   "who pulled the source" and "who merged the fact" preserves the
   audit trail's value.
3. **Source-folder discipline.** Backfill from folders the firm
   intends as institutional truth. A sandbox / draft folder shouldn't
   be the source; the firm's curated `Knowledge Base` or
   `Investment Thesis` folder should.

## What this skill does NOT do

- No LLM call. Extraction lives in `skills/extract-from-staging`.
- No `MentionTracker` updates.
- No direct connector `invoke()` calls. The harness is mandatory.
- No live OAuth flow. The Composio client is injected by the caller.
- No personal-plane staging. This skill is firm-only; personal
  content comes from `skills/backfill-gmail` and
  `skills/backfill-granola`.
- No auto-promotion. The skill stages — `skills/review-proposals`
  is the merge gate.
- No file-content scrubbing beyond what the connector harness does.
  If a Drive folder mixes confidential and public material, scope the
  backfill to the public folder; don't expect the skill to triage.

## On crash

The durable run guarantees resume-from-last-completed-step semantics.
Re-running the same `thread_id` after a crash skips already-processed
files and continues from where the previous run stopped. Each file is
processed exactly once across the lifetime of a thread, even across
multiple crashes.

## Self-rewrite hook

After every 5 uses OR on any failure:

1. Read the last 5 `ConnectorInvocationEvent` rows for
   `connector_name=drive` from the observability log.
2. If a new failure mode appears (mime types Composio doesn't export
   well, files producing empty bodies, latency spikes on large decks),
   append a one-line lesson to `KNOWLEDGE.md` next to this file.
3. If a constraint above was violated (personal-plane write, LLM call,
   non-administrator running the skill, sandbox folder backfilled by
   mistake), escalate as a project memory so the boundary is logged
   for future sessions.
4. Commit: `skill-update: backfill-firm-artefacts, <one-line reason>`.
