# Recipe: Personal-plane hot cache via host-agent hooks

An employee's agent should start every session warm — with a summary of what was just worked on, what's in progress, what lessons were learned. Memory Mission's Step 12 already defines `personal/<emp>/working/WORKSPACE.md` as the working-state file. This recipe turns it into a hook-maintained session cache: the host agent writes it at the end of each session, reads it at the start of the next.

Borrowed from [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian)'s `hooks.json` pattern — same mechanism, different governance context.

**Scope:** personal plane only. Firm plane is never session-scoped — governance requires human review for every write. Do not wire the `Stop` hook to touch `firm/`.

## Install

Add to your host agent's settings (example here is Claude Code — paths differ for Codex / Cursor / Windsurf but the four hook types are the same):

`~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "[ -f personal/${EMPLOYEE_ID}/working/WORKSPACE.md ] && cat personal/${EMPLOYEE_ID}/working/WORKSPACE.md || true"
          },
          {
            "type": "prompt",
            "prompt": "If a personal-plane vault is configured (check for personal/<employee_id>/ under the current project), silently read personal/${EMPLOYEE_ID}/working/WORKSPACE.md to restore recent context. Do not announce. Do not report. Just have the context available."
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Hook-injected context does not survive context compaction. If personal/${EMPLOYEE_ID}/working/WORKSPACE.md exists, silently re-read it now to restore the hot cache."
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "If personal/${EMPLOYEE_ID}/working/ exists in the current project AND this session touched personal plane files, update personal/${EMPLOYEE_ID}/working/WORKSPACE.md with a brief session summary (under 500 words). Use four sections: Last Updated, Key Recent Facts, Recent Changes, Active Threads. Keep it factual. Overwrite the file completely — it is a cache, not a journal. Do NOT write to firm/ under any circumstances."
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "[ -d .git ] && git add personal/${EMPLOYEE_ID}/ 2>/dev/null && (git diff --cached --quiet || git commit -m \"wip: auto-commit $(date '+%Y-%m-%d %H:%M')\" 2>/dev/null) || true"
          }
        ]
      }
    ]
  }
}
```

Set `EMPLOYEE_ID` in your shell environment (e.g., `export EMPLOYEE_ID=alice`) or substitute your hardcoded employee_id into the commands.

## What each hook does

| Hook | Trigger | Effect |
|---|---|---|
| `SessionStart` | New or resumed session | `cat WORKSPACE.md` into context. Host LLM reads it and continues where the last session left off. |
| `PostCompact` | Conversation compaction | Re-read `WORKSPACE.md`. Hook-injected context does not survive compaction — this restores the hot cache mid-session. |
| `Stop` | End of assistant response | Prompt the LLM to rewrite `WORKSPACE.md` with a 500-word session summary. Overwrites; not append. |
| `PostToolUse` | After Write or Edit | Auto-commit `personal/<emp>/` to git. Zero config when the vault is already a git repo. |

## WORKSPACE.md format

Keep it short and factual. The LLM rewrites the whole file every `Stop`:

```markdown
## Last Updated
2026-04-23 14:05 — Finished Q3 prep for the Acme meeting

## Key Recent Facts
- Sarah Chen is now CFO at Acme (was VP Finance)
- Q3 board deck landed in Drive, need to cross-ref against last quarter's allocation doc
- Alice asked for a summary of the Beta Fund exit options

## Recent Changes
- Added [[Acme Corp]] with updated org chart
- Linted `personal/alice/semantic/deals/` — 3 stale pages flagged

## Active Threads
- Beta exit evaluation (deadline: 2026-05-01)
- Q4 investment pipeline review (waiting on partners)
```

## Why personal only

Three reasons the `Stop` hook must not touch firm plane:

1. **Governance.** Every firm-plane write goes through the `review-proposals` skill with reviewer + rationale. A hook auto-rewriting `firm/` bypasses that gate.
2. **Correctness.** One employee's session summary is not firm truth. Three employees' personal working memory files can contradict each other; the federated detector's job is to reconcile them, not the hook's.
3. **Auditability.** The observability log records every firm-plane change via `ProposalDecidedEvent`. Auto-commits from a hook would not map back to a reviewer or rationale. Audit chain broken.

`WORKSPACE.md` is deliberately ephemeral — the session cache, not canonical memory. Anything worth promoting to the firm plane goes through the normal extraction → proposal → review loop.

## Troubleshooting

- **"Plugin hook STDOUT bug"** — Claude Code issue [#10875](https://github.com/anthropics/claude-code/issues/10875) documents that plugin-level hooks may not reliably inject STDOUT. The workaround is to install the hooks in your `~/.claude/settings.json` directly (as shown above), not as a plugin.
- **Hot cache not restored after compaction** — the `PostCompact` hook is what fixes this. Verify it's present in your settings.
- **Auto-commit noisy** — the `PostToolUse` hook commits on every Write/Edit. If you prefer manual control, remove it. Git still works normally; you just commit by hand.
- **LLM writes to `firm/` anyway** — the `Stop` hook prompt explicitly says "do NOT write to firm/." If the host LLM is ignoring this, tighten the prompt or drop to a `command`-type hook that guards the path with `grep -v firm/ | xargs git add`.

## Further reading

- Memory Mission docs: `src/memory_mission/personal_brain/working.py` — the `WORKSPACE.md` schema + helpers.
- Claude Code hooks: [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/hooks).
- Pattern source: [claude-obsidian hooks README](https://github.com/AgriciDaniel/claude-obsidian/blob/main/hooks/README.md).
