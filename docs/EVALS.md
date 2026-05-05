# Evals for Seoul

Status: **planning doc, Phase 1.** No code yet. This is the eval strategy the project will execute as we move from step 13 → 18.

Audience: senior IC picking up the eval work. Assumes you've read the roadmap and know the components in `src/memory_mission/`.

**Stance.** Context construction is measurable engineering, not guesswork. Every primitive in Memory Mission that feeds an agent (extraction output, retrieval results, compiled agent context, coherence warnings, federated candidates) ships with binary-pass/fail criteria and a labeled fixture set. Generic "helpfulness" and 5-star scales don't appear anywhere in this document. When we say "the federated detector is accurate," we mean precision ≥ 0.90 on a named fixture set, not a vibe.

---

## 1. Universal principles

The principles below are distilled from the 11 sources listed at the bottom. They apply to every component in this repo. Wherever a local design decision contradicts one of these, we should have a written reason.

### P1. Error analysis before metrics, always
Manually review 20–50 real traces and cluster failures into a taxonomy before you write a single metric or judge prompt. Skipping this step is the most common mistake teams make and produces judges that score irrelevant things. [#2, #8, #10]

### P2. Binary pass/fail beats Likert and 5-star scales
Force each criterion into a yes/no question. "What's the difference between a 3 and a 4?" has no reliable answer across annotators or across runs, and Likert variance masks real regressions. Pair binary verdicts with a short freeform critique for debuggability. [#2, #6, #3]

### P3. An LLM judge is a classifier you have to validate
A judge is not a ground-truth oracle. Measure its agreement with your human labels (precision/recall and Cohen's κ, not raw accuracy) and iterate the judge prompt until it hits ≥ 90% agreement on a held-out labeled set. If you haven't validated the judge, you don't have an eval — you have a vibe. [#2, #10, #11]

### P4. Generic metrics ("helpfulness", "coherence", "toxicity") don't measure product quality
Off-the-shelf scores let you ship dashboards that go up while the product gets worse. Evals must be derived from observed failure modes in your product, not from a metrics library. [#7, #2]

### P5. Build the eval set before the prompt
Evaluation-driven development inverts the traditional order: define success criteria, write tasks, then build the system. For Seoul this means every new extractor, judge, or agent ships with its eval set in the same PR. [#5, #1]

### P6. 20–50 real-failure tasks is a complete starting eval set
Teams stall because they think they need hundreds. Anthropic's guidance (and everyone else's) is that 20–50 real tasks drawn from bug reports and traces already has large enough effect sizes to drive decisions. Scale later if signal is noisy. [#3]

### P7. Prefer deterministic graders; use LLM judges only for the rest
Code-based assertions (string match, schema validation, set membership, precision/recall on labeled data) are "fast, cheap, objective, reproducible." Reach for an LLM judge only for things that genuinely require language understanding. [#3]

### P8. Grade the output, not the path
For agents, evaluate what was produced, not which tools or reasoning steps were used. Path-based grading penalizes creative valid solutions and rewards overfitting to a canonical trajectory. [#3]

### P9. LLM judges have known biases — design around them
Position bias (models favor the first of two options), verbosity bias (models prefer longer responses >90% of the time), self-preference bias (a model rates its own output higher by 10–25%). Randomize order, cap output length in the judge prompt, use a different family for judging than for generating when possible. [#10, #11]

### P10. Pairwise comparison is more stable than absolute scoring
When a binary criterion isn't enough (e.g., "which of these two extracted fact sets is better"), pairwise A/B judgments have lower variance than direct 1–N scores. [#10]

### P11. The judge prompt gets treated like production code
Version it, test it against fixed labeled examples on every change, and alarm when meta-agreement drops. A silently-drifting judge silently corrupts every downstream decision. [#2, #10]

### P12. Three eval regimes: development, regression, production
Same eval assets, three different runners. Dev eval runs on a feature-subset for speed; regression runs the full set in CI before merge; production samples live traffic (random + stratified + signal-based) to detect drift. Don't conflate them. [#1]

---

## 2. Per-component eval plan

Each subsection: **failure modes → eval type → first eval recipe**. "First eval recipe" is specific enough to implement in an afternoon.

### 2.1 `extraction/` — LLM fact extraction

**Failure modes.**
- Hallucinated facts not present in source.
- Missed facts that are clearly present.
- Wrong entity resolution ("Sven" vs "Sven W." vs "svenozwellmann@").
- Confidence mis-calibration (high confidence on shaky inferences).
- Schema violations (wrong fact type, malformed payload).

**Eval type.** Hybrid: (a) schema validation as deterministic assertion; (b) labeled reference set with **precision** (of facts extracted, how many are correct?) and **recall** (of facts present, how many were extracted?); (c) binary LLM judge for "is this fact faithful to the source?" on edge cases only.

**First eval recipe.**
- **N = 30** staged inputs: 20 drawn from real transcripts/emails we've already ingested, 10 synthetic adversarial cases (ambiguous pronouns, near-duplicates, conflicting statements, null cases where nothing should be extracted).
- Human-labeled ground truth: the set of facts each input *should* produce, with entity IDs. Label once, review twice.
- Deterministic checks: JSON parses, fact type in allowed enum, required fields present. Failure = 0 for that case.
- Metrics: precision ≥ 0.85, recall ≥ 0.70, zero schema violations on the 30-case set to pass.
- Judge prompt (binary, faithfulness only): *"Given only the source text, is every claim in this fact directly supported? Answer YES or NO, then one sentence why."* Used only to spot-check; human labels are the authority. [#2, #4, #7]

### 2.2 `memory.search` — hybrid retrieval

**Failure modes.**
- Relevant pages not in top-K.
- Top-K full of topically-related but not-answering pages.
- BM25 and embedding halves disagreeing and producing worse results than either alone.
- Recency/recency-decay misfiring (stale page wins over fresh one).

**Eval type.** Classic IR: **retrieval precision and recall** on a labeled `(query, relevant_page_ids)` set. No LLM judge needed — this is the rare case where ground-truth labels are cheap. Aligns with #4's "Retrieval Precision & Recall" (pre-RAG tier).

**First eval recipe.**
- **N = 40** queries: 25 from real user prompts captured from skill invocations, 15 synthetic edge cases (ambiguous queries, queries with no correct answer, near-duplicate queries).
- For each query, human-label the minimal set of page IDs that are genuinely relevant.
- Metrics: `recall@10 ≥ 0.80`, `MRR ≥ 0.60`, zero crashes.
- Run on every change to retrieval weights, tokenizer, or embedding model. [#4]

### 2.3 `memory.knowledge_graph` corroboration (Bayesian)

**Failure modes.**
- Evidence aggregates in the wrong direction (contradicting observations raising posterior).
- Duplicate evidence double-counted (same promotion counted as two independent observations).
- Prior never budges (numerics stuck).
- Posterior crosses decision threshold on stale data.

**Eval type.** **Binary correctness on synthetic sequences.** This is math, not judgment — write scripted evidence streams with known correct posteriors and assert the output matches within tolerance.

**First eval recipe.**
- **N = ~50** scripted scenarios. Categories:
  - Single corroborating observation (posterior up, bounded).
  - Single contradicting observation (posterior down, bounded).
  - Sequence of N corroborations from *different* sources (strong update).
  - Sequence of N corroborations from the *same* source (weak update — dedupe check).
  - Contradictions mixed with corroborations (should oscillate toward the majority).
  - Boundary cases: prior at 0.01, 0.5, 0.99.
- Metric: `|predicted_posterior - expected_posterior| < 0.02` on every case. Pure deterministic, no judge. [#3, #7]

### 2.4 `promotion/` — proposal → review → merge pipeline

**Failure modes.**
- Merged proposal that contradicts existing canonical fact.
- Rejected proposal that was actually correct (false negative at the gate).
- Merge that succeeds but leaves the page store/KG in inconsistent state.
- Permissions bypass (proposal merged without required reviewers).

**Eval type.** Two-layer:
1. **Deterministic integration tests** for state invariants (page store + KG stay consistent; permissions enforced; idempotency holds). These are pytest, not evals.
2. **Binary correctness on labeled proposals:** given a proposal + current firm plane, should the merge gate say "merge", "request changes", or "reject"? Labeled by human; graded by a combination of deterministic rules (permissions) and an LLM judge (semantic contradiction detection).

**First eval recipe.**
- **N = 25** hand-crafted proposals covering: clean merge, contradicts existing fact, duplicate of existing fact, missing reviewer, malformed proposal, proposal that depends on another unmerged proposal.
- Ground truth: human-labeled expected decision per case.
- Judge prompt (binary): *"Does this proposal contradict any of these existing firm facts? YES/NO."*
- Meta-eval the judge: it must hit ≥ 90% agreement with human labels before it gates real merges. [#2, #11]

### 2.5 `identity/` — person resolution (being added)

**Failure modes.**
- Two people merged into one (Type I: over-merge, high cost, hard to reverse).
- One person split across two identities (Type II: under-merge, low cost, self-corrects).
- Identity confused across channels (Gmail `sven@foo.com` not linked to Granola speaker `Sven`).
- Identity flipped on ambiguous input (flaky resolution across runs).

**Eval type.** **Precision/recall on a labeled pairwise set.** Frame as: "given two identity records, are they the same person?" Binary. This is a classifier eval.

**First eval recipe.**
- **N = 100** pairs (balanced: 50 same-person, 50 different-person). Source from real ingested data across Drive/Gmail/Granola; hand-label. Include hard negatives (same first name, different people) and hard positives (nickname vs legal name, email alias vs canonical).
- Metrics: **precision ≥ 0.98 on "same person" prediction** (Type I errors are the expensive kind), `recall ≥ 0.85`. Track both — this is a precision-favored problem.
- Run on every change to the resolver. When it drops below threshold, do not ship. [#3, #10]

### 2.6 Federated pattern detector (Step 16 — evals before code)

**Failure modes.**
- Fires on N=3 employees extracting the same fact when it's actually the same source artefact reaching all three (no independence). **This is the dominant failure mode.**
- Fails to fire when three employees genuinely independently extract equivalent facts that are phrased slightly differently.
- Over-generalizes (aggregates facts that are about different domains into one firm proposal).

**Eval type.** **Precision/recall on a labeled event set**, with a strong emphasis on precision. Additionally: **independence-check eval** — the detector must correctly identify whether N observations came from the same root source.

**First eval recipe.** (Spec for when Step 16 lands.)
- **N = 50** scenarios. Each scenario is a set of personal-plane extraction events across ≥ 2 employees. Categories:
  - True firm pattern (≥ 3 independent sources, each from a different root artefact) — detector should fire.
  - Fake pattern: same Granola transcript shared with 3 people, each extracts the same fact — detector should NOT fire.
  - Near-duplicate phrasing of the same fact — detector should cluster them and fire.
  - Genuinely different facts that share a surface keyword — detector should NOT cluster.
- Metrics: **precision ≥ 0.90** (false positive = noisy firm proposal, corrodes trust fast), `recall ≥ 0.70`.
- Ship the eval set before the detector. Write the 50 scenarios as fixtures in the same PR that introduces the detector code. [#5]

### 2.7 Distilled-doctrine coherence (Step 15)

**Failure modes.**
- Distilled doctrine contradicts one of its source facts.
- Distilled doctrine drops a load-bearing nuance.
- Distilled doctrine invents constraints not present in any source.
- Two doctrines in the same tier mutually contradict.

**Eval type.** **Binary LLM judge** per (doctrine, source-fact) pair for faithfulness, plus a **pairwise contradiction judge** over the set of doctrines in a tier. Faithfulness is close to RAG groundedness (#4, eval #2).

**First eval recipe.**
- **N = 20** distilled doctrines drawn from early doctrine generation runs, each with its full source fact set (5–15 source facts each).
- For each (doctrine, source_fact): binary judge — *"Is the doctrine consistent with this source fact? YES/NO."* Flag all NOs for human review.
- For each pair of doctrines in the same tier: pairwise judge — *"Do these two doctrines make contradictory claims? YES/NO, and if YES, cite the contradicting spans."*
- Meta-eval: 30 human-labeled items; the judge must hit ≥ 90% agreement before it gates doctrine promotion.
- Pass bar for a doctrine to promote: zero unresolved NOs on faithfulness, zero unresolved contradictions within the tier. [#4, #2, #10]

### 2.8 `workflows/` end-to-end — meeting-prep agent (final step)

**Failure modes.**
- Misses a person who is attending the meeting but not yet resolved in `identity/`.
- Retrieves stale context (fact that's been superseded but not marked).
- Retrieves plenty of context but assembles a brief that misses the "what do I need to know for *this* meeting" angle.
- Cites sources that aren't load-bearing; omits sources that are.

**Eval type.** **End-to-end binary rubric per meeting**, not a scalar score. Multiple independent binary criteria — Anthropic's partial-credit / weighted pattern (#3). Human-labeled expected briefs as reference; LLM judge for each criterion with meta-eval against humans.

**First eval recipe.**
- **N = 15** real historical meetings from the user's calendar (with retrospective access to what actually mattered in each). The sample is small on purpose — this is the highest-value, highest-cost-to-label case, and #3 explicitly says 20–50 real tasks is enough.
- Binary criteria per meeting (all independent, all yes/no):
  1. Are all attendees correctly identified?
  2. Is the most recent relevant interaction with each attendee surfaced?
  3. Are any facts cited that have been superseded by newer facts? (Must be NO.)
  4. Is there at least one load-bearing fact per attendee-relationship?
  5. Does the brief omit any of the 3 user-labeled "must-have" items for this meeting?
- Pass bar: 4/5 criteria on ≥ 80% of meetings. Track per-criterion pass rate — regression on any single one is actionable.
- **Do not** use a single 1–10 "brief quality" score. It will drift and tell you nothing. [#3, #2, #6]

---

## 3. Anti-patterns to avoid

- **Don't ship 5-star or Likert scores from an LLM judge.** Variance is too high, "3 vs 4" has no stable meaning, and real regressions get buried in the noise. Collapse to binary. [#2, #6]
- **Don't use off-the-shelf "helpfulness" / "coherence" / "toxicity" judges.** They measure generic capabilities, not product quality, and let dashboards go up while the product gets worse. [#7]
- **Don't skip human labeling and trust the judge cold.** An unvalidated judge is a random number generator with a confidence interval. Meta-eval against human labels is mandatory, not aspirational. [#2, #10, #11]
- **Don't grade agent trajectories ("did it use the right tool?") when you care about outcomes ("was the brief correct?").** Path grading penalizes valid creativity. [#3]
- **Don't judge two options in a fixed order.** Position bias is real and large (Claude v1 favors position 1 ~70% of the time in the Yan survey). Randomize, or use two-pass with swap. [#10]
- **Don't let judges prefer longer answers.** Verbosity bias shows up at >90% in some studies. Cap output length in the judge prompt or normalize for it. [#10]
- **Don't use the same model family for generating and judging without mitigation.** Self-preference adds a 10–25% win-rate bump. [#10]
- **Don't wait for "hundreds of examples" before starting.** 20–50 real tasks is a complete starting eval set; delay-to-perfect is a known trap. [#3]
- **Don't treat evals as a one-time artifact.** Judge prompts drift, source distributions drift, product goals drift. Evals are versioned production code. [#2, #11]
- **Don't conflate dev / regression / production eval runs.** They have different speed/cost/coverage budgets. Same assets, three runners. [#1]

---

## 4. First eval to build

**Ship the `extraction/` eval first.**

Three reasons. (1) Extraction is the root of the pipeline — every downstream component (memory, KG, promotion, doctrine, workflows) degrades invisibly if extraction silently regresses, and we have no current floor on its quality. (2) It's the cheapest eval to label: we already have staged transcripts and emails sitting in the ingestion fixtures, and the correct-facts set for each is human-labelable in a few hours. (3) It gives us the infrastructure (eval harness, judge meta-eval, CI wiring, three-regime runners from P12) that every subsequent component's eval will reuse — so the marginal cost of eval #2 and #3 drops sharply once extraction's eval lands.

Recipe is section 2.1. Target: 30 labeled cases, precision ≥ 0.85, recall ≥ 0.70, zero schema violations, wired into CI as a regression gate before step 14 merges.

---

## 5. Capture + replay (BrainBench-Real-style)

Synthetic test corpora are easy to write but miss the queries agents actually make against the production palace. Memory Mission ships a capture-and-replay instrument modeled on GBrain's BrainBench-Real (v0.25.0) so we can detect retrieval-quality regressions on real-distribution queries instead of synthetic ones.

### What's captured

When `MM_CONTRIBUTOR_MODE=1` is set on an individual-mode MCP server, two read tools record their args + result signature + latency to a per-employee SQLite store at `<root>/personal/<user_id>/eval_captures.sqlite3`:

- `mm_boot_context(task_hint, token_budget)` — captured for status / list reporting; **replay deferred** because `task_hint` is redacted on capture (free-text PII).
- `mm_query_entity(name, direction, as_of)` — captured **and replayed faithfully**. All three args pass through PII scrubbing unchanged because they are the queries.

Capture writes are wrapped in `try/except` so they can never break the tool path. The DB file is not created when capture is disabled.

### Privacy posture

Captures live in the same per-employee fence as `personal_kg.db` — read access to one means read access to the other (consistent with how observability JSONL events are scoped today). Free-text user content (`task_hint`, `query`, `summary`, `description`) is redacted to `{_redacted: True, length: N, hash: <16-char sha256>}`. Entity names, IDs, statuses, and dates pass through because they are the queries themselves; replay needs them and they already exist in the production KG.

`MM_CONTRIBUTOR_MODE` is off by default. Operators opt in explicitly per session.

### Usage

```bash
# Start an MCP session with capture on:
MM_CONTRIBUTOR_MODE=1 python -m memory_mission.mcp.individual_server \
    --root ~/.memory-mission --user-id sven

# Inspect what's been captured:
python -m memory_mission eval status --root ~/.memory-mission --user-id sven
python -m memory_mission eval list   --root ~/.memory-mission --user-id sven --limit 20

# Replay captured mm_query_entity calls at HEAD and diff signatures:
python -m memory_mission eval replay --root ~/.memory-mission --user-id sven --limit 50

# Show which captures differ from stored signatures:
python -m memory_mission eval replay --root ~/.memory-mission --user-id sven --show-diffs
```

The `replay` command reports total / matches / differs / skipped, match rate when at least one capture replayed, mean latency delta when timings are present, and skip reasons grouped by cause (`tool_not_replayable:<name>`, `args_json_invalid`, `name_missing`, `direction_invalid`, `as_of_invalid`).

### What this answers

- **Did the substrate get better, worse, or stay the same after Keagan's last 5 commits?** Run `mm eval replay` before and after; compare match rate + latency delta.
- **Which `mm_*` tools is the agent actually using, at what frequency?** `mm eval status` per-tool counts.
- **What's the live distribution of `direction=` and `as_of=` across real queries?** Iterate `mm eval list` output.

### What this does NOT answer

- **Is the substrate semantically correct?** Match-rate detects regressions against a previous version; it does not say either version is "right." Combine with the per-component evals in section 2.
- **Should `mm_boot_context` results have changed?** Replay is deferred for boot context until we resolve the `task_hint` privacy story (store unscrubbed in contributor mode? replay with `task_hint=None`? hash-bucketed comparison?). Captures still happen for visibility into call patterns.

### When to actually use it

Defer running replay routinely until either (a) substrate-change cadence stays high enough that silent regressions become a real risk, or (b) Hermes reports a quality complaint we can't trace. Until then this is a safety net, not a daily tool.

---

## Sources

1. Paul Iusztin, "Integrating AI Evals Into Your AI App" (AI Evals & Observability series, Part 1), DecodingAI. https://www.decodingai.com/p/integrating-ai-evals-into-your-ai-app
2. Hamel Husain, "Using LLM-as-a-Judge for Evaluation: A Complete Guide." https://hamel.dev/blog/posts/llm-judge/
3. Mikaela Grace, Jeremy Hadfield, Rodrigo Olivares, Jiri De Jonghe, "Demystifying evals for AI agents," Anthropic Engineering, 2026-01-09. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
4. Jason Liu, "There Are Only 6 RAG Evals," 2025-05-19. https://jxnl.co/writing/2025/05/19/there-are-only-6-rag-evals/
5. "Escaping POC Purgatory: Evaluation-Driven Development for AI Systems," DecodingAI. https://www.decodingai.com/p/escaping-poc-purgatory-evaluation
6. "The 5-star Likert problem / binary-evals" piece, DecodingAI. URL not recoverable from search [unreachable — synthesized from secondary references #2 and #10, which make the same thesis explicit: binary pass/fail, not Likert].
7. Hamel Husain (guest post), "The Mirage of Generic AI Metrics," DecodingAI / Decoding ML, 2025-09-13. https://www.decodingai.com/p/the-mirage-of-generic-ai-metrics
8. Hamel Husain, "Evals: Doing Error Analysis Before Writing Tests" (companion write-up for the error-analysis talks; primary thesis of the YouTube videos is that error analysis is a manual data-inspection pass that precedes any metric or judge, and that teams should look at 20–50 traces and cluster failures into a taxonomy before writing a single eval). https://hamel.dev/notes/llm/officehours/erroranalysis.html
9. "Carrying out error analysis" — same Hamel Husain error-analysis talk as #8; treated as the companion video. [unreachable — canonical YouTube transcript not located; synthesized from #8 companion post.]
10. Eugene Yan, "Evaluating the Effectiveness of LLM-Evaluators (aka LLM-as-Judge)," 2024-08. https://eugeneyan.com/writing/llm-evaluators/
11. Doug Turnbull, "LLM Judges aren't the shortcut you think," 2025-11-02 — companion write-up for the YouTube talk of the same name; primary thesis is that LLM judges do not remove the need for human labels and that the 10–30% human-LLM disagreement gap is exactly where the hardest and most valuable cases live. https://softwaredoug.com/blog/2025/11/02/llm-judges-arent-the-shortcut-you-think
