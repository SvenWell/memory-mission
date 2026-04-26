---
slug: template-ic-decision
title: Template IC Decision
domain: meetings
tier: decision
aliases: []
sources: []
confidence: 0.99
type: ic_decision
deal: template-deal
ic_date: 2026-01-01
attendees: []
ic_decision: invest
yes_votes: 0
no_votes: 0
abstain_votes: 0
quorum_met: true
scope: partner-only
---

# Template IC Decision

> **Template page.** Replace `slug` (e.g. `ic-decision-acme-2026-04-29`),
> `title`, `deal`, `ic_date`, and vote counts when recording a real IC
> decision. The `record-ic-decision` skill validates quorum against the
> constitution's `ic_quorum` field at promotion time.

## Decision

**Outcome: `<invest | pass | defer | follow_up>`** (per frontmatter
`ic_decision`)

Vote: <yes_votes> yes / <no_votes> no / <abstain_votes> abstain.
Quorum: <quorum_met> (constitution requires `ic_quorum: 3`).

## Investment terms (if `invest`)

- Amount: <amount> for <ownership>%
- Valuation: <valuation> post-money
- Lead negotiator: [[<PARTNER_EMPLOYEE_ID>]]
- Closing target: <YYYY-MM-DD>

## Rationale

[Free-form: why the IC voted this way. What thesis the decision rests
on. What concerns were raised. What conditions (if any) are attached
to the vote.]

## Dissenting views (if `no` or `abstain` votes)

[Each dissenter's stated rationale, attributed by partner. Important
for downstream coherence checks if the decision turns out poorly.]

## Conditions / next steps

[Any conditions attached to the decision (e.g., "invest contingent on
reference calls completing positively"). Each becomes a `next_step`
fact with `due_by`.]

## Quorum check

The constitution requires `ic_quorum: 3` (see
`firm/concepts/venture-constitution.md`). With <yes_votes + no_votes>
voters present, quorum is `<met | not met>`. Decisions without quorum
cannot promote to `tier: decision` — they remain `tier: policy`
(advisory) until a follow-up vote with quorum.

---

## Timeline

[Auto-maintained: when this decision was created, when promoted, when
challenged or amended.]
