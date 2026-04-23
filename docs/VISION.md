# Memory Mission — Vision

*A governed context engine for agents. Working synthesis, 2026-04-22. Update as the vision evolves.*

---

## Why this, why now

The last two years produced broad agent capability — coding agents, chat agents, voice agents — and a brutal gap on the data layer beneath them. Firms running knowledge work (VCs, wealth managers, law firms, consultancies, corporate strategy teams) now watch their agents repeat questions across meetings, contradict what the firm decided last quarter, and misattribute facts across people who share a first name. The bottleneck is not raw intelligence. It is **governed institutional memory** that agents can read, write, and build on without corrupting what the firm actually believes.

The personal-memory layer (Tolaria, Obsidian, second-brain vaults) is a solved-enough problem for individuals. The enterprise layer — shared truth, review gates, coherence across multiple knowledge workers — is not. Memory Mission is the **context engine for agents** at that layer: not a chat wrapper, not a vector-store retrofit, but a governed substrate that turns scattered firm knowledge into the trusted context an agent needs to be useful.

---

## The problem

Four specific failures agents keep producing at firms:

1. **Silent drift.** One employee's agent extracts a "fact" into shared memory. A week later a different employee's agent asserts the opposite. Neither agent knows the other was there. The firm's memory now says contradicting things and nobody is sure which is right.
2. **Fragmented identity.** Alice Smith, `alice@acme.com`, `alice.s@beta.vc` (after her job change), and `@alicevc` on Twitter are four nodes instead of one. Relationship history is split across fragments. Meeting-prep returns half of what the firm knows about her.
3. **Context collapse.** Every agent gets the full vault or nothing. Sales agents see strategy docs. Operations agents see client meeting notes. The more context each agent carries, the more the firm's thinking drags it in conflicting directions.
4. **No trail.** When an agent writes into firm memory, nobody can later ask *which source, which employee, which reviewer* landed that fact. Audit is theoretical.

Each of these is solvable. They are not solved together anywhere we've found.

---

## The insight

Two moves compose cleanly:

**Governed promotion between two planes.** Every employee gets a rich private memory — their own notes, their own extractions, their agent's working state. That plane stays private. The firm plane — the shared institutional truth — receives updates only through an explicit human-in-the-loop review gate. Pull-request semantics applied to knowledge. Emile's contribution.

**Constitutional doctrine on top of that.** Firm memory is not a flat bag of facts. It's tiered — constitution, doctrine, policy, decision — and lower tiers must remain coherent with higher ones. When a new fact contradicts existing doctrine, the system surfaces the conflict rather than silently accepting it. Maciek's contribution.

Both rest on a simple technical substrate: plain markdown files with YAML frontmatter for pages, append-only SQLite triples with validity windows for structured facts, per-firm isolation, full provenance on everything.

The moat is not memory capture. The moat is **trusted promotion from private working context into shared institutional memory**, with coherence enforcement and full provenance.

---

## The method

### Two planes

- **Personal plane** — per-employee, private. Four layers inside: `working/` (current task state), `episodic/` (observations, agent learnings), `semantic/` (curated pages), `preferences/` + `lessons/`. Looks like a personal Obsidian vault. The employee's agent operates freely here.
- **Firm plane** — shared, governed, write-only via `promote()`. Structured pages, same markdown-plus-YAML format, plus a knowledge graph of typed facts.
- **Staging** — proposals awaiting review. The only path between the two planes.

### Promotion flow

1. A connector pulls source material (email, meeting transcript, Drive memo) into staging for one plane.
2. An extraction skill (driven by the host agent's LLM) reads the source, produces structured facts with required support-quote provenance.
3. Facts become a `Proposal` grouped by target entity.
4. A reviewer surfaces proposals via the `review-proposals` skill, approves or rejects each with required rationale.
5. Approved facts land in the knowledge graph. Coherence warnings surface for tier conflicts; firms in constitutional mode block them.

Nothing writes to firm memory without that path.

### Federated learning loop

When three employees independently extract the same fact from three different source documents, that's high signal the fact belongs to the firm. The federated detector finds these patterns, stages proposals, and the normal review gate accepts or rejects. Independence is enforced: three people ingesting one shared Granola transcript is one source, not three — the detector filters that out.

### Identity as infrastructure

The same person reached via multiple channels (email, LinkedIn, Twitter, phone) resolves to one stable ID. Raw entity names from extraction canonicalize at ingest time. Meeting-prep, federated detection, and every downstream query speak in stable IDs.

### Bayesian corroboration

Re-extracting a currently-true fact from a new source bumps confidence via Noisy-OR (capped at 0.99) and appends the source to provenance history. It does not create a duplicate row. Accumulated agent evidence never reaches full certainty without explicit human override.

---

## Foundation

The method is only as good as the substrate it runs on. Memory Mission is built around one principle: **the firm's knowledge is governed, auditable, and owned — permanently, by the firm.**

- **Files-first.** Pages are plain markdown with YAML frontmatter. Portable, Obsidian-compatible, readable by any text editor in twenty years.
- **Per-firm isolation.** One firm instance = one directory tree + one SQLite knowledge graph. No cross-firm queries, no shared storage, no accidental leakage.
- **Provenance mandatory.** Every fact traces to source. Every promotion traces to reviewer and rationale. Every corroboration appends to a history table that never loses a source.
- **LLM lives with the host agent.** Memory Mission ships prompts, schemas, ingest validators, and skill markdown. It never imports an LLM SDK. Whoever has the API key runs the model.
- **Skills are markdown.** Workflows live as `skills/<name>/SKILL.md` with typed frontmatter and a destinations-and-fences body. The host-agent runtime reads and executes them.
- **Deterministic primitives, human judgment.** Every mechanical step (scan, corroborate, check coherence, detect federated patterns) is deterministic Python + SQL. Every judgment call (approve, reject, merge identities, change tier) routes through human review.

---

## Who it's for

**V1 target:** small-to-mid firms where a handful of knowledge workers share institutional thinking. Venture firms are the cleanest wedge — explicit investment thesis, typed entities (people, companies, deals, mandates), ritualized meeting cadence, real ROI on faster partner-level synthesis.

**Secondary:** wealth-management shops, boutique consultancies, law firms, corporate strategy teams. Anyone where "the firm believes X" is a load-bearing sentence and drift between what individual people think and what the firm officially believes costs real money.

**Not yet:** large enterprises where IAM integration dominates the conversation. Individual knowledge workers (Tolaria serves them). Open-internet products.

---

## Design principles

1. **Governed promotion is the moat** — not capture, not retrieval.
2. **Structure before trust** — raw memory is unverifiable; structured memory is reviewable.
3. **Two planes, one-way bridge** — personal stays private, firm receives only through the gate.
4. **Provenance on everything** — source traceability is mandatory, not optional.
5. **Deterministic detection, human judgment** — mechanical steps are testable; editorial acts need a human.
6. **Coherence under change** — lower tiers must not silently contradict higher tiers.
7. **Identity is infrastructure** — stable person IDs across channels, not a feature.
8. **LLM ownership sits with the host** — Memory Mission is a library + skills, not an agent platform.
9. **Files-first, Obsidian-compatible** — the exit door is always open.
10. **Composable skills, not monolithic workflow** — small markdown skills with typed frontmatter; host agent routes.
