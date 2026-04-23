# Writing Skills

Skills are the unit of learned workflow behavior. Good ones compound;
bad ones rot. Rules below keep them honest.

## File layout

```
skills/<name>/
├── SKILL.md         # required — frontmatter + body
└── KNOWLEDGE.md     # optional — accumulated skill-local lessons
```

Plus the registry at the top level:

```
skills/_index.md         # human-readable list, read first
skills/_manifest.jsonl   # one JSON line per skill, machine-parseable
skills/_writing-skills.md  # this file
```

## Frontmatter

```yaml
---
name: backfill-gmail
version: "2026-04-21"
triggers: ["backfill gmail", "import email history"]
tools: [gmail_connector, durable_run, staging_writer]
preconditions: ["gmail connector has a ComposioClient injected"]
constraints: ["never write directly to MECE wiki domains"]
category: ingestion
---
```

Every field matters. The manifest is generated from these; the future
pre-call hook will enforce `constraints`. `version` is the date of last
non-trivial edit so reviewers can tell stale from current. **Quote the
version** so YAML doesn't parse it as a `date` object — the registry
test compares it as a string against `_manifest.jsonl`.

## Destinations and fences, not driving directions

**Bad (micromanagement):**
> 1. Run `python list_messages.py`. 2. Loop with `for id in ids:`. 3. Call
> `connector.invoke("get_message", {"message_id": id})`...

**Good (structure):**
> Pull the list of message ids inside an observability scope. For each id
> not yet processed, fetch the message through the harness and stage it.
> Wrap the loop in a durable run so a crash mid-loop resumes cleanly.

The first form rots when our primitive names change. The second form
stays true across refactors.

## Self-rewrite hook (every skill ends with this)

```markdown
## Self-rewrite hook
After every 5 uses OR on any failure:
1. Read the last 5 skill-specific entries from the observability log.
2. If a new failure mode has appeared, append it to KNOWLEDGE.md.
3. If a constraint was violated, escalate as a project memory.
4. Commit: `skill-update: <name>, <one-line reason>`.
```

## Registering a new skill

1. Add an entry to `skills/_index.md` (human-readable line).
2. Append a line to `skills/_manifest.jsonl` (one JSON object matching
   the SKILL.md frontmatter shape — the registry test enforces this).
3. Note the addition in `BUILD_LOG.md`.

## Body cap

Aim for under 100 lines. If a skill is longer than that, it's probably
two skills, or it's prescribing the driving directions instead of the
destination.

## Anti-patterns

- Skills with overlapping triggers (progressive disclosure breaks).
- Skills that hard-code shell commands (the bitter lesson — the model
  gets better, your scripts don't).
- Skills that bundle multiple workflows (split them).
- Skills without a self-rewrite hook (no path to learn from failure).

Adopted from `https://github.com/codejunkie99/agentic-stack` (Apache 2.0).
