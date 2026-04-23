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

More ADRs will land as decisions accumulate. Good candidates for the next few:

- `0003` — LLM lives with the host agent (no SDK imports)
- `0004` — Constitutional mode as opt-in firm-level flag (not default strict)
- `0005` — Identity resolver Protocol + adapter pattern (Composio-shaped)
- `0006` — Independence threshold on federated detector (distinct source_files, not just employees)
- `0007` — SQL-over-KG as read-only primitive (Step 16.5)
- `0008` — AGENTS.md canonical under `docs/` because claude-mem owns repo-root (Move 4)
