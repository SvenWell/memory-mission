# Memory Mission — Operating-State Vocabulary

*Canonical predicates for "operational memory." Synthesized 2026-05-05 from the operational-memory-for-agents reframe (see [VISION.md](VISION.md)). Update when adding a new operating-state predicate or status enum; do not invent synonyms in extraction prompts.*

---

## What this doc is

When Memory Mission turns communication residue into operating state, the agent needs to ask:

- What do I owe this person?
- What's blocking this deal?
- What objections are unresolved?
- What's at risk?
- Who owns the next step?
- What's the customer's actual pain?
- What needs to escalate?
- What questions are still open?

Each answer rests on a predicate or a status enum in the KG. This doc is the canonical list. Extraction prompts (`src/memory_mission/extraction/prompts.py`), MCP tools (`src/memory_mission/mcp/`), and skills (`skills/<name>/SKILL.md`) reach for the names below — not synonyms.

The substrate already supports every predicate in this doc. Each one slots into `RelationshipFact` (`src/memory_mission/extraction/schema.py`) or appears as a status enum on an existing object (`ActiveThread`, `Commitment` in `src/memory_mission/synthesis/individual_boot.py`). No new fact buckets are needed; predicates inside `RelationshipFact` carry the load. Status enums extend on existing objects.

---

## Lifecycle convention

Stateful operating-state items follow one canonical lifecycle:

```
open → investigating → resolved
                    ↘ closed
```

- `open` is the default on first observation.
- `investigating` is the active-work state — someone is on it.
- `resolved` means the item concluded with a recorded outcome (predicate `resolution` carries the outcome, or a follow-up `UpdateFact` invalidates the prior state).
- `closed` means the item ended without resolution — deferred, dropped, superseded.
- Reopen by transitioning back to `open` with a new `valid_from`.

Predicates that are not stateful (e.g., `owner`, `customer_pain`) are plain relationships. They invalidate via `valid_from` / `valid_to` on the underlying triple, not via a status enum.

The four-state enum on `Commitment` (`open` / `completed` / `blocked` / `cancelled`) and the four-state enum on `ActiveThread` (`active` / `in_progress` / `blocked` / `deferred`) are the existing precedent. New stateful predicates default to the open/investigating/resolved/closed convention; existing enums stay as-is for backward compatibility.

---

## Predicates

### `objection`

What it captures. A stated or strongly implied disagreement with a proposal, decision, or direction. Sales calls produce these constantly: "we'd need SSO before we could deploy," "valuation feels high," "the timeline doesn't work for us." Internal versions: a partner pushing back on a deal, a designer flagging a UX concern in spec review, an engineer questioning a launch assumption.

Shape. `RelationshipFact(subject=<who>, predicate="objection", object=<what they object to>)`. Plus a paired status triple: `(<objection-subject>, "objection_status", <state>)` where state ∈ {open, investigating, resolved, closed}. Severity travels via `confidence` on the underlying fact.

Example. From a deal call: `(p_alice_acme, objection, deal:acme-series-b)` with `objection_status=open`, support_quote="Alice said the 25M valuation is hard for them to justify against current ARR." A later partner sync resolves it: `objection_status=resolved`, support_quote="Partner pair agreed to 22M; Alice confirmed acceptable."

### `blocker`

What it captures. A relationship where one entity prevents progress on another. Distinct from `commitment_status="blocked"` (a Boolean state on a commitment) — `blocker` names *what* is blocking. Used for deal blockers (legal review pending), technical blockers (auth dependency), sequencing blockers (deal A must close before deal B).

Shape. `RelationshipFact(subject=<blocking entity>, predicate="blocker", object=<blocked entity>)`. Status via `(<blocker triple identifier>, "blocker_status", <state>)`. The blocking entity can be a person, an org, a deal, a decision, a missing artefact.

Example. `(legal_review:acme-msa, blocker, deal:acme-series-b)` with `blocker_status=open`. When legal completes the review, status flips to `resolved` with a follow-up `UpdateFact` invalidating the original triple.

### `dependency`

What it captures. A relationship where one entity must complete or be true before another can proceed. Stateless — dependencies don't have an "investigating" phase, they either hold or are released. The release is modeled as `valid_to` on the triple.

Shape. `RelationshipFact(subject=<prerequisite>, predicate="dependency", object=<dependent>)`. Direction: subject must be true before object can advance.

Example. `(ddq_complete:acme, dependency, ic_meeting:acme)`. When DDQ finishes, the dependency triple gets `valid_to` stamped; downstream IC scheduling can proceed.

### `risk`

What it captures. A condition under which an outcome could degrade. Distinct from `objection` (which is voiced disagreement) and `blocker` (which is current obstruction). Risks are conditional and forward-looking: "if churn signal recurs," "if regulatory environment shifts," "if founder leaves."

Shape. `RelationshipFact(subject=<risk source or condition>, predicate="risk", object=<at-risk entity>)`. Status via `(<risk triple identifier>, "risk_status", <state>)`. Severity travels via `confidence`. A `risk` that materializes typically transitions to a `blocker` or an `objection` via a follow-up extraction.

Example. `(founder_health, risk, deal:acme-series-b)` with `risk_status=open`, support_quote="Founder mentioned 12-hour days are unsustainable; partner flagged as a watchlist item." If the founder takes a leave, a follow-up extraction creates a `blocker`.

### `owner`

What it captures. The accountable party for an entity — typically a deal, an action, an open question, a decision. Stateless. Distinct from `lead_negotiator` and other venture-overlay role predicates, which name *function*; `owner` names *accountability*.

Shape. `RelationshipFact(subject=<person>, predicate="owner", object=<entity>)`. Single owner per entity at a time; transfer of ownership invalidates the prior triple via `valid_to` and writes a new one.

Example. `(p_kai_partner, owner, deal:acme-series-b)`. On reassignment: prior triple gets `valid_to`; new triple `(p_jordan_partner, owner, deal:acme-series-b)` with new `valid_from`.

### `customer_pain`

What it captures. An expressed problem, friction, or unmet need from a customer or prospect. Distinct from `objection` (disagreement with a proposal); `customer_pain` names what the customer is *trying to solve*. Critical for diligence (does the founder understand customer pain) and for product (does our roadmap address it).

Shape. `RelationshipFact(subject=<customer or org>, predicate="customer_pain", object=<pain description>)`. Stateless. Severity via `confidence`. Aggregating multiple `customer_pain` triples across customers is the structural ground for "this is the third churn signal" pattern detection (see `src/memory_mission/federated/detector.py`).

Example. `(o_acme_corp, customer_pain, "manual reconciliation of three CRMs takes 6 hours/week per AE")` with `confidence=0.85`, support_quote from the discovery call transcript.

### `escalation`

What it captures. A condition under which an item must be raised to a higher decision-maker, or the act of raising. Two flavors: prospective (`escalate_if`) and reactive (`escalated_to`). Used when a commitment slips past `due_by`, when a `risk_status` transitions to `materialized`, when a customer objection threatens deal close.

Shape. Two predicates:
- `RelationshipFact(subject=<entity>, predicate="escalate_if", object=<condition>)` — prospective rule
- `RelationshipFact(subject=<entity>, predicate="escalated_to", object=<person or role>)` — reactive event with `valid_from` as escalation date

Example prospective: `(commitment:acme-msa-redline, escalate_if, "no response from Acme legal by 2026-05-12")`. Example reactive: `(deal:acme-series-b, escalated_to, p_managing_partner)` with `valid_from=2026-05-13`.

### `unresolved_question`

What it captures. A question raised in interaction that does not yet have an answer the firm trusts. The substrate already has `OpenQuestion` (`src/memory_mission/extraction/schema.py:105-110`) as a fact bucket — this is its KG-projection counterpart for tracking lifecycle. `OpenQuestion` extracts the question; `unresolved_question` carries it as queryable state.

Shape. `RelationshipFact(subject=<topic or entity>, predicate="unresolved_question", object=<the question>)`. Status via `(<unresolved-question triple identifier>, "question_status", <state>)`. Resolution writes a paired `(<topic>, "answer", <the answer>)` triple and transitions status to `resolved`.

Example. `(deal:acme-series-b, unresolved_question, "what's the customer concentration in Acme's top 5 accounts?")` with `question_status=open`. After diligence: `question_status=resolved`, paired triple `(deal:acme-series-b, answer, "top 5 accounts = 38% of revenue per data room file 4.2")`.

---

## How extraction reaches for these

When a new extraction prompt is written or revised (`src/memory_mission/extraction/prompts.py`), it must reach for the predicate names above when the underlying communication residue maps to one of them. Examples of mappings:

- "We can't move forward until legal signs off" → `blocker` (legal is blocking) + `dependency` (legal review is a prerequisite)
- "Alice flagged the valuation" → `objection` (Alice objects to valuation)
- "Founder mentioned the 12-hour days are unsustainable" → `risk` (founder health is a risk to deal)
- "Customers keep asking for SSO" → `customer_pain` (across multiple customers; federated detector catches the pattern)
- "Kai owns the redline turnaround" → `owner` (Kai is accountable)
- "If no response by Friday, escalate to Jordan" → `escalate_if` (prospective rule)
- "Still don't know if their AWS bill is sustainable at scale" → `unresolved_question`

Synonyms ("worry," "concern," "risk factor," "blocker") all canonicalize to the predicates above. The extraction prompt is responsible for mapping; the doc is the source of truth for the names.

---

## Tool surface (as of 2026-05-05)

13 individual-mode MCP tools (`src/memory_mission/mcp/individual_server.py`) and 14 firm-mode tools (`src/memory_mission/mcp/server.py`) currently expose operating state. Grouped by what they do for the predicates above:

**Lifecycle-transition primitives (writes):**

- `record_facts(facts: list[dict], ...)` — write multiple facts atomically with append-only audit events for source quotes (commit `09e4e0d`).
- `invalidate_fact(...)` — append `valid_to` to a currently-true triple with required rationale; this is the lifecycle-transition primitive for `open → resolved`, `open → closed`, and `open → investigating` shifts on the predicates above (commit `09e4e0d`).
- `record_commitment`, `record_preference`, `record_decision` — typed convenience wrappers.
- `upsert_thread_status` — `ActiveThread` lifecycle moves.

**Read surface (operating-state queries):**

- `get_boot_context(task_hint, token_budget)` — startup snapshot returning active_threads, commitments, preferences, recent_decisions, relevant_entities, project_status.
- `query_entity(name, direction, as_of)` — outgoing/incoming triples; **now annotates `conflicts_with`** for currently-true triples sharing a subject + predicate but a different object (commit `574bb5c`). This is partial visibility into "what state is contested right now" without a separate read tool.
- `compile_agent_context(role, task, attendees, …)` and `render_agent_context(...)` — individual-mode versions shipped (commit `80f647f`); produce operating-state-shaped output for a specific task.
- `search_recall(query, limit)` — evidence-layer search across the MemPalace/recall backend.

**Not yet shipped (signal-gated; Move 3 of `the-real-insight-i-floofy-pumpkin.md`):**

- `mm_list_open_commitments(to, status)` — bulk read for commitments by recipient/status.
- `mm_list_blockers(entity)` — what's blocking a given entity.
- `mm_list_unresolved_questions()` — unresolved-question lifecycle view.

Mid-session agents currently use `query_entity` + parse predicate/object pairs against the vocabulary above. `compile_agent_context` returns task-relevant operating state for workflow agents (`meeting-prep`, etc.). New predicates surface the same way once extraction starts producing them.

---

## How to extend

Adding a new operating-state predicate:

1. Propose the predicate name + lifecycle (stateful or stateless) + at least one mapping example in this doc.
2. Confirm the substrate carries it via `RelationshipFact` (no new bucket should be required; if a new bucket *is* required, escalate — that's an `ExtractedFact` schema change).
3. Update extraction prompts in `src/memory_mission/extraction/prompts.py` to recognize the trigger phrases.
4. Add a contract test pinning the predicate name in `tests/test_provider_contract.py` so external consumers (Hermes, future agents) can rely on it.
5. If the predicate is stateful, add the `<predicate>_status` paired predicate to this doc and to extraction prompts.

Adding a new status value to an existing enum (e.g., extending `commitment_status`):

1. Update the `Literal[...]` definition at the source (`synthesis/individual_boot.py` for personal-plane enums).
2. Update this doc with the new value + when it applies.
3. Add a test that exercises the transition.
4. Avoid renaming existing enum values — old data will not migrate without a `re_extract_staged_item` pass.

---

## Notes on positioning

This vocabulary is what makes "operational memory for agents" different from "personal AI memory" (SuperMemory, Mem0, Honcho) and "second brain" (Tolaria, Obsidian, Rowboat). Storage is converging across the field; ontology is where the differentiation lives. Keeping this doc tight is more important than making it comprehensive — every predicate not pulled into use within 90 days of landing should be reviewed and either picked up by an extraction prompt or removed.
