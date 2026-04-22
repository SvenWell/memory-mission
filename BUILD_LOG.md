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
