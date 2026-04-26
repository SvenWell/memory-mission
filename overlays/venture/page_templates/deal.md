---
slug: template-deal
title: Template Deal
domain: deals
tier: decision
aliases: []
sources: []
confidence: 0.5
type: deal
lifecycle_status: sourced
deal_owner: replace-with-partner-employee-id
ddq_status: not_sent
co_investors: []
scope: partner-only
---

# Template Deal

> **Template page.** When instantiating: replace `slug` (regex-compliant
> kebab-case), `title`, frontmatter placeholders, and the body section
> headers below. The frontmatter shape itself is the operating contract —
> keep all keys present, fill in real values.

## Compiled truth

[One-paragraph summary: company / sector / stage / why this is interesting
to the firm / current state. Updated as the deal moves through the
lifecycle.]

## Lifecycle

Current status: `sourced` (per frontmatter `lifecycle_status`)

Authoritative lifecycle vocabulary lives in
`firm/concepts/venture-constitution.md` `lifecycle_stages`. Do not invent
new stages — propose a constitutional amendment instead.

## Diligence required artefacts

[As the deal advances to `memo`, populate this list per the constitution's
`diligence_required_artefacts` field. Each artefact is a wiki link to a
staged or curated source.]

- [ ] First-call transcript: <link>
- [ ] Founder references: <link>
- [ ] Market analysis: <link>
- [ ] Financial review: <link>
- [ ] Tech review: <link>
- [ ] Reference calls: <link>
- [ ] DDQ response: <link>

## Open questions

[Questions surfaced by the host LLM during extraction that weren't
high-confidence enough to become facts. Reviewer triages these; some
become next-step actions.]

## Next steps

[Free-form bullet list of next-step commitments. Each maps to a
`next_step` predicate fact with a `due_by`. The
`update-deal-status` skill maintains this section.]

---

## Timeline

[Auto-maintained by the promotion pipeline. Each lifecycle status
change appears here with reviewer + rationale + source citation.]
