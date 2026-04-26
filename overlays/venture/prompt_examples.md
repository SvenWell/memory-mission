# Venture extraction prompt addenda

This file is **prompt-tuning content** — pure text the host agent
appends to `EXTRACTION_PROMPT` (`src/memory_mission/extraction/prompts.py`)
when running extraction over venture-firm staged items. It teaches the
LLM the venture predicate vocabulary + gives it three worked examples
in venture language. **No code change required** — the
`RelationshipFact` / `UpdateFact` schemas accept any string predicate
(predicate vocabulary is a soft cultural guide, not a hard
allowlist; see `extraction/prompts.py:173-176` for the convention).

## How to use

The host agent constructs the extraction call as:

```
{EXTRACTION_PROMPT}

{contents of overlays/venture/prompt_examples.md}

Source item:
{the staged item's body}
```

For a non-venture firm using a different vertical overlay, the agent
substitutes the appropriate `overlays/<vertical>/prompt_examples.md`.

---

## Venture predicate vocabulary (preferred over inventing)

When extracting facts from venture sources (founder calls, IC discussions,
DDQ responses, board meetings, partner emails), prefer the following
predicates. Consistency lets the firm KG roll up across deals + people +
portfolio companies.

### Lifecycle + decision predicates (canonical + parallel sub-states)

A venture deal has **one canonical high-level lifecycle** plus several
**parallel sub-state machines**. The constitution declares the
authoritative vocabulary for each. Single source events commonly emit
multiple facts at once: a sub-state update + an EventFact + a
*recommended-but-not-applied* high-level lifecycle change. The reviewer
decides whether to advance the high-level stage.

| Predicate | Subject | Object (authoritative vocab) | Notes |
|---|---|---|---|
| `lifecycle_status` | deal entity | `sourced` / `screening` / `diligence` / `ddq` / `memo` / `ic` / `decision` / `portfolio` / `passed` | Canonical high-level stage. Authoritative vocab in `firm/concepts/venture-constitution.md` `lifecycle_stages`. |
| `ddq_status` | deal entity | `not_sent` / `sent` / `partial` / `complete` / `reviewed` | Sub-state: diligence questionnaire workflow. |
| `memo_status` | deal entity | `not_started` / `draft` / `ready_for_ic` | Sub-state: investment memo authoring. |
| `ic_status` | deal entity | `not_scheduled` / `scheduled` / `discussed` / `decided` | Sub-state: IC meeting workflow. |
| `closing_status` | deal entity | `not_started` / `negotiating` / `term_sheet_signed` / `closed` | Sub-state: post-decision capital deployment. |
| `portfolio_status` | deal/company entity | `active` / `exited` / `written_off` | Sub-state: post-investment portfolio state. |
| `ic_decision` | deal entity | `invest` / `pass` / `defer` / `follow_up` | Records IC vote outcome. Always paired with IC meeting transcript as source. |
| `next_step` | deal or person entity | free-form string description | Use with optional `due_by` field on the fact for time-bounded commitments. |
| `valuation_at_entry` | deal entity | numeric (USD) | Records the post-money valuation at investment. Confidence ≤ 0.9 unless from term sheet. |
| `closing_date` | deal entity | ISO date | Date capital deployed. |
| `data_room_link` | deal entity | URL | Pointer to the firm-internal data room. |

**Multi-fact emission pattern.** A single source event commonly
warrants emitting several facts at once. Example: "DDQ received" →
emit (1) `ddq_status` update to `complete`, (2) an `EventFact`
recording the receipt date, (3) optionally a *recommended* (not
applied) `lifecycle_status` advance flagged for the reviewer to
confirm. The extraction prompt should not silently auto-advance the
high-level lifecycle on a sub-state event — the reviewer makes that
call.

### Relationship predicates (extend existing vocab)

The core vocab (`works_at`, `invested_in`, `reports_to`, `advises`,
`founded`) already covers most relationships. Venture-specific additions:

| Predicate | Subject | Object | Notes |
|---|---|---|---|
| `co_investor` | organization | organization | "Acme Capital co-invested with Beta VC on Deal X". Bidirectional pair. |
| `lead_negotiator` | person | deal entity | The deal-team partner driving terms. |
| `board_observer` | person | organization | Non-voting board attendance (vs `board_member` which has voting rights). |
| `intro_source` | person | deal entity or organization | The person who originated the firm's awareness of the company. |
| `lp_committed_at` | organization | numeric (USD) | LP commitment to a fund (subject = LP entity, object = USD amount). |

### Identity predicates (use existing)

`works_at`, `founded`, `previously_worked_at` from the core vocab cover
most identity relationships. Venture sources commonly add `degrees_from`
(person → school) and `previous_company_role` (free-form description).

---

## Worked example 1 — first founder call (transcript)

**Source item:** Granola transcript titled "Sarah Chen / Northpoint deck review", 2026-04-15.

**Excerpt:** "...Sarah's CEO, founded Northpoint two years ago after leaving Stripe where she ran the credit risk team. Northpoint is a regulated AML/KYC platform for community banks. They're at $2M ARR, growing 30% MoM, ~$200k burn. Sarah said they'd raise a $4M seed at $20M post. Reference: Diane Brady at Sequoia, who also passed but stayed close. Diane's view: 'Founder is exceptional, market timing risk is the question.' Next step: send DDQ this week, target IC in two weeks."

**Expected extraction (selected facts):**

```json
[
  {
    "kind": "identity",
    "entity_name": "Sarah Chen",
    "identifiers": ["email:sarah@northpoint.bank"],
    "properties": {"role": "CEO", "previous_role": "Head of Credit Risk at Stripe"},
    "confidence": 0.95,
    "support_quote": "Sarah's CEO, founded Northpoint two years ago after leaving Stripe where she ran the credit risk team."
  },
  {
    "kind": "relationship",
    "subject_name": "Sarah Chen",
    "predicate": "founded",
    "object_name": "Northpoint",
    "confidence": 0.95,
    "support_quote": "founded Northpoint two years ago"
  },
  {
    "kind": "relationship",
    "subject_name": "Northpoint",
    "predicate": "lifecycle_status",
    "object_name": "diligence",
    "confidence": 0.85,
    "support_quote": "Next step: send DDQ this week, target IC in two weeks."
  },
  {
    "kind": "update",
    "subject_name": "Northpoint",
    "predicate": "ddq_status",
    "object_name": "sent",
    "supersedes_object": "not_sent",
    "confidence": 0.7,
    "support_quote": "Next step: send DDQ this week",
    "valid_from": "2026-04-15"
  },
  {
    "kind": "event",
    "subject_name": "Northpoint",
    "predicate": "next_step",
    "object_name": "schedule IC presentation",
    "confidence": 0.85,
    "support_quote": "target IC in two weeks",
    "due_by": "2026-04-29"
  },
  {
    "kind": "relationship",
    "subject_name": "Diane Brady",
    "predicate": "intro_source",
    "object_name": "Northpoint",
    "confidence": 0.9,
    "support_quote": "Reference: Diane Brady at Sequoia, who also passed but stayed close."
  }
]
```

**Notes:**
- The `lifecycle_status` change is encoded as both a *current* relationship
  (the new state) AND an *update* (the supersedes pair from `not_sent` →
  `sent` for `ddq_status`). The promotion gate handles invalidation +
  add atomically.
- Confidence below 0.6 routes to `open_question` rather than a triple.
- Every fact carries `support_quote`. No quote, no fact.

## Worked example 1.5 — DDQ received (multi-fact event)

**Source item:** Email titled "Re: Northpoint DDQ — responses
attached", 2026-04-22, from Sarah Chen.

**Excerpt:** "Attached please find our complete responses to your DDQ.
Happy to set up a follow-up call this week to discuss the GTM section
in more detail."

**Expected extraction (selected facts — emphasis on multi-fact pattern):**

```json
[
  {
    "kind": "update",
    "subject_name": "Northpoint",
    "predicate": "ddq_status",
    "object_name": "complete",
    "supersedes_object": "sent",
    "confidence": 0.9,
    "support_quote": "Attached please find our complete responses to your DDQ.",
    "valid_from": "2026-04-22"
  },
  {
    "kind": "event",
    "subject_name": "Northpoint",
    "predicate": "ddq_received_at",
    "object_name": "2026-04-22",
    "confidence": 0.95,
    "support_quote": "Attached please find our complete responses to your DDQ."
  },
  {
    "kind": "open_question",
    "subject_name": "Northpoint",
    "topic": "lifecycle_advance_recommendation",
    "confidence": 0.6,
    "support_quote": "Attached please find our complete responses to your DDQ.",
    "note": "DDQ now complete — RECOMMEND but do not auto-apply lifecycle_status: ddq → memo. Reviewer confirms after reading the responses."
  },
  {
    "kind": "event",
    "subject_name": "Northpoint",
    "predicate": "next_step",
    "object_name": "schedule GTM follow-up call",
    "confidence": 0.85,
    "support_quote": "Happy to set up a follow-up call this week to discuss the GTM section",
    "due_by": "2026-04-26"
  }
]
```

**Notes:**
- ONE source event ("DDQ received") emits FOUR facts: a sub-state
  update (`ddq_status: sent → complete`), an event (the
  receipt date), an `open_question` flagging a recommended (not
  applied) high-level lifecycle change, and a separate next-step
  commitment.
- `lifecycle_status` is NOT auto-advanced. The reviewer decides
  whether `ddq → memo` is warranted after reading the DDQ
  responses themselves.
- `valid_from` is the source-attested event date (when DDQ was
  received: 2026-04-22), NOT the extraction date. This matters for
  timeline-correct queries: "what was Northpoint's ddq_status as of
  2026-04-21?" should return `sent`, not `complete`.

## Worked example 2 — IC decision (meeting transcript)

**Source item:** Granola transcript "Northpoint IC", 2026-04-29, attendees: 5 partners.

**Excerpt:** "Vote: 4 yes, 1 no. Decision: invest $4M at $20M post. Lead: Alice. Closing target: 2026-05-15. Action: legal due diligence to start immediately, term sheet to Sarah by Friday."

**Expected extraction:**

```json
[
  {
    "kind": "update",
    "subject_name": "Northpoint",
    "predicate": "lifecycle_status",
    "object_name": "decision",
    "supersedes_object": "ic",
    "confidence": 0.99,
    "support_quote": "Vote: 4 yes, 1 no. Decision: invest $4M at $20M post.",
    "valid_from": "2026-04-29"
  },
  {
    "kind": "relationship",
    "subject_name": "Northpoint",
    "predicate": "ic_decision",
    "object_name": "invest",
    "confidence": 0.99,
    "support_quote": "Decision: invest $4M at $20M post."
  },
  {
    "kind": "relationship",
    "subject_name": "Northpoint",
    "predicate": "valuation_at_entry",
    "object_name": "20000000",
    "confidence": 0.95,
    "support_quote": "$20M post"
  },
  {
    "kind": "relationship",
    "subject_name": "Alice",
    "predicate": "lead_negotiator",
    "object_name": "Northpoint",
    "confidence": 0.95,
    "support_quote": "Lead: Alice."
  },
  {
    "kind": "event",
    "subject_name": "Northpoint",
    "predicate": "next_step",
    "object_name": "term sheet to founder",
    "confidence": 0.9,
    "support_quote": "term sheet to Sarah by Friday",
    "due_by": "2026-05-03"
  }
]
```

**Notes:**
- `lifecycle_status: decision` and `ic_decision: invest` are *both* useful.
  The first tracks *where in the lifecycle* the deal is; the second
  tracks *the IC's specific vote* (which is permanent, even if the deal
  later moves to `portfolio`).
- The IC quorum check (`ic_quorum: 3` per the constitution) is satisfied
  (4 yes votes). The `record-ic-decision` skill validates this at
  promotion time.

## Worked example 3 — co-investor mention (email)

**Source item:** Gmail message "Re: Acme Series B", 2026-03-30.

**Excerpt:** "We're leading at $40M post for $8M. Beta VC is co-investing $2M. Charlie Capital is following with $500k. Sarah's bringing Diane on as board observer."

**Expected extraction:**

```json
[
  {
    "kind": "relationship",
    "subject_name": "Acme",
    "predicate": "valuation_at_entry",
    "object_name": "40000000",
    "confidence": 0.9,
    "support_quote": "leading at $40M post for $8M"
  },
  {
    "kind": "relationship",
    "subject_name": "Beta VC",
    "predicate": "co_investor",
    "object_name": "Acme",
    "confidence": 0.95,
    "support_quote": "Beta VC is co-investing $2M"
  },
  {
    "kind": "relationship",
    "subject_name": "Charlie Capital",
    "predicate": "co_investor",
    "object_name": "Acme",
    "confidence": 0.9,
    "support_quote": "Charlie Capital is following with $500k"
  },
  {
    "kind": "relationship",
    "subject_name": "Diane",
    "predicate": "board_observer",
    "object_name": "Acme",
    "confidence": 0.9,
    "support_quote": "Sarah's bringing Diane on as board observer"
  }
]
```

**Notes:**
- `co_investor` is a one-way fact per source; ingestion deduplicates the
  bidirectional pair when both directions appear.
- `board_observer` distinguishes from `board_member` (the latter has
  voting rights, the former doesn't).

---

## What NOT to extract

- **Speculation, hypotheticals, conditional statements.** "If they raise
  at $30M, we might lead" is not a fact about valuation. Skip it or route
  to `open_question`.
- **Casual social references.** "Sarah's pretty smart" is not a useful
  identity property. "Sarah ran credit risk at Stripe for 5 years" is.
- **Status changes without temporal grounding.** "Northpoint is in
  diligence" without a transition event isn't an `UpdateFact` — it's a
  current state assertion. Use `RelationshipFact` for current state,
  `UpdateFact` only for *transitions* (with `supersedes_object`).
- **Anything from a DM channel that wasn't explicitly opt-in for firm
  staging.** The Slack envelope helper structurally routes DMs to
  personal-plane staging; extraction running on personal-plane staging
  produces personal-plane facts, never firm-plane.

## Confidence calibration (venture-flavored)

| Confidence | When to use |
|---|---|
| 0.95+ | Direct quote in a primary source (founder said it on a recorded call; appears in signed term sheet). |
| 0.85–0.95 | Restated in a primary source (partner summary of founder claim). |
| 0.70–0.85 | Inferred from context (deal stage implied from "we're sending DDQ this week" → status is at-or-before diligence). |
| 0.50–0.70 | Speculative — route to `open_question` rather than `RelationshipFact`. |
| < 0.50 | Drop. Not extracted. |

---

This file is loaded by the host agent at extraction time. Update it
when the firm's predicate vocabulary or example flavor evolves —
extraction is a prompt-tuning task, not a code task.
