# Memory Mission

**A governed context engine for agents: turn a firm's scattered knowledge into one queryable, auditable layer that AI agents can act on safely.**

Python infrastructure for pulling data from external sources (email, transcripts, calendars), distilling it into git-versioned markdown the firm owns, and surfacing it through a hybrid-search retrieval interface — with every extraction, promotion, and retrieval logged for compliance audit.

The first deployment is a wealth-management firm. The system itself is vertical-neutral: domain-specific taxonomies and policies plug in via config; nothing in the core is wealth-specific.

## Quickstart

```bash
git clone https://github.com/SvenWell/memory-mission.git
cd memory-mission

python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

make check          # ruff + format + mypy strict + 269 tests
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
    engine.put_page(new_page(
        slug="sarah-chen", title="Sarah Chen", domain="people",
        compiled_truth="CEO of [[acme-corp]]. Direct, numbers-heavy.",
    ))
    hits = engine.query("CEO of acme")
    # Each query writes a RetrievalEvent to .observability/<firm>/events.jsonl
```

## What this is

A working set of foundational components, an in-memory reference implementation, and a clear shape for the agents that compose them. Concretely, what's shipping today:

- **Foundations.** Append-only observability log scoped per firm; checkpointed durable execution that resumes mid-loop on crash; LLM-call middleware with PII redaction tuned to wealth/compliance regex defaults plus pluggable extras.
- **Memory layer.** Compiled-truth + timeline page format with `[[wikilinks]]` and validity windows; MECE directory schema (vertical-neutral GBrain base); `BrainEngine` Protocol + dict-backed `InMemoryEngine`; SQLite temporal knowledge graph ported from MemPalace; hybrid search with RRF fusion, compiled-truth boost, and 70/30 cosine blend.
- **Ingestion primitives.** `Connector` Protocol + `invoke()` harness that threads observability + PII through every external call; `ComposioConnector` adapter (stubbed); Granola + Gmail factories; `StagingWriter` for the human-review zone; `MentionTracker` for tier-based enrichment escalation.

Built as five layers on top of eight open-source references (GBrain, MemPalace, Mem0, Rowboat, Honcho, Supermemory, LLM-Wiki, Composio). Most components borrow shape; the harness wiring (observability + middleware + durable threading through every call) is the net-new engineering.

## What's shipped (Steps 1–7a)

| Step | Component | What landed |
|---|---|---|
| 1 | Project scaffolding | Python package, `make check`, 5 smoke tests |
| 2 | Observability (0.4) | Append-only JSONL audit log, per-firm isolation, path-traversal hardened |
| 3 | Durable execution (0.6) | Checkpointed `DurableRun`, resume-on-crash, terminal-state respected |
| 4 | Middleware + PII (0.7) | `MiddlewareChain`, `PIIRedactionMiddleware`, frozen `ModelCall` / `ModelResponse` |
| 5 | Connectors (1.3) | Protocol + `invoke()` harness; Composio + Granola + Gmail factories (stubs) |
| 6a | Memory: pages + schema + engine | Compiled-truth pages, MECE schema, `BrainEngine` + `InMemoryEngine` |
| 6b | Memory: knowledge graph | SQLite temporal KG, validity windows, time-travel queries |
| 6c | Memory: hybrid search | RRF + cosine blend + `COMPILED_TRUTH_BOOST=2.0`, `HashEmbedder` stub |
| 7a | Ingestion primitives | `StagingWriter`, `MentionTracker` (GBrain enrichment tiers) |

**269 tests passing** across the suite. `mypy --strict` clean on 40 source files.

## What's stubbed or open

- **Live connector wiring.** `ComposioConnector` adapter shape is in; the actual SDK calls raise `NotImplementedError` until a `ComposioClient` is injected.
- **Real embeddings.** `EmbeddingProvider` Protocol is in; only `HashEmbedder` (deterministic, non-semantic, for tests) is implemented. OpenAI / Gemini adapters land when a real flow needs them.
- **Filesystem-backed engine.** Source-of-truth markdown-on-disk pattern is documented; the engine is in-memory only today.
- **Backfill workflow.** Primitives shipped (Step 7a); the workflow itself lands in Step 7b as a Hermes skill in markdown, not Python code.
- **Phases 2-4.** Workflow agents (meeting prep, email draft, CRM update), promotion pipeline, multi-tenancy hardening, runtime adapter.

See `BUILD_LOG.md` for the per-step record and what's next.

## How it compounds

Each component composes into the next without rewriting the layer below.

1. A `Connector` pulls from an external source through `invoke()` — the harness writes a `ConnectorInvocationEvent` with PII-scrubbed preview.
2. A `DurableRun` wraps the loop so each item is a checkpointed step. Crash mid-run; restart picks up where it stopped.
3. `StagingWriter` lands the raw payload + a frontmatter-headed markdown file under `<wiki_root>/staging/<source>/`.
4. `MentionTracker` increments per-entity counts; threshold crossings (`stub` → `enrich` → `full`) trigger enrichment.
5. The extraction agent (next phase) reads from staging, calls an LLM through `MiddlewareChain` (PII-redacted in/out), writes structured triples to the `KnowledgeGraph` and a curated `Page` to the `BrainEngine`.
6. Workflow agents (meeting prep, email draft) call `engine.query()` — RRF + cosine blend + truth boost surface the relevant pages; every retrieval logs a `RetrievalEvent`.
7. `git log` over the wiki root becomes the firm's institutional memory; `events.jsonl` is the audit trail.

Verifiability + traceability are not retrofitted. They ship in Phase 1 because every later component writes through them.

## Repo layout

```
src/memory_mission/
├── __init__.py / __main__.py / cli.py / config.py
├── observability/                # 0.4 — append-only audit, per-firm scoped
├── durable/                      # 0.6 — checkpointed runs, resume-on-crash
├── middleware/                   # 0.7 — LLM-call chain + PII redaction
├── memory/                       # 0.1 + 0.2
│   ├── pages.py                  # compiled truth + timeline format
│   ├── schema.py                 # MECE directories (vertical-neutral)
│   ├── engine.py                 # BrainEngine Protocol + InMemoryEngine
│   ├── knowledge_graph.py        # SQLite temporal triples (MemPalace port)
│   └── search.py                 # RRF + cosine + COMPILED_TRUTH_BOOST
├── ingestion/                    # 1.1 + 1.2 + 1.3
│   ├── connectors/               # Protocol + harness + Composio adapter
│   ├── staging.py                # raw sidecar + distilled markdown
│   └── mentions.py               # tier-escalation tracker
├── workflows/                    # 2.x — meeting prep, email draft, CRM update (stubs)
└── runtime/                      # Layer 5 — Hermes adapter (stub)

tests/                            # 269 passing
BUILD_LOG.md                      # per-step record
Makefile                          # install, check, lint, test, dev, clean
```

## Operational notes

Configuration is environment-driven via `MM_*` vars (see `src/memory_mission/config.py`):

| Variable | Default | Purpose |
|---|---|---|
| `MM_WIKI_ROOT` | `./wiki` | Root for firm content (curated pages + staging) |
| `MM_OBSERVABILITY_ROOT` | `./.observability` | Append-only audit log root |
| `MM_DATABASE_URL` | (empty) | Postgres URL when we move off in-memory |
| `MM_LLM_PROVIDER` | `anthropic` | `anthropic` / `openai` / `gemini` |
| `MM_LLM_MODEL` | `claude-sonnet-4-6` | Default model identifier |

Per-firm isolation is filesystem-based today: each firm gets its own subdirectory under `MM_OBSERVABILITY_ROOT` and its own SQLite files for durable execution + knowledge graph + mention tracker. Multi-tenancy hardening (row-level security, schema-per-tenant) lands in Phase 4.

Day-to-day:

```bash
make check          # ruff + format + mypy strict + pytest
make lint-fix       # auto-apply ruff fixes
pytest -k <pattern> # run a subset
```

## Open the personal plane in Obsidian

The on-disk format is vault-native. Point Obsidian at
`<MM_WIKI_ROOT>/personal/<employee_id>/` and you get a working vault
for free — graph view, linked mentions, search, tag panel, all of it.
The four-layer agent brain (`working/`, `episodic/`, `semantic/`,
`preferences/`, `lessons/`) shows up as four top-level folders; the
MECE domains (`people/`, `companies/`, etc.) live under `semantic/`.

Hidden directories (`.facts/`, `.raw/`) are skipped by Obsidian by
default, so the vault stays clean. The `---` zone separator inside
each curated page renders as a horizontal rule rather than a two-zone
split — cosmetic, doesn't affect content.

Treat Obsidian as the safety hatch (browse / grep / hand-annotate),
not the primary UX. Workflow-agent chat is where the daily work
happens.

## Status

Phase 1 foundations + memory layer + ingestion primitives complete. Step 7b (Hermes backfill skill in markdown) is next, then Step 8 (extraction agent), then promotion pipeline (Step 9) and workflow agents (Phase 2).

This is engineering infrastructure. It is not a finished product, and the current commit history (`git log`) is the most accurate description of what works.

## License

Proprietary — see `pyproject.toml`.
