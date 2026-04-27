---
type: ADR
id: "0012"
title: "Context-farming surface as first-class operating concept"
status: active
date: 2026-04-27
---

## Context

The micro.so "Agentic Micro Company" manifesto (and the conversation
arc in
`/Users/svenwellmann/.claude/plans/okay-lets-envision-a-joyful-prism.md`)
introduces the **Context Farmer** role: a human whose primary job is
not consuming retrieval but *tending the brain* — keeping context
quality high, surfacing decay, filling gaps, attributing weak claims.
"Hire great people and get out of their way" applied to firm memory.

Memory Mission's substrate already encodes the metadata a Context
Farmer needs: every page has `tier` (constitution / doctrine / policy /
decision), `confidence` (KG-side and page-side), `reviewed_at`
(Move 2 polish), `scope`, `valid_from` / `valid_to`. Every triple
carries `source_closet` + `source_file` provenance. Pages carry
`compiled_truth` + a timeline. We have everything needed to make
context quality inspectable.

What's missing is a **surface** — the "context-health cockpit" the
Context Farmer actually looks at every Monday morning. Today the
operator can write ad-hoc SQL or grep through pages, but there's no
canonical way to ask "what's the brain's state right now?" without
inventing it.

The existing `dashboard.base` (Move 2) ships five firm-plane views
(Recent / Low confidence / Stale / Constitution+doctrine / By domain).
Those are good but they're *retrieval-oriented* (what's in the brain?)
rather than *farming-oriented* (what does the brain need from the
farmer?). A farming surface answers different questions.

## Decision

**Add a "context-farming" surface that splits cleanly into two
layers:**

1. **`dashboard.farming.base`** — Obsidian Bases YAML extending the
   existing `dashboard.base`. Native page-level views the operator
   reads in Obsidian without leaving the vault. Best for views Bases
   can compute natively (per-domain coverage, decay flags, simple
   filter+formula combinations on page frontmatter).

2. **`src/memory_mission/synthesis/coverage.py`** — pure Python module
   exposing 5 named farming primitives as functions over the existing
   `BrainEngine` + `KnowledgeGraph` + `ProposalStore`. Pydantic
   structured aggregates. Best for cross-cutting analytics that need
   joins across pages + KG + proposals (which Bases can't do).

The two layers are complementary, not redundant. Bases is the
*always-on operator UX*. Python coverage primitives are the
*programmatic surface* a workflow skill, an admin script, or the
context-farmer-console (facet G — deferred) consumes. Both report on
the same underlying substrate.

## The 5 farming primitives

Each is a named function in `synthesis/coverage.py` returning a
typed Pydantic aggregate. Each maps to a specific Context Farmer
intervention:

### 1. Per-domain coverage

`compute_domain_coverage(engine, *, plane) -> list[DomainCoverage]`

Returns page count per domain (people / companies / deals / meetings
/ concepts / sources / inbox / archive), broken down by tier. The
farmer sees: "we have 47 deals pages but only 3 are at policy tier;
93% are decision tier — the deal-team isn't promoting decisions to
policy."

**Acts on:** noticing under-promoted domains (lots of decision-tier
content, no doctrine).

### 2. Decay flags

`find_decayed_pages(engine, *, plane, min_age_days=90, min_tier="doctrine") -> list[DecayedPage]`

Returns pages of tier ≥ doctrine that haven't been touched in N+
days. Doctrine is supposed to be reviewed periodically; constitution
even more so. A doctrine page untouched for 6 months is suspect —
either still true (mark `reviewed_at`), or stale (amend or
invalidate).

**Acts on:** scheduled doctrine review; constitutional amendment
proposals.

### 3. Missing page coverage

`find_missing_page_coverage(engine, kg, store=None, *, plane=None, employee_id=None, min_triple_mentions=3, min_proposal_mentions=0, count_objects=False) -> list[MissingPageCoverage]`

Returns entities that appear in N+ KG triples (subject position by
default; objects gated on entity-likeness when ``count_objects=True``)
or N+ proposals targeting the requested ``plane``/``employee_id``,
but don't have a doctrine-or-higher page in the firm wiki. The
farmer sees: "Sarah Chen appears in 12 proposals as the deal sponsor
but we don't have a person-page for her."

Object-position counts are off by default because raw literals
(``portfolio``, ``active``, ``$20m``) routinely land in object
position and would produce false missing-page work.

**Acts on:** create + propose missing pages; promote stub pages to
doctrine after the first N corroborated facts land.

### 4. Source-attribution debt

`find_attribution_debt(kg) -> list[AttributionDebt]`

Returns triples missing `source_closet` or `source_file`. Provenance
is mandatory in our model (ADR-0002 + the "no quote, no fact" rule
in extraction). Triples that slipped through without it are debt:
correctness-grade truth without compliance-grade provenance.

**Acts on:** trace the un-cited triple back to its proposal, attach
source post-hoc, OR invalidate (if the source can't be reconstructed).

### 5. Low-corroboration concentrations

`find_low_corroboration_clusters(kg, *, confidence_floor=0.7, min_cluster_size=3) -> list[LowCorroborationCluster]`

Returns entities with N+ currently-true triples below confidence
floor. Bayesian corroboration (ADR-0001) means low confidence
clusters indicate either the entity needs more evidence (run more
ingestion) or the existing evidence is genuinely uncertain
(reviewer should weaken or invalidate).

**Acts on:** trigger targeted re-extraction on the weak entity; or
promote a coherence-warning that highlights the uncertainty in
downstream queries.

## What goes in Bases vs Python

| Primitive | Bases dashboard | Python coverage.py |
|---|---|---|
| Per-domain coverage | ✅ (groupBy + count) | ✅ (more detailed by-tier breakdown) |
| Decay flags | ✅ (age_days formula + tier filter) | ✅ (returns typed list for scripts) |
| Missing page coverage | ❌ (cross-source join not possible in Bases) | ✅ |
| Source-attribution debt | ❌ (KG isn't a Bases-readable file) | ✅ |
| Low-corroboration clusters | ❌ (KG isn't a Bases-readable file) | ✅ |

Bases handles the page-level views the operator scans daily. Python
handles the analytics that need to join across pages + KG +
proposals. A workflow skill (e.g., a `weekly-context-health` skill)
can compose Python primitives into a rendered report; the operator
sees both surfaces (Bases dashboard always-on; weekly report on
demand).

## Options considered

- **Option A (chosen):** Two layers — Bases for native page views,
  Python for cross-cutting analytics.
- **Option B:** Bases-only. Misses 3 of the 5 primitives entirely
  (no KG joins, no proposal joins). Dishonest as a "context farming
  surface" if half the views can't exist.
- **Option C:** Python-only with a CLI report generator. Loses the
  always-on operator UX that makes farming a daily habit. The Bases
  dashboard is what makes farming feel like *operating the brain*,
  not running batch jobs.
- **Option D:** Web console (facet G). Best UX, biggest commitment.
  Defer until Bases hits its limit.

## Rationale

1. **Bases is the daily UX, Python is the programmatic surface.**
   The Context Farmer reads Bases every morning. Workflow skills +
   admin scripts call Python. Same underlying data, two access
   patterns matched to two consumers.

2. **The 5 primitives are operational, not aesthetic.** Each maps
   to a specific intervention the farmer can take. "Decay flags
   shows 12 doctrine pages > 6 months old → schedule a review pass.
   Missing-page coverage shows Sarah Chen mentioned in 12
   proposals with no page → create + propose her person-page."
   Without a named intervention, a farming primitive is decoration.

3. **Reuses the existing substrate.** No new tables, no new
   storage, no new SQLite files. Coverage primitives are pure
   functions over `BrainEngine` + `KnowledgeGraph` + `ProposalStore`.
   The substrate already has every metadata field the views need.

4. **Manifesto-aligned vocabulary, but architecture-coupled
   semantics.** "Context Farmer" is the manifesto framing; we adopt
   the term because it's apt. The primitives themselves are
   defined in our substrate's language (tier, confidence,
   `valid_to`, `source_closet`) — if the manifesto term fades the
   surface still works.

## Consequences

- **One new Python module** (`synthesis/coverage.py`) + **one new
  Bases dashboard file** (`memory/templates/dashboard.farming.base`)
  + **5 named primitives** documented + tested.
- **The Context Farmer role becomes operable.** Operator opens
  Obsidian → sees `dashboard.farming.base` → reads daily; runs a
  weekly skill that composes the Python primitives into a report.
- **Future workflow skills** (e.g., `weekly-context-health`,
  `quarterly-doctrine-review`) compose these primitives. Each is a
  thin SKILL.md + thin Python wrapper around `synthesis/coverage.py`
  calls.
- **G (web console) defers.** Bases handles the daily surface; G
  only earns its keep when Bases hits a structural limit (most
  likely when the joins-across-pages-and-KG views need to be
  rendered with non-trivial UX).

## Follow-ups

- **`weekly-context-health` workflow skill** — composes the 5
  primitives into a renderable report; operator runs Mondays.
  Separate commit.
- **Email digest** of the coverage report — optional integration
  for firms that want it pushed rather than pulled.
- **Coverage trend tracking** — write the per-domain coverage to
  a `firm/_coverage/snapshots/<date>.json` so trends over time are
  visible (which domain grew? which decayed? are doctrine pages
  being reviewed at cadence?). Adds a small storage overhead;
  defer until trend questions actually surface.

## Related decisions

- **ADR-0001** (Bayesian corroboration) — confidence floor used by
  `find_low_corroboration_clusters`.
- **ADR-0002** (two planes + mandatory provenance) —
  `find_attribution_debt` enforces the provenance contract.
- **ADR-0007** (capability-based connectors) — provenance starts at
  the envelope; coverage primitives are downstream readers.
- **ADR-0013** (personal-plane temporal KG) — the same coverage
  primitives could be applied per-employee in a future personal-
  plane farming surface. Out of scope today.
- **Move 2 polish** (`docs/recipes/vault-dashboard.md`) — the
  existing `dashboard.base` is the precedent this dashboard extends.
