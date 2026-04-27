# Connecting Hermes to Memory Mission Individual

This recipe wires Memory Mission Individual into a Hermes agent
runtime as a first-class memory provider — drop-in alongside
``mem0`` / ``honcho`` / ``supermemory`` / ``builtin``.

ADR reference: `docs/adr/0015-individual-brain-mode.md`. Strategic
framing: `project_individual_brain_architecture.md` (operating-vs-
evidence memory split + boot-substrate framing).

## Three integration paths

| Path | When to use | Status |
|---|---|---|
| **1. Hermes-native provider** | Daily use; canonical | ✅ Ready |
| **2. MCP server (stdio)** | Non-Hermes agents (Codex, Cursor, etc.) or debug | ✅ Ready |
| **3. Direct Python import** | Scripts, smoke tests, one-off compilations | ✅ Ready |

Path 1 is the recommended primary integration. Paths 2 and 3 stay
available for cases that aren't a Hermes runtime.

## Path 1 — Hermes-native provider (canonical)

### Plugin layout

Hermes discovers memory providers under
``$HERMES_HOME/plugins/memory/<name>/``. Use the underscored name so
it matches the provider's ``.name`` property and Hermes config
dispatch:

```
$HERMES_HOME/
└── plugins/
    └── memory/
        └── memory_mission/
            ├── plugin.yaml
            └── __init__.py
```

### `plugin.yaml`

```yaml
name: memory_mission
version: 0.1.0
description: "Memory Mission individual memory backend for Hermes."
pip_dependencies:
  - "git+https://github.com/SvenWell/memory-mission.git@v0.1.0"
```

Pin to `@v0.1.0` (or a later release tag) for reproducibility. Use
`@main` only when actively co-developing — main moves with every
substrate-level commit.

### `__init__.py`

```python
"""Memory Mission individual brain — Hermes plugin discovery hook."""

from memory_mission.integrations.hermes_provider import register

# Hermes calls this at startup; we forward a fresh provider instance.
__all__ = ["register"]
```

That's the entire plugin. The package itself owns the provider class,
the lifecycle methods, and the tool surface.

### Hermes config

```yaml
memory:
  memory_enabled: true
  provider: memory_mission
```

### Environment variables

```bash
export MM_USER_ID=sven                   # required
export MM_ROOT=~/.memory-mission         # required
export MM_AGENT_ID=hermes                # optional (defaults to "hermes")
```

`MM_PROFILE` is accepted as a legacy alias for `MM_USER_ID`. The
provider validates the user id (path-traversal-safe) before any
filesystem side effect.

### What Hermes gets

The provider implements the full Hermes ``MemoryProvider`` ABC:

| Lifecycle hook | What it does |
|---|---|
| ``is_available()`` | Cheap env check (``MM_USER_ID`` + ``MM_ROOT``) |
| ``initialize(session_id, ...)`` | Opens per-user ``PersonalKnowledgeGraph`` + ``BrainEngine`` + ``IdentityResolver`` |
| ``system_prompt_block()`` | Static identity preamble injected into every system prompt |
| ``prefetch(query)`` | **Compact** task-relevant boot slice injected before each inference call (token budget ≈ 1500). NOT a full state dump — that's the explicit `mm_boot_context` tool |
| ``sync_turn(user, assistant)`` | V1 no-op (see "Known limitations" below) |
| ``shutdown()`` | Closes the per-user KG handle |

And exposes 8 opinionated tools:

| Tool | Role |
|---|---|
| ``mm_boot_context`` | FULL state digest — call when prefetch slice isn't enough |
| ``mm_list_active_threads`` | Currently-true thread states |
| ``mm_upsert_thread_status`` | Set / change a thread's state (invalidates prior) |
| ``mm_record_commitment`` | Open a commitment with description + optional due date |
| ``mm_record_preference`` | Durable preference (predicate must start with ``prefers_``) |
| ``mm_record_decision`` | Tier=decision page on personal plane (surfaced for 60 days) |
| ``mm_query_entity`` | **STATE** — currently-true / compiled facts about a person, project, thread, entity |
| ``mm_search_recall`` | **EVIDENCE** — source-backed recall via personal MemPalace (when wired) |

The state-vs-evidence split is structural: `mm_query_entity` is
"what is true now" (operating memory); `mm_search_recall` is "what
does the source say" (evidence memory). Use both when you need the
compiled answer + its provenance.

### Wiring MemPalace recall (optional but recommended)

By default `mm_search_recall` returns `{"error": "no_recall_backend",
"hits": []}` — Memory Mission Individual is fully usable for state
without it. Wire MemPalace when you want the agent to also search
past source documents (emails, transcripts, notes) with citations.

The provider's `initialize(backend=...)` accepts any
``PersonalMemoryBackend``. Today the canonical backend is
``MemPalaceAdapter`` (ADR-0004). Construct it once, hand it in:

```python
# Inside the Hermes plugin discovery hook, OR in a custom wrapper
# around the default register() entrypoint.
from pathlib import Path

from memory_mission.integrations.hermes_provider import MemoryMissionProvider
from memory_mission.personal_brain import MemPalaceAdapter


def register(ctx) -> None:  # type: ignore[no-untyped-def]
    """Hermes plugin discovery hook with MemPalace wired in."""
    provider = MemoryMissionProvider()

    # Hermes will call provider.initialize(session_id) later; we monkey-
    # the backend onto the call so MemPalace shows up at the right time.
    original_initialize = provider.initialize

    def _initialize_with_backend(session_id: str, **kwargs: object) -> None:
        adapter = MemPalaceAdapter(
            firm_root=Path("~/.memory-mission").expanduser(),
        )
        original_initialize(session_id, backend=adapter, **kwargs)

    provider.initialize = _initialize_with_backend  # type: ignore[method-assign]
    ctx.register_memory_provider(provider)
```

Or, for non-Hermes hosts that own the provider lifecycle directly:

```python
provider = MemoryMissionProvider()
provider.initialize(
    "session-id",
    user_id="sven",
    root="~/.memory-mission",
    backend=MemPalaceAdapter(firm_root=Path("~/.memory-mission")),
)
```

After wiring, `mm_search_recall(query="...")` returns
`{"hits": [PersonalHit, ...]}` with citations. The hits include
`role` (email / transcript / etc.), `external_id`, `url`, and
`modified_at` per the `MemPalaceAdapter` contract.

**Environment note for MemPalace.** MemPalace pulls chromadb +
opentelemetry + protobuf transitively. Some Python environments hit a
protobuf descriptor mismatch under the C++ binding. If chromadb fails
to import on first call, set this env var before launching Hermes:

```bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
```

The MemPalaceAdapter import is already lazy in
`memory_mission.personal_brain.__init__` (we keep the protobuf cost
out of consumers that don't need recall) but the env override may
still be required at MemPalace's own init.

See ADR-0004 for the design context — why MemPalace was adopted as the
personal-substrate recall layer and how it sits alongside the
operating-memory KG.

## Path 2 — MCP server (stdio)

For agents that prefer MCP over native provider integration:

```bash
python -m memory_mission.mcp.individual_server \
  --root ~/.memory-mission \
  --user-id sven \
  --agent-id hermes
```

Server name registered in MCP: ``memory-mission-individual/v1``.
Same 8 ``mm_*`` tools as Path 1 — both routes converge on one
contract.

## Path 3 — Direct Python import

For dev scripts and smoke tests:

```python
from pathlib import Path

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.synthesis import compile_individual_boot_context

root = Path("~/.memory-mission").expanduser()
resolver = LocalIdentityResolver(root / "identity.sqlite3")
kg = PersonalKnowledgeGraph.for_employee(
    firm_root=root,
    employee_id="sven",
    identity_resolver=resolver,
)
engine = InMemoryEngine()
engine.connect()

ctx = compile_individual_boot_context(
    user_id="sven",
    agent_id="hermes",
    kg=kg,
    engine=engine,
    identity_resolver=resolver,
    task_hint="resume work on memory-mission integration",
)
print(ctx.render())
kg.close()
```

## Known limitations (V1 dogfood ready, not yet primary continuity layer)

1. **Hermes seed migration adapter** is not yet shipped. Memory
   Mission starts with empty state on first Hermes connection —
   threads / commitments / preferences / decisions populate as the
   user volunteers them and Hermes invokes the ``mm_record_*``
   tools. Existing Hermes memory is NOT auto-imported.

2. **`sync_turn` is a no-op in V1.** Conversational turns aren't
   automatically ingested into the evidence layer. Recall via
   `mm_search_recall` only finds material that's already in
   MemPalace (pre-existing source documents). Real turn-ingestion
   needs the conversational-envelope shape decided + a
   ``NormalizedSourceItem`` mapping.

3. **`search_recall` requires a wired MemPalace backend.** When
   ``initialize()`` is called without a ``backend=`` arg, the recall
   tool returns a structured ``no_recall_backend`` marker rather
   than crashing. Individual mode is fully usable without recall —
   you just only get state, no evidence search.

4. **No `on_pre_compress` / `on_session_end` content yet.** Hooks
   exist as no-ops; future versions can extract candidate facts
   from session transcripts.

These are explicit V1 boundaries. None block first-connection
dogfood.

## Verification

After connecting, smoke-check:

1. ``provider memory_mission`` shows in Hermes' provider list.
2. The agent has the 8 ``mm_*`` tools available.
3. ``mm_boot_context`` returns a render with all six aspect
   sections (active threads, commitments, preferences, recent
   decisions, relevant entities, project status). Each starts
   empty on a fresh install.
4. Recording a commitment and immediately calling
   ``mm_boot_context`` shows it under "Open commitments."
5. The directory layout under ``~/.memory-mission/`` looks like:

   ```
   ~/.memory-mission/
   ├── identity.sqlite3
   └── personal/
       └── sven/
           └── personal_kg.db
   ```

## Related

- ADR-0013 — `docs/adr/0013-personal-plane-temporal-kg.md` (the
  per-employee temporal KG underneath Individual mode).
- ADR-0015 — `docs/adr/0015-individual-brain-mode.md` (the design
  decision driving this recipe).
- ADR-0004 — `docs/adr/0004-personal-layer-substrate-decision.md`
  (MemPalace as the recall substrate).
- `src/memory_mission/integrations/hermes_provider.py` — the
  provider implementation.
- `src/memory_mission/synthesis/individual_boot.py` — the
  boot-context primitive.
- `src/memory_mission/mcp/individual_server.py` — the Path 2 MCP
  server.
