# Memory Mission Build Log

Running progress log. Each entry tracks a completed step, what was built, how it
was verified, and what's next. Read this any time to see exactly where we are.

Full build plan: `/Users/svenwellmann/.claude/plans/gentle-painting-phoenix.md`.

---

## Step 1: Project Scaffolding — DONE (2026-04-18)

**Goal:** Working Python package with Hermes runtime dependency declared, test +
CI infrastructure in place. Every component is a stub; we fill them in over
later steps.

**Files created:**
- `pyproject.toml` — Python 3.12+, Pydantic/Typer/structlog core deps,
  extras for `db`, `memory`, `integrations`, `runtime`, `dev`
- `README.md` — top-level orientation
- `BUILD_LOG.md` — this file (your observability mechanism)
- `Makefile` — install, test, lint, typecheck, dev, clean
- `.gitignore` — Python + IDE + app-specific patterns
- `.python-version` — 3.12
- `src/memory_mission/` — package skeleton:
  - `__init__.py`, `__main__.py`, `config.py`, `cli.py`
  - `observability/` (stubs for 0.4)
  - `durable/` (stubs for 0.6)
  - `middleware/` (stubs for 0.7)
  - `memory/` (stubs for 0.1 + 0.2)
  - `ingestion/` (stubs for 1.1, 1.2, 1.3)
  - `workflows/` (stubs for 2.1, 2.2, 2.3)
  - `runtime/` (Hermes adapter stub)
- `tests/test_scaffold.py` — 5 smoke tests

**Verification:**
- [x] `pip install -e '.[dev]'` — succeeded (41 packages installed)
- [x] `python -m memory_mission --help` — prints CLI help with `version` and `info` commands
- [x] `python -m memory_mission version` — prints `memory-mission 0.1.0`
- [x] `python -m memory_mission info` — prints loaded config
- [x] `pytest` — 5/5 tests passed
- [x] `ruff check src/ tests/` — all checks passed
- [x] `mypy src/` — no issues in 24 source files (1 note about future-use overrides, harmless)

**Install environment:** Python 3.13.1 local, venv at `.venv/`, editable install.

**Next:** Step 2 — Observability foundation (component 0.4). Real append-only
JSONL logger. Every subsequent component writes to it.

---

## Step 2: Observability Foundation (Component 0.4) — DONE (2026-04-18)

**Goal:** Append-only audit trail that every subsequent component writes to.
Compliance-grade (7-year retention), immutable, per-firm isolated.

**Files created:**
- `src/memory_mission/observability/events.py` — Pydantic event schema
  - `_EventBase` (firm_id, employee_id, trace_id, timestamp, event_id, schema_version)
  - `ExtractionEvent`, `PromotionEvent`, `RetrievalEvent`, `DraftEvent`
  - Discriminated union via `event_type` field
  - Frozen (immutable), `extra="forbid"` (strict schema)
- `src/memory_mission/observability/logger.py` — Append-only JSONL writer
  - `ObservabilityLogger(observability_root, firm_id)` — per-firm scoped
  - `write(event)` — uses `O_APPEND` for POSIX-atomic concurrent appends
  - `read_all()` / `tail()` / `count()` / `parse_event_line()`
  - Rejects cross-firm writes at runtime
- `src/memory_mission/observability/context.py` — Ambient context
  - `observability_scope()` context manager binds firm_id/employee_id/trace_id/logger
  - `current_firm_id()`, `current_employee_id()`, `current_trace_id()`, `current_logger()`
  - Nested scopes isolate + restore correctly (LIFO reset)
- `src/memory_mission/observability/api.py` — Convenience logging API
  - `log_extraction()`, `log_promotion()`, `log_retrieval()`, `log_draft()`
  - Each reads firm/employee/trace from ambient scope
- `src/memory_mission/cli_log.py` — `memory-mission log` subcommands
  - `log tail --firm <id> [--event-type ...] [--follow] [--limit N]`
  - `log count --firm <id>`
  - `log path --firm <id>`
- `tests/test_observability.py` — 19 tests
  - Event schema: creation, serialization round-trip, extra fields rejected
  - Logger: write/read, append-only on reopen, cross-firm rejection, multi-firm isolation
  - Context: scope requires firm_id, bindings, trace_id propagation, nested scopes
  - Concurrency: 4 processes × 20 events = 80 parseable lines (no torn writes)
  - CLI: count, path, tail, event-type filter

**Verification:**
- [x] `pytest` — 24/24 passed (19 new + 5 from Step 1)
- [x] `ruff check src/ tests/` — clean
- [x] `ruff format --check` — clean
- [x] `mypy src/` — strict mode, no issues in 28 files
- [x] End-to-end demo: extraction + retrieval events share auto-generated trace_id,
  CLI `log tail` prints them as JSONL.

**Key invariants enforced by tests:**
- Events are immutable once constructed (frozen Pydantic models)
- Schema evolution is additive (SCHEMA_VERSION on every event)
- Multi-firm isolation at the logger level (firm A's writer refuses firm B events)
- File-level isolation (separate directory per firm)
- Concurrent writes from multiple processes don't corrupt the log

**Next:** Step 3 — Durable Execution + Checkpointing (component 0.6). Required
before backfill agent can run (backfill = 24h+ job, must survive crashes).

---

## Step 3: Durable Execution + Checkpointing (Component 0.6) — DONE (2026-04-18)

**Goal:** Long-running agents (backfill = 24h+, dreaming loop, HITL pauses)
survive crashes/deploys. Resume from last checkpoint, not from scratch.

**Files created:**
- `src/memory_mission/durable/store.py` — SQLite checkpoint store
  - `CheckpointStore(db_path)` with WAL journal, foreign keys on
  - Schema: `threads` (thread_id, firm_id, employee_id, workflow_type,
    status, state_json, timestamps) + `checkpoints` (thread_id, step_name,
    state_json, created_at, PK on pair)
  - Idempotent writes via `INSERT OR REPLACE`
  - Firm-scoped lookups, status filtering, transaction context manager
  - Zero-dep (stdlib `sqlite3`), migrate-ready to Postgres later
- `src/memory_mission/durable/run.py` — `DurableRun` API
  - `durable_run()` context manager handles start/fail/save lifecycle
  - `is_done(step)` / `mark_done(step, state)` / `run_step(step, fn)`
  - `state` — mutable per-thread dict for carrying context across resumptions
  - `pause()`, `complete()`, `fail()` status transitions
  - Cross-firm thread access rejected at runtime
  - Auto-seeds state with observability `trace_id` when scope is active
    (cross-references durable threads with audit events)
- `src/memory_mission/durable/__init__.py` — public API exports
- `tests/test_durable.py` — 18 tests

**Verification:**
- [x] `pytest` — 42/42 passed (18 new + 24 from earlier steps)
- [x] `ruff check src/ tests/` — clean
- [x] `ruff format --check` — clean
- [x] `mypy src/` — strict, no issues in 29 files
- [x] **Crash/resume test**: 7-step run crashes at step 3 → thread marked failed,
  3 checkpoints persisted. Re-running with same thread_id resumes, processes
  steps 4-7 only, each item processed exactly once across both runs.

**Key invariants enforced:**
- Cross-firm access to a thread is rejected (firm A can't resume firm B's run)
- `run_step` + `mark_done` are idempotent — safe to re-run completed steps
- State is persisted on clean exit, failed exit, or explicit `pause()`
- Trace_id bridges durable threads and observability audit events
- SQLite WAL = atomic commits, no torn writes on crash

**Next:** Step 4 — Middleware Layer (component 0.7). PII redaction ships
with V1 for wealth management compliance. ToolCallLimit, ModelFallback,
and Summarization middleware follow incrementally.

---

## Step 4: Middleware Layer (Component 0.7) — DONE (2026-04-18)

**Goal:** Guardrails that wrap every LLM call. PII redaction ships in V1 for
wealth management compliance. Framework is ready for more middleware
(ToolCallLimit, ModelFallback, Summarization) as later needs arrive.

**Files created:**
- `src/memory_mission/middleware/types.py` — core types
  - `ModelCall` (messages, model, provider, tools, metadata) — frozen Pydantic
  - `ModelResponse` (content, tool_calls, usage, metadata) — frozen Pydantic
  - `Middleware` protocol with optional `before_model` / `wrap_model_call` /
    `after_model` hooks (duck-typed, no inheritance required)
- `src/memory_mission/middleware/chain.py` — composition
  - `MiddlewareChain(middlewares=...)` applies hooks in documented order:
    before → wrap (onion) → model → reverse wrap → after (reverse)
  - Skips hooks the middleware doesn't implement via `_has_hook()`
  - `.append()` for incremental registration
- `src/memory_mission/middleware/pii.py` — PII redaction
  - Pattern list: SSN, email, APIKEY, phone (strict NXX-NXX-NNNN), card, account
  - Pattern ordering matters: specific patterns run first so greedy ones
    don't swallow them
  - `redact_input` / `redact_output` both default True
  - `extra_patterns` for firm-specific policies
  - `literal_redactions` for client-name lists
  - Stamps `metadata["pii_redactions_input"]` / `["pii_redactions_output"]`
    so observability can log what was scrubbed without re-scanning
- `tests/test_middleware.py` — 23 tests

**Verification:**
- [x] `pytest` — 65/65 passed (23 new + 42 from earlier steps)
- [x] `ruff check src/ tests/` — clean
- [x] `ruff format --check` — clean
- [x] `mypy src/` — strict, no issues in 31 files
- [x] End-to-end test: PII middleware integrated with a chain; leaky model
  that echoes input produces output with placeholders, never raw PII

**Key invariants enforced:**
- `ModelCall` and `ModelResponse` are frozen — middleware must use
  `model_copy(update=...)` to produce new instances
- Middleware hooks compose predictably: before-in-order, wrap-as-onion,
  after-in-reverse (matches web framework mental model)
- Subset middleware (implements only some hooks) doesn't break the chain
- PII redaction is idempotent (re-running on already-redacted text is a no-op)
- Non-content message fields (tool_call_id, name) preserved across redaction

**Deferred to later steps:**
- `wrap_tool_call` hook shipped as Protocol stub; concrete tool-call
  middleware (ToolCallLimit) comes with Step 5 when we have actual tools
- ModelFallback and Summarization middleware come when multi-provider
  setup + token-counting infrastructure exists

**Next:** Step 5 — Connector Layer (component 1.3). Composio Python SDK
integration, OAuth flows for Gmail/Outlook/Calendar/Salesforce/Notion,
custom MCP servers for Granola/Otter.ai (if needed).

---

## Review Fixes (post-Step-4 hardening) — DONE (2026-04-21)

External review flagged three issues. All fixed with regression-guard tests.

**Fix #1 (HIGH): Path traversal via firm_id**
- `src/memory_mission/observability/logger.py` — added `_validate_firm_id()`
  regex gate (alphanumeric + `-_.`, 1-128 chars, no leading dot, no path
  separators, no NUL) + `_safe_firm_dir()` which resolves the final path and
  verifies it stays under observability_root
- 15 new tests: parametrized malicious-id rejection (``..``, ``../escape``,
  ``foo/bar``, ``foo\bar``, absolute paths, hidden dotfiles, 129-char strings,
  NUL bytes, empty), end-to-end traversal-write-outside-root test, valid
  shape acceptance

**Fix #2 (HIGH): Reopening a completed thread flipped status back to running**
- `src/memory_mission/durable/run.py` — restructured `start()` so completed
  threads are a true no-op: don't update status, don't flip state
- New test: complete → reopen → exit cleanly, status remains `completed`

**Fix #3 (MEDIUM): 8-digit account numbers not redacted**
- `src/memory_mission/middleware/pii.py` — ACCOUNT_PATTERN had off-by-one:
  ``{8,17}\d`` required 9+ digits. Fixed to ``{7,16}\d`` = 8-17 digit match
  per docstring + compliance spec
- 4 new parametrized tests (8, 8-with-hyphen, 12, 17 digits) + negative test
  for 7-digit non-account numerics

**Cleanup (pyproject.toml):**
- Relaxed `runtime = []` (hermes-agent not yet on PyPI under stable name)
- Removed unused mypy overrides for modules we haven't imported yet
  (composio, mempalace, hermes_agent — will add back when we import them)

**Verification (rebuilt venv from scratch):**
- [x] `pip install -e '.[dev]'` succeeded
- [x] `pytest` — 89/89 passed (24 new review-guard tests + 65 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 31 files

---

## Step 5: Connector Layer (component 1.3) — DONE (2026-04-21)

**Goal:** Connector Protocol + invocation harness that threads observability,
PII redaction, and durability through every call into an external data
source. Concrete SDK calls are stubbed — the point of this step is the
harness. Granola and Gmail factories ship as first concrete connectors.

**Scope narrowing (vs original plan):**
- Composio SDK already works in adjacent production systems. Adapter in this
  repo stays as a stub until credentials are wired in a later step.
- **Granola only** for transcription in V1. Otter.ai dropped — revisit after
  Granola-only produces clear wins.
- **Gmail backfill** gets the harness but no live API test. GBrain's
  `sync_gmail.ts` pattern is known-good; no value in re-proving it here.

**Files created:**
- `src/memory_mission/ingestion/connectors/` package (replaces prior stub
  `connectors.py`):
  - `base.py` — `Connector` Protocol, `ConnectorAction` / `ConnectorResult`
    (frozen Pydantic), `invoke()` harness helper
  - `composio.py` — `ComposioClient` Protocol + `ComposioConnector` adapter
    (raises `NotImplementedError` when no client is attached)
  - `granola.py` — `GRANOLA_ACTIONS` + `make_granola_connector()` factory
  - `gmail.py` — `GMAIL_ACTIONS` + `make_gmail_connector()` factory
  - `testing.py` — `InMemoryConnector` test double with callable responders
- `tests/test_connectors.py` — 31 tests

**Files extended:**
- `src/memory_mission/observability/events.py` — added
  `ConnectorInvocationEvent` (additive schema change, version stays at 1)
- `src/memory_mission/observability/api.py` — added
  `log_connector_invocation()`
- `src/memory_mission/observability/__init__.py` — export both
- `src/memory_mission/middleware/pii.py` — added public `scrub(text)` method
  so callers outside the LLM chain share the same redaction policy
- `src/memory_mission/ingestion/__init__.py` — updated docstring

**Harness behavior:**
- `invoke(connector, action, params)` runs the connector call, measures
  latency, scrubs a truncated preview (default 500 chars) via
  `PIIRedactionMiddleware`, then writes a `ConnectorInvocationEvent` into
  the active `observability_scope`
- Raw `ConnectorResult.data` flows back to the caller unchanged — only what
  lands in the audit log is redacted
- Errors are logged with `success=False` and re-raised (no silent swallow)
- Durability is the caller's responsibility: backfill loops wrap each
  `invoke()` in a `durable_run` step (demonstrated by integration test)

**Verification:**
- [x] `pytest` — 120/120 passed (31 new + 89 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 36 files
- [x] Integration test: durable_run + observability_scope + invoke()
  backfill loop crashes mid-run, resumes on restart, processes each
  message exactly once (5 events in the audit log, 5 expected)

**Key invariants enforced by tests:**
- `ConnectorAction` / `ConnectorResult` are frozen Pydantic with
  `extra="forbid"`
- `Connector` Protocol `isinstance` checks succeed for both concrete impls
- `ComposioConnector` raises `ValueError` for unknown actions and
  `NotImplementedError` when no client is attached
- Harness threads `firm_id` / `employee_id` / `trace_id` from scope
- PII-scrubbed preview survives JSONL round-trip with redaction counts
- Long previews truncated to `preview_chars` before scrubbing
- Custom redactor (with literal client-name redaction) honored per-call
- Failure path logs `success=False` then re-raises

**Stubbed for later steps:**
- Live Composio SDK wiring (inject a real `composio.Client` via
  `ComposioClient` Protocol)
- OAuth credentials flow for Gmail / Granola accounts
- Custom MCP server for anything Composio doesn't cover (Otter.ai deferred
  to post-V1)

**Next:** Step 6 — Memory Layer (components 0.1 + 0.2). Port GBrain
`BrainEngine` interface to Python, hybrid search with RRF_K=60 and
70/30 cosine blend, compiled truth + timeline page format, MemPalace
knowledge graph integration.

---

## Step 6a: Memory Layer — Pages, Schema, BrainEngine (Step 6 slice 1/3) — DONE (2026-04-21)

**Goal:** Establish the data shape (compiled truth + timeline pages),
MECE directory schema adapted for wealth management, and the `BrainEngine`
Protocol with a dict-backed in-memory implementation. No DB, no embeddings,
no knowledge graph — those land in 6b/6c once this interface is stable.

**Scope decisions (2026-04-21):**
- **Embeddings provider:** OpenAI `text-embedding-3-small` OR Gemini, wired
  as a provider Protocol (Composio-style) in 6b. QMD stays a separate
  system; this repo doesn't depend on it.
- **Vector store:** pure Python. In-memory for V1. SQLite + sqlite-vec or
  pgvector via Postgres swap in behind `BrainEngine` when a real extraction
  flow needs them.
- **MemPalace:** port `knowledge_graph.py` (~300 LOC, SQLite, temporal
  triples) into our codebase in 6b. No pip install.

**Files created:**
- `src/memory_mission/memory/pages.py` — page parser + renderer:
  - `PageFrontmatter` — Pydantic with core fields (slug / title / domain /
    aliases / sources / valid_from / valid_to / confidence / created /
    updated); `extra="allow"` preserves hand-edited custom keys
  - `Page` — frontmatter + compiled_truth + timeline; `wikilinks()` extracts
    `[[slug]]` / `[[slug|display]]` references
  - `TimelineEntry` — frozen, `YYYY-MM-DD [source-id]: text` format
  - `parse_page()` / `render_page()` — round-trip through markdown with
    YAML frontmatter and dual `---` zone separators
  - `new_page()` factory stamps `created` / `updated` in UTC
  - Slug regex enforces lowercase kebab-case, 1-128 chars, no traversal
- `src/memory_mission/memory/schema.py` — MECE directory constants:
  - 8 core domains (GBrain base, vertical-neutral): `people`, `companies`,
    `deals`, `meetings`, `concepts`, `sources`, `inbox`, `archive`
  - Vertical-specific taxonomies (wealth, legal, CRM) extend via config,
    NOT by editing this list — keeps the infrastructure deployable across
    verticals without forking the schema
  - `page_path()` / `raw_sidecar_path()` return `PurePosixPath` so storage
    backends bind the concrete root
- `src/memory_mission/memory/engine.py` — `BrainEngine` Protocol +
  `InMemoryEngine`:
  - Lifecycle (`connect` / `disconnect`), page CRUD, keyword search, graph
    links (`links_from` / `links_to`), `EngineStats`
  - Every `search()` logs a `RetrievalEvent` with query, tier, pages
    loaded, latency — audit trail unified with extractions + promotions
  - Keyword scoring: `2.0 * truth_hits + 1.0 * title_hits` (truth zone
    outranks title, placeholder for `COMPILED_TRUTH_BOOST=2.0` in 6b)
- `tests/test_memory.py` — 39 tests

**Files modified:**
- `src/memory_mission/memory/__init__.py` — public API exports
- `pyproject.toml` — added `types-PyYAML>=6.0` to dev for mypy strict

**Verification:**
- [x] `pytest` — 159/159 passed (39 new + 120 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 37 files
- [x] Round-trip: parse → render → parse preserves frontmatter (including
  extras), compiled truth, and timeline entries
- [x] Path safety: slug regex rejects `../escape`, `Sarah`, `sarah chen`,
  `sarah/chen`, empty string

**Key invariants enforced by tests:**
- `Page`, `TimelineEntry`, `SearchHit`, `EngineStats` are frozen Pydantic
- `confidence` in `[0, 1]` via Pydantic validator
- Slug must match `[a-z0-9][a-z0-9-]{0,126}[a-z0-9]?`
- `PageFrontmatter` preserves unknown keys through round-trip
- `InMemoryEngine` rejects unknown domains at both `put_page` and `list_pages`
- `links_to` excludes self-links
- Search ranks truth-zone matches above title-only matches
- `RetrievalEvent` lands in the audit log with correct query / tier /
  pages_loaded when search runs inside an `observability_scope`

**Deferred to 6b / 6c:**
- MemPalace `KnowledgeGraph` port (temporal triples, SQLite, validity
  windows + confidence + provenance)
- Hybrid search (keyword + vector + RRF fusion + cosine re-score)
- Embedding provider Protocol + OpenAI / Gemini adapters
- Filesystem-backed `BrainEngine` (markdown-on-disk source of truth)
- Postgres / pgvector concrete backend

**Next:** Step 6b — Port MemPalace `knowledge_graph.py` into
`src/memory_mission/memory/knowledge_graph.py`. SQLite schema for entities
+ triples with `valid_from` / `valid_to` / `confidence` / source tracking.
API: `add_entity`, `add_triple`, `invalidate`, `query_entity`,
`query_relationship`, `timeline`, `stats`.

---

## Step 6b: Temporal Knowledge Graph (port from MemPalace) — DONE (2026-04-21)

**Goal:** A per-firm entity-relationship graph where every fact carries a
validity window, confidence, and provenance, so queries can ask "what was
true on ``as_of``?" instead of only "what's the current state?". Ported
from MemPalace's ``knowledge_graph.py`` — not installed, re-implemented so
we own the schema and can evolve firm-scoping + observability hooks
without forking a third-party package.

**Files created:**
- `src/memory_mission/memory/knowledge_graph.py` — SQLite-backed store:
  - `Entity` (frozen Pydantic) — canonical by name, holds entity_type +
    free-form properties dict
  - `Triple` (frozen Pydantic) — subject-predicate-object with
    `valid_from` / `valid_to` / `confidence` / `source_closet` /
    `source_file`; `is_valid_at(as_of)` for time-travel semantics
  - `GraphStats` — entity count, triple count, currently-true triple count
  - `KnowledgeGraph` — SQLite-backed store with per-firm DB path
  - API parity with MemPalace: `add_entity`, `add_triple`, `invalidate`,
    `query_entity` (with `direction` + `as_of`), `query_relationship`,
    `timeline`, `stats`, `seed_from_entity_facts`, `close`
  - Schema: `entities` + `triples` tables with indexes on subject /
    predicate / object plus partial index on currently-true triples
  - Context manager support (`with KnowledgeGraph(path) as kg:`)
- `tests/test_knowledge_graph.py` — 37 tests

**Files modified:**
- `src/memory_mission/memory/__init__.py` — exported `KnowledgeGraph`,
  `Entity`, `Triple`, `GraphStats`, `Direction`

**Semantics locked by tests:**
- `valid_to` is **exclusive**: a triple with `valid_to=2025-01-01` is
  invalid ON 2025-01-01 (the fact ended "as of" that day)
- `valid_from` / `valid_to` both None = "always true" (no window bound)
- `invalidate()` only updates currently-true rows (`valid_to IS NULL`),
  returning the row count so callers can detect misses
- `add_entity` is upsert-by-name: second call with richer type / properties
  updates the row, doesn't create a duplicate
- `seed_from_entity_facts` accepts ISO date strings and coerces them
- `timeline(entity=None)` returns the whole graph chronologically; null
  `valid_from` rows come first (undated facts)
- Per-firm isolation is filesystem-based — different DB paths never share
  state

**Adaptations vs MemPalace original:**
- Pydantic models for public types (matches the rest of the codebase)
- Per-firm DB path (MemPalace is single-user)
- No ChromaDB dependency (vector search lives in 6c, behind
  `BrainEngine`)
- No AAAK / dialect compression (MemPalace authors themselves flagged it
  as a regression)

**Verification:**
- [x] `pytest` — 196/196 passed (37 new + 159 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 38 files
- [x] Persistence: graph round-trips across `KnowledgeGraph` open/close
- [x] Parametrized `is_valid_at` covers 8 cases across bounded, open-ended,
  and always-true triples

**Deferred to 6c (and beyond):**
- Hybrid search with RRF fusion + vector scoring + `COMPILED_TRUTH_BOOST`
- Embedding provider Protocol (OpenAI / Gemini adapters)
- Filesystem-backed `BrainEngine` (markdown-on-disk source of truth)
- Entity detection + canonicalization (MemPalace's `entity_detector.py` /
  `entity_registry.py` — needed when the extraction agent lands)

**Next:** Step 6c — hybrid search shell. Extend `BrainEngine` with
`query(question, as_of=...)` that combines keyword (current) + vector
(stubbed) via RRF fusion. Wire in the `COMPILED_TRUTH_BOOST=2.0` + 70/30
blend constants from GBrain so when a real embedding provider plugs in,
the search pipeline already has the right shape.

---

## Step 6c: Hybrid Search Shell (RRF + cosine blend, stub embedder) — DONE (2026-04-21)

**Goal:** The full GBrain hybrid-search pipeline — keyword pass + vector
pass + RRF fusion + compiled-truth boost + cosine blend — wired into
`BrainEngine`. No live embedding provider yet; a deterministic
`HashEmbedder` stands in so tests can verify plumbing end-to-end. When a
real `EmbeddingProvider` (OpenAI, Gemini, QMD) gets injected in a later
step, the pipeline already has the right shape.

**Files created:**
- `src/memory_mission/memory/search.py` — hybrid-search primitives:
  - `EmbeddingProvider` Protocol (`dimension` + `embed(text)`)
  - `HashEmbedder` — SHA256-hashed bag-of-tokens, L2-normalized,
    deterministic across processes (explicit hash — Python's built-in
    `hash()` is randomized per-process by PYTHONHASHSEED)
  - `cosine_similarity(a, b)` — returns 0 on zero-norm vectors, raises
    on dimension mismatch
  - `rrf_fuse(ranked_lists, k=60)` — reciprocal rank fusion
  - Constants: `RRF_K = 60`, `COMPILED_TRUTH_BOOST = 2.0`,
    `VECTOR_RRF_BLEND = 0.7` — GBrain's starting values, tunable later on
    pilot data

**Files extended:**
- `src/memory_mission/memory/engine.py`:
  - `BrainEngine` Protocol gained `query()` method
  - `InMemoryEngine(embedder=None)` — optional embedder, eager page
    embedding on `put_page`, cleanup on `delete_page`
  - New `query(question, *, limit=10, tier="cascade")` method runs the
    full pipeline: keyword + vector → RRF fuse → compiled-truth boost →
    cosine blend → logged `RetrievalEvent`
  - When no embedder is attached, vector pass is skipped cleanly and the
    pipeline degrades to keyword-only with the same boost shape
- `src/memory_mission/memory/__init__.py` — exported `HashEmbedder`,
  `EmbeddingProvider`, `cosine_similarity`, `rrf_fuse`, and all three
  constants
- `tests/test_search.py` — 31 tests

**Pipeline behavior locked by tests:**
- Pure keyword mode (no embedder): single page with query in truth scores
  exactly `(1/61) * COMPILED_TRUTH_BOOST = 2/61`
- Truth-match always outranks title-only match (quantitative check:
  boosted `2/61` vs unboosted `1/62`)
- With embedder: final score = `0.7 * RRF + 0.3 * cosine` (verified via
  reproducing the exact cosine from `embedder.embed(title + truth)`)
- RRF accumulates across lists — item in both lists scores higher than
  item in one
- `RRF_K` is tunable: `k=1` is tight, `k=1000` flattens the rank curve
- `HashEmbedder` is deterministic across processes (same text → same
  vector), L2-normalized, empty string → zero vector
- `delete_page` drops the embedding alongside the page (no orphan
  embeddings poisoning future queries)

**Verification:**
- [x] `pytest` — 227/227 passed (31 new + 196 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 38 files
- [x] `HashEmbedder` satisfies `EmbeddingProvider` Protocol via
  `isinstance()` check
- [x] Cosine similarity: identical → 1.0, orthogonal → 0.0, opposite →
  -1.0, zero-norm → 0.0, mismatched dims → ValueError
- [x] Pipeline degrades cleanly: no embedder + no keyword match = empty
  hits, still logs the event

**Deferred:**
- Real `EmbeddingProvider` adapters (OpenAI `text-embedding-3-small`,
  Gemini) — wire when extraction flow needs semantic search
- Vector store persistence (SQLite + sqlite-vec or Postgres + pgvector) —
  wire when in-memory doesn't cut it
- Filesystem-backed `BrainEngine` (markdown-on-disk source of truth)
- Query expansion (optional callback path from GBrain)
- `as_of=<date>` filtering on `query()` respecting page validity windows
- Four-layer deduplication across chunk variants (becomes useful when
  pages get chunked for retrieval)

**Next:** Step 7 — Backfill Agent (component 1.1). Port Rowboat's
`sync_gmail.ts` pattern to Python using the Gmail connector + durable
execution. Each message is a checkpointed step; output lands in
`/staging/` for human review; extraction happens in-loop via the
observability-scoped middleware chain.

---

## Step 7a: Backfill Primitives (StagingWriter + MentionTracker) — DONE (2026-04-21)

**Goal:** Two pure-Python primitives the Hermes backfill skill (7b) will
compose: a writer for the staging area + a tracker for entity-mention
counts. Step 7 splits into "primitives in Python" (this commit) plus
"workflow as a markdown skill" (next commit) so future backfills (calendar,
Granola) reuse the same primitives without each shipping its own loop code.

**Architecture decision (2026-04-21):**
- The backfill orchestration lives as a Hermes markdown skill, not a
  Python class. Matches GBrain's "thin harness, fat skills" philosophy
  + the Hermes runtime we picked. Python provides the testable pieces;
  the skill composes them.
- OAuth + token refresh handled by Composio (already wired in Step 5),
  so we don't need to port Rowboat's googleapis OAuth code. Rowboat
  remains the *reference* for sync strategy (full vs incremental,
  lookback windows); GBrain remains the reference for processing pattern
  (raw sidecar + tiered enrichment).

**Files created:**
- `src/memory_mission/ingestion/staging.py` — `StagingWriter`:
  - Writes pulled items to `<wiki_root>/staging/<source>/`:
    - `.raw/<item_id>.json` — connector payload verbatim
    - `<item_id>.md` — frontmatter (`source`, `source_id`,
      `ingested_at`, plus caller extras) + body
  - Atomic writes via temp + rename
  - Path-segment validation (alnum + `._-`, length-bounded; same shape
    as `_SAFE_FIRM_ID` in observability/logger)
  - `get` / `list_pending` / `remove` / `iter_raw` for the promotion flow
  - Canonical fields locked: caller extras can't override `source`,
    `source_id`, `ingested_at`
- `src/memory_mission/ingestion/mentions.py` — `MentionTracker`:
  - Per-firm SQLite store of entity mention counts
  - `record(name) -> (prev_tier, new_tier)` — caller checks the pair to
    detect threshold crossings
  - Tier mapping: `none` (0) → `stub` (1+) → `enrich` (3+) → `full` (8+),
    matching GBrain's `enrichment-service.ts` thresholds
  - `get`, `all` (count-desc), `stats` (counts per tier)
  - Context manager + idempotent `close()`
- `tests/test_staging.py` — 18 tests
- `tests/test_mentions.py` — 24 tests

**Files modified:**
- `src/memory_mission/ingestion/__init__.py` — public exports

**Key invariants enforced by tests:**
- Source label and item_id are validated as safe path segments — no
  traversal, no NUL, no path separators
- Caller-supplied frontmatter extras can't override `source` /
  `source_id` / `ingested_at` (these belong to the writer)
- Re-writing same item_id replaces both files cleanly
- `list_pending` skips orphan markdown without a matching raw sidecar
- Per-source isolation (gmail and granola write to disjoint subdirs)
- `MentionTracker.record` is transactional (all-or-nothing increment)
- Tier crossings are detectable: `(prev, new)` tuple per record() call
- Per-firm isolation (different DB paths = independent counts)

**Verification:**
- [x] `pytest` — 269/269 passed (42 new + 227 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 40 files

**Deferred to 7b:**
- The backfill workflow itself: `skills/backfill-gmail/SKILL.md` (Hermes
  skill) that composes `make_gmail_connector` + `invoke` (harness) +
  `durable_run` + `StagingWriter` + `MentionTracker` + `KnowledgeGraph`
- The same skill, generalized: backfill-granola, backfill-calendar
  re-use the same primitives, only the connector + per-item parsing
  differ

**Deferred to later steps:**
- Per-message LLM extraction (Step 8 — Extraction Agent)
- Promotion from staging into curated MECE pages (Step 9 — Promotion
  Pipeline)

**Next:** Step 7b — write the Hermes backfill skill in markdown. Pulls
through the Gmail connector, wraps the loop in `durable_run`, writes
each message to staging, records entity mentions, surfaces tier
crossings.

---

## Step 7b: Hermes Backfill Skill (markdown workflow + skill registry) — DONE (2026-04-21)

**Goal:** The Gmail backfill workflow as a Hermes-compatible markdown
skill that composes the Step 7a primitives. First skill in our registry
— sets up the convention so calendar / Granola / extraction skills slot
in without re-inventing the layout. Adopts the agentic-stack skill
format verbatim.

**Architecture decision:**
- Mention extraction is **deferred to Step 8** (Extraction Agent). The
  backfill skill stays focused on pull + stage. Reasoning belongs in
  the agent that has the LLM, not in the loop that just moves bytes.
- Skills live at `skills/<name>/SKILL.md` at the repo root, plus
  registry files at `skills/_*` — matches agentic-stack convention so
  any Hermes-compatible runtime mounts our brain without translation.

**Files created:**
- `skills/_index.md` — human-readable registry (one entry: backfill-gmail)
- `skills/_manifest.jsonl` — machine-readable, one JSON line per skill
- `skills/_writing-skills.md` — convention guide (frontmatter spec,
  "destinations and fences not driving directions" rule, self-rewrite
  hook footer template, anti-patterns)
- `skills/backfill-gmail/SKILL.md` — the workflow itself
  - Frontmatter: name, version (quoted to keep it a string),
    triggers, tools, preconditions, constraints, category
  - Body sections: what this does, workflow, where the data lands,
    what it does NOT do (LLM, MentionTracker, direct connector
    invokes, OAuth bootstrap), on crash, self-rewrite hook
  - 4 hard constraints encoded — extraction agent in Step 8 will run
    inside this skill's output, not modify it
- `tests/test_skills_registry.py` — 19 tests for layout + per-skill
  invariants + manifest ↔ frontmatter agreement

**Registry invariants enforced by tests:**
- `skills/`, `_index.md`, `_manifest.jsonl`, `_writing-skills.md` all
  exist
- Every skill directory (non-underscore) has a `SKILL.md`
- Every `SKILL.md` has the seven required frontmatter fields
- `version` is a `YYYY-MM-DD` date string (not a YAML date object —
  caught the auto-parse footgun on first test run, documented in
  `_writing-skills.md`)
- Frontmatter `name` matches directory name
- `triggers` and `constraints` are non-empty lists of strings
- Every `SKILL.md` includes a `Self-rewrite hook` section
- `_manifest.jsonl` covers exactly the set of skill directories on
  disk (no orphans either way)
- Every manifest field agrees with the corresponding SKILL.md
  frontmatter (parametrized over the seven fields)
- `_index.md` mentions every skill by name

**What the skill does NOT do (explicit constraints):**
- No LLM call. Extraction is Step 8.
- No `MentionTracker.record()`. Mentions are extracted from facts, not
  raw email bodies; wired in Step 8.
- No direct `connector.invoke()`. The harness `invoke()` is mandatory
  for observability + PII redaction.
- No OAuth bootstrap. Caller injects the `ComposioClient`; missing
  client → `NotImplementedError` surfaced.
- No writes outside `staging/`. Promotion to MECE domains is Step 9.

**Verification:**
- [x] `pytest` — 319/319 passed (19 new + 300 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 42 files
- [x] Frontmatter parses; manifest matches frontmatter for every field

**Deferred to Step 8:**
- Per-message LLM extraction (entity detection, fact triples)
- `MentionTracker.record()` calls — wired into the extraction agent
- The extraction agent will be its own skill at
  `skills/extract-from-staging/SKILL.md` and will consume from
  `<wiki_root>/staging/<source>/`

**Next:** Step 8 — Extraction Agent. Reads from `staging/`, calls an
LLM through the middleware chain (PII-redacted in/out), writes
structured triples to `KnowledgeGraph`, increments `MentionTracker` on
each entity seen, surfaces tier crossings to a stage-2 enrichment
queue.

---

## Step 8: Two Memory Planes + Permissions Layer — DONE (2026-04-22)

**Goal:** Bake Emile's governance principles into the architecture
before any more workflow code lands. Split storage into personal
(per-employee private) + firm (shared, governed) planes, and add a
native access-control policy layer. Plan reshape
(`to-be-the-agents-enchanted-petal.md`) promoted this ahead of Step 9
extraction because the retrofit cost grows fast if we ship extraction
into the flat layout first.

Three sub-commits, each fully verified:

### Step 8a — plane-aware data model (commit `6a59483`)

- `src/memory_mission/memory/schema.py`:
  - `Plane = Literal["personal", "firm"]`
  - `plane_root()` — resolves plane + employee_id to posix path prefix
  - `validate_employee_id()` with same safety shape as observability
    firm-id regex (alnum + ._-, length bound, no traversal)
  - Plane-aware `page_path()` / `raw_sidecar_path()` take plane + opt
    employee_id
  - `staging_source_dir()` for the staging zone layout
- `src/memory_mission/memory/engine.py`:
  - `PageKey(plane, slug, employee_id)` composite key — same slug
    coexists across Alice's personal, Bob's personal, and firm plane
  - `BrainEngine` Protocol: `put_page` / `get_page` / `delete_page` /
    `links_from` / `links_to` take `plane=` required + optional
    `employee_id=`
  - `list_pages` / `search` / `query` take optional plane + employee_id
    filter for scoped retrieval
  - `SearchHit` gains `plane` + `employee_id` fields
  - `EngineStats` reports `pages_by_plane`
  - Cross-plane leakage impossible by construction (links_to is
    scope-local)
- `src/memory_mission/ingestion/staging.py`:
  - `StagingWriter(target_plane=, employee_id=None)` required
  - Layout: `staging/personal/<emp>/<source>/` or `staging/firm/<source>/`
  - `StagedItem` gains `target_plane` + `employee_id`
  - Frontmatter records both; canonical fields locked against caller
    spoofing

### Step 8b — permissions layer (commit `9597c5b`)

- `src/memory_mission/permissions/policy.py`:
  - `Policy`, `Scope`, `EmployeeEntry` Pydantic models (frozen)
  - `can_read(policy, employee_id, page)` — default deny on unknown
    employee; public always allowed; restricted scope must be in
    employee's allowed set; unknown scope fails closed
  - `can_propose(policy, employee_id, proposed_scope)` — no-escalation
    rule: can only propose into scopes you already have read access to
  - `parse_policy_markdown()` — typed object from markdown source
  - `load_policy(path)` — convenience
  - `page_scope(page)` — reads `scope:` from frontmatter extras, falls
    back to policy default
- `protocols/permissions.md.template` — per-firm template demonstrating
  three access tiers (public / partner-only / client-confidential /
  deal-team) + three employees across them
- Pure library — no engine integration. Host-agent skills call
  `can_read` / `can_propose` as utility functions before returning
  results or staging proposals. Same check lands in both retrieval and
  proposal paths without tight coupling.

### Step 8c — migrate backfill-gmail skill to plane-aware paths (this commit)

- `skills/backfill-gmail/SKILL.md` — updated constraints to require
  `target_plane="personal"` + employee_id; updated paths in "Where the
  data lands" section; added explicit "firm-plane staging comes from
  `skills/backfill-firm-artefacts` (Step 11), not this skill"
- `skills/_manifest.jsonl` — regenerated frontmatter
- `skills/_index.md` — updated summary to reflect personal-plane
  destination

**Verification (across all sub-commits):**
- [x] `pytest` — 368/368 passed (25 new permissions tests + 24 new
  plane-isolation tests + 319 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 44 files
- [x] Registry integrity tests still green after skill migration
- [x] Plane isolation tests: personal pages don't leak across employees;
  same slug coexists across planes; delete on one plane doesn't affect
  others; `links_to` doesn't cross planes
- [x] Permission tests: scope enforcement, public always allowed,
  unknown-scope fail-closed, no-escalation on `can_propose`, template
  round-trip parse

**Key invariants enforced by tests:**
- `Plane = Literal["personal", "firm"]` — staging is a zone, not a plane
- Personal plane requires employee_id; firm plane rejects it
- `PageKey` hashes across all three fields — plane isolation at the
  data-structure level
- `SearchHit` carries plane + employee_id so downstream callers know
  where the hit came from
- Permission `can_read` defaults deny on unknown employee, public
  always-allowed, scope must match, unknown scope fails closed
- Permission `can_propose` enforces no-escalation — can only propose
  into scopes you already have read access to

**Deferred:**
- `BrainEngine` integration with `PolicyLoader` (V1 uses can_read /
  can_propose as utility functions at the skill layer, which is
  sufficient given host agents orchestrate retrieval)
- Scope-based glob matching (V1 uses explicit `scope:` frontmatter
  field per page; glob-driven default scoping can land when a firm
  wants path-prefix rules)
- Administrator UX for editing permissions.md (manual file edit is
  fine for V1 pilot)

**Next:** Step 9 — Extraction Agent. `ExtractedFact` Pydantic schema
(6 buckets: Identity / Relationship / Preference / Event / Update /
Open question). `EXTRACTION_PROMPT` markdown template with
venture-firm examples. `ingest_facts(facts, source_id, source,
employee_id)` writes `Claim` / `Update` entries to staging (not direct
to KG yet). `skills/extract-from-staging/SKILL.md` — host agent runs
its own LLM with our prompt, returns parsed output, calls
`ingest_facts`. No LLM SDK imports in our code.

---

## Step 9: Extraction Agent (host-run LLM, our schema + ingest) — DONE (2026-04-22)

**Goal:** Ship the extraction *interface* — prompt template, output
schema, ingest function, skill file — so any host agent (Claude Code,
Hermes, Codex sub-agent) can run its own LLM with our prompt and hand
back structured facts. Memory Mission imports no LLM SDK.

**Files created:**
- `src/memory_mission/extraction/schema.py` — the six-bucket taxonomy
  as a Pydantic discriminated union:
  - `IdentityFact` → maps to `KnowledgeGraph.add_entity`
  - `RelationshipFact` → maps to `KnowledgeGraph.add_triple`
  - `PreferenceFact` → triple with `prefers_*` predicate
  - `EventFact` → `TimelineEntry` append
  - `UpdateFact` → `invalidate` + `add_triple` pair on promotion
  - `OpenQuestion` → flagged for human review; never auto-promoted
  - `ExtractionReport` — all facts from one source item plus the
    `source` / `source_id` / `target_plane` / `employee_id` /
    `extracted_at` metadata the promotion pipeline needs
  - Every fact requires `support_quote` (non-empty) — "no quote, no
    fact" is the extraction rule.
- `src/memory_mission/extraction/ingest.py`:
  - `ExtractionWriter` — per-plane, per-source writer for fact staging
    at `<wiki_root>/staging/<plane_root>/.facts/<source>/<source_id>.json`
  - `ingest_facts(report, wiki_root, mention_tracker=None)` — persists
    report + records one mention per unique entity (not per fact) +
    returns `IngestResult` with `TierCrossing` entries
  - `TierCrossing.is_promotion` — `new_tier > previous_tier` in the
    none → stub → enrich → full order. Review skill uses this to
    decide what to surface for human attention.
- `src/memory_mission/extraction/prompts.py` — `EXTRACTION_PROMPT`:
  - Markdown template with all six fact-kind schemas
  - Venture-firm worked example (partner meeting notes → facts JSON)
  - Rules section: `support_quote` required, confidence honesty,
    kebab-case entity names, stable snake_case predicates,
    open-question fallback on uncertainty
- `skills/extract-from-staging/SKILL.md` — orchestration workflow
  for the host agent. Reads source staging, runs host LLM with the
  prompt, validates output, calls `ingest_facts`. Forcing questions
  surface entity-match ambiguity, low-confidence facts, tier crossings,
  validation failures — never guesses.
- `tests/test_extraction.py` — 33 tests

**Files updated:**
- `skills/_index.md` — new skill entry
- `skills/_manifest.jsonl` — new skill entry

**Key invariants enforced by tests:**
- Every fact has `confidence ∈ [0, 1]`, non-empty `support_quote`,
  frozen model, `extra="forbid"`
- Discriminated union parses all six kinds by `kind` field; unknown
  kind rejected
- Firm-plane reports allow `employee_id=None`; personal reports
  require it
- `ExtractionReport.entity_names()` dedupes across facts — same
  entity in 5 facts counts as one mention per ingest call
- `OpenQuestion` contributes no entity names (no promotion signal)
- `ExtractionWriter` validates source / target_plane / employee_id
  match the report being written (catches the "wrong writer for this
  report" mistake)
- Atomic writes via temp file + rename
- `ingest_facts` with no tracker → zero crossings; with tracker →
  one crossing per unique entity, `is_promotion` reflects the tier
  jump
- Third mention of an entity crosses stub → enrich (`is_promotion`
  True); second mention stays at stub (False)
- `EXTRACTION_PROMPT` contains all six kind names, the "No quote, no
  fact" rule, and venture-firm example (`Series B`, `post-money`)

**Verification:**
- [x] `pytest` — 401/401 passed (33 new + 368 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 48 files
- [x] Registry-integrity tests still green with the new skill
- [x] No `import anthropic` / `import openai` / `import google` — the
  host agent owns the LLM call

**Deferred:**
- Promotion from staged reports into curated pages (Step 10 —
  `Proposal` + `promote` / `reject` / `reopen`)
- Retrieval paths (workflow agents consume firm + personal pages;
  Step 13+)
- Live LLM integration — the host agent brings that

**Next:** Step 10 — Promotion Pipeline. `Proposal` Pydantic model
(bundles `Claim` / `Update` entries from an `ExtractionReport`),
`ProposalStore` (per-firm SQLite), `promote()` / `reject()` /
`reopen()` with rationale required. `skills/review-proposals/SKILL.md`
surfaces thresholded proposals via forcing questions; human approves
in chat; skill calls `promote()` → writes to firm plane with full
provenance + `PromotionEvent`. This is V1's centerpiece.

---

## Step 10: Promotion Pipeline (V1 centerpiece) — DONE (2026-04-22)

**Goal:** The PR-model merge gate. Nothing lands on a memory plane
without an explicit human decision with rationale. Bad promotion is
worse than missing promotion — default deny on auto-merge.

Two sub-commits.

### Step 10a — promotion infrastructure (commit `65c9bfe`)

- `src/memory_mission/promotion/proposals.py`:
  - `Proposal` frozen Pydantic — target_plane + target_entity +
    facts + proposer + source_report_path + lifecycle fields
    (status, decision_history, rejection_count)
  - `DecisionEntry` — one audit entry per approve/reject/reopen
  - `ProposalStore` — per-firm SQLite queue, indexed on status +
    target_entity + target_plane; context manager; save/get/list/stats
  - `generate_proposal_id` — deterministic hash so retries don't
    duplicate
- `src/memory_mission/promotion/pipeline.py`:
  - `create_proposal` — validates inputs, inserts to store,
    emits `ProposalCreatedEvent`. Idempotent by proposal_id.
  - `promote` — loads pending proposal, applies facts to KG
    atomically (identity → add_entity, relationship/preference →
    add_triple, event → dated triple, update → invalidate +
    add_triple, open_question → skipped), marks approved, emits
    `ProposalDecidedEvent`. Rationale required (empty/whitespace
    blocked structurally via `_require_rationale`). Raises
    `ProposalStateError` on non-pending proposals.
  - `reject` — marks rejected, bumps rejection_count, preserves
    decision history. Same rationale requirement.
  - `reopen` — only valid on rejected proposals; flips back to
    pending so a reviewer with new evidence can reconsider. Full
    history preserved across the lifecycle.
- `src/memory_mission/observability/events.py` — additive:
  - `ProposalCreatedEvent` (event_type="proposal_created")
  - `ProposalDecidedEvent` (event_type="proposal_decided" —
    covers approved/rejected/reopened)
  - `log_proposal_created` / `log_proposal_decided` in api.py
- `tests/test_promotion.py` — 33 tests

**Provenance:** each promoted triple carries `source_closet` —
"firm" for firm-plane promotions, "personal/<employee_id>" for
personal. `source_file` is the `ExtractionReport` path that grounded
the proposal. The KG time-travel semantics stay intact: `UpdateFact`
with `supersedes_object` produces an `invalidate` + `add_triple` pair
so `query_entity(as_of=<date>)` returns the right value for each
point in time.

### Step 10b — review-proposals skill (this commit)

- `skills/review-proposals/SKILL.md` — the merge-gate workflow:
  - Read pending proposals, rank by rejection_count → tier
    crossings → age
  - Permission pre-check via `can_propose(policy, reviewer_id,
    proposal.target_scope)` — skip what reviewer can't decide
  - Surface ONE proposal at a time via forcing questions
  - Three decisions: Approve / Reject / Skip (all via host agent's
    question mechanism)
  - Call `promote` / `reject` — pipeline enforces rationale
  - Stop on error; don't cascade
- Forcing questions surface: contradictions with existing firm truth,
  permission uplift warnings, recurring rejections, low-confidence
  bundles. Never guess on the human's behalf.
- `skills/_index.md` + `skills/_manifest.jsonl` updated

**Verification (both sub-commits):**
- [x] `pytest` — 434/434 passed (33 new + 401 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 51 files
- [x] Registry-integrity tests still green with the new skill
- [x] Observability tests still green (new events are additive,
  existing PromotionEvent unchanged)

**Key invariants enforced by tests:**
- `generate_proposal_id` deterministic for same inputs; differs on
  plane / employee / fact changes
- `create_proposal` is idempotent — second call returns existing
  proposal
- `promote` writes facts to KG atomically; raises before marking
  approved if apply fails
- Every decision requires non-empty rationale (empty string,
  whitespace-only → `ValueError`)
- `promote` raises `ProposalStateError` on non-pending proposals
- `reopen` only works on rejected; raises on pending or approved
- `UpdateFact` produces correct time-travel shape in KG
  (`as_of=<before effective_date>` returns old value; after returns
  new)
- `OpenQuestion` facts never write to KG even when part of approved
  proposal
- Full lifecycle (created → rejected → reopened → approved) emits
  4 observable events; final proposal has `rejection_count=1` and
  full decision_history chain

**Deferred:**
- Curated page rewrites (compiled-truth regeneration) — that's a
  workflow-agent job, not promotion's
- Scope widening check — `can_propose` is called in the skill but
  the pipeline itself doesn't enforce scope (the skill is the gate)
- Live host-agent integration — the `AskUserQuestion` surface lives
  with the host, not Memory Mission

**Next:** Step 11 — firm-artefact backfill skill. Pull from Drive /
SharePoint / memos via Composio into firm-plane staging. Routes
through the same promotion pipeline, solving Emile's authority
problem for cold-start firm knowledge. Can ship in parallel with
Granola backfill (same pattern as Gmail skill, different connector
+ target plane).

---

## Step 11: Firm-Artefact Backfill + Parallel Granola — DONE (2026-04-22)

**Goal:** Solve Emile's authority problem for cold-start firm
knowledge — institutional truth seeded from firm-authored documents
(memos, decks, training docs, quarterly updates), not from one
employee agent's extracted opinions. Plus the parallel Granola
backfill skill for personal-plane meeting transcripts (Step 5's
connector finally gets a workflow on top of it).

One commit covers both — pattern is well-established at this point;
both skills clone backfill-gmail's shape with different connector +
target plane.

**Files created:**
- `src/memory_mission/ingestion/connectors/drive.py`:
  - `make_drive_connector(client=None)` factory using the existing
    `ComposioConnector` adapter pattern from Step 5
  - `DRIVE_ACTIONS`: `list_files` (optional folder/mime/modified-since
    filters), `get_file` (one file by id; Composio handles Google
    Docs export to markdown server-side)
  - `_drive_preview` formats `name | mime — body[:400]` for the
    PII-scrubbed audit trail
- `skills/backfill-granola/SKILL.md` — parallel personal-plane
  backfill (employee_id required, target_plane="personal", source
  "granola"). Uses the existing Granola connector from Step 5.
- `skills/backfill-firm-artefacts/SKILL.md` — firm-plane cold-start
  backfill via Drive (no employee_id, target_plane="firm", source
  "drive"). Three explicit governance guardrails baked into the
  skill text:
  1. Administrator-only: regular employees should not run this.
  2. Reviewer at the merge gate is separate from the administrator
     who pulled the source — preserves audit-trail value.
  3. Source-folder discipline: backfill from the firm's curated
     knowledge folders, not sandboxes.

**Files modified:**
- `src/memory_mission/ingestion/connectors/__init__.py` — exports
  `make_drive_connector` + `DRIVE_ACTIONS`
- `tests/test_connectors.py` — 5 new Drive tests
- `skills/_index.md` — entries for both new skills
- `skills/_manifest.jsonl` — entries for both new skills

**Verification:**
- [x] `pytest` — 439/439 passed (5 new + 434 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 52 files
- [x] Registry-integrity tests still green with two new skills

**Key invariants enforced by tests:**
- `make_drive_connector()` returns a `ComposioConnector` with name
  "drive" and exactly the two `DRIVE_ACTIONS`
- Drive preview includes file name + mime type + body snippet
- Empty-payload preview collapses cleanly to empty string (no header
  dash artifact)
- Drive connector raises `NotImplementedError` until a Composio
  client is injected — same stub-on-demand contract as Gmail/Granola

**Architecture: V1 ingestion surface complete.** Three connectors
(Gmail / Granola / Drive), three pull-and-stage skills (one per
connector + plane combination):

```
backfill-gmail            → staging/personal/<emp>/gmail/
backfill-granola          → staging/personal/<emp>/granola/
backfill-firm-artefacts   → staging/firm/drive/
```

All three feed the same downstream:
1. `extract-from-staging` reads each, runs the host LLM, writes
   `ExtractionReport` to `.facts/`
2. The promotion-pipeline skill (Step 10) bundles facts into
   proposals
3. `review-proposals` surfaces them for human approval with rationale
4. Approved proposals apply to the firm's `KnowledgeGraph` with full
   provenance chain (source closet + source file + reviewer +
   rationale + timestamps)

**Deferred:**
- SharePoint connector (similar adapter, can clone Drive when needed)
- Microsoft Calendar / Google Calendar connectors (when meeting-prep
  workflow needs them)
- Otter.ai (post-V1 per Step 5 scope)
- Per-folder access control on the Drive connector (V1 trusts the
  administrator running the skill to choose appropriate folders)

**Next:** Step 12 — CRM output channel. `Proposal.target` field
gains `"crm"` vs `"firm_knowledge"` distinction; differentiated
gates per target (CRM auto-merge when confidence threshold + scope
permits + non-contradiction; firm_knowledge always human-reviewed).
`skills/push-to-crm/SKILL.md` writes approved CRM proposals via
Composio.

Or jump to Step 13 — first workflow agent skill (meeting-prep) —
which closes the end-to-end loop and proves V1 is shippable.

---

## Step 12: Per-Employee Brain — Four-Layer Personal Plane — DONE (2026-04-22)

**Goal:** Extend the personal plane from "curated pages + staging" to
a real per-employee brain. The user's framing: how we work at the
individual level is structurally different from the firm system, and
the personal side needs working / episodic / semantic / preferences /
lessons layers — same shape as agentic-stack adapted to firm context.
GBrain and Supermemory both ship strong per-person brains; we should
too.

Two sub-commits.

### Step 12a — semantic/ layer in personal-plane paths (commit `9f3cb85`)

- `src/memory_mission/memory/schema.py`:
  - `page_path("personal", domain, slug, employee_id="alice")` →
    `personal/alice/semantic/<domain>/<slug>.md` (was
    `personal/alice/<domain>/<slug>.md`)
  - `raw_sidecar_path` similarly gets `semantic/` inserted
  - New `curated_root(plane, employee_id=None)` helper
  - Firm plane unchanged (firm is purely curated, no per-layer split)
- `tests/test_memory.py` — 3 new tests for `curated_root`,
  2 path tests updated
- 442/442 passed (3 new + 439 previous)

### Step 12b — personal_brain package (this commit)

New package at `src/memory_mission/personal_brain/` with four modules,
each shipping a primitive for one layer:

- `working.py` — `WorkingState` Pydantic + `read_/write_working_state`
  + `archive_stale` helper. Stored as `working/WORKSPACE.md` with
  YAML frontmatter (employee_id, updated_at, focus) + an "## Open
  items" bullet section + free-form body. Vault-friendly so users
  can hand-edit in Obsidian. Default 2-day archive threshold per
  agentic-stack convention.
- `episodic.py` — `AgentLearning` Pydantic + `EpisodicLog` (append /
  all / top_k / filter) + `record_learning` convenience function.
  Stored as `episodic/AGENT_LEARNINGS.jsonl`, append-only.
  `top_k` ranks by salience using the existing
  `memory.salience.salience_score()` formula.
- `preferences.py` — `Preferences` Pydantic + `read_/write_/update_`
  helpers. Stored as `preferences/PREFERENCES.md` (frontmatter +
  body). Typed core fields (name / timezone / communication_style /
  explanation_style / test_strategy) with `extra="allow"` for firm-
  specific custom keys. `update_preferences` merges into existing
  state without clobbering.
- `lessons.py` — `Lesson` Pydantic + `LessonsStore` (append /
  all / filter / render). Two files: `lessons.jsonl` is the source
  of truth (append-only, deterministic `lesson_id` from rule text →
  idempotent appends), `LESSONS.md` is rendered from it on every
  append (newest-first, never hand-edit).
- `personal_brain/__init__.py` — public exports.

**Files modified:**
- `README.md` — new "Open the personal plane in Obsidian" section
  documents the four-layer layout + safety-hatch UX positioning

**Verification (both sub-commits):**
- [x] `pytest` — 472/472 passed (33 new + 439 previous)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 57 files
- [x] All four layer paths land under `personal/<employee_id>/`
  exactly where the schema docstring says they should
- [x] Round-trip serialization for each Pydantic model
- [x] Archive-stale moves WORKSPACE.md only when older than threshold
- [x] Salience-ranked top_k surfaces high-pain recent recurring
  entries before old neutral ones
- [x] Preferences extras (firm-specific keys) round-trip cleanly
- [x] Lessons render orders newest-first; idempotent append by rule
  text; rejects empty rule/rationale; confidence bounded [0, 1]

**Why this matters:** workflow agents (Step 15+) will read the personal
brain to answer "what does Alice know about this client" before
drafting anything. Without the four-layer split, the agent sees only
curated pages — no working state, no episodic context, no preferences,
no learned lessons. With it, the personal plane behaves like a real
agent brain.

**Pairs with deferred work:**
- Bayesian corroboration on the firm KG (Step 13) — when the same
  triple is re-extracted, bump confidence rather than insert
  duplicates
- Federated cross-employee pattern detector (Step 14) — admin skill
  that scans personal planes (now richer with the four layers) and
  surfaces "the same fact appears across N employees" as firm-plane
  proposal candidates

**Deferred:**
- Onboarding wizard for `PREFERENCES.md` (manual edit fine for V1)
- Skill that consumes `top_k` episodic entries before agent decisions
  (will land naturally in Step 15 workflow agents)
- Cross-employee promotion suggestion (Step 14 federated detector)

**Next:** Step 13 — Bayesian corroboration on the KG. New
`KnowledgeGraph.corroborate()` op that bumps confidence + appends
sources when an extraction matches an existing currently-true triple.
`promote()._apply_facts` checks for existing matches before adding.
Independent-evidence formula `new_conf = 1 - (1 - old) * (1 - new)`,
capped at 0.99 so nothing reaches certainty without human review.
Time-decay multiplier on old confidence using `salience_score`'s
recency curve.

---

## Step 13 — Bayesian corroboration

**Goal.** Re-extracting the same fact from a new source should
strengthen belief, not duplicate a row. The firm knowledge graph
starts behaving like a Bayesian posterior over independent evidence,
not an append-only claim log.

**Files added:** none (extends existing modules).

**Files modified:**
- `src/memory_mission/memory/knowledge_graph.py`
  - `CORROBORATION_CAP = 0.99` module constant — no auto-certainty
  - `Triple.corroboration_count: int = 0` field on existing model
  - `TripleSource` Pydantic model for provenance rows
  - New `triple_sources` table with `ON DELETE CASCADE` on triples
  - `add_triple` now seeds one `triple_sources` row per insert so
    every triple has at least one provenance entry
  - `find_current_triple(s, p, o)` helper — returns the matching
    currently-true triple or `None`
  - `corroborate(s, p, o, *, confidence, source_closet, source_file)`
    applies Noisy-OR (`1 - (1 - old) * (1 - new)`), caps at 0.99,
    appends to `triple_sources`, increments `corroboration_count`;
    returns `None` when no currently-true match exists
  - `triple_sources(s, p, o)` query returns full provenance history
    oldest-first
  - `_run_migrations()` adds the `corroboration_count` column to DBs
    that predate this schema version
  - `_row_to_triple` defends against the missing column when reading
    pre-migration rows
- `src/memory_mission/promotion/pipeline.py`
  - `_add_or_corroborate()` helper — checks for match, corroborates
    or adds
  - `_apply_facts` now routes `RelationshipFact` / `PreferenceFact` /
    `EventFact` / `UpdateFact` (new side) through the helper so
    re-promotions bump confidence instead of duplicating
  - `UpdateFact.supersedes_object` still invalidates the prior value
    before the new-side corroboration check
- `src/memory_mission/memory/__init__.py` — exports
  `CORROBORATION_CAP` + `TripleSource`
- `tests/test_knowledge_graph.py` — 17 new tests covering the
  corroboration math, cap, source history, invalidate interaction,
  persistence
- `tests/test_promotion.py` — 4 new tests covering the pipeline
  integration (same-fact-twice corroborates, distinct facts add,
  preferences corroborate, audit trail preserved) + adapted
  `test_promote_provenance_carries_source_closet` to the new
  semantics (one triple, two sources in history)

**Verification:**
- [x] `pytest` — 493/493 passed (21 new since Step 12b)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 57 source files
- [x] Two independent sources at 0.9 confidence combine via
      Noisy-OR to 0.99 (the cap)
- [x] Corroborate on invalidated (past) triple returns `None` — no
      zombie updates
- [x] Corroborate preserves triple row identity (no duplicates)
- [x] `triple_sources` returns all sources oldest-first with
      confidence-after-corroboration monotonically non-decreasing
- [x] Migrations run on fresh and pre-migration DBs

**Architectural significance.**

- **First Bayesian primitive in the KG.** Until now a triple's
  confidence was whatever the first extraction claimed. Now it
  reflects accumulated independent evidence. Agents asking
  "how sure is the firm?" get a meaningful answer.
- **Preserves Emile's provenance-mandatory rule.** Every
  corroboration appends to `triple_sources` — no confidence change
  without a source. Every source file traceable back through
  `ExtractionReport` → `Proposal` → `decision_history`.
- **0.99 cap on agent-path corroboration.** The full-certainty
  confidence (1.0) remains reachable only through a caller
  explicitly passing `confidence=1.0` on `add_triple` (human-in-
  the-loop override). Accumulated agent evidence never gets there.
- **Unblocks Step 16 (federated detector).** When the detector
  sees the same fact across N employees' planes, each detection
  corroborates through the same pipeline — confidence climbs toward
  the cap as evidence piles up, but never becomes silent truth.

**Deferred (intentionally out of scope):**
- Time-decay multiplier on old confidence (recency curve per
  `salience_score`). Current implementation treats all independent
  evidence equally. Worth adding once we have real staleness data,
  not now.
- Corroboration-aware `CoherenceWarningEvent` — lands naturally
  with Step 15 tier work.
- Multi-plane corroboration UI in `review-proposals` skill —
  current skill already surfaces confidence; exposing the
  corroboration history in chat review can be a small follow-up.

**Pairs with Steps 14 / 15 / 16.**
- Step 14 (identity resolution) makes corroboration *more*
  accurate: stable person IDs collapse "alice@acme" and
  "alice-smith" into one node, so the same fact extracted under
  different entity strings actually corroborates instead of
  fragmenting.
- Step 15 (doctrine tier) composes: corroboration math runs at
  every tier; a fact corroborated at `policy` tier may eventually
  warrant promotion to `doctrine` — that's Step 18+'s legislative
  cycle.
- Step 16 (federated detector) is the volume source for
  corroboration. N employees independently extracting the same
  fact → N corroborations through the pipeline.

**Next:** Step 14 — Identity resolution layer. `IdentityResolver`
Protocol + `LocalIdentityResolver` default (email-based dedup + fuzzy
name match) + `KnowledgeGraph.merge_entities(a, b)` with reviewer
gate + extraction-side canonicalization so `ExtractedFact` subjects
resolve to stable `PersonID` / `OrgID` at `ingest_facts` time. Same
adapter pattern as Composio connectors: ship the Protocol + local
impl, host agents wire Graph One / Clay / firm-custom resolvers
later. Precedes Step 15 (tier) because tier pages depend on stable
entity keys — doing tier first would force a migration later when
identity lands.

---

## Step 14 — Identity resolution layer

**Goal.** Stop the LLM's noisy entity names (`alice-smith`, `a-smith`,
`alice-at-acme`) from fragmenting the KG. Anchor every person and org
to a stable ID that persists across channels, job changes, and
extractions from different sources. Unblocks Step 16 (federated
detector must collapse Alice-via-Alice's-plane and Alice-via-Bob's-
plane) and Step 17 (meeting-prep needs aggregated relationship
history).

Shipped as three atomic sub-commits.

### 14a — IdentityResolver Protocol + LocalIdentityResolver

**Files added:**
- `src/memory_mission/identity/__init__.py` — package exports.
- `src/memory_mission/identity/base.py` — ``IdentityResolver``
  Protocol (``resolve`` / ``lookup`` / ``bindings`` /
  ``get_identity``), ``Identity`` Pydantic model, ``EntityKind``
  literal, ``IdentityConflictError``, ``parse_identifier`` helper,
  ``make_entity_id`` (``p_<token>`` / ``o_<token>``).
- `src/memory_mission/identity/local.py` — SQLite-backed
  ``LocalIdentityResolver``. Two tables (``identities``,
  ``identity_bindings``), per-firm isolation via DB path.
- `tests/test_identity.py` — 27 tests: parse / make, happy-path
  resolve, binding propagation, conflict detection + DB-unchanged-
  on-conflict, persistence, per-firm isolation.

**Policy:** exact match on ``type:value`` identifiers only
(``email:...``, ``linkedin:...``, ``domain:...``, ``phone:...``,
``twitter:...``). No fuzzy name matching in V1 — too easy to merge
unrelated "John Smith" records. Conservative by design:
false-negatives are recoverable via ``merge_entities`` (14b);
false-positives are expensive to unwind.

**Behavior:**
- First resolve creates an identity and binds all given identifiers.
- Later resolve with any overlapping identifier returns the same
  ID and binds any new identifiers.
- resolve with identifiers spanning different existing identities
  raises ``IdentityConflictError`` — caller decides whether to
  merge or abort.

### 14b — KnowledgeGraph.merge_entities

**Files modified:**
- `src/memory_mission/memory/knowledge_graph.py`
  - `MergeResult` Pydantic model.
  - New `entity_merges` SQLite table with reviewer / rationale /
    triples_rewritten audit fields.
  - `merge_entities(source, target, *, reviewer_id, rationale)`
    rewrites `triples.subject` and `triples.object` from source to
    target, deletes the source entity row, records the event.
    Atomic transaction.
  - `merge_history(entity_name)` returns every merge where the entity
    appears as source or target (oldest-first).
- `src/memory_mission/memory/__init__.py` — exports `MergeResult`.
- `tests/test_knowledge_graph.py` — 11 new tests: subject rewrites,
  object rewrites, source deleted, provenance preserved, audit row,
  empty rationale rejected, source==target rejected, persistence.

**Notes:**
- `triple_sources` provenance rows are UNTOUCHED by the rewrite.
  They key by ``triple_id``, so the full source chain survives any
  number of merges.
- NO automatic dedup of triples that collapse into duplicates after
  the rewrite (e.g., both source and target had ``works_at acme``).
  Separate concern — can land later once federated detection
  surfaces the volume.

### 14c — Extraction-side canonicalization

**Files modified:**
- `src/memory_mission/extraction/schema.py`
  - `IdentityFact.identifiers: list[str] = []` — opt-in hook for the
    LLM to emit typed identifiers alongside each entity mention.
- `src/memory_mission/extraction/ingest.py`
  - `ingest_facts(..., identity_resolver=None)` new optional kwarg.
  - When resolver is provided: `_canonicalize_report` builds a
    `raw_name -> stable_id` map from each IdentityFact's identifiers,
    then `_rewrite_fact_names` copies every fact in the report to use
    the resolved IDs. The canonicalized report is what lands in
    staging.
  - `_resolver_entity_kind(entity_type)` maps the schema's free-form
    `entity_type` to the resolver's `person` / `organization` literal.
    Conservative default: anything outside `{organization, company,
    firm, org}` resolves as person.
- `tests/test_extraction.py` — 11 new tests + updated `_identity`
  helper to forward `identifiers`. Covers: optional field, backcompat
  (no resolver = no rewrite), IdentityFact rewrite, cross-fact
  rewrite (relationship / preference / event / update), org prefix,
  entities-without-identifiers stay raw, cross-report collapse to
  same stable ID, mention tracker counts canonical ID.

**Backwards compatibility:** Reports without `identifiers` on their
IdentityFacts flow through untouched — canonicalization is opt-in per
entity. Existing pre-Step-14 reports still work.

### Combined numbers

- `pytest` — 541/541 passed (49 new since Step 13):
  27 identity + 11 merge_entities + 11 canonicalization
- `ruff check` + `ruff format --check` clean
- `mypy src/` strict, no issues in 60 source files

### Architectural significance

- **First identity layer in Memory Mission.** Until now every entity
  mention the LLM emitted was a free string, and the same person
  fragmented into multiple KG nodes. Step 14c makes the stable ID
  the canonical name in the staged report, so the promotion
  pipeline, corroboration, and federated detection all speak in
  stable IDs from Step 14c onward.
- **Adapter pattern held.** Same shape as Composio connectors: ship
  the Protocol and a local default; Graph One / Clay / firm-custom
  resolvers plug in by satisfying `IdentityResolver`. No external
  SDK imports in our code.
- **Reviewer gate on merge.** `merge_entities` requires rationale,
  records reviewer — same discipline as `promote()`. Identity is
  governed the same way knowledge is.
- **Pairs with Step 13 (corroboration).** Before Step 14,
  re-extracting "alice-smith" and "a-smith" as the same person
  created two triples that corroboration couldn't collapse because
  the subjects differed. With Step 14c, both resolve to the same
  `p_<id>` and corroboration naturally aggregates evidence across
  extractions.

### Deferred (intentionally)

- **Fuzzy name match.** Doable but risky. Ship when we have real
  data and can measure false-positive rate.
- **Graph One / Clay adapters.** Written as a Protocol so they slot
  in cleanly when a pilot firm asks. No stub in V1 — adapter-pattern
  precedent held without adding dead code.
- **Auto-merge on corroboration.** When federated detection produces
  high-confidence evidence that two existing entities are the same
  person, the system could suggest a merge. Current flow requires
  the human to call `merge_entities` directly. Ship the signal /
  proposal shape later.
- **Entity_type aliasing.** Only `organization`, `company`, `firm`,
  `org` map to the resolver's `organization` kind. Add more terms to
  `_ORGANIZATION_TYPES` as needed.

**Next:** Step 15 — Doctrine tier + coherence check. Add
`tier: Literal["constitution", "doctrine", "policy", "decision"]`
(default `decision`) to `PageFrontmatter` and `Triple`. Advisory
coherence check in `promote()` when a new fact touches an entity
already described at a higher tier — warning surfaces in
`decision_history`. `BrainEngine.query()` accepts optional
`tier_floor`. Opt-in constitutional-mode flag in firm policy makes
the coherence check blocking instead of advisory.

---

## Step 15 — Doctrine tier + coherence check

**Goal.** Maciek's constitutional frame as infrastructure: every
fact in the KG (and every Page in the vault) now carries a
doctrinal tier, and the promotion pipeline surfaces warnings when
a new fact contradicts existing doctrine. Advisory by default;
firms that want stricter governance opt into blocking mode via a
firm-policy flag.

Shipped as two atomic sub-commits.

### 15a — Tier storage + filtering

**Files added:**
- `src/memory_mission/memory/tiers.py` — new module with
  `Tier = Literal["constitution", "doctrine", "policy", "decision"]`,
  ordinal helpers (`tier_level`, `is_above`, `is_at_least`), and
  `DEFAULT_TIER = "decision"`.

**Files modified:**
- `src/memory_mission/memory/pages.py` — `PageFrontmatter.tier` field
  (default `"decision"`). `new_page()` accepts an optional `tier`.
- `src/memory_mission/memory/knowledge_graph.py`:
  - `Triple.tier` Pydantic field (default `"decision"`).
  - New `tier TEXT NOT NULL DEFAULT 'decision'` column on the
    `triples` table, with migration hook in `_run_migrations()`.
  - `add_triple` accepts an explicit `tier` kwarg; default is
    decision so every existing call site continues to work.
  - `_row_to_triple` reads the column defensively — rows from
    pre-migration DBs fall back to "decision".
- `src/memory_mission/memory/engine.py` — `BrainEngine.search` and
  `BrainEngine.query` accept optional `tier_floor`. Filter uses
  `is_at_least(page.frontmatter.tier, tier_floor)`. `None` (the
  default) keeps backwards-compatible behavior.
- `src/memory_mission/memory/__init__.py` — exports tier types +
  helpers.
- `tests/test_knowledge_graph.py` — 10 new tests: tier ordering,
  helpers, default triple tier, explicit tier persisted, corroborate
  preserves tier, merge preserves tier, migration adds column.
- `tests/test_memory.py` — 4 new tests: PageFrontmatter default,
  `new_page` accepts tier, render/parse round-trip preserves tier,
  `query(tier_floor=...)` filters correctly, `search(tier_floor=...)`
  filters correctly.

**Semantics:**
- **Default is `decision`.** Every existing call site — connectors,
  extraction, promotion, tests — continues unchanged.
- **Corroboration preserves tier.** Re-extracting a decision from
  three sources bumps confidence; it never silently promotes to
  doctrine. Tier changes must be deliberate editorial acts.
- **Merge preserves tier.** `merge_entities` rewrites
  subject/object strings; it does not touch tier.

### 15b — Coherence warnings + observability + policy flag

**Files added:**
- `tests/test_coherence.py` — 15 new tests. Covers the deterministic
  layer (`KnowledgeGraph.check_coherence`), the advisory promotion
  path (warning logged, facts still apply), and constitutional-mode
  blocking (CoherenceBlockedError, proposal stays pending, KG
  untouched). UpdateFact's `supersedes_object` is correctly
  excluded from the scan.

**Files modified:**
- `src/memory_mission/memory/knowledge_graph.py`:
  - `CoherenceWarning` Pydantic model with structured fields:
    `subject` / `predicate` / `new_object` / `new_tier` /
    `conflicting_object` / `conflicting_tier` / `conflict_type`.
    `higher_tier` and `lower_tier` computed properties.
  - `check_coherence(subject, predicate, obj, *, new_tier)` method
    returns `list[CoherenceWarning]`. Deterministic — no LLM.
    Finds currently-true triples on the same `(subject, predicate)`
    with a different `object`. Ignores invalidated triples. Returns
    empty list for corroboration (same object).
- `src/memory_mission/memory/__init__.py` — exports
  `CoherenceWarning`.
- `src/memory_mission/observability/events.py`:
  - New `CoherenceWarningEvent` type joined to the discriminated
    `Event` union. Fields mirror `CoherenceWarning` plus
    `proposal_id` and `blocked: bool`.
- `src/memory_mission/observability/api.py`:
  - `log_coherence_warning()` helper that writes the event under
    the current observability scope.
- `src/memory_mission/observability/__init__.py` — exports the new
  event type and log helper.
- `src/memory_mission/permissions/policy.py`:
  - `Policy.constitutional_mode: bool = False` flag. Off by default;
    firms that want strict doctrinal governance opt in.
- `src/memory_mission/promotion/pipeline.py`:
  - New `CoherenceBlockedError` exception carrying the full
    `warnings: list[CoherenceWarning]` list for UI display.
  - `promote()` accepts an optional `policy: Policy | None` kwarg.
  - `_apply_facts` now runs a two-pass scan: (1) `_coherence_scan`
    collects every warning via `kg.check_coherence`, each one logs
    via `log_coherence_warning`; (2) if `policy.constitutional_mode`
    is True and warnings exist, raise `CoherenceBlockedError` BEFORE
    writing anything (KG stays untouched, proposal stays pending).
    Advisory path logs warnings and proceeds with the writes.
  - `UpdateFact`'s `supersedes_object` is filtered from the scan so
    a valid supersession does not fire a false coherence warning.
- `src/memory_mission/promotion/__init__.py` — exports
  `CoherenceBlockedError`.
- `skills/review-proposals/SKILL.md` — new forcing-question entry
  for coherence warnings and a line under the "where state changes"
  section documenting the `CoherenceWarningEvent` log row.

**Verification:**
- [x] `pytest` — 570/570 passed (29 new since Step 14c):
  14 tier storage + 15 coherence
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 61 source files
- [x] Same (subject, predicate, object) is corroboration, not
      conflict — no warning fires
- [x] Invalidated triples never produce a warning
- [x] Multiple currently-true triples on the same (subject,
      predicate) each produce their own warning
- [x] Advisory mode: warning is logged, facts still land, both
      conflicting rows remain currently true (reviewer cleanup is
      a deliberate follow-up)
- [x] Constitutional mode: `CoherenceBlockedError` raised, KG
      untouched, proposal stays pending
- [x] `UpdateFact` with `supersedes_object` does not fire a
      coherence warning on the replaced value
- [x] PreferenceFact and RelationshipFact both participate in the
      check (predicate `prefers` / arbitrary predicate)

**Architectural significance.**

- **Structured, deterministic, eval-friendly.** `CoherenceWarning`
  is a Pydantic record; `CoherenceWarningEvent` is a first-class
  observability event. The stream of warnings is the corpus
  section 2.7 of `docs/EVALS.md` asks for — no LLM judge, no
  Likert, binary yes/no per (subject, predicate, new_object) pair.
- **Default safe, opt-in strict.** `constitutional_mode=False` is
  the default, so every existing test and every firm that hasn't
  opted in keeps the same promotion behavior. Firms that want
  Maciek-style legal governance flip the flag.
- **Block before write.** When strict mode triggers, `_apply_facts`
  raises BEFORE any KG write. No partial state; the proposal is
  still available for the reviewer to resolve.
- **Pairs with Step 14 (identity resolution).** Coherence checks
  run on stable IDs post-canonicalization, so "alice-smith works_at
  acme" and "a-smith works_at beta" correctly surface as a conflict
  on the same resolved Person ID rather than slipping past the scan
  as two unrelated subjects.
- **Pairs with Step 13 (corroboration).** Corroboration is the
  same-object path; coherence is the different-object path. Every
  triple-like fact takes exactly one of the two branches.

**Deferred (intentionally out of scope):**
- **Tier promotion op.** A dedicated `retier_triple` with reviewer
  gate lets a human promote a `decision` to `policy` / `doctrine`.
  Not needed in V1 — reviewers can add a new triple at a higher
  tier directly.
- **Distillation coherence** (eval doc 2.7's LLM-judge layer) —
  this is Step 17 territory, once `compile_agent_context()` ships.
- **Automatic conflict resolution** (e.g., "higher tier wins, auto-
  invalidate lower") — too opinionated for V1. Advisory warnings +
  reviewer judgment is the safer starting point.
- **Tier-aware `review-proposals` ranking** — currently the skill
  ranks by rejection_count / enrichment tier; a follow-up can add
  "constitution-tier conflicts first." Documented in the
  self-rewrite footer.

**Next:** Step 16 — Federated cross-employee pattern detector.
Admin-only skill that scans personal planes, collapses across
resolved person IDs (thanks to Step 14), and produces tier-aware
firm proposals when N ≥ threshold employees independently assert
the same fact. Coherence checks from Step 15 run on every
detector-generated proposal, so cross-plane aggregation cannot
silently overwrite firm doctrine.

---

## Step 16 — Federated cross-employee pattern detector

**Goal.** When N employees independently arrive at the same fact,
it is high-signal evidence the fact belongs to the firm, not just
to each individual. Close the federated loop by detecting these
patterns across personal planes and feeding them through the same
PR-model review that governs every other firm-plane write.

**The dominant failure mode** (per `docs/EVALS.md` 2.6): firing
on three employees ingesting THE SAME Granola transcript. The
detector defends with an independence check — N distinct
`source_file` values required, not just N distinct employees.

**Files added:**
- `src/memory_mission/federated/__init__.py` — package exports.
- `src/memory_mission/federated/detector.py` — core logic:
  - `CandidateSource` Pydantic record (source_closet, source_file,
    triple_id, confidence).
  - `FirmCandidate` Pydantic record (subject, predicate, object,
    tier, distinct_employees, distinct_source_files,
    contributing_sources, confidence). `to_relationship_fact()`
    helper that builds a reviewer-readable `RelationshipFact` with
    a structured support_quote.
  - `detect_firm_candidates(kg, *, min_employees=3, min_sources=3)`
    — deterministic SQL-backed scan. Filters by
    `source_closet LIKE 'personal/%'`, groups by triple, thresholds
    on BOTH distinct employees AND distinct source files. Result is
    sorted `(-distinct_employees, -confidence, subject, predicate,
    object)` so reviewers see strongest signals first.
  - `propose_firm_candidate(candidate, *, store, ...)` — turns a
    candidate into a pending `Proposal` via the normal
    `create_proposal` path. Idempotent (deterministic proposal_id
    means re-running the scan is a no-op on previously-staged
    candidates). Source path is `federated-detector://` so the
    origin is visible in the audit log.
  - `aggregate_noisy_or(confidences)` utility — matches the KG's
    `CORROBORATION_CAP` (0.99) semantics; exposed for tests and
    future callers but NOT used inside the detector (the triple's
    current confidence already reflects corroboration).
- `skills/detect-firm-candidates/SKILL.md` — admin-only workflow
  skill with forcing questions for threshold changes, identity
  confidence, already-firm fact, and high rejection churn.
- `tests/test_federated.py` — 21 new tests covering the eval
  doc's scenario categories: true firm pattern fires,
  shared-single-source does NOT fire, below-threshold does NOT
  fire, distinct groups ranked independently, firm-plane triples
  excluded, invalidated triples excluded, end-to-end
  detect→propose→promote path corroborates with firm source, and
  constitutional-mode blocks contradictions.

**Files modified:**
- `src/memory_mission/memory/knowledge_graph.py` — new
  `scan_triple_sources(*, closet_prefix, currently_true_only)`
  method that joins `triples` with `triple_sources` and returns
  plain dicts. Kept as list-of-dicts (not Pydantic) so detectors
  can group with cheap dict ops.
- `skills/_index.md` and `skills/_manifest.jsonl` — registry entry
  for the new skill.

**Verification:**
- [x] `pytest` — 591/591 passed (21 new in `test_federated.py`)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 63 source files
- [x] Three employees on one `source_file` → detector returns
      empty list (independence check holds)
- [x] Three employees on three source_files → candidate fires
      with correct distinct counts
- [x] Two employees → no fire regardless of source count
- [x] Multiple groups in one scan → each independently evaluated;
      candidates ranked by distinct_employees desc, confidence
      desc
- [x] `propose_firm_candidate` is idempotent — re-running returns
      the same pending proposal instead of duplicating
- [x] End-to-end detect→propose→promote path corroborates the
      existing personal-plane triple with `source_closet='firm'`,
      bumping confidence (no duplicate triple row)
- [x] Constitutional-mode firm: federated proposal conflicting
      with existing firm doctrine raises `CoherenceBlockedError`,
      proposal stays pending

**Architectural significance.**

- **The federated learning loop is now concrete in code.** Three
  employees independently assert a fact via three different source
  documents → detector surfaces a candidate → admin proposes →
  reviewer approves → firm-plane corroboration with full
  provenance. No employee's agent unilaterally speaks for the firm.
- **Step 14 + 15 compose cleanly.** Identity resolution
  canonicalizes the entity names the detector groups by (so
  `alice-smith` and `a-smith` collapse before thresholding). The
  coherence check on the generated proposal means
  cross-employee aggregation cannot silently overwrite firm
  doctrine in constitutional-mode firms.
- **Deterministic grader per `docs/EVALS.md` P7.** The detector is
  pure SQL + Python set ops. The tests double as the seed corpus
  for the section-2.6 eval recipe; adding more scenarios is a
  copy-paste of a test function.
- **Admin-boundary respected.** The skill declares `administrator-
  run only` in its frontmatter; the module itself is a pure
  library. Permission enforcement is host-agent responsibility.

**Deferred (intentionally):**
- **Threshold auto-tuning.** Per eval doc: monitor the precision
  of approved-vs-total federated proposals; if it drops, raise
  the threshold. Ship once we have real rejection signals.
- **Tier inference beyond max.** The detector uses the highest
  tier seen across contributing personal-plane triples. A future
  refinement could weight by recency or confidence delta.
- **50-scenario eval harness.** Test fixtures today cover the
  key categories. The full 50-case eval per section 2.6
  recipe lands as a follow-up, ideally against real anonymized
  production-like data.
- **Cross-firm patterns.** Detector is strictly per-firm. Firm-
  of-firms aggregation (e.g., "all our portfolio companies
  believe X") is out of V1 scope.

**Pairs with Step 17 (meeting-prep).** Once cross-employee
patterns are corroborated to firm plane, meeting-prep's
`compile_agent_context` can pull authoritative firm doctrine +
corroborated decisions for any attendee, using the stable
person IDs identity resolution produced.

**Next:** Step 17 — Meeting-prep workflow agent. Closes the V1
synthesis loop. `compile_agent_context(role="meeting-prep",
task=<client>)` builds a distilled doctrine package: constitution
+ relevant doctrine + relevant policy + matched decisions, scoped
to the attendees via stable person IDs. Skill wraps it. This is
the first workflow-level consumer of the whole stack — extraction
→ promotion → corroboration → identity → tier → federated
detection all contribute to what the brief says.

---

## Step 16.5 — SQL-over-KG read primitive

**Goal.** Let workflow agents and eval scripts answer open-ended
questions about the KG without adding a bespoke method for each
one. "Every doctrine-tier triple touched in the last 30 days by
more than 2 employees" should be one SELECT, not a feature request.

Inspired by Ricky's observation that serious agents benefit from
SQL access AND graph-shaped queries over the same data. Google
Spanner Graph does this natively; we get the SQL half on SQLite
with one method.

**Files modified:**
- `src/memory_mission/memory/knowledge_graph.py`
  - `KnowledgeGraph.sql_query(query, params, *, row_limit=1000)` —
    read-only SQL over the existing tables (`entities`, `triples`,
    `triple_sources`, `entity_merges`).
- `tests/test_knowledge_graph.py` — 11 new tests covering: basic
  SELECT, parameterized queries, WITH / CTE support, INSERT /
  UPDATE / DELETE rejected, row limit enforcement, triple_sources
  JOIN.

**Safety model:**
- **Dedicated read-only connection** per call: opened as
  `file:<path>?mode=ro` via URI. The SQLite engine itself refuses
  any write, even if the string check misses something.
- **SELECT / WITH validation** as a nice error path (the engine
  is the real defense).
- **Row limit** (default 1000) prevents runaway results; overflow
  raises rather than silently truncating.
- **Parameterized placeholders** recommended via docstring;
  callers who f-string untrusted text get what they deserve — the
  library can't prevent that, but the RO connection limits blast
  radius.

**Verification:**
- [x] `pytest` — 602/602 passed (11 new)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 63 source files
- [x] INSERT, UPDATE, DELETE all rejected
- [x] CTE (WITH clause) works
- [x] Row limit raises on overflow

**Why this is ~20 LOC that changes capability:** workflow agents
(meeting-prep, future email-draft, deal-memo) can now answer
questions we didn't anticipate without us shipping a new method.
That matters specifically because the meeting-prep distilled
context is computed against the KG — the richer the query surface,
the smarter the context package.

---

## Docs: VISION + ARCHITECTURE + ABSTRACTIONS + first ADR

Three-doc architecture spine modeled on Tolaria's pattern, plus the
first ADR as a retroactive capture of Step 13's load-bearing
decisions. Future contributors and future-us can now understand
the current state without archaeology.

**Files added:**
- `docs/VISION.md` — why Memory Mission, problem, insight, method,
  10 design principles. Short enough to scan in a sitting.
- `docs/ARCHITECTURE.md` — current shipped state. Design principles
  first, system diagram, three-representations-one-authority rule,
  module-by-module walkthrough, stack, non-goals, concrete
  end-to-end data flow example.
- `docs/ABSTRACTIONS.md` — every Pydantic model + every predicate +
  every tier + every event type in one place. Reference for anyone
  writing against the library. Canonical predicate vocabulary and
  module-level constants table.
- `docs/adr/README.md` — ADR format, lifecycle, rules, index.
- `docs/adr/0001-bayesian-corroboration.md` — first ADR,
  retroactive for Step 13. Captures Noisy-OR vs alternatives,
  0.99-cap rationale, re-evaluation triggers.

BUILD_LOG remains the per-step narrative; the new docs are the
synthesis layer. BUILD_LOG answers "what happened at each step";
ARCHITECTURE answers "what is the system now"; ABSTRACTIONS
answers "what does each thing mean"; ADRs answer "why that choice
and not X."

---

## Step 17 — Meeting-prep workflow agent

**Goal.** Close the V1 synthesis loop. `compile_agent_context`
is the first workflow-level primitive that composes the full
stack — identity resolution, tier filtering, corroboration-aware
triples, coherence-checked facts, federated-aggregated beliefs
all contribute to what the host-agent LLM sees before drafting.

The shape follows Tolaria's Neighborhood-mode design (ADR-0069):
structured, grouped, empty categories visible, machine-inspectable
as well as renderable.

**Files added:**
- `src/memory_mission/synthesis/__init__.py` — package exports.
- `src/memory_mission/synthesis/context.py` — Pydantic models:
  - `AttendeeContext` — one attendee's neighborhood (outgoing /
    incoming / events / preferences / related pages). Properties:
    `fact_count`, `display_name`.
  - `DoctrineContext` — firm-authoritative pages at/above
    `tier_floor`, sorted highest-tier first.
  - `AgentContext` — top-level package. `role` / `task` / `plane` /
    `as_of` / `tier_floor` / attendees / doctrine / `generated_at`.
    Properties: `fact_count`, `attendee_ids`. `.render()` method
    produces markdown with inline provenance citations, empty
    groups explicitly shown as `(none on file)` so the LLM sees
    absence.
- `src/memory_mission/synthesis/compile.py` — the primitive:
  `compile_agent_context(role, task, attendees, kg, *, engine=None,
  plane="firm", employee_id=None, tier_floor=None, as_of=None,
  identity_resolver=None) -> AgentContext`. Read-only, idempotent,
  single-pass.
- `skills/meeting-prep/SKILL.md` — admin-facing workflow skill.
  Forcing questions for ambiguous attendee, empty context, tier
  floor choice, plane selection.
- `tests/test_synthesis.py` — 21 tests covering:
  - Empty KG → empty attendee (no crash)
  - Triple classification by predicate (`event` / `prefers` /
    other)
  - Superseded facts omitted (EVALS.md 2.8 criterion 3 — hard
    requirement, explicit test)
  - Events sorted newest-first (criterion 2)
  - Multiple attendees scoped independently (criterion 1)
  - IdentityResolver produces canonical name
  - `as_of` time-travel
  - Doctrine tier_floor filter (criterion 5)
  - No-engine, no-tier-floor paths
  - Rendering (role + task, empty groups, doctrine section,
    provenance citations, round-trip JSON)

**Files modified:**
- `skills/_index.md` and `skills/_manifest.jsonl` — meeting-prep
  registry entry.

**Verification:**
- [x] `pytest` — 623/623 passed (21 new)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict, no issues in 66 source files
- [x] Invalidated triples never appear in attendee context
- [x] Tier floor filters doctrine correctly; highest tier sorts first
- [x] Round-trip `AgentContext.model_dump_json()` →
      `model_validate_json()` preserves structure
- [x] Render produces markdown with every attendee, empty groups
      shown explicitly, provenance cited as
      `[source_closet/file]`

**Architectural significance.**

- **First workflow-level primitive.** Every prior step was
  infrastructure (extraction, promotion, KG, identity, tier,
  federated). Meeting-prep is the first thing a user actually
  *does* with the stack — and it shows every prior step earning
  its keep.
- **Structured over prose.** `AgentContext` is a frozen Pydantic
  tree. `.render()` is a convenience. The eval harness
  (`docs/EVALS.md` section 2.8) grades the structured form
  directly — no prose parsing, no judge drift.
- **Composable primitive, not monolithic workflow.** `role` is a
  free-form string — `"meeting-prep"` today, `"email-draft"` /
  `"deal-memo"` / `"crm-update"` later reuse the same
  `compile_agent_context` with different rendering.
- **Coherence enforcement inherited for free.** Triples that land
  in the context have already passed through Step 15's coherence
  check at promote time. The package cannot contain contradicting
  facts that were blocked in constitutional mode.
- **Identity resolution inherited for free.** Attendees referred
  to by stable `p_<id>` / `o_<id>` resolve once at prep time for
  canonical name; all triple queries run against the same stable
  IDs. No entity fragmentation in the rendered brief.

**V1 loop is now complete.** End-to-end traceable:

1. Connector pulls Gmail / Granola / Drive → staging.
2. `extract-from-staging` skill runs host LLM → `ExtractionReport` → `ingest_facts` (identity canonicalized) → fact staging.
3. `create_proposal` groups facts → `ProposalStore` pending.
4. `review-proposals` skill surfaces each proposal to a human → `promote()` → `_apply_facts` (coherence scan + corroborate-or-add) → KG updated with provenance.
5. `detect-firm-candidates` skill (admin) scans personal planes → stages firm-plane proposals for cross-employee patterns → back through review.
6. `meeting-prep` skill calls `compile_agent_context` → host LLM drafts against the rendered context → briefing.

Every write is reviewed. Every read is scoped. Every fact traces
back to a source file, a reviewer, and a rationale.

**Deferred:**
- **Richer page lookup.** V1's `_related_pages_for` matches by
  exact slug. Future: resolve `attendee_id` (stable ID) to
  wikilinked page via an `identity -> slug` map on page
  frontmatter.
- **Relationship-strength scoring** (eval doc 2.5 + 2.6). Derived
  view over interaction counts + recency + direction. Add when
  real interaction volume supports it.
- **Non-meeting roles.** `email-draft`, `deal-memo`, `crm-update`
  all reuse `compile_agent_context` — skill + rendering refinement
  per use case, not primitive changes.
- **Full 15-meeting eval set** per section 2.8. Fixture shape
  exists; populating with real meetings is a dogfood pass.

**V1 summary.**

17 steps shipped, 623 tests passing, mypy strict on 66 source
files. Three architectural frames composed: Keagan's CRM-like
system-of-record, Emile's governed PR-model promotion, Maciek's
constitutional-plus-identity frame. One eval strategy documented.
Six skills. Three doc pillars. One ADR.

The firm now has: governed institutional memory, stable person
IDs across channels, tiered doctrine with coherence enforcement,
cross-employee federated learning, and a distilled-context
primitive that turns the whole stack into a briefing.

Post-V1 roadmap (deferred per plan):
- Step 18: Legislative amendment cycle (batched promotions)
- Step 19: Constitution bootstrap skill (cold-start firm truth)
- Step 20: Relationship strength view + Graph One adapter
- Ongoing: 50-scenario federated eval harness, distillation
  coherence eval, threshold auto-tuning, MCP server surface for
  host-agent tools.

---

## V1 Polish Pass — six additive moves (post-Step 17)

**Goal.** Fold in high-value mechanisms from comparative reviews
(Tolaria, claude-obsidian, Google Knowledge Catalog, Ricky's
cloud-code agent stack) without changing the governance model.
All six moves are backwards-compatible; every new parameter
defaults to a behavior that matches the pre-polish state.

Approved via plan file `virtual-petting-tarjan.md`.

### Move 1 — Hot-cache hook recipe

**Files added:** `docs/recipes/personal-hot-cache.md`.

Hook recipe documenting how to wire host-agent `Stop` /
`SessionStart` / `PostCompact` / `PostToolUse` hooks around
`personal/<emp>/working/WORKSPACE.md`. Employee agent gets session-
persistent working memory without hand maintenance. Personal-plane
only — firm plane is never session-scoped, by design.

### Move 2 — Obsidian Bases dashboard + `reviewed_at`

**Files added:**
- `src/memory_mission/memory/templates/dashboard.base` — native
  Obsidian Bases YAML with five views.
- `docs/recipes/vault-dashboard.md` — install + customize guide.

**Files modified:**
- `src/memory_mission/memory/pages.py` — `PageFrontmatter.reviewed_at:
  datetime | None = None` new field.
- `tests/test_memory.py` — round-trip + default tests.

Requires Obsidian ≥ v1.9.10 (Bases is core since August 2025).
Partners open the vault and see Recent changes / Low confidence /
Stale or unreviewed / Constitution + doctrine / By domain in one
click. Non-technical governance surface.

### Move 3 — Contradiction callout rendering

**Files modified:**
- `src/memory_mission/observability/api.py` — new
  `coherence_warnings_for(entity_id, *, since=None)` helper.
- `src/memory_mission/observability/__init__.py` — export.
- `src/memory_mission/synthesis/context.py` —
  `AttendeeContext.coherence_warnings` field + callout rendering
  in `_render_attendee`.
- `src/memory_mission/synthesis/compile.py` —
  `_compile_attendee_context` pulls warnings from the observability
  log when a scope is active; best-effort (empty on no scope).
- `src/memory_mission/memory/pages.py` — `render_page()` accepts
  optional `coherence_warnings` kwarg and emits `> [!contradiction]`
  callout above compiled-truth when non-empty.

Reads `CoherenceWarningEvent` rows from the append-only JSONL.
Callouts render natively in Obsidian with the warning's subject,
predicate, and conflicting object / tier. Eval-corpus ready via the
structured event stream (per `docs/EVALS.md` 2.7).

### Move 4 — AGENTS.md canonical + CLAUDE.md shim

**Files added:**
- `docs/AGENTS.md` — canonical agent instructions (skill routing +
  Memory Mission skill list + build discipline).
- `CLAUDE.md` at repo root — one-line `@docs/AGENTS.md` shim.

The canonical file lives under `docs/` because the repo-root
`AGENTS.md` is claimed by the claude-mem MCP (session-context cache)
and gets overwritten per-session. Keeping canonical under `docs/`
means Codex / Gemini / Cursor / Windsurf adopters point at
`docs/AGENTS.md` directly and Claude Code picks it up via the shim.

### Move 5 — Permission-aware `BrainEngine` read path

**Files modified:**
- `src/memory_mission/memory/engine.py`:
  - `BrainEngine.get_page` / `search` / `query` accept optional
    `viewer_id: str | None` + `policy: Policy | None`.
  - New module helper `_viewer_can_read(key, page, *, viewer_id,
    policy)` routes firm pages through `can_read()` and drops
    personal pages whose owner isn't the viewer.
  - `can_read` / `Policy` imported from `permissions.policy`.
- `tests/test_memory.py` — 8 new tests covering firm-scope
  filtering, personal-plane owner gating, fail-closed on unknown
  employee, backwards compat when either argument is `None`.

Closes the read-time permission gap. Permissions were advisory at
the utility layer; now the engine enforces when the caller asks
for enforcement. Matches Google Knowledge Catalog's "access-control-
aware search" pattern without adopting the rest of their SaaS
architecture.

### Move 6 — "Context engine" framing

**Files modified:**
- `docs/VISION.md` — subtitle + opening paragraph now call Memory
  Mission "a governed context engine for agents."
- `README.md` — hero sentence updated to match.
- `docs/EVALS.md` — stance paragraph adds "context construction as
  measurable engineering, not guesswork" from Google Knowledge
  Catalog's framing.

No code change. Sharpens the external pitch without changing
architecture.

### Combined verification

- [x] `pytest` — 643/643 passed (+20 since Step 17: 2 frontmatter +
      8 permission + 4 callout + synthesis updates + Move 4/6 test
      preservation)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy src/` strict clean on 66 files
- [x] Obsidian compatibility preserved on all new fields
- [x] Every existing caller unchanged (Move 5/3 new params default
      to None / empty list)

### Separate: promotion-pipeline tightening (committed alongside)

Independently of the polish pass, `create_proposal` / `promote` /
`reject` / `reopen` now call `_require_observability_scope()` at
entry — promotion operations cannot run without a live audit
trail. Tests wrap affected calls in `observability_scope(...)`.
Tightens Emile's "provenance mandatory" principle from "logged
when scoped" to "scope required."

### Saved to memory

Post-V1 items captured as memory entries for future sessions:
`project_post_v1_roadmap.md` (authoritative deferred-items list)
and `reference_google_knowledge_catalog.md` (GCP's enterprise
context engine — overlap + wedge phrasing).

### Status

V1 + polish complete. Branch `SvenWell/office-hours` pushed to
`origin`. Ready for review by colleagues.

---

## Step 18: MCP Server Surface — DONE (2026-04-23)

**Goal:** Close the multi-user access gap by exposing Memory Mission
as tools to any MCP-compatible host agent. Before Step 18, every host
had to import the Python package directly — no way for Codex, Cursor,
Claude Desktop, or a remote Hermes instance to read the firm KG
without a bespoke per-host adapter.

**Decision:** ship a FastMCP server at `src/memory_mission/mcp/`.
One process per employee. 14 tools total — 8 read, 6 write. Auth
via per-firm YAML manifest. Every mutating call opens an
`observability_scope` so audit trail coverage is complete over MCP,
not just over the Python API. Full rationale in
`docs/adr/0003-mcp-as-agent-surface.md`.

**Scope mapping:**

- `read` — `query` / `get_page` / `search` / `get_entity` /
  `get_triples` / `check_coherence` / `compile_agent_context`
- `propose` — `create_proposal` / `list_proposals`
- `review` — `approve_proposal` / `reject_proposal` /
  `reopen_proposal` / `merge_entities` / `sql_query_readonly`

SQL sits at the `review` tier because raw SQL can enumerate the
whole KG regardless of page-level `can_read`. Different guardrail.

### Files created

- `src/memory_mission/mcp/__init__.py` — package + re-exports
- `src/memory_mission/mcp/auth.py` — YAML manifest loader, `Scope`
  enum (StrEnum), `ClientEntry`, `AuthError`
- `src/memory_mission/mcp/context.py` — `McpContext` + `tool_scope()`
- `src/memory_mission/mcp/tools.py` — 14 tool implementations
- `src/memory_mission/mcp/server.py` — FastMCP wiring, CLI
  entrypoint, `initialize_from_handles()` test/embed seam, wiki
  bootstrap loader
- `src/memory_mission/mcp/__main__.py` — enables
  `python -m memory_mission.mcp`
- `tests/test_mcp_server.py` — 37 tests
- `docs/adr/0003-mcp-as-agent-surface.md` — rationale ADR
- `docs/recipes/mcp-integration.md` — operator guide

### Files modified

- `pyproject.toml` — added `mcp>=1.0` + `PyYAML>=6.0` to
  runtime deps; `[[tool.mypy.overrides]]` for `mcp.*` to skip
  missing stubs
- `src/memory_mission/runtime/hermes_adapter.py` — TODO stub
  replaced with a pointer to `memory_mission.mcp`
- `docs/ARCHITECTURE.md` — new module walkthrough entry for `mcp/`
- `docs/AGENTS.md` — added MCP line to the repo map

### Reuse

No new domain logic in `mcp/tools.py`. Every tool is a thin wrapper
over existing primitives:

- `BrainEngine` — already `viewer_id` + `policy` aware since Move 5
- `KnowledgeGraph` — already exposes `corroborate`, `merge_entities`,
  `sql_query`, `check_coherence`
- `create_proposal` / `promote` / `reject` / `reopen` — already
  require active observability scope
- `compile_agent_context` — already returns structured
  `AgentContext` with `.render()`

The MCP layer is the protocol boundary. Everything below it is the
same code the Python API uses.

### Verification

- [x] `pytest` — 680/680 passed (+37 since V1 polish)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy --strict` clean on 71 source files (+5)
- [x] All 14 tools registered in FastMCP
- [x] Unknown employees fail closed at startup
- [x] Missing scopes fail closed per tool call
- [x] `sql_query_readonly` rejects writes; requires `review` scope
- [x] Full round-trip: create_proposal → list_proposals →
      approve_proposal → get_triples returns the new fact

### Post-MCP deferred (per plan Phase B)

Complexity cut is evidence-gated: only cut after two weeks of real-
data dogfooding. Explicitly kept:

- Federated detector — admin-skill-opt-in already
- Bayesian corroboration — KGs update Bayesianly by design
- Identity layer — extend with Slack / Telegram / WhatsApp / phone
  channel types + E.164 normalization as a separate step

### Next

Dogfood real Gmail + Granola through MCP for two weeks, then review
`personal_brain/working.py`, `personal_brain/lessons.py`, and
`permissions/policy.py` for evidence-based cuts. ADR-0004 for
identity channel extension is the natural next ADR.

## P2 — Capability-based connector manifest + fail-closed visibility (2026-04-25)

First half of the next-chapter sequence (P0–P9 in
`/Users/svenwellmann/.claude/plans/we-ve-built-this-and-curious-unicorn.md`).
Closes the loop between the `NormalizedSourceItem` envelope (landed in
P0 Track C) and the per-firm config that drives capability binding +
fail-closed visibility mapping.

### What landed

- **`SystemsManifest` + visibility map** —
  `src/memory_mission/ingestion/systems_manifest.py`. Pydantic
  `SystemsManifest` / `RoleBinding` / `VisibilityRule`, YAML loader at
  `load_systems_manifest(path)`, and the `map_visibility(metadata, *,
  role, manifest) -> str` runtime primitive. Fail-closed by default;
  `default_visibility=None` means no rule match → raise
  `VisibilityMappingError`. Operators opt into a fallback explicitly.

- **Per-app envelope helpers** —
  `src/memory_mission/ingestion/envelopes.py`. Three pure functions
  (`gmail_message_to_envelope`, `granola_transcript_to_envelope`,
  `drive_file_to_envelope`) take a Composio-shape raw dict + manifest
  and return a `NormalizedSourceItem`. Helpers refuse to run against a
  manifest binding that names a different concrete app.

- **`StagingWriter.write_envelope`** —
  `src/memory_mission/ingestion/staging.py`. Higher-level write path
  that takes a `NormalizedSourceItem`, validates plane + concrete-app
  alignment with the writer's scope, and persists raw + markdown +
  structural frontmatter (including `target_scope`, `source_role`,
  `external_object_type`, `modified_at`) into the staging zone. The
  pre-existing `write()` stays for ad-hoc / non-envelope writes.

- **ADR-0007** — `docs/adr/0007-capability-based-connectors.md`
  documents the role/manifest/envelope/visibility design + fail-closed
  default + helper-vs-binding sanity check.

### What's deliberately out of scope for P2

- Calendar / Notion / Attio connectors and their envelope helpers —
  they don't exist as connectors yet. P3 / P4 land them.
- Skill markdown updates (`backfill-gmail/`, `backfill-granola/`) —
  P3 reroutes them through the envelope helpers + `write_envelope`.
- Live Composio credentials. Connectors stay credential-free; envelope
  helpers test against fake raw dicts shaped like documented Composio
  responses.

### Verification

- [x] `pytest` — 748/748 passed (+38 since the MemPalace hardening
      pass: 19 manifest, 12 envelope, 7 staging-envelope)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy --strict` clean on 73 source files (+2)
- [x] Manifest rejects non-mapping YAML, missing `firm_id`, unknown
      role keys, unknown `target_plane`
- [x] `VisibilityRule` requires at least one matcher
- [x] `map_visibility` first-match-wins; fail-closed when no rule
      matches and no default; uses operator-set default when present
- [x] Each helper extracts the right id / title / body /
      modified_at; preserves raw verbatim under `item.raw`
- [x] Helpers raise `ValueError` when invoked against a manifest
      binding that names a different concrete app
- [x] `write_envelope` rejects plane and concrete-app mismatches;
      writes `target_scope` + `source_role` + `external_object_type`
      into staging frontmatter

### Next

P3 — personal-source ingestion. Wire Gmail + Granola backfill skills
to invoke the envelope helpers + `write_envelope`, and rewire the
personal substrate (`MemPalaceAdapter`) to consume the resulting
`NormalizedSourceItem` shape end-to-end. Calendar connector +
envelope helper land here.

## P3-prep — Calendar connector + envelope helper + skill rewires (2026-04-25)

Stage-setting work for P3 (personal-source ingestion). Adds the
missing Calendar connector + envelope helper, rewires the Gmail and
Granola skills onto the P2 envelope path, and lands an operator
recipe for `firm/systems.yaml`. After this, an external host agent
(Sven's personal agent, or any host with Composio credentials) can
backfill Gmail / Granola / Calendar end-to-end against the
`MemPalaceAdapter` personal substrate.

### What landed

- **Calendar connector** —
  `src/memory_mission/ingestion/connectors/calendar.py`. Composio-backed
  with `list_events` + `get_event` actions and a `summary | start —
  N attendees` preview formatter. Connector name is `gcal` so it
  composes cleanly with the manifest binding `app: gcal`.
- **`calendar_event_to_envelope`** —
  `src/memory_mission/ingestion/envelopes.py`. Visibility surface
  carries `gcal_visibility` (Google Calendar's built-in
  `default`/`public`/`private`/`confidential`) as a top-level metadata
  key so `if_field` rules match it directly. Handles both attendee-
  dict form (`{"email": "...", "responseStatus": "..."}`) and raw-
  string form. Falls back from `updated` to `created` if `updated` is
  absent.
- **Skill rewires** — `skills/backfill-gmail/SKILL.md` and
  `skills/backfill-granola/SKILL.md` rewritten to load
  `firm/systems.yaml`, call the envelope helpers, and route through
  `StagingWriter.write_envelope`. New constraint: `VisibilityMappingError`
  halts the loop (no silent fallback). Versions bumped to 2026-04-25.
  `skills/backfill-calendar/SKILL.md` created from scratch (mirrors the
  Gmail shape, with calendar-specific notes on recurring events,
  non-primary calendars, and the `gcal_visibility` field).
  `skills/_index.md` and `skills/_manifest.jsonl` updated to register
  the new skill and reflect the new tool / precondition / constraint
  surface.
- **`docs/recipes/systems-yaml.md`** — operator recipe. Includes a
  one-person personal-test firm template (Gmail + Granola + Calendar,
  fail-closed-with-employee-private-default), a strict-by-default
  firm template, a firm-plane Drive template, the binding +
  `VisibilityRule` schema reference, and the per-app
  `visibility_metadata` shape table.

### What's deliberately out of scope

- **Notion / Attio / workspace connectors** — P4.
- **Live Composio credentials wiring in this codebase** — connectors
  stay credential-free. Sven's host agent injects the client.
- **End-to-end test against real-shape Composio responses** — that
  validation comes from the actual host-agent backfill run.

### Verification

- [x] `pytest` — 763/763 passed (+15 since P2: 5 calendar-connector +
      9 calendar-envelope, plus 1 ordering bump)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy --strict` clean on 74 source files (+1)
- [x] `skills/_manifest.jsonl` parses as valid JSONL (8 entries; was 7)

### Next

P3 proper begins when an external host agent (Sven's, or another
operator's) actually runs `backfill-gmail` / `backfill-granola` /
`backfill-calendar` against live data. Findings from that run drive
the typed-Python convenience wrappers (e.g.
`src/memory_mission/ingestion/backfill_runner.py`) that codify the
skill workflow into a callable Python entry point.

## P3-prep continued — Affinity connector (venture-CRM, P4 first venture-app) (2026-04-25)

First venture-specific app connector lands. Affinity is the dominant
relationship-intelligence CRM at venture firms; covering it is the
single biggest signal-to-effort move for an actual venture pilot.

### What landed

- **Affinity connector** —
  `src/memory_mission/ingestion/connectors/affinity.py`. Composio-backed.
  9 read actions (list/get for organizations, persons, opportunities;
  plus list_lists, get_list_metadata, list_list_entries that drive
  visibility mapping). API-key auth at the Composio layer (Affinity
  doesn't expose OAuth2 publicly).
- **`affinity_record_to_envelope`** —
  `src/memory_mission/ingestion/envelopes.py`. Single dispatching helper
  that takes `object_type` ("organization" / "person" / "opportunity")
  + manifest and returns a `NormalizedSourceItem`. Visibility surface
  surfaces each Affinity list-membership as a `list:<list_id>` label
  (matches `if_label` rules in the manifest); flags globally-known
  records with a `global` label that typically maps to
  `external-shared`. `external_id` is type-prefixed (`org_<id>` /
  `person_<id>` / `opp_<id>`) to avoid id collision across types.
  Body is a structured text summary (Affinity records aren't
  documents; reviewers and downstream extraction see key fields
  without parsing the raw payload).
- **`backfill-affinity` skill** —
  `skills/backfill-affinity/SKILL.md`. Three-pass strategy
  (organizations → persons → opportunities) so identity resolution
  canonicalizes orgs before persons/opportunities link to them.
  Per-type durable runs. Administrator-run only.
- **`docs/recipes/systems-yaml.md`** — venture-CRM example added with
  list-membership-driven scope mapping. Per-app
  `visibility_metadata` shape table extended with the Affinity row.
- **Skill index + manifest** — `skills/_index.md` and
  `skills/_manifest.jsonl` updated.

### Why Affinity first (vs Outlook / OneDrive / Attio)

The Composio research scan
(commit `b431df2..` on 2026-04-25) showed Affinity has 20 tools at
Composio with **API key** auth and a clean read surface. It is the
single biggest venture wedge — every venture firm in the pilot
demographic uses it. Cost: ~half day. Outlook + OneDrive are next,
then Attio + Notion + Slack.

### Verification

- [x] `pytest` — 776/776 passed (+13 since previous: 4 connector
      + 9 envelope tests)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy --strict` clean on 75 source files (+1)
- [x] Manifest parses as valid JSONL (9 entries)

### Next

Outlook + OneDrive (M365 bundle) — single move that swaps the firm's
primary email + document substrate when a pilot firm is on Microsoft
365 instead of Google Workspace. Then Attio (second venture CRM),
Notion (workspace + document dual-binding), and finally Slack with
its own ADR for the new `chat_system` role.

## P3-prep continued — Outlook + OneDrive M365 bundle (email + document) (2026-04-25)

Second venture-relevant batch lands. M365 stack swap-in unlocks pilot
firms on Microsoft 365 instead of Google Workspace. Both connectors
land together because they share the auth model + customer profile
(an M365 firm needs both, not one or the other).

### What landed

- **Outlook connector** —
  `src/memory_mission/ingestion/connectors/outlook.py`. 5 read actions
  (list_messages, get_message, list_mail_folders, search_messages,
  get_mail_delta — the last one for incremental resume). OAuth2.
  Connector name 'outlook' matches manifest 'app: outlook'.
- **`outlook_message_to_envelope`** — bound to `email` role.
  Visibility surface includes `outlook_sensitivity` (Outlook's
  built-in `normal`/`personal`/`private`/`confidential` field) as a
  top-level metadata key for `if_field` rules; categories surface as
  `labels` so `if_label` rules work like Gmail.
- **OneDrive connector** —
  `src/memory_mission/ingestion/connectors/onedrive.py`. 10 read
  actions covering OneDrive personal/business AND SharePoint
  document-library items + sites + list items + page content.
  OAuth2. Connector name 'one_drive' matches manifest 'app: one_drive'.
  Composio's toolkit conflates OneDrive and SharePoint doc libraries
  through Microsoft Graph's drive-item API.
- **`onedrive_item_to_envelope`** — bound to `document` role.
  Visibility surface synthesizes `drive_anyone` (anonymous-link
  permission), `drive_organization_link` (organization-scoped
  permission), `is_sharepoint` (parentReference.siteId present), and
  `sharepoint_site_id` for per-site rules. Mirrors Drive helper's
  shape with M365-flavored extensions.
- **`backfill-outlook` skill** — M365 equivalent of `backfill-gmail`.
  Notes incremental-sync mode via `get_mail_delta` after the first
  full backfill.
- **`backfill-onedrive` skill** — administrator-run firm-plane
  backfill for OneDrive + SharePoint document libraries. Per-scope
  durable runs (one per site / one for personal root) so resume
  contracts stay clean. Notes that SharePoint pages and list items
  have different shapes — separate helpers when a pilot needs them.
- **`docs/recipes/systems-yaml.md`** — M365 firm example added with
  rules combining `outlook_sensitivity`, drive-link-scope, and
  per-site SharePoint rules. Per-app `visibility_metadata` shape
  table extended with the Outlook + OneDrive rows.
- **Skill index + manifest** — `skills/_index.md` and
  `skills/_manifest.jsonl` updated.

### Verification

- [x] `pytest` — 796/796 passed (+20 since Affinity: 6 connector
      tests + 14 envelope tests across the two new helpers)
- [x] `ruff check` + `ruff format --check` clean (after fixing two
      E501 line-length issues)
- [x] `mypy --strict` clean on 77 source files (+2)
- [x] Manifest parses as valid JSONL (11 entries; was 9)

### Next

Attio (second venture CRM, slimmer surface than Affinity but cleaner
OAuth2 auth). Then Notion (workspace + document dual-binding for
firms that use Notion as their wiki). Then Slack with its own ADR
for the new `chat_system` role.

## P3-prep continued — Attio connector (schema-flexible venture-CRM) (2026-04-25)

Second venture-CRM lands. Attio is the cleaner-OAuth2 alternative to
Affinity for firms that want a customizable schema. Both fit the
`workspace` role; firms pick one (or both) per the manifest.

### What landed

- **Attio connector** —
  `src/memory_mission/ingestion/connectors/attio.py`. Composio-backed.
  6 read actions (list_objects, get_object_details, list_records,
  find_record, list_notes, list_lists). OAuth2 via Composio.
  Connector name 'attio' matches manifest 'app: attio'.
- **`attio_record_to_envelope`** —
  `src/memory_mission/ingestion/envelopes.py`. Single dispatching
  helper that takes `object_slug` (the Attio object identifier the
  record belongs to — `people`, `companies`, `deals`, or any custom
  object) + manifest. Visibility surface surfaces each Attio
  list-membership as a `list:<list_id>` label and surfaces
  `attio_object_slug` as a top-level field for object-level scope
  rules. external_id is slug-prefixed (`companies_<uuid>`, etc.).
  Body is a structured key:value dump of attribute values (Attio's
  versioned `values: {<attr>: [{value: ...}]}` shape unwrapped).
- **`backfill-attio` skill** —
  `skills/backfill-attio/SKILL.md`. Per-object durable runs.
  Recommended order: system objects first (people → companies →
  deals) so identity resolution canonicalizes entities before custom
  objects link to them. Administrator-run only.
- **`docs/recipes/systems-yaml.md`** — Attio example added with
  list-membership + object-slug rules. Per-app
  `visibility_metadata` shape table extended.
- **Skill index + manifest** — entries for backfill-attio added.

### Verification

- [x] `pytest` — 809/809 passed (+13 since M365: 5 connector tests +
      8 envelope tests)
- [x] `ruff check` + `ruff format --check` clean
- [x] `mypy --strict` clean on 78 source files (+1)
- [x] Manifest parses as valid JSONL (12 entries; was 11)

### Next

Notion (workspace + document dual-binding for firms that use Notion
as their wiki). Then Slack with its own ADR for the new
`chat_system` role.
