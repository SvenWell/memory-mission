# Skill Registry

Read this file first. Full `SKILL.md` contents load only when a skill's
triggers match the current task. Machine-readable equivalent:
`skills/_manifest.jsonl`. Conventions: `skills/_writing-skills.md`.

## backfill-gmail

Pull historical email through the Gmail connector (Composio-backed) into
the **employee's personal staging plane**
(`<wiki_root>/staging/personal/<employee_id>/gmail/`) for the extraction
agent (Step 9) to consume. Each message becomes a checkpointed step
under a durable run, so a crash mid-loop resumes from the last processed
message. No LLM calls, no extraction, no firm-plane writes.

Triggers: "backfill gmail", "import email history", "sync gmail mailbox",
"pull historical email"

Constraints: personal plane only (never firm staging), no writes to
curated wiki pages, no LLM inside the loop, every fetch flows through
the connector harness.

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
