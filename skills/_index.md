# Skill Registry

Read this file first. Full `SKILL.md` contents load only when a skill's
triggers match the current task. Machine-readable equivalent:
`skills/_manifest.jsonl`. Conventions: `skills/_writing-skills.md`.

## backfill-gmail

Pull historical email through the Gmail connector (Composio-backed) into
the firm's staging area for human review. Each message becomes a
checkpointed step under a durable run, so a crash mid-loop resumes from
the last processed message. No LLM calls, no extraction — extraction
agents (Step 8+) consume what this skill stages.

Triggers: "backfill gmail", "import email history", "sync gmail mailbox",
"pull historical email"

Constraints: stages only (no writes to wiki MECE domains), no LLM
inside the loop, every fetch flows through the connector harness.
