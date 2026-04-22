---
name: detect-firm-candidates
version: "2026-04-22"
triggers: ["detect firm candidates", "find cross-employee patterns", "federated detection", "what do employees agree on", "scan for firm truth"]
tools: [knowledge_graph, proposal_store, federated_detector, observability_scope]
preconditions:
  - "firm_id resolved; running session belongs to a firm administrator"
  - "KnowledgeGraph for this firm contains at least two employees' personal-plane triples"
  - "ProposalStore available for this firm"
  - "identity resolution (Step 14) has canonicalized entity names so the same person across employee extractions collapses to one stable ID"
constraints:
  - "administrator-run only — this skill reads across every employee's personal-plane provenance"
  - "no direct KG writes from this skill; firm-plane changes go through create_proposal + review-proposals"
  - "do not bypass the independence check — N employees sharing one source_file is NOT a firm candidate"
  - "do not auto-promote candidates; every generated proposal waits for human review"
  - "stop and surface errors on detector or proposer failure — do not cascade"
category: governance
---

# detect-firm-candidates — cross-employee pattern → proposals

## What this does

Scans the firm's `KnowledgeGraph` for personal-plane triples that
appear across multiple employees via multiple distinct source
documents. Each qualifying pattern becomes a `FirmCandidate`, which
this skill turns into a pending `Proposal` targeting the firm plane.
The `review-proposals` skill picks them up like any other proposal —
humans approve, reject, or reopen with rationale.

This is the federated-learning loop made concrete. Three employees
independently extracting "Sarah Chen is CEO of Acme" from three
different meeting transcripts is a high-signal indicator that the
firm should carry that fact. Three employees ingesting the same
team-wide transcript is not — the independence check filters that out.

## Workflow

Open an observability scope for the firm + reviewer identity (the
administrator running this skill). Open the `ProposalStore` and the
firm's `KnowledgeGraph`. Load the firm's permissions `Policy` if
you want coherence blocking at review time.

Loop:

1. **Detect candidates.** Call
   `detect_firm_candidates(kg, min_employees=3, min_sources=3)`. The
   default thresholds come from `docs/EVALS.md` section 2.6. Lower
   them only with a deliberate reason and record why in the audit
   log before proceeding.
2. **Short-circuit empty.** If no candidates returned, log a
   one-line note and stop. Noise-free is the expected steady-state.
3. **Rank already handled.** The detector sorts candidates by
   distinct_employees desc, then confidence desc, then subject asc.
   First in the list is the highest-signal pattern.
4. **Per candidate, stage a proposal.** Call
   `propose_firm_candidate(candidate, store=store)`. This uses the
   normal `create_proposal` path, so:
   - `proposal_id` is deterministic: re-running this skill is a
     no-op on previously-staged candidates (no duplicate queue
     entries).
   - A `ProposalCreatedEvent` lands in the audit log.
   - The `source_report_path` is `federated-detector://...` so
     reviewers see its origin.
5. **Surface a summary.** Tell the administrator how many
   candidates were found, how many were newly staged, and how many
   already had pending proposals. Do not auto-invoke
   `review-proposals`; the administrator decides when to review.
6. **Done.** Proposal review is a separate responsibility.

## Forcing questions (never guess)

- **Threshold change:** "Defaults are min_employees=3,
  min_sources=3. You asked to lower to <N>/<M>. Confirm and
  record the reason in the audit note?"
- **Identity confidence:** "Candidate for `alice-smith works_at
  acme`. But `alice-smith` is not yet a stable `p_<id>`. Do you
  want to canonicalize via `merge_entities` first so cross-employee
  observations on `alice-smith`, `a-smith`, etc. collapse?"
- **Already-firm fact:** "Candidate for `sarah works_at acme`
  already exists at `doctrine` tier on the firm plane. Stage a
  reinforcing proposal (adds firm-source corroboration) or skip?"
- **High rejection churn:** "Candidate was previously proposed N
  times and rejected each time. New evidence, or same call?"

Surface these as `QUESTION:` lines. The host agent's question
mechanism presents them.

## Where state changes

- `ProposalStore` gains one pending proposal per newly-seen
  candidate (`proposal_id` is deterministic, so re-runs are safe).
- Observability log gains one `ProposalCreatedEvent` per new
  proposal.
- `KnowledgeGraph` is NOT written by this skill — only by
  subsequent `promote()` calls from `review-proposals`.

## What this skill does NOT do

- No direct firm-plane writes. Every change flows through the
  review gate.
- No auto-promotion. Surfacing a candidate is not approval.
- No LLM. The detector is a deterministic SQL scan; the
  proposer is pure Python. Anything requiring judgment waits for a
  human reviewer.
- No cross-firm scanning. Per-firm isolation is enforced by the
  KnowledgeGraph path and ProposalStore path the caller passes in.
- No personal-plane writes. This is strictly firm-plane proposal
  generation.

## On crash / resume

Idempotent by construction. Re-running the skill after a crash
re-detects the same candidates and tries to stage proposals for
each; `create_proposal` returns the existing pending proposal
instead of duplicating. State across sessions is safe.

## Self-rewrite hook

After every 5 runs OR on any failure:

1. Read the last 5 `ProposalCreatedEvent` rows with
   `source_report_path` matching `federated-detector://`. Check
   how many ended in approved vs rejected vs pending. If the
   precision (approved / total) is below 0.80, the thresholds are
   probably too loose; consider raising.
2. If a pattern of rejections shows the same failure mode
   (e.g., "same source shared across team"), flag it — the
   independence check may need tightening or a domain-specific
   rule.
3. If the detector or proposer raised, escalate as a project
   memory with the stack trace and the input that triggered it.
4. Commit: `skill-update: detect-firm-candidates, <one-line reason>`.
