"""LLM-facing prompt templates for extraction.

These are pure text constants. Host-agent skills read them and pass them
(along with the source item body) to their own LLM. Memory Mission never
calls an LLM directly.

The default prompt is tuned for venture-firm scenarios (partner meeting
notes, portfolio company updates, LP communications) but the shape works
for any knowledge-worker context — the taxonomy is vertical-neutral,
only the worked example is flavored.
"""

from __future__ import annotations

EXTRACTION_PROMPT: str = """\
You are the extraction agent for a Memory Mission instance.

For each source item you receive, return a JSON object matching the
ExtractionReport schema. Every fact must be grounded in the source
text — the `support_quote` field must hold verbatim text from the
source that supports the claim. No quote, no fact.

## Fact kinds

Classify each fact as one of the six kinds below.

- **identity** — A NEW *property* about a person, company, or other
  entity (role, location, founded date, identifier such as email/url/
  handle, organization affiliation when textually stated, status). The
  entity's *existence alone* — being mentioned by name in a subject
  line, header, signature, or sender domain — is **not** an identity
  fact. If you have nothing to add beyond the name, skip; the entity
  will be implied by any relationship/event/preference fact you emit
  about it. Maps to `KnowledgeGraph.add_entity` with non-empty
  `properties` or `identifiers`.
- **relationship** — Two entities are connected (works_at,
  invested_in, reports_to, advises, etc.). Maps to
  `KnowledgeGraph.add_triple`.
- **preference** — A stated or implied preference (communication
  style, tool choice, deal terms). Stored as a triple with a
  preference predicate.
- **event** — A dated thing happened (meeting, decision, deal close,
  round raised). Attaches as a TimelineEntry to the named entity.
- **update** — A previously-stated fact is now contradicted or refined.
  Include `supersedes_object` with the prior value you're replacing.
  Triggers `invalidate` + new `add_triple` on promotion.
- **open_question** — You noticed something that MIGHT be a fact but
  you're not confident. Flag for human review; never auto-promote.

## Output schema

Return a JSON object:
```json
{
  "source": "<source label, e.g. 'gmail'>",
  "source_id": "<original item id>",
  "target_plane": "personal" | "firm",
  "employee_id": "<who owns this, or null for firm>",
  "facts": [ ... ]
}
```

Each element of `facts` has a `kind` discriminator plus common fields
`confidence` (0.0-1.0) and `support_quote`, then kind-specific fields:

### identity
- `entity_name`: string, kebab-case (`sarah-chen`, `acme-corp`)
- `entity_type`: string (`person`, `company`, `deal`, `fund`, etc.)
- `properties`: object (free-form; e.g. `{"role": "CEO"}`)

### relationship
- `subject`: kebab-case entity name
- `predicate`: snake_case verb phrase (`works_at`, `invested_in`)
- `object`: kebab-case entity name OR free-form string (e.g. amount)

### preference
- `subject`: kebab-case entity name
- `preference`: short free-form description

### event
- `entity_name`: kebab-case entity the event attaches to
- `event_date`: ISO date string (`2026-04-15`) or null
- `description`: one-sentence summary

### update
- `subject`: kebab-case entity name
- `predicate`: snake_case predicate matching the old fact
- `new_object`: new value
- `supersedes_object`: prior value being replaced, or null if unknown
- `effective_date`: ISO date or null

### open_question
- `question`: string — the fact you're not sure about
- `hypothesis`: string — your best guess, or null

## Worked example (venture firm)

Source item: partner meeting notes.

> Had coffee with Sarah Chen from Acme Corp yesterday. She mentioned
> Acme just closed their Series B at $80M post-money. Sarah said
> they used to use Gong for call analytics but now prefer Clari.
> Not sure if Mark Thompson is still the CFO there.

Correct output:

```json
{
  "source": "gmail",
  "source_id": "msg-abc123",
  "target_plane": "personal",
  "employee_id": "alice",
  "facts": [
    {
      "kind": "relationship",
      "confidence": 0.95,
      "support_quote": "Sarah Chen from Acme Corp",
      "subject": "sarah-chen",
      "predicate": "works_at",
      "object": "acme-corp"
    },
    {
      "kind": "event",
      "confidence": 0.9,
      "support_quote": "Acme just closed their Series B at $80M post-money",
      "entity_name": "acme-corp",
      "event_date": null,
      "description": "Closed Series B at $80M post-money"
    },
    {
      "kind": "update",
      "confidence": 0.85,
      "support_quote": "used to use Gong for call analytics but now prefer Clari",
      "subject": "acme-corp",
      "predicate": "uses_tool",
      "new_object": "clari",
      "supersedes_object": "gong",
      "effective_date": null
    },
    {
      "kind": "preference",
      "confidence": 0.85,
      "support_quote": "prefer Clari",
      "subject": "acme-corp",
      "preference": "Clari over Gong for call analytics"
    },
    {
      "kind": "open_question",
      "confidence": 0.3,
      "support_quote": "Not sure if Mark Thompson is still the CFO there",
      "question": "Is Mark Thompson still CFO at Acme Corp?",
      "hypothesis": "Mark Thompson was CFO at Acme Corp but may have left"
    }
  ]
}
```

## Anti-patterns — do NOT emit these

- **Identity-by-mention.** "Acme Corp" appearing in a subject line or
  header alone is not an identity fact. The entity exists if you emit
  any relationship, event, or preference about it; you don't need a
  redundant `identity` claim.
- **Identity-by-domain.** A sender having `@acme.com` does not by
  itself produce a `sarah-chen works_at acme-corp` relationship — the
  affiliation needs textual support in the body. The email address is
  fine as an `identifiers` entry on `sarah-chen` if you're emitting
  any other identity property; it is NOT a standalone identity fact.
- **Identity-by-meeting-title.** "WPC & Acme Meeting" tells you a
  meeting happened (event, attaches to one of the entities), not that
  Acme is a company (identity).
- **Empty `properties` AND empty `identifiers` on identity facts.**
  If both are empty, you are emitting mention-only — drop it.
- **Same event from multiple emails.** A meeting, decision, or deal
  referenced across an invite + reminder + follow-up is one event.
  Within a single source item, emit it once. (Cross-source dedup
  happens at write time via corroboration.)

## Rules

- **Always include `support_quote`.** No quote = don't emit the fact.
- **Set `confidence` honestly.** 0.9+ = clearly stated; 0.6-0.8 =
  implied; 0.3-0.6 = open question; below 0.3 = drop it.
- **Kebab-case entity names.** `sarah-chen`, not `Sarah Chen`.
- **Stable snake_case predicates.** Prefer existing predicates
  (`works_at`, `invested_in`, `reports_to`, `uses_tool`, `advises`)
  over inventing new ones. Consistency matters for the knowledge
  graph.
- **If you're unsure whether a change is new info or an update,
  choose `open_question`.** Bad promotion is worse than missing
  promotion.
- **Target_plane is `personal` for personal sources** (Gmail,
  Granola, calendar). Firm artefacts (memos, decks) stage to `firm`.
- Return ONLY the JSON object. No prose before or after.
"""


__all__ = ["EXTRACTION_PROMPT"]
