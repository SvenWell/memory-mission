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
