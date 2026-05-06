---
type: ADR
id: "0016"
title: "Personal observation layer — read-shape over triples + sources"
status: active
date: 2026-05-06
---

## Context

Memory Mission's three-layer framing (`docs/VISION.md`, landed in commit
`4655ce5`) splits operational memory into factual (current truth) +
interaction (raw evidence) + action (forward-looking guardrails). The
factual layer is implemented as the personal/firm KG (`Triple` +
`triple_sources` in `memory/knowledge_graph.py`). The interaction layer
is implemented as MemPalace evidence drawers + the `OpenQuestion` fact
bucket + mandatory `support_quote` on every fact.

What's missing is a **bridge between them**: an "evidence-backed belief
about the world" surface — one that says "here is what we know about
Sarah Chen, here is how many sources back it, here is whether the picture
is strengthening, stable, or going stale." Today an agent calling
`mm_query_entity("sarah")` gets a flat list of triples with no aggregated
freshness or trend; it has to compute that itself, every call, with
inconsistent results across agents.

Hindsight (`vectorize-io/hindsight`) names this layer **Observations** —
auto-consolidated evidence-backed beliefs with `proof_count`,
`freshness_trend`, and a `history` array tracking how the belief
evolved. The strategy note `.context/plans/hindsight-patterns-for-memory-mission.md`
identifies it as the highest-leverage Hindsight steal for our personal
layer.

Two non-trivial design questions need locking before any implementation:

1. **Storage shape.** Is `PersonalObservation` a separately-persisted
   entity (like `Triple`), or a **read-shape over existing triples + sources**?
2. **Freshness rule.** Hindsight's public docs do not specify the exact
   `proof_count` increment rule or `freshness_trend` thresholds (verified
   2026-05-05 web research). What's our initial rule, and how do we
   iterate it without a migration?

This ADR locks both decisions before the Phase 1 implementation commit.

## Decision

`PersonalObservation` is a **frozen Pydantic read-shape** over existing
`Triple` + `triple_sources` rows. Nothing new persists. Everything is
derived from data the substrate already stores.

### Shape

```python
@dataclass(frozen=True)
class PersonalObservation:
    subject: str                       # entity name
    predicate: str                     # the relationship/attribute
    object: str                        # the value
    proof_count: int                   # = len(triple_sources for this triple)
    freshness_trend: FreshnessTrend    # see rule below
    confidence: float                  # = triples.confidence
    valid_from: date | None
    valid_to: date | None              # None = currently true
    last_corroborated_at: datetime     # = MAX(triple_sources.added_at)
    history: list[ObservationSource]   # one entry per corroboration
    tier: Tier
    scope: str

@dataclass(frozen=True)
class ObservationSource:
    source_closet: str | None
    source_file: str | None
    confidence_after: float
    added_at: datetime
```

`FreshnessTrend = Literal["new", "strengthening", "stable", "weakening", "stale", "contradicted"]`

### Freshness rule (initial, iterable)

Computed at read time from the underlying triple + sources:

| Trend            | Condition                                                                                  |
|------------------|---------------------------------------------------------------------------------------------|
| `contradicted`   | A coherence warning is currently open against the underlying triple.                        |
| `new`            | `last_corroborated_at` < 7 days ago AND `proof_count == 1`.                                 |
| `strengthening`  | `proof_count` increased in the last 14 days.                                                |
| `stable`         | `proof_count > 1` AND last corroboration 14–30 days ago.                                    |
| `weakening`      | Last corroboration 30–60 days ago AND no new sources in that window.                        |
| `stale`          | Last corroboration > 60 days ago.                                                           |

Order of evaluation matches the table top-to-bottom: `contradicted` wins
over everything; `new` wins over `strengthening`; etc.

The rule is intentionally simple. Hindsight's exact thresholds are not
publicly documented, and committing to a column would commit us to a rule
we'll likely tune. Computing on read lets us iterate the rule in code
(unit-test-driven) without a migration.

Promotion to a stored column or a materialized view is gated on
**measured `mm_observe` p95 latency exceeding 200ms on the brian palace**.
Until then, computed-on-read.

### Boundary with the KG

`PersonalObservation` is **read-only**. There is no `record_observation`
write tool. State changes route through the existing write surface:

- New evidence → `record_facts` (Keagan, `09e4e0d`) → triple corroborated
  → observation's `proof_count` and `last_corroborated_at` update on next
  read.
- Outdated evidence → `invalidate_fact` (`09e4e0d`) → triple's `valid_to`
  set → observation reflects `valid_to` on next read.
- Contradicted evidence → coherence warning landed → observation's
  `freshness_trend` flips to `contradicted` on next read.

This keeps `PersonalObservation` consistent with **ADR-0004 §5 (net
complexity reduction or wash)**: zero new write paths, zero new state
storage, one new read shape that composes existing primitives.

### Citations contract (ADR-0004)

Every `ObservationSource` in `history` carries `(source_closet,
source_file)` from the underlying `triple_sources` row. There is no
synthesized observation without exact source attribution. This satisfies
ADR-0004 §3 (citations contract) by reusing the same provenance fields
the rest of the substrate already requires.

### Where it slots in the three-layer framing

`PersonalObservation` sits between Layer 2 (interaction memory in
MemPalace evidence drawers) and Layer 1 (factual memory in the KG):

- **Layer 1 (KG triples)** — what's currently true, with validity windows
  and Bayesian corroboration. Already shipped.
- **Layer 1.5 (PersonalObservation, this ADR)** — what's currently
  *believed*, with proof count and freshness trend. Read-shape.
- **Layer 2 (MemPalace evidence)** — the raw drawers the beliefs trace
  to. Already shipped via ADR-0004.

The "1.5" framing matters: PersonalObservation is *not* a third layer.
It's a read-projection of Layer 1 enriched by source aggregation. No new
source of truth.

## Consequences

### What gets simpler

- Agents asking "what do we know about X" get one query that returns
  observations directly, without each agent reimplementing freshness
  aggregation.
- The "what changed about X recently?" workflow becomes one filter on
  `freshness_trend`, not a join across triple_sources timestamps.
- Boot context (`compile_individual_boot_context`) can surface
  observations alongside threads/commitments/preferences without a
  schema change.
- Adoption of further Hindsight patterns (mental-model pages with delta
  refresh, Suggested* proposals) has a clean foundation — they all
  consume `PersonalObservation`, not raw triples.

### What gets harder

- The freshness rule is in Python, not SQL. Iterating it requires a code
  change + test update + a release. Acceptable trade for not committing
  the rule to a migration.
- Composability with bulk operations (e.g., "find all stale observations
  across the KG") requires a JOIN-and-compute pass, not an indexed lookup.
  Acceptable while the personal KG is single-employee-scale; revisit if
  observation count exceeds 10k per employee.

### What we commit to

- No new write surface for observations. Corrections route through
  `invalidate_fact` / `record_facts`.
- No new persisted state. The ADR can be reversed by deleting one Pydantic
  model + one method + one MCP tool, with zero data migration.
- Freshness rule lives in `personal_brain/observations.py:_compute_freshness`
  as a pure function, behind unit tests. It IS expected to change. The
  ADR records the initial rule, not the final one.
- The 9 → 10 MCP tool count bump in `tests/test_provider_contract.py` is
  the only pinned-contract change.

## Acceptance criteria

Phase 0 (this ADR) is accepted when:

1. The throwaway `tests/test_observation_spike.py` parametrizes both
   "freshness as new column" and "freshness as computed-on-read"
   approaches and demonstrates both produce the same observable shape on
   a synthetic `PersonalKnowledgeGraph`. The test is deleted in Phase 1.
2. The freshness rule above survives review against five worked examples
   from real Hermes-touched data (Sven, Memory Mission, Wealthpoint,
   Brian, Justin/Aaron commitment).
3. `make check` passes with the ADR + spike test in place.

Phase 1 (the implementation commit) is accepted when:

1. `mm_observe` returns ≥5 distinct observation shapes against the
   production palace at `/Users/svenwellmann/brian/.mempalace/palace`.
2. Provider contract test green at 10 tools.
3. `mm eval replay --tool mm_observe` replays cleanly.
4. Released as `v0.1.5`; Hermes can pull on next reinstall.

## Stop conditions

If, post-Phase-1, EITHER of:

- `mm_observe` p95 latency exceeds 500ms on the brian palace, OR
- Hermes does not call `mm_observe` within 14 days of pin

then the Hindsight pattern-adoption track halts and we re-assess pattern
selection. Both stop conditions are on `project_hermes_feedback_log.md`
to track.

## References

- `docs/VISION.md` — three-layer operational memory framing
- `docs/OPERATING_STATE.md` — canonical predicate vocabulary
- ADR-0001 — Bayesian corroboration via Noisy-OR (the proof-count
  semantics already exist as `corroboration_count` on `Triple`)
- ADR-0004 — Personal-layer substrate (MemPalace adopted; complexity
  acceptance criterion #5)
- ADR-0013 — Personal-plane temporal KG (the `Triple` + `triple_sources`
  this read-shape projects over)
- ADR-0015 — Individual brain mode (write policy this ADR conforms to)
- `.context/plans/hindsight-patterns-for-memory-mission.md` — full
  strategy note
- `.context/plans/i-dug-into-the-current-hindsight-docs-and-repo-my-.md` —
  Hindsight docs/repo dig
- Hindsight Observations docs: https://hindsight.vectorize.io/developer/observations
- `~/.claude/plans/the-real-insight-i-floofy-pumpkin.md` — phased build
  plan (Phase 0 = this ADR + spike test; Phase 1 = `mm_observe` MCP tool)
