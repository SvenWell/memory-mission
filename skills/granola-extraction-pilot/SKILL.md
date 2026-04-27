---
name: granola-extraction-pilot
version: "2026-04-27"
triggers: ["granola pilot", "narrow-slice extraction", "wealth-ai backfill", "extract granola pilot", "granola extraction pilot"]
tools: [staging_reader, llm, ingest_facts, knowledge_graph, brain_engine, observability_scope, ask_user_question]
preconditions:
  - "firm_id resolved; running session is the user (or an admin acting on the user's behalf)"
  - "backfill-granola has already pulled the source corpus into staging — items live under staging/personal/<emp>/granola/"
  - "the user has selected a NARROW slice (e.g. wealth-ai-tagged transcripts, a specific date range, or a small set of meeting IDs) — pilot extraction MUST NOT run on the full corpus"
  - "the host agent (Hermes / Codex / etc.) has an LLM available for extraction; provenance is mandatory on every emitted fact"
constraints:
  - "INVARIANT: this is a 3-layer narrow-slice pilot. Evidence stays in staging (lossless); extraction emits a dry-run JSONL of candidate facts the operator reviews; only HIGH-SIGNAL facts get promoted to the firm/personal KG via review-proposals. NEVER auto-promote."
  - "narrow-slice ONLY — running this skill against the full Granola corpus pollutes the KG with low-confidence transcript noise. The skill enforces this by requiring an explicit slice criterion (filter tag, date range, or explicit meeting IDs)"
  - "every extracted fact MUST carry provenance: source_closet=granola, source_file=<meeting_path>, source_quote=<excerpt> — no quote, no fact"
  - "dry-run JSONL output at staging/personal/<emp>/granola/.dry_run/<run_id>.jsonl BEFORE any KG write — operator inspects + approves the slice before promotion"
  - "low-confidence facts (< 0.6) get DROPPED, not promoted as open_questions — pilot is for high-signal patterns only; refine the prompt before re-running"
  - "do NOT auto-merge entities surfaced by extraction — entity merges go through identity_resolver.merge() or the federated detector, which require explicit operator decisions"
category: workflow
---

# granola-extraction-pilot — narrow-slice 3-layer transcript import

## What this does

Runs Hermes' proposed 3-layer Granola backfill pipeline against a
**narrow slice** of staged transcripts (not the full corpus). Lets
the operator preview structured facts as a dry-run JSONL before any
KG write, then promotes only high-signal patterns through the
existing review gate.

The architectural framing (Hermes 2026-04-27): "Memory Mission
should store current truth and useful structured history while
Granola remains the dated evidence base." This skill is the
controlled bridge.

## The 3 layers

1. **Evidence layer** — every Granola transcript stays in
   `staging/personal/<emp>/granola/` after `backfill-granola`. Each
   meeting keeps its full text, ID, date, title, participants, path.
   Raw memory; never deleted by this pilot.
2. **Extraction layer** — host LLM reads the narrow slice and
   proposes structured facts (people, companies, projects, pain
   points, decisions, commitments, "X believes Y," "Wealthpoint
   needs Z"). Output is a dry-run JSONL — NO KG writes yet.
3. **Curated layer** — operator inspects the JSONL, marks
   high-signal facts, runs `review-proposals` on the marked subset.
   Promotion writes to the KG with full provenance pointing back at
   the meeting/date/path.

## Workflow

1. **Resolve the slice.** Operator names a narrow filter — examples:
   - Tag-based: `--filter-tag wealth-ai` (the ~17 wealth-ai-tagged
     meetings in the 2026-Apr corpus).
   - Entity-based: `--filter-entity Wealthpoint` (mentions of one
     entity).
   - Date-based: `--from 2026-04-01 --to 2026-04-15`.
   - Explicit IDs: `--meeting-ids vineyard-2026-04-17,...`.
   The skill REFUSES to run without a filter — full-corpus extraction
   is an anti-pattern at V1. If the operator insists, surface as a
   forcing question: "you'd be running ~104 transcripts through
   extraction; that's noisy and slow. Confirm or narrow?"

2. **Open observability scope.** Same firm/employee scope used for
   `backfill-granola`. Each extraction is a `RetrievalEvent` +
   eventual `IngestEvent` per the existing observability contract.

3. **Compose extraction prompt.** Reuse `EXTRACTION_PROMPT` from
   `extraction/prompts.py`. Layer overlay-specific addenda when the
   slice has a vertical (e.g. `overlays/venture/prompt_examples.md`
   for venture-deal extraction, the wealth-ai overlay later when it
   exists). The host LLM extracts typed facts per the 6-bucket
   schema (RelationshipFact / EventFact / UpdateFact / AttributeFact
   / OpinionFact / next-step).

4. **Dry-run write to JSONL.** Output:
   `staging/personal/<emp>/granola/.dry_run/<run_id>.jsonl`. One JSON
   object per candidate fact. Schema: `{meeting_id, meeting_date,
   meeting_path, fact_type, subject, predicate, object, confidence,
   quote, suggested_target_plane}`. The operator can grep, sort,
   filter, hand-edit before promotion.

5. **Surface counts to operator.** "Slice: 17 wealth-ai meetings.
   Extracted: 142 candidate facts (43 RelationshipFact, 38
   EventFact, 27 UpdateFact, ...). High-confidence (>= 0.8): 89.
   Low-confidence (< 0.6): 22 — DROPPED. Review JSONL at
   `<path>`. Approve full / approve subset / refine prompt / abort?"

6. **Promote approved subset.** Operator marks approved facts (e.g.
   by editing the JSONL down). Skill calls `ingest_facts` on the
   approved subset → `staging/<emp>/.fact_staging/...` → operator
   then runs `review-proposals` to do the actual KG promotion.

7. **Log a `DraftEvent`.** Captures slice criterion, count of
   candidate facts, count promoted, time spent. Self-rewrite hook
   below uses this to track precision over time.

## Why narrow-slice (not full corpus)

Hermes' 2026-04-27 corpus stats: 126 Granola files, 104 with
transcripts. Running extraction on all 104 produces hundreds of
low-confidence facts — exactly the "transcript noise pollutes the
KG" anti-pattern the architecture rejects. Narrow slices give:

- **Fast feedback loop.** Run + review in minutes, not hours.
- **Better prompt tuning.** A tight slice surfaces the kinds of
  facts that matter; the prompt can be refined per slice (venture
  meetings vs wealth-ai meetings vs internal builder meetings have
  different structured facts).
- **Trust building.** Operator inspects ~140 facts vs 1400+; the
  review pass is meaningful, not rubber-stamped.
- **Safety.** A bad prompt against 17 meetings is recoverable; a bad
  prompt against 104 is a state cleanup project.

## What this skill does NOT do

- **Auto-promote.** Promotion runs through `review-proposals` after
  the operator inspects the dry-run JSONL.
- **Run full-corpus extraction.** Refuses without a filter; surfaces
  a forcing question if the operator tries.
- **Write to MemPalace.** MemPalace stays disposable + canonical.
  Source transcripts are already indexed there (post `reindex`
  cycle); this skill only writes operating-truth facts to the KG.
- **Auto-merge entities.** Surfaced entity duplicates (e.g.
  "Keagan" vs "Keagan Lloyd") become operator decisions via
  `identity_resolver.merge()` — not silent merges from extraction.
- **Skip provenance.** Every emitted fact carries
  `source_quote` from the transcript. No quote → fact dropped before
  reaching the JSONL.

## Forcing questions

- **Empty slice:** "Filter `--filter-tag wealth-ai` matched zero
  staged meetings. Did backfill-granola run with the right tags?
  Run that first."
- **Full corpus attempted:** "You'd extract from ~104 transcripts
  (~12K candidate facts at average density). The pilot pattern is
  narrow-slice. Confirm full corpus, or narrow to a tag / date /
  entity?"
- **High low-confidence rate:** "55 of 142 candidate facts scored
  < 0.6 (dropped). The prompt is producing noise. Refine the prompt
  (which categories are over-firing?) or proceed with the high-
  confidence 89?"
- **Entity duplicate suspected:** "Extraction surfaced 'Keagan' and
  'Keagan Lloyd' as separate subjects in 6 facts. Defer to
  `merge_entities` decision before promoting?"

## Where state changes

- **Always:** dry-run JSONL at
  `staging/personal/<emp>/granola/.dry_run/<run_id>.jsonl`. No KG
  writes here.
- **Conditional on operator approval:** fact-staging entries via
  `ingest_facts`, then promotion via `review-proposals`. Both go
  through the existing audit + provenance contracts.
- **Never:** auto-promotion, MemPalace writes, identity merges.

## Self-rewrite hook

After every 5 pilot runs OR on any operator-flagged failure:

1. Read the last 5 `DraftEvent` rows. Compute high-confidence /
   total ratio per slice. If < 50% across 3+ slices, the extraction
   prompt is undertuned for the corpus; flag in `KNOWLEDGE.md`.
2. Track which fact_types most often get dropped at review time.
   If `OpinionFact` is consistently dropped, add to the prompt:
   "skip subjective interpretations; stick to attested facts."
3. If operator flags a slice as "wrong corpus" (filter matched the
   wrong meetings), surface that the filter logic needs sharpening
   — possibly add tag-derivation rules in `backfill-granola`.
4. Commit: `skill-update: granola-extraction-pilot, <one-line reason>`.

## Related

- `skills/backfill-granola/SKILL.md` — pulls Granola → staging.
  Prerequisite for this skill.
- `skills/extract-from-staging/SKILL.md` — generic extraction. This
  skill is a narrow-slice composition over it.
- `skills/review-proposals/SKILL.md` — the promotion gate. Operator
  runs it after marking approved facts.
- `src/memory_mission/extraction/prompts.py` — `EXTRACTION_PROMPT`
  template the host LLM consumes.
- `overlays/venture/prompt_examples.md` — vertical-specific extraction
  vocabulary; future `overlays/wealth-ai/prompt_examples.md` will
  cover the wealth-ai slice when authored.
- `project_three_layer_architecture_validated.md` (memory) — the
  framing this skill operationalizes: KG = current truth, MemPalace
  = evidence, this skill = the controlled bridge.
