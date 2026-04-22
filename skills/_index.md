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
