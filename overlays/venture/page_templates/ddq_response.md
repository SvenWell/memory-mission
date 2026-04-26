---
slug: template-ddq-response
title: Template DDQ Response
domain: deals
tier: policy
aliases: []
sources: []
confidence: 0.85
type: ddq_response
deal: template-deal
ddq_sent_date: 2026-01-01
ddq_received_date: 2026-01-15
ddq_status: complete
key_findings: []
flags_raised: []
follow_up_questions: []
scope: partner-only
---

# Template DDQ Response

> **Template page.** Replace `slug` (e.g. `ddq-acme-2026-04-15`),
> `title`, `deal`, dates, and `respondent` when recording a real DDQ
> response.

## Compiled summary

[One-paragraph: what the DDQ surfaced. Key new information learned.
Confidence in the founder's responses. Whether the response materially
changes the investment thesis.]

## Key findings

[Bullet list of the 3-5 most important things the DDQ revealed. Each
becomes a fact in the KG with the DDQ as source.]

- Finding 1: [...]
- Finding 2: [...]
- Finding 3: [...]

## Flags raised

[Anything that warrants follow-up before IC. Each becomes an
`open_question` or `next_step` fact.]

- Flag 1: [...]

## Follow-up questions

[Questions the DDQ response prompted that need answering before the
deal advances. Each becomes a `next_step` fact with a `due_by`.]

- Question 1: [...]

## Workflow status

This DDQ response advances the deal's `ddq_status` from `sent` →
`complete`. The `update-deal-status` skill picks this up and
proposes the corresponding `UpdateFact`. After review, the deal can
transition `lifecycle_status: ddq → memo` (per the constitution's
authoritative stage list).

---

## Timeline

[Auto-maintained: DDQ sent, partial response received, full response
received, follow-up questions issued, follow-up resolved.]
