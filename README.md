# Memory Mission

**Operational memory for agents.** Compile a firm's communication residue into current operating state — facts, commitments, people, decisions, unresolved loops, triggers, and action rules — so agents can participate without constantly rediscovering how the human and team work.

Python infrastructure organized as three layers ([factual](docs/VISION.md) / [interaction](docs/VISION.md) / [action](docs/ACTION_MEMORY.md)), with two planes (personal and firm) separated by a PR-model review gate. Every fact traces to a source, a reviewer, and a rationale. Nothing lands on firm-plane memory without an explicit human decision. **Doing nothing is a first-class action** — the system acts because the context says it should, and stays still when it should not.

See [`docs/VISION.md`](docs/VISION.md) for the full framing, [`docs/OPERATING_STATE.md`](docs/OPERATING_STATE.md) for the canonical predicate vocabulary, [`docs/ACTION_MEMORY.md`](docs/ACTION_MEMORY.md) for the forward-looking action layer.

## Where to start

| For | Read |
|---|---|
| The thesis, three-layer framing, who it's for | [`docs/VISION.md`](docs/VISION.md) |
| The shipped architecture + module walkthrough | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Canonical predicate vocabulary (objection, blocker, dependency, risk, owner, …) | [`docs/OPERATING_STATE.md`](docs/OPERATING_STATE.md) |
| Forward-looking action layer (procedural / trigger / execution / outcome) | [`docs/ACTION_MEMORY.md`](docs/ACTION_MEMORY.md) |
| Every Pydantic model, predicate, tier in one place | [`docs/ABSTRACTIONS.md`](docs/ABSTRACTIONS.md) |
| How we measure whether the system is right | [`docs/EVALS.md`](docs/EVALS.md) |
| Load-bearing decisions with rationale | [`docs/adr/`](docs/adr/) |
| Operator recipes (hot-cache hooks, Bases dashboard, Hermes integration) | [`docs/recipes/`](docs/recipes/) |
| How agents should navigate this repo | [`docs/AGENTS.md`](docs/AGENTS.md) |
| Per-step chronology | [`BUILD_LOG.md`](BUILD_LOG.md) |

## Quickstart

```bash
git clone https://github.com/SvenWell/memory-mission.git
cd memory-mission

python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

make check          # ruff + format + mypy --strict + 1091 tests
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

V1 complete + MCP surface (firm-mode + individual-mode) + MemPalace personal substrate (ADR-0004) + P2 capability-based connector manifest + venture pack (11 connectors across 6 capability roles) + **P7-A venture overlay** (constitution + lifecycle predicate vocabulary + 4 workflow skills) + **Hermes integration** (`MemoryMissionProvider` + 20 markdown skills + `granola-extraction-pilot` validated end-to-end against real data: 35 meetings → 423 candidates → 18 promoted facts). Released `v0.1.0` → `v0.1.3` (Hermes-ready substrate, SQLite cross-thread fix, multi-agent identifier-coordination, `mm_resolve_entity` tool). Three-layer operational substrate per [`docs/VISION.md`](docs/VISION.md). **1,116 tests passing**, `mypy --strict` clean on 94 source files.

| Layer | What you can do today |
|---|---|
| **Foundations** | Append-only observability log per firm; checkpointed durable execution with resume-on-crash; PII-redacted middleware around every LLM call |
| **Connectors** | `Connector` Protocol + `invoke()` harness. Composio-backed adapters (SDK stub; host wires the client) for **Gmail, Outlook, Google Calendar, Granola, Google Drive, OneDrive/SharePoint, Affinity, Attio, HubSpot, Notion, Slack** — 11 apps across 6 capability roles (`email` / `calendar` / `transcript` / `document` / `workspace` / `chat`). Per-firm `firm/systems.yaml` binds roles → apps; envelope helpers normalize each app's raw payload to `NormalizedSourceItem` with fail-closed visibility mapping (ADR-0007 + ADR-0011) |
| **Memory (factual layer)** | Compiled-truth + timeline page format (Obsidian-compatible); SQLite temporal knowledge graph with `valid_from` / `valid_to` validity windows and Bayesian corroboration (Noisy-OR, 0.99 cap); auto-registered entities on triple write; hybrid search (RRF + cosine + compiled-truth boost); tier-aware authority hierarchy (`constitution` / `doctrine` / `policy` / `decision`); durable `FileSystemEngine` for markdown-backed page persistence; rereadability via preserved `.raw/` sidecars + MemPalace evidence (ARCHITECTURE.md principle 11) |
| **Evidence (interaction layer)** | MemPalace evidence layer indexed by drawer key (ADR-0004); `OpenQuestion` fact bucket with mandatory `support_quote`; `triple_sources` append-only provenance per corroboration; `PersonalMemoryBackend` Protocol for swappable evidence backends |
| **Identity** | `IdentityResolver` Protocol + SQLite-backed local resolver; stable `p_<id>` / `o_<id>` across email / LinkedIn / Twitter / phone; `merge_entities` with reviewer gate |
| **Extraction** | Six-bucket `ExtractedFact` Pydantic union (`identity` / `relationship` / `preference` / `event` / `update` / `open_question`) with mandatory support-quote; `EXTRACTION_PROMPT` template tightened against mention-only identity facts; ingest-time canonicalization to stable IDs; zero LLM-SDK imports in-repo |
| **Permissions** | Per-firm `Policy` with scopes + employees; `can_read` + `can_propose` with no-escalation; read-path enforcement inside `BrainEngine` |
| **Promotion** | `Proposal` + `ProposalStore`; `create_proposal` / `promote` / `reject` / `reopen` with required rationale; coherence check emits structured warnings; opt-in constitutional mode blocks on contradictions |
| **Federated** | Cross-employee pattern detector with distinct-source-file independence check; stages firm-plane proposals from N≥3 employees' personal planes |
| **Synthesis** | `compile_agent_context(role, task, attendees, ...)` returns a structured `AgentContext` package — both firm-mode and individual-mode shipped; `compile_individual_boot_context` returns active threads + commitments + preferences + recent decisions + relevant entities + project status; Obsidian `[!contradiction]` callouts on rendered pages |
| **MCP surface** | Two FastMCP servers — **firm-mode (14 tools)** + **individual-mode (13 tools)**. Lifecycle-transition primitives `record_facts` + `invalidate_fact` match `OPERATING_STATE.md`'s `open → investigating → resolved/closed` convention. `query_entity` annotates `conflicts_with` on currently-true triples sharing subject + predicate (partial visibility into contested operating state). `compile_agent_context` shipped both modes. Per-firm `mcp_clients.yaml` manifest with NFKC + dup-key + symlink rejection. Every mutating tool opens an `observability_scope` |
| **Scope enforcement** | Fail-closed when no policy configured. `viewer_scopes` filter on KG triples. `can_propose` no-escalation on `create_proposal`. Scope column on every triple; pre-flight scope scan in `_apply_facts` so scope mismatches never leave partial KG writes. WAL + busy_timeout on all per-firm SQLite DBs |
| **Hermes integration** | `MemoryMissionProvider` plugin mirrors Hermes' `MemoryProvider` ABC (`src/memory_mission/integrations/hermes_provider.py`). Seed migration adapter (`hermes_seed_migrate`) ports an existing Hermes session into the personal substrate. Contract test suite (`tests/test_provider_contract.py`) pins the public API shape against drift |
| **Skills** | 19 shipped. **Personal-source backfills** (employee plane): `backfill-gmail`, `backfill-outlook`, `backfill-granola`, `backfill-calendar`. **Firm-source backfills** (firm plane, admin-run): `backfill-firm-artefacts` (Drive), `backfill-onedrive` (OneDrive + SharePoint), `backfill-affinity`, `backfill-attio`, `backfill-notion`. **Mixed-plane**: `backfill-slack`. **Workflow**: `extract-from-staging`, `review-proposals`, `detect-firm-candidates`, `meeting-prep`. **Venture overlay (P7-A)**: `update-deal-status`, `record-ic-decision`, `onboard-venture-firm`, `weekly-portfolio-update`. **Pilot**: `granola-extraction-pilot` (Hermes-validated, 18 promoted facts) |

Full per-step chronology in [`BUILD_LOG.md`](BUILD_LOG.md).

## How it composes

Each layer reads from the one below without rewriting it.

1. A **connector** pulls from any of the 10 supported apps through the `invoke()` harness — PII-scrubbed, durable-checkpointed, logged as a `ConnectorInvocationEvent`.
2. The connector's raw payload feeds an **envelope helper** (`gmail_message_to_envelope`, `slack_message_to_envelope`, etc.) which consults `firm/systems.yaml` for the firm-shaped `target_scope` and `target_plane`. **Staging** then writes the `NormalizedSourceItem` envelope to `staging/<plane>/<source>/<id>.md` plus a raw sidecar via `StagingWriter.write_envelope`. Visibility mapping is fail-closed by default — items whose visibility metadata can't map to a firm scope are rejected, not silently defaulted.
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
├── ingestion/              # connectors (Composio harness + Gmail/Granola/Drive),
│                           # systems_manifest, envelopes, staging, mentions
├── memory/
│   ├── pages.py            # compiled-truth + timeline format
│   ├── schema.py           # MECE domains + plane paths
│   ├── engine.py           # BrainEngine Protocol + InMemoryEngine
│   ├── knowledge_graph.py  # SQLite temporal KG + corroborate + coherence
│   ├── tiers.py            # constitution / doctrine / policy / decision
│   ├── search.py           # RRF + cosine + compiled-truth boost
│   └── templates/          # dashboard.base
├── personal_brain/         # PersonalMemoryBackend Protocol + MemPalaceAdapter
├── extraction/             # 6-bucket ExtractedFact + ingest_facts + dry-run previews
├── identity/               # IdentityResolver Protocol + LocalIdentityResolver
├── permissions/            # Policy + can_read / can_propose
├── promotion/              # Proposal + PR-model review gate
├── federated/              # cross-employee pattern detector
├── synthesis/              # compile_agent_context + compile_individual_boot_context
├── integrations/           # MemoryMissionProvider (Hermes plugin) + hermes_seed_migrate
└── mcp/                    # FastMCP servers — firm-mode (14 tools) + individual-mode (13 tools)

skills/                     # 19 shipped, markdown + YAML frontmatter (10 backfill + 4 workflow + 4 venture overlay + 1 pilot)
tests/                      # 1091 passing
docs/                       # VISION + ARCHITECTURE + ABSTRACTIONS + OPERATING_STATE + ACTION_MEMORY + EVALS + AGENTS + adr/ + recipes/
overlays/venture/           # P7-A constitution + page templates + lifecycle vocabulary
deploy/                     # operational artefacts — MCP launcher, ingestion scripts, daily cron
├── individual_with_mempalace.py   # stdio MCP entry point (consuming agent spawns this)
├── scripts/                       # backfills, extraction, promotion, KG migrations, viz
├── cron/                          # mm-refresh.sh + install-cron.sh (daily ingestion)
├── .env.example                   # env contract — copy to .env.local on the deploy host
└── README.md                      # deploy contract + setup
deploy.sh                   # idempotent VPS deploy: pull main, refresh deps, restart Hermes
BUILD_LOG.md                # per-step record
```

## Operational notes

Configuration is environment-driven via `MM_*` vars (see [`src/memory_mission/config.py`](src/memory_mission/config.py)):

| Variable | Default | Purpose |
|---|---|---|
| `MM_WIKI_ROOT` | `./wiki` | Root for firm content (curated pages + staging) |
| `MM_OBSERVABILITY_ROOT` | `./.observability` | Append-only audit log root |
| `MM_DATABASE_URL` | (empty) | Unused pre-pilot — SQLite-per-firm is the default (see ADR-0005). Placeholder for a future hosted option if a pilot demands it |
| `MM_LLM_PROVIDER` | `anthropic` | `anthropic` / `openai` / `gemini` (the host agent uses this; we don't import the SDK) |
| `MM_LLM_MODEL` | `claude-sonnet-4-6` | Default model identifier |

Per-firm isolation is filesystem-based: each firm gets its own subdirectory + its own SQLite files (KG, identity, proposals, mentions, durable). WAL + `busy_timeout=5000` on all stores so multiple MCP processes per employee coexist safely. No hosted DB pre-pilot (ADR-0005).

Day-to-day:

```bash
make check           # full pre-commit check
make lint-fix        # auto-apply ruff fixes
pytest -k <pattern>  # run a subset
```

## Deploying + daily ingestion

The `deploy/` directory holds everything you need to run memory-mission as a
long-lived MCP server with a daily ingestion pipeline (Gmail, Calendar,
Granola → MemPalace + KG). All deploy-specific values (identity, account
labels, API keys) come from `deploy/.env.local` (gitignored). Anyone can
clone, fill in `.env.example`, and run the same flow against their own data.

**MCP server.** `deploy/individual_with_mempalace.py` is the stdio entry
point. Wire it into your consuming agent's `mcp_servers` config with
`MM_USER_ID` / `MM_AGENT_ID` / `MM_ROOT` set in env. See
[`deploy/README.md`](deploy/README.md).

**One-command deploy.** On a host pinned to `main`, `./deploy.sh`
fast-forwards `main`, refreshes the editable install (so new dependency
pins land), ensures the launcher symlink, and restarts the consuming
agent's systemd unit. Idempotent — safe to re-run.

**Daily refresh.** `deploy/cron/mm-refresh.sh` runs the full pipeline once:

| Phase | Step |
|---|---|
| 1. Backfill (per source, isolated) | `backfill.py calendar <label>`, `backfill.py gmail <label>`, `backfill_granola.py` |
| 2. Stage → MemPalace | `mempalace_ingest.py` |
| 3. Extract facts via Codex CLI (subscription) | `extract_pilot.py` |
| 4. Promote → proposals | `promote_staged.py` |
| 5. Project KG → CRM (per target, isolated) | `push_to_crm.py --target=hubspot --apply`, `push_to_crm.py --target=notion --apply` |

Each step has its own log under `/var/log/memory-mission/<step>.log` and is
isolated — one source failing does not stop the others. The whole pipeline
is idempotent: re-runs over overlapping windows skip already-processed
items rather than duplicating.

**KG → CRM projection.** `push_to_crm.py` is the single entry point for
projecting personal-KG entities (people + companies) into a CRM. Targets
are pluggable: drop in `_target_<name>.py` implementing `CRMTarget`
(see `_crm_target.py`) and register it in the orchestrator. Currently
wired: HubSpot (matches by email/domain, delta-aware updates) and
Notion (auto-provisions Contacts/Companies databases on first run,
matches by `mm_entity_id` rich_text). Each target runs only if its env
is set, so the cron stays a no-op for unconfigured CRMs.

To wire it up on a fresh host:

```bash
cp deploy/.env.example deploy/.env.local
$EDITOR deploy/.env.local                    # MM_USER_ID, MM_FIRM_ROOT, account labels, COMPOSIO_API_KEY, ...
codex login --device-auth                    # extract_pilot uses `codex exec`; subscription mode required
./deploy/cron/install-cron.sh                # idempotent — installs a single 04:00 UTC daily entry
```

Full setup, env contract, diagnostics, and disable instructions in
[`deploy/cron/README.md`](deploy/cron/README.md).

## Next chapter — signal-driven, post-Hermes-validation

Substrate is at signal-driven stance: the `granola-extraction-pilot` ran end-to-end against real data (35 meetings → 423 candidates → 18 promoted facts) with zero substrate blockers, and Hermes is in production on `memory-mission==0.1.x`. Next moves are signal-gated, not speculative.

Active plan: [`~/.claude/plans/okay-lets-envision-a-joyful-prism.md`](file:///Users/svenwellmann/.claude/plans/okay-lets-envision-a-joyful-prism.md). Status across the pilot phases:

- **P0 — DONE.** MemPalace adopted as personal-layer substrate behind `PersonalMemoryBackend` (ADR-0004).
- **P1 — DONE.** Tight adapter boundary; only `MemPalaceAdapter` imports `mempalace.*`.
- **P2 — DONE.** Capability-based connector manifest + fail-closed visibility mapping (ADR-0007 + ADR-0011).
- **P3 — DONE.** Personal-source ingestion validated via `granola-extraction-pilot` end-to-end against real Granola data.
- **P7-A — DONE.** Venture overlay: constitution + page templates + lifecycle vocabulary + 4 workflow skills.
- **P4 — DEFERRED.** Firm-source ingestion bridge (Notion / Attio / Drive). Wait for pilot signal.
- **P5 — DEFERRED.** Typed sync-back for approved facts. Wait for pilot signal.
- **P6 — DEFERRED.** Evidence-pack retrieval + firm auto-wiring at promote time.
- **P8 — DEFERRED.** Pilot rehearsal + benchmarks.

**Signal-driven triage triggers** (build on real data, not speculation):

- A bug Hermes hits agent-side that requires substrate change → patch + tag (`v0.1.x`).
- A primitive Hermes explicitly requests → add + pin in `tests/test_provider_contract.py`.
- A tool description Hermes consistently misuses → fix description, possibly split tool.
- Three-employee pattern (federated detector) producing real candidates → tighten promotion gate.

## Post-V1 roadmap (parked)

Deferred items, each with a real-data trigger. See `project_post_v1_roadmap.md` for triggers.

- **Action-memory primitives** (`docs/ACTION_MEMORY.md`): trigger memory, threshold rules, "ask first" gates, outcome-feedback loops. Build only when a pilot needs them or Hermes asks.
- **Mid-session operating-state read tools**: `mm_list_open_commitments`, `mm_list_blockers`, `mm_list_unresolved_questions`. Bulk reads for the predicates in `OPERATING_STATE.md`. Substrate already supports via `query_entity` + parsing; convenience tools are signal-gated.
- **Rereadability primitive**: `re_extract_staged_item(item_path, *, schema_version)` + `extraction_schema_version` field on `ExtractedFact`. Build when ontology evolves and we need to migrate prior batches.
- **Step 19:** Legislative amendment cycle (batched promotions triggered by evidence pressure).
- **Step 20:** Constitution bootstrap skill (cold-start firm truth from existing strategy docs).
- **Step 21:** Relationship-strength view + Graph One adapter (needs real interaction volume).
- Identity channel extension (Slack/Telegram/WhatsApp/phone/E.164 normalization).
- 50-scenario federated eval harness (per EVALS § 2.6).
- Distillation coherence eval (per EVALS § 2.7).
- `CoherenceResolvedEvent` so the contradiction callout hides acknowledged conflicts.
- BrainBench-Real-style eval pattern (capture real `mm_*` queries → PII-scrub → replay against retrieval changes; portable from GBrain v0.25.0).
- `/save` (conversation → personal-plane note) + `/autoresearch` (WebSearch + WebFetch loop) as optional skills.
- ML-powered query rewriting on hybrid search.

## License

Proprietary — see [`pyproject.toml`](pyproject.toml).
