# Memory Mission

**A governed context engine for agents: turn a firm's scattered knowledge into one queryable, auditable layer that AI agents can act on safely.**

Python infrastructure for pulling data from external sources (email, transcripts, calendars, Drive), distilling it into git-versioned markdown the firm owns, and surfacing it through a hybrid-search retrieval interface — with every extraction, promotion, and retrieval logged for compliance audit.

Two planes (personal and firm) separated by a PR-model review gate. Every fact traces to a source, a reviewer, and a rationale. Nothing lands on firm-plane memory without an explicit human decision.

## Where to start

| For | Read |
|---|---|
| The thesis and who it's for | [`docs/VISION.md`](docs/VISION.md) |
| The shipped architecture + module walkthrough | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Every Pydantic model, predicate, tier in one place | [`docs/ABSTRACTIONS.md`](docs/ABSTRACTIONS.md) |
| How we measure whether the system is right | [`docs/EVALS.md`](docs/EVALS.md) |
| Load-bearing decisions with rationale | [`docs/adr/`](docs/adr/) |
| Operator recipes (hot-cache hooks, Bases dashboard) | [`docs/recipes/`](docs/recipes/) |
| How agents should navigate this repo | [`docs/AGENTS.md`](docs/AGENTS.md) |
| Per-step chronology | [`BUILD_LOG.md`](BUILD_LOG.md) |

## Quickstart

```bash
git clone https://github.com/SvenWell/memory-mission.git
cd memory-mission

python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

make check          # ruff + format + mypy --strict + 643 tests
python -m memory_mission info
```

A working slice you can run today (in-memory, no external services):

```python
from pathlib import Path
from memory_mission.memory import HashEmbedder, InMemoryEngine, new_page
from memory_mission.observability import observability_scope

with observability_scope(observability_root=Path("./.observability"),
                         firm_id="acme"):
    engine = InMemoryEngine(embedder=HashEmbedder())
    engine.put_page(
        new_page(
            slug="sarah-chen", title="Sarah Chen", domain="people",
            compiled_truth="CEO of [[acme-corp]]. Direct, numbers-heavy.",
        ),
        plane="firm",
    )
    hits = engine.query("CEO of acme")
    # Each query writes a RetrievalEvent to .observability/<firm>/events.jsonl
```

End-to-end with the full stack (connectors → extract → review → promote → meeting-prep):

```python
from memory_mission.synthesis import compile_agent_context

# Assuming a populated KG + identity resolver (see ARCHITECTURE.md for setup)
context = compile_agent_context(
    role="meeting-prep",
    task="Prep the Q3 review with Acme Corp",
    attendees=["p_alice_abc123"],
    kg=kg,
    engine=engine,
    identity_resolver=resolver,
    plane="firm",
    tier_floor="policy",          # only authoritative doctrine + policy
)
briefing = context.render()       # markdown for the host-agent LLM
```

## What shipped

V1 complete. 17 build steps plus a six-move polish pass. **643 tests passing**, `mypy --strict` clean on 66 source files.

| Layer | What you can do today |
|---|---|
| **Foundations** | Append-only observability log per firm; checkpointed durable execution with resume-on-crash; PII-redacted middleware around every LLM call |
| **Connectors** | `Connector` Protocol + `invoke()` harness. Composio-backed Gmail / Granola / Drive adapters (SDK is a stub; host wires the client) |
| **Memory** | Compiled-truth + timeline page format (Obsidian-compatible); SQLite temporal knowledge graph with Bayesian corroboration (Noisy-OR, 0.99 cap); hybrid search (RRF + cosine + compiled-truth boost); tier-aware authority hierarchy; read-only SQL surface for ad-hoc queries |
| **Identity** | `IdentityResolver` Protocol + SQLite-backed local resolver; stable `p_<id>` / `o_<id>` across email / LinkedIn / Twitter / phone; `merge_entities` with reviewer gate |
| **Extraction** | Six-bucket `ExtractedFact` Pydantic union with mandatory support-quote; `EXTRACTION_PROMPT` markdown template; ingest-time canonicalization to stable IDs; zero LLM-SDK imports in-repo |
| **Permissions** | Per-firm `Policy` with scopes + employees; `can_read` + `can_propose` with no-escalation; read-path enforcement inside `BrainEngine` |
| **Promotion** | `Proposal` + `ProposalStore`; `create_proposal` / `promote` / `reject` / `reopen` with required rationale; coherence check emits structured warnings; opt-in constitutional mode blocks on contradictions |
| **Federated** | Cross-employee pattern detector with distinct-source-file independence check; stages firm-plane proposals from N≥3 employees' personal planes |
| **Synthesis** | `compile_agent_context(role, task, attendees, ...)` returns a structured `AgentContext` package; Tolaria Neighborhood-mode shape; Obsidian `[!contradiction]` callouts on rendered pages |
| **Skills** | Seven shipped: `backfill-gmail`, `backfill-granola`, `backfill-firm-artefacts`, `extract-from-staging`, `review-proposals`, `detect-firm-candidates`, `meeting-prep` |

Full per-step chronology in [`BUILD_LOG.md`](BUILD_LOG.md).

## How it composes

Each layer reads from the one below without rewriting it.

1. A **connector** pulls from Gmail / Granola / Drive through the `invoke()` harness — PII-scrubbed, durable-checkpointed, logged as a `ConnectorInvocationEvent`.
2. **Staging** lands the payload as `staging/<plane>/<source>/<id>.md` plus a raw sidecar. Hand-reviewable, plane-scoped, filesystem-level.
3. The **`extract-from-staging` skill** runs the host agent's LLM against `EXTRACTION_PROMPT`. Response JSON parses into `ExtractionReport`; `ingest_facts` canonicalizes entity names via the `IdentityResolver` and writes structured facts to fact-staging.
4. **`create_proposal`** groups facts by entity into a `Proposal`. Deterministic `proposal_id` — idempotent under re-extraction.
5. The **`review-proposals` skill** surfaces each proposal to a human reviewer. Coherence warnings on tier conflicts surface as forcing questions. Approve / reject / reopen always requires rationale.
6. **`promote()`** corroborates an existing currently-true triple (Bayesian update) or adds a new one. Every source appends to `triple_sources`. Firm-plane writes are the only way facts leave staging.
7. The **`detect-firm-candidates` skill** (admin) scans personal planes for cross-employee patterns; distinct-source-file threshold defeats the "three people sharing one Granola transcript" failure mode. Candidates route through the same proposal pipeline.
8. The **`meeting-prep` skill** calls `compile_agent_context` — distilled doctrine + per-attendee neighborhoods with inline provenance + coherence callouts. The host-agent LLM drafts the briefing; Memory Mission doesn't own the generation.

Every link in the chain is auditable. Every write is reviewed. No link is silent.

## Open the vault in Obsidian

The on-disk format is vault-native. Point Obsidian at `<firm_root>/` and the whole governed memory appears as a working vault — graph view, linked mentions, search, tag panel, all of it.

The Bases dashboard (Obsidian ≥ v1.9.10) gives partners a native database view:

```bash
cp src/memory_mission/memory/templates/dashboard.base <firm_root>/firm/dashboard.base
```

Five views out of the box: Recent changes, Low confidence, Stale or unreviewed, Constitution + doctrine, By domain. Install notes in [`docs/recipes/vault-dashboard.md`](docs/recipes/vault-dashboard.md).

For employees running a host-agent session against their personal plane, the hot-cache hook recipe makes session memory persistent across restarts: [`docs/recipes/personal-hot-cache.md`](docs/recipes/personal-hot-cache.md).

## Repo layout

```
src/memory_mission/
├── observability/          # append-only JSONL audit, per-firm scoped
├── durable/                # checkpointed runs, resume-on-crash
├── middleware/             # LLM-call chain + PII redaction
├── connectors/             # Composio harness + Gmail/Granola/Drive factories
├── memory/
│   ├── pages.py            # compiled-truth + timeline format
│   ├── schema.py           # MECE domains + plane paths
│   ├── engine.py           # BrainEngine Protocol + InMemoryEngine
│   ├── knowledge_graph.py  # SQLite temporal KG + corroborate + coherence
│   ├── tiers.py            # constitution / doctrine / policy / decision
│   ├── search.py           # RRF + cosine + compiled-truth boost
│   └── templates/          # dashboard.base
├── ingestion/              # StagingWriter + MentionTracker
├── personal_brain/         # working / episodic / semantic / preferences / lessons
├── extraction/             # 6-bucket ExtractedFact + ingest_facts
├── identity/               # IdentityResolver Protocol + LocalIdentityResolver
├── permissions/            # Policy + can_read / can_propose
├── promotion/              # Proposal + PR-model review gate
├── federated/              # cross-employee pattern detector
└── synthesis/              # compile_agent_context + AgentContext

skills/                     # 7 shipped, markdown + YAML frontmatter
tests/                      # 643 passing
docs/                       # VISION + ARCHITECTURE + ABSTRACTIONS + EVALS + AGENTS + adr/ + recipes/
BUILD_LOG.md                # per-step record
```

## Operational notes

Configuration is environment-driven via `MM_*` vars (see [`src/memory_mission/config.py`](src/memory_mission/config.py)):

| Variable | Default | Purpose |
|---|---|---|
| `MM_WIKI_ROOT` | `./wiki` | Root for firm content (curated pages + staging) |
| `MM_OBSERVABILITY_ROOT` | `./.observability` | Append-only audit log root |
| `MM_DATABASE_URL` | (empty) | Postgres URL when we move off in-memory |
| `MM_LLM_PROVIDER` | `anthropic` | `anthropic` / `openai` / `gemini` (the host agent uses this; we don't import the SDK) |
| `MM_LLM_MODEL` | `claude-sonnet-4-6` | Default model identifier |

Per-firm isolation is filesystem-based today: each firm gets its own subdirectory + its own SQLite files (KG, identity, proposals, mentions, durable). Multi-tenant RLS lands post-V1 when firm count justifies it.

Day-to-day:

```bash
make check           # full pre-commit check
make lint-fix        # auto-apply ruff fixes
pytest -k <pattern>  # run a subset
```

## Post-V1 roadmap

Deferred items (see `project_post_v1_roadmap.md` in memory or the plan file at `virtual-petting-tarjan.md`):

- **Step 18:** Legislative amendment cycle (batched promotions triggered by evidence pressure).
- **Step 19:** Constitution bootstrap skill (cold-start firm truth from existing strategy docs).
- **Step 20:** Relationship-strength view + Graph One adapter (needs real interaction volume).
- 50-scenario federated eval harness (per EVALS § 2.6).
- Distillation coherence eval (per EVALS § 2.7).
- MCP server surface exposing KG ops to any MCP-compatible host agent.
- `CoherenceResolvedEvent` so the contradiction callout hides acknowledged conflicts.
- `/save` (conversation → personal-plane note) + `/autoresearch` (WebSearch + WebFetch loop) as optional skills.

## License

Proprietary — see [`pyproject.toml`](pyproject.toml).
