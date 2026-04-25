---
slug: venture-constitution
title: Venture Firm Constitution
domain: concepts
tier: constitution
aliases:
  - constitution
  - operating-doctrine
sources:
  - overlays/venture/constitution_seed.md
confidence: 1.0
lifecycle_stages:
  - sourced
  - screening
  - diligence
  - ddq
  - memo
  - ic
  - decision
  - portfolio
  - passed
ic_quorum: 3
decision_rights:
  partner_solo: invest_lt_500k
  partner_pair: invest_lt_5m
  ic_full: invest_gte_5m
sourcing_targets:
  per_partner_per_quarter: 50
diligence_required_artefacts:
  - first_call_transcript
  - founder_references
  - market_analysis
  - financial_review
  - tech_review
  - reference_calls
  - ddq_response
ic_meeting_cadence: weekly
fund_thesis_review_cadence: quarterly
---

# Venture Firm Constitution

This page is **constitution-tier** — the highest authority in the firm's
knowledge graph. Anything below it (doctrine / policy / decision) must
be consistent with the structure declared here. Coherence checks at
promote-time will surface conflicts as forcing questions for the
reviewer.

Override per-firm: copy this file into `firm/concepts/venture-constitution.md`
and edit the frontmatter `extra` fields (lifecycle_stages, ic_quorum,
decision_rights, etc.) to match the firm's actual operating model.

## What this constitution governs

A venture-firm Memory Mission deployment is structured around three
operating concepts:

1. **Deals**: discrete investment opportunities. A deal moves through a
   bounded lifecycle (sourced → screening → diligence → ddq → memo → ic
   → decision → portfolio | passed). Every transition is an `UpdateFact`
   with a citation and reviewer rationale. The lifecycle vocabulary
   above (in `lifecycle_stages`) is authoritative — extraction agents
   that propose a `lifecycle_status` value outside this list trigger a
   coherence warning.
2. **People**: founders, operators, co-investors, LPs, advisors. A
   person carries a stable `p_<id>` that survives email + LinkedIn +
   Twitter + phone-channel changes. Memory of a person is the
   relationship history (intros, calls, references, deal-team
   participation), not just their resume.
3. **Portfolio companies**: post-decision deals where the firm has
   capital deployed. Portfolio companies generate ongoing context
   (board meetings, quarterly updates, reserves decisions, follow-on
   rounds, exits). The `portfolio` lifecycle stage is the entry into
   this regime; subsequent state (board cadence, reserve allocation,
   exit modeling) is firm-doctrine, not constitution.

## Lifecycle stages (authoritative vocabulary)

The values declared in frontmatter `lifecycle_stages`:

| Stage | Meaning | Typical artefacts |
|---|---|---|
| `sourced` | Deal exists in firm's awareness; no active investment work | Source attribution (intro person / inbound channel) |
| `screening` | Initial assessment underway (memo skeleton, market scan) | First-call transcript, market notes |
| `diligence` | Active diligence — multiple workstreams in flight | Reference calls, financial review, tech review |
| `ddq` | Due-diligence questionnaire issued; awaiting responses | DDQ document sent + responses received |
| `memo` | Investment memo being drafted for IC | Memo draft, comparables, valuation analysis |
| `ic` | Memo presented to IC; vote pending or scheduled | IC meeting transcript, partner sentiment |
| `decision` | IC vote complete; outcome recorded | Decision page (`tier: decision`), wire details if invest |
| `portfolio` | Capital deployed; ongoing portfolio relationship | Board meetings, quarterly updates, reserves |
| `passed` | Decided not to invest; reason recorded | Pass-rationale page |

Every stage transition writes an `UpdateFact` with predicate
`lifecycle_status`, supersedes the prior status triple, and cites the
artefact that drove the transition (which DDQ doc, which IC meeting).

## Decision rights (authoritative vocabulary)

The values declared in frontmatter `decision_rights`:

| Right | Holder | Authority |
|---|---|---|
| `partner_solo` | Any partner | Investments under $500k (e.g. small bridge / pro-rata participation) |
| `partner_pair` | Two partners aligned | Investments $500k–$5M |
| `ic_full` | Full IC vote (quorum: see frontmatter `ic_quorum`) | Investments $5M+ |

Higher-authority decisions require all lower-authority approvals (a
partner-pair-authorized investment also needs the partner-solo
threshold met by both partners). The promotion gate enforces this
cascade structurally — a `decision` tier page proposing an investment
above the partner-pair ceiling without an IC quorum citation will
coherence-warn.

## IC quorum (authoritative)

Frontmatter `ic_quorum: 3` — three IC members must vote yes for a
full-IC decision to pass. The `record-ic-decision` skill validates
quorum at decision time; missing quorum blocks promotion of the
decision page.

## Diligence required artefacts (authoritative)

Frontmatter `diligence_required_artefacts` — the minimum artefact set
required before a deal can transition `diligence → memo`. The
promotion gate doesn't enforce this (artefacts are evidence, not
facts), but the `update-deal-status` skill checks coverage when
asked to advance to `memo` and surfaces missing artefacts as forcing
questions for the human reviewer.

## Sourcing targets (advisory)

Frontmatter `sourcing_targets.per_partner_per_quarter: 50` — advisory
target for sourced-stage deals per partner per quarter. The Context
Farmer dashboard surfaces under-target partners as a coverage signal
(this is health monitoring, not a hard gate).

## Cadences (advisory)

- `ic_meeting_cadence: weekly` — IC meets weekly. Used by skill
  `weekly-portfolio-update` to schedule pre-IC briefings.
- `fund_thesis_review_cadence: quarterly` — fund thesis reviewed
  quarterly. Used by skill `quarterly-lp-update` to bound the
  thesis-evolution narrative shipped to LPs.

## How extraction agents see this

The host LLM, when extracting facts from sources, is told (via
`overlays/venture/prompt_examples.md`) that:

- Lifecycle status updates use predicate `lifecycle_status` with
  values from the authoritative list.
- IC outcomes use predicate `ic_decision` with values
  `{invest, pass, defer, follow_up}`.
- DDQ status uses predicate `ddq_status` with values
  `{not_sent, sent, partial, complete}`.
- Next-step commitments use predicate `next_step` with a free-form
  string + a `due_by` field.
- Co-investor relationships use predicate `co_investor` (between two
  organizations on a specific deal).

The constitution is the source of vocabulary. Coherence checks
flag predicate values outside the declared lists.

## How reviewers see this

When promoting a triple touching `lifecycle_status` / `ic_decision` /
`ddq_status`, the review-proposals skill loads this constitution page
and renders the authoritative vocabulary inline as part of the
reviewer's decision context. The reviewer can reject promotion if
the proposed value isn't in the list, or update the constitution
itself (which is its own promotion through the gate — constitutional
amendments are not casual edits).

## Override pattern

A firm with a different operating model copies this file into
`firm/concepts/venture-constitution.md` and edits frontmatter only.
The body text is informational (it explains the structure to humans
and agents); the authoritative substance lives in the
machine-readable frontmatter `extra` fields.

## Related

- `overlays/venture/permissions_preset.md` — role presets aligned to
  the decision_rights declared here.
- `overlays/venture/prompt_examples.md` — extraction prompt addenda
  teaching the predicate vocabulary.
- `overlays/venture/page_templates/` — page skeletons that reference
  this constitution's vocabulary.
- `skills/update-deal-status/`, `skills/record-ic-decision/`,
  `skills/onboard-venture-firm/` — workflow skills that consume this
  constitution at runtime.

---

## Timeline

- 2026-04-25 — Constitution seeded as part of `overlays/venture/`
  scaffold (P7-A in `/Users/svenwellmann/.claude/plans/okay-lets-envision-a-joyful-prism.md`).
