# Architecture Decision Records

Load-bearing decisions that shaped Memory Mission's architecture. Each ADR captures context, the decision, the alternatives considered, and the consequences — so a new contributor can understand *why* a choice was made without archaeology.

Pattern lifted from [Tolaria's ADRs](https://github.com/refactoringhq/tolaria/tree/main/docs/adr) — one decision per file, monotonic numbering, never edit active ADRs (supersede instead).

## Format

Each ADR is a markdown file with YAML frontmatter:

```markdown
---
type: ADR
id: "0001"
title: "Short decision title"
status: proposed          # proposed | active | superseded | retired
date: YYYY-MM-DD
superseded_by: "0007"     # only when status: superseded
---

## Context
What situation led to this decision? What forces and constraints were at play?

## Decision
**What was decided.** State it clearly in one or two sentences — bold so it stands out.

## Options considered
- **Option A** (chosen): brief description — pros / cons
- **Option B**: brief description — pros / cons
- **Option C**: brief description — pros / cons

## Consequences
What becomes easier or harder as a result?
What would trigger re-evaluation of this decision?
```

### Status lifecycle

```
proposed → active → superseded
                 ↘ retired  (decision no longer relevant, not replaced)
```

## Rules

- One decision per file
- Files named `NNNN-short-title.md` (monotonic numbering)
- Once `active`, never edit — create a new ADR that supersedes it
- When superseding: the older ADR gets `status: superseded` + `superseded_by: "NNNN"`
- `ARCHITECTURE.md` reflects the current state (active decisions only)

## Index

| ID | Title | Status |
|----|-------|--------|
| [0001](0001-bayesian-corroboration.md) | Bayesian corroboration via Noisy-OR with 0.99 cap | active |
| [0002](0002-two-plane-split.md) | Two-plane split (personal / firm) with one-way promotion bridge | active |
| [0003](0003-mcp-as-agent-surface.md) | MCP as the multi-agent access surface | active |
| [0004](0004-personal-layer-substrate-decision.md) | Personal-layer substrate — MemPalace adopted via adapter | active |
| [0005](0005-sqlite-per-firm.md) | SQLite per firm for all persistent state | active |
| [0007](0007-capability-based-connectors.md) | Capability-based connector roles + fail-closed visibility mapping | active |
| [0011](0011-chat-system-role.md) | `chat_system` role for Slack-shape integrations (per-message envelope + helper-side plane override) | active |
| [0013](0013-personal-plane-temporal-kg.md) | Personal-plane temporal KG alongside MemPalace (per-employee `KnowledgeGraph` instance, scope auto-applied) | active |

Pending ADRs — will be written as the corresponding phase lands (see `/Users/svenwellmann/.claude/plans/we-ve-built-this-and-curious-unicorn.md`):

- `0006` — Grounded evidence pack pattern (Spanner-inspired interface, SQLite backend) — lands with P4
- `0008` — Typed outbound mutations for approved-fact sync-back — lands with P3
- `0009` — Firm-plane auto-wiring typed edges at promote time (GBrain pattern) — lands with P5
- `0010` — Graphify as optional multimodal ingestion adapter — only if P8 spike succeeds

Retroactive candidates (decisions made but not yet written up):

- LLM lives with the host agent (no SDK imports)
- Constitutional mode as opt-in firm-level flag
- Identity resolver Protocol + adapter pattern
- Independence threshold on federated detector (distinct source_files)
- SQL-over-KG as read-only primitive, removed from MCP
- AGENTS.md canonical under `docs/`
