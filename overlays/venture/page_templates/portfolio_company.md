---
slug: template-portfolio-company
title: Template Portfolio Company
domain: companies
tier: doctrine
aliases: []
sources: []
confidence: 0.95
type: portfolio_company
investment_lead: replace-with-partner-employee-id
board_members: []
board_observers: []
co_investors: []
follow_on_decisions: []
quarterly_update_cadence_days: 90
scope: firm-internal
---

# Template Portfolio Company

> **Template page.** Replace `slug`, `title`, frontmatter values when
> instantiating a real portfolio company.

## Compiled truth

[One-paragraph summary of the company: what they do, where they are in
their lifecycle, key metrics, current state of the firm's relationship.
Updated each board meeting / quarterly update.]

## Investment summary

- Initial check: <amount> at <valuation>
- Lead partner: [[<PARTNER_EMPLOYEE_ID>]]
- Co-investors: [auto-populated from `co_investor` triples]
- Board: [[<PERSON_SLUG_1>]], [[<PERSON_SLUG_2>]]
- Board observers: [[<PERSON_SLUG_3>]]

## Cadences

- Board meeting frequency: [from contract]
- Quarterly update due: <next-due-date>

## Recent quarterly updates

[Wiki links to staged + promoted quarterly update items. The
`weekly-portfolio-update` skill surfaces stale companies (no update >
`quarterly_update_cadence_days` ago) as forcing questions.]

## Open questions

[Things the firm needs to track but haven't resolved: pending
follow-on rounds, governance issues, performance flags.]

## Next steps

[Reserves decisions, follow-on participation, board agenda items.]

---

## Timeline

[Auto-maintained: investment events, board meetings, follow-on
rounds, exit events.]
