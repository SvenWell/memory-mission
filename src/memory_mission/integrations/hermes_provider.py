"""``MemoryMissionProvider`` — Hermes ``MemoryProvider`` ABC implementation.

Hermes (the personal-agent runtime) has a ``MemoryProvider`` plugin
contract. Multiple backends already implement it (Honcho, Mem0,
Supermemory, etc.). Per Hermes' 2026-04-27 strategic feedback
(``project_hermes_feedback_log.md``) the right integration shape is to
mirror that contract — make Memory Mission Individual a drop-in
alongside the others — rather than invent a new integration philosophy.

This module **does not import Hermes**. We duck-type the contract
(method names + signatures the Hermes runtime calls) so the package
stays decoupled. When Hermes is installed the discovery hook
(``register(ctx)``) wires this provider into the runtime; when Hermes
is not installed, the provider class is still importable for testing
and for non-Hermes consumers (Codex / Cursor / etc.) that adopt the
same shape.

The contract surface (verified 2026-04-27 via public docs;
``reference_memory_provider_apis.md``):

Required:

- ``name`` (property), ``is_available()``, ``initialize(session_id, **kwargs)``,
  ``get_tool_schemas()``, ``handle_tool_call(name, args)``,
  ``get_config_schema()``, ``save_config(values, hermes_home)``

Optional hooks:

- ``system_prompt_block()``, ``prefetch(query)``, ``queue_prefetch(query)``,
  ``sync_turn(user_content, assistant_content)``, ``on_session_end(messages)``,
  ``on_pre_compress(messages)``, ``on_memory_write(action, target, content)``,
  ``shutdown()``

The provider exposes the same 8 opinionated tools as the MCP server
(``mcp/individual_server.py``) — name-prefixed with ``mm_`` so they
don't collide with other providers' tool names in the agent runtime.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.engine import BrainEngine, InMemoryEngine
from memory_mission.memory.schema import validate_employee_id
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.personal_brain.working_pages import new_decision_page
from memory_mission.synthesis.individual_boot import (
    COMMITMENT_DESCRIPTION_PREDICATE,
    COMMITMENT_DUE_PREDICATE,
    COMMITMENT_STATUS_PREDICATE,
    PREFERENCE_PREDICATE_PREFIX,
    TASK_ACTIVE_STATUSES,
    TASK_COMPLETED_AT_PREDICATE,
    TASK_DUE_PREDICATE,
    TASK_LINKED_THREAD_PREDICATE,
    TASK_NEXT_ACTION_PREDICATE,
    TASK_OUTCOME_PREDICATE,
    TASK_OWNER_PREDICATE,
    TASK_STATUS_PREDICATE,
    TASK_STATUS_VALUES,
    TASK_TITLE_PREDICATE,
    THREAD_STATUS_PREDICATE,
    Task,
    compile_individual_boot_context,
)

if TYPE_CHECKING:
    from memory_mission.personal_brain.backend import PersonalMemoryBackend


# Token budget used by the ``prefetch()`` hook. Tighter than the
# default ``mm_boot_context`` budget so per-turn context stays
# high-signal rather than full-state dump. Hermes' integration
# feedback: "the win is high-signal operating state before the model
# reasons, not dump all state every turn."
PREFETCH_TOKEN_BUDGET = 1500


# Tool names — prefixed so they don't collide with other Hermes
# memory providers' tools at the runtime level.
TOOL_BOOT_CONTEXT = "mm_boot_context"
TOOL_LIST_THREADS = "mm_list_active_threads"
TOOL_THREAD_STATUS = "mm_upsert_thread_status"
TOOL_RECORD_COMMITMENT = "mm_record_commitment"
TOOL_RECORD_PREFERENCE = "mm_record_preference"
TOOL_RECORD_DECISION = "mm_record_decision"
TOOL_QUERY_ENTITY = "mm_query_entity"
TOOL_SEARCH_RECALL = "mm_search_recall"
TOOL_RESOLVE_ENTITY = "mm_resolve_entity"
TOOL_OBSERVE = "mm_observe"
TOOL_CREATE_TASK = "mm_create_task"
TOOL_UPDATE_TASK_STATUS = "mm_update_task_status"
TOOL_COMPLETE_TASK = "mm_complete_task"
TOOL_LIST_TASKS = "mm_list_tasks"


class MemoryMissionProvider:
    """Memory Mission Individual as a Hermes ``MemoryProvider``.

    Lifecycle:

    1. Hermes calls ``is_available()`` at startup. We return ``True`` only
       when ``MM_PROFILE`` and ``MM_ROOT`` env vars (or a previously
       saved config) are present.
    2. Hermes calls ``initialize(session_id, ...)``. We open the per-user
       handles (KG + engine + identity resolver, optionally MemPalace).
    3. Hermes calls ``get_tool_schemas()``; the 8 ``mm_*`` tools become
       available to the agent for this session.
    4. Before every inference, Hermes calls ``prefetch(query)``. We
       return the rendered ``IndividualBootContext`` — that's our
       boot-substrate insertion point.
    5. After every model turn, Hermes calls ``sync_turn(...)``. We
       enqueue an ingest on a daemon thread (must NOT block).
    6. ``on_session_end(messages)`` flushes any deferred work.
    7. ``shutdown()`` closes handles cleanly.

    Construct with no args; all configuration flows through
    ``initialize`` + env / saved-config files. Tests construct directly
    and pre-set handles via ``install_handles_for_test``.
    """

    _CONFIG_FILE = "memory-mission.json"

    # ------------------------------------------------------------------ #
    # Lifecycle / required surface                                         #
    # ------------------------------------------------------------------ #

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._user_id: str | None = None
        self._agent_id: str = "hermes"
        self._root: Path | None = None
        self._kg: PersonalKnowledgeGraph | None = None
        self._engine: BrainEngine | None = None
        self._identity: LocalIdentityResolver | None = None
        self._backend: PersonalMemoryBackend | None = None

    @property
    def name(self) -> str:
        """Provider identifier used in Hermes config dispatch.

        Underscored to match Python identifier conventions and the
        ``plugins/memory/<name>/`` directory layout — this is the
        string Hermes config references via ``provider: memory_mission``.
        """
        return "memory_mission"

    def is_available(self) -> bool:
        """Cheap, no-network check that this provider can serve.

        Checks ``MM_USER_ID`` (preferred) or ``MM_PROFILE`` (legacy alias)
        plus ``MM_ROOT``. Saved-config fallback runs in ``initialize()``.
        """
        user = os.environ.get("MM_USER_ID") or os.environ.get("MM_PROFILE")
        return bool(user and os.environ.get("MM_ROOT"))

    def initialize(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        root: str | Path | None = None,
        backend: PersonalMemoryBackend | None = None,
        **_: Any,
    ) -> None:
        """Open per-user handles for this session.

        Resolution order: explicit kwargs → ``MM_USER_ID`` /
        ``MM_AGENT_ID`` / ``MM_ROOT`` env vars. ``MM_PROFILE`` is
        accepted as a legacy alias for ``MM_USER_ID``.
        """
        self._session_id = session_id
        resolved_user = user_id or os.environ.get("MM_USER_ID") or os.environ.get("MM_PROFILE")
        resolved_root = Path(root) if root else None
        if resolved_root is None and "MM_ROOT" in os.environ:
            resolved_root = Path(os.environ["MM_ROOT"])
        if resolved_user is None or resolved_root is None:
            raise ValueError(
                "MemoryMissionProvider.initialize requires user_id + root "
                "(via kwargs or MM_USER_ID/MM_ROOT env vars)"
            )
        validate_employee_id(resolved_user)
        resolved_root = resolved_root.expanduser()
        resolved_root.mkdir(parents=True, exist_ok=True)

        self._user_id = resolved_user
        self._agent_id = agent_id or os.environ.get("MM_AGENT_ID") or "hermes"
        self._root = resolved_root
        self._identity = LocalIdentityResolver(resolved_root / "identity.sqlite3")
        self._kg = PersonalKnowledgeGraph.for_employee(
            firm_root=resolved_root,
            employee_id=resolved_user,
            identity_resolver=self._identity,
        )
        engine: BrainEngine = InMemoryEngine()
        engine.connect()
        self._engine = engine
        self._backend = backend

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Config fields surfaced in ``hermes memory setup``."""
        return [
            {
                "key": "user_id",
                "description": (
                    "Memory Mission user id (e.g. 'sven'). MM_PROFILE accepted as legacy alias."
                ),
                "required": True,
                "env_var": "MM_USER_ID",
                "secret": False,
            },
            {
                "key": "root",
                "description": "Absolute path to the Memory Mission root (e.g. ~/.memory-mission)",
                "required": True,
                "env_var": "MM_ROOT",
                "secret": False,
            },
            {
                "key": "agent_id",
                "description": "Agent runtime identifier (default 'hermes')",
                "required": False,
                "env_var": "MM_AGENT_ID",
                "secret": False,
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str | Path) -> None:
        """Persist provider config to ``$HERMES_HOME/memory-mission.json``."""
        target = Path(hermes_home) / self._CONFIG_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(values, indent=2, sort_keys=True))

    # ------------------------------------------------------------------ #
    # Tool schemas + dispatch                                              #
    # ------------------------------------------------------------------ #

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """The opinionated Memory Mission Individual tool surface.

        Mirrors the MCP server tools (``mcp/individual_server.py``) so
        Hermes-driven and MCP-driven access converge on the same
        contract. Schema follows the JSON-schema dialect Hermes expects
        for tool registration.
        """
        return [
            {
                "name": TOOL_BOOT_CONTEXT,
                "description": (
                    "FULL operating-state digest — active threads, "
                    "commitments, preferences, recent decisions, "
                    "relevant entities, project status. Use this when you "
                    "need the complete picture (e.g. resuming after a "
                    "long gap). The provider's prefetch hook already "
                    "injects a compact slice on every turn; only call "
                    "this tool when that slice isn't enough."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_hint": {"type": "string"},
                        "token_budget": {"type": "integer", "default": 4000},
                    },
                },
            },
            {
                "name": TOOL_LIST_THREADS,
                "description": "List currently-true active threads (status + last signal).",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": TOOL_THREAD_STATUS,
                "description": (
                    "Set or change a thread's status. Invalidates the "
                    "prior status if any. Provenance required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": [
                                "active",
                                "in_progress",
                                "blocked",
                                "deferred",
                                "completed",
                            ],
                        },
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": ["thread_id", "status", "source_closet", "source_file"],
                },
            },
            {
                "name": TOOL_RECORD_COMMITMENT,
                "description": (
                    "Open a commitment (status + description + optional "
                    "due date). Provenance required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "commitment_id": {"type": "string"},
                        "description": {"type": "string"},
                        "due_by": {"type": "string", "format": "date"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": [
                        "commitment_id",
                        "description",
                        "source_closet",
                        "source_file",
                    ],
                },
            },
            {
                "name": TOOL_RECORD_PREFERENCE,
                "description": (
                    "Record a durable preference. Predicate must start "
                    "with 'prefers_'. Replaces prior value for the same "
                    "(subject, predicate)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "predicate": {"type": "string"},
                        "value": {"type": "string"},
                        "subject": {"type": "string"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": [
                        "predicate",
                        "value",
                        "source_closet",
                        "source_file",
                    ],
                },
            },
            {
                "name": TOOL_RECORD_DECISION,
                "description": (
                    "Log a tier=decision page on the personal plane. "
                    "Surfaced in boot context for 60 days."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "decided_at": {"type": "string", "format": "date"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": [
                        "slug",
                        "title",
                        "summary",
                        "source_closet",
                        "source_file",
                    ],
                },
            },
            {
                "name": TOOL_QUERY_ENTITY,
                "description": (
                    "STATE — currently-true / compiled facts about a "
                    "person, project, thread, or other entity. Returns "
                    "structured triples already corroborated and time-"
                    "valid (the substrate's operating-memory layer). "
                    "Use when you need to know 'what is true now' about "
                    "X. Pair with mm_search_recall when you also need "
                    "the source excerpts that established those facts."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming", "both"],
                            "default": "outgoing",
                        },
                        "as_of": {"type": "string", "format": "date"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": TOOL_SEARCH_RECALL,
                "description": (
                    "EVIDENCE — source-backed search across past "
                    "interactions and ingested documents (the personal "
                    "MemPalace recall index). Returns hits with citations, "
                    "NOT distilled current state. Use when you need raw "
                    "source excerpts (emails, transcripts, notes) that "
                    "support or contradict a claim. Pair with "
                    "mm_query_entity for the compiled-state version. "
                    "Returns a structured no_recall_backend marker when "
                    "no backend is wired up (Memory Mission Individual "
                    "is usable without MemPalace; recall just isn't)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": TOOL_RESOLVE_ENTITY,
                "description": (
                    "IDENTITY RESOLUTION — resolve a typed identifier "
                    "(e.g. 'email:sven@example.com', 'linkedin:sven-w-123') "
                    "to the canonical entity record: identity_id, "
                    "canonical_name, and all bound identifiers. For bare "
                    "names not registered as typed identifiers, returns the "
                    "name as entity_name with null identity_id — KG triples "
                    "are indexed by entity name directly so that's a valid "
                    "pass-through. Use as STEP 1 of any retrieval planner "
                    "that wants to disambiguate 'sven' / 'email:sven@x.com' "
                    "before querying state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": TOOL_OBSERVE,
                "description": (
                    "BELIEF — currently-true observations enriched with "
                    "proof_count + freshness_trend (new / strengthening / "
                    "stable / weakening / stale / contradicted). Use when "
                    "you need 'what do we believe about X, and is the "
                    "picture strengthening or going stale?' Distinct from "
                    "mm_query_entity which returns raw triples without "
                    "aggregation. All filters optional; absent filter "
                    "means 'any.' Sorted by last_corroborated_at desc."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "since": {"type": "string", "format": "date"},
                    },
                },
            },
            {
                "name": TOOL_CREATE_TASK,
                "description": (
                    "Open a new task. A task is a durable obligation — "
                    "never deleted on completion, only state-changed. "
                    "Status defaults to 'open'. Owner defaults to the "
                    "current user_id. Returns the generated task_id."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "owner": {"type": "string"},
                        "due_at": {"type": "string", "format": "date"},
                        "linked_thread": {"type": "string"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": ["title", "source_closet", "source_file"],
                },
            },
            {
                "name": TOOL_UPDATE_TASK_STATUS,
                "description": (
                    "Transition a task to a new status. Invalidates the "
                    "prior status. Use mm_complete_task instead when "
                    "marking complete (it also writes completed_at + "
                    "outcome). Status enum: open / in_progress / waiting "
                    "/ blocked / deferred / completed / cancelled / "
                    "superseded."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "new_status": {
                            "type": "string",
                            "enum": [
                                "open",
                                "in_progress",
                                "waiting",
                                "blocked",
                                "deferred",
                                "completed",
                                "cancelled",
                                "superseded",
                            ],
                        },
                        "valid_from": {"type": "string", "format": "date"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": ["task_id", "new_status", "source_closet", "source_file"],
                },
            },
            {
                "name": TOOL_COMPLETE_TASK,
                "description": (
                    "Mark a task completed. Never deletes the task — "
                    "writes status=completed, completed_at, and "
                    "(optionally) outcome. The task remains queryable "
                    "as completed history."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "outcome": {"type": "string"},
                        "completed_at": {"type": "string", "format": "date"},
                        "source_closet": {"type": "string"},
                        "source_file": {"type": "string"},
                    },
                    "required": ["task_id", "source_closet", "source_file"],
                },
            },
            {
                "name": TOOL_LIST_TASKS,
                "description": (
                    "List currently-true tasks filtered by status / "
                    "owner / linked_thread / due_before / since. Special "
                    "status value 'active' filters to open / in_progress "
                    "/ waiting / blocked / deferred. Sorted: due_at ASC "
                    "nulls last, then last_signal_at DESC."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "owner": {"type": "string"},
                        "linked_thread": {"type": "string"},
                        "due_before": {"type": "string", "format": "date"},
                        "since": {"type": "string", "format": "date"},
                    },
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch a tool call from the agent runtime to the substrate."""
        kg = self._require_kg()
        if name == TOOL_BOOT_CONTEXT:
            return self._tool_boot_context(args)
        if name == TOOL_LIST_THREADS:
            return self._tool_list_threads()
        if name == TOOL_THREAD_STATUS:
            return self._tool_thread_status(args)
        if name == TOOL_RECORD_COMMITMENT:
            return self._tool_record_commitment(args)
        if name == TOOL_RECORD_PREFERENCE:
            return self._tool_record_preference(args)
        if name == TOOL_RECORD_DECISION:
            return self._tool_record_decision(args)
        if name == TOOL_QUERY_ENTITY:
            return self._tool_query_entity(args)
        if name == TOOL_SEARCH_RECALL:
            return self._tool_search_recall(args)
        if name == TOOL_RESOLVE_ENTITY:
            return self._tool_resolve_entity(args)
        if name == TOOL_OBSERVE:
            return self._tool_observe(args)
        if name == TOOL_CREATE_TASK:
            return self._tool_create_task(args)
        if name == TOOL_UPDATE_TASK_STATUS:
            return self._tool_update_task_status(args)
        if name == TOOL_COMPLETE_TASK:
            return self._tool_complete_task(args)
        if name == TOOL_LIST_TASKS:
            return self._tool_list_tasks(args)
        # Unknown tool — keep KG reference live to satisfy the type checker.
        del kg
        raise ValueError(f"Unknown Memory Mission tool: {name}")

    # ------------------------------------------------------------------ #
    # Optional hooks                                                       #
    # ------------------------------------------------------------------ #

    def system_prompt_block(self) -> str:
        """Static preamble Hermes injects into every system prompt.

        Names the provider + the per-user root so the model knows
        "Memory Mission Individual is mounted; here is where to look."
        The dynamic boot-context render is handled by ``prefetch``.
        """
        if self._user_id is None:
            return ""
        return (
            "## Memory Mission Individual\n"
            f"User: {self._user_id} · Root: {self._root}\n"
            "Use the `mm_*` tools to query / update operating memory. "
            "Prefer `mm_boot_context` over recompiling state by hand."
        )

    def prefetch(self, query: str) -> str:
        """Inject a COMPACT task-relevant boot slice before each inference call.

        Per Hermes' integration feedback: prefetch is high-signal-per-token
        operating state, NOT a full boot dump. The task-hint biases what
        surfaces; the token budget is intentionally tighter than the
        ``mm_boot_context`` tool (which agents call explicitly when they
        want the full picture). Active threads + open commitments +
        preferences win the budget; relevant entities + project status
        get trimmed first if over.
        """
        if self._kg is None:
            return ""
        boot = compile_individual_boot_context(
            user_id=self._require_user_id(),
            agent_id=self._agent_id,
            kg=self._kg,
            engine=self._engine,
            identity_resolver=self._identity,
            task_hint=query,
            token_budget=PREFETCH_TOKEN_BUDGET,
        )
        return boot.render()

    def queue_prefetch(self, query: str) -> None:
        """Pre-warm hook called by Hermes after a turn. V1 no-op.

        We're duck-typing the Hermes ABC (no inheritance), so an absent
        method would surface as AttributeError if Hermes calls it. The
        compile is fast enough today that the synchronous ``prefetch``
        path doesn't need pre-warming. Wire this up if profiling shows
        prefetch latency dominating per-turn cost.
        """
        del query

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Record the turn for later evidence-layer ingestion. NON-BLOCKING.

        Hermes' contract requires this to return immediately. V1 is a
        no-op: ``PersonalMemoryBackend.ingest`` takes a structured
        ``NormalizedSourceItem`` and the conversational-turn → envelope
        mapping (which connector role? what visibility?) isn't decided
        yet. When the conversational envelope shape lands (post-V1) we
        spawn a daemon thread here that wraps the turn into an envelope
        and calls ``backend.ingest`` non-blocking.
        """
        del user_content, assistant_content  # V1: turns aren't ingested yet

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Hook for transcript-level processing after the conversation ends.

        V1 no-op. Future: extract candidate facts via the host LLM and
        surface them as user-confirmation prompts on the next session
        boot.
        """

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> None:  # pragma: no cover
        """Hermes calls before context compression. V1 no-op."""

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Observability hook — Hermes notifies on every memory-mutating tool call.

        V1: no-op (we already record provenance on every write). Useful
        later for cross-provider drift detection.
        """
        del action, target, content

    def shutdown(self) -> None:
        """Close handles cleanly when the agent runtime shuts down."""
        if self._kg is not None:
            self._kg.close()
            self._kg = None

    # ------------------------------------------------------------------ #
    # Test helpers                                                         #
    # ------------------------------------------------------------------ #

    def install_handles_for_test(
        self,
        *,
        user_id: str,
        kg: PersonalKnowledgeGraph,
        engine: BrainEngine,
        identity: LocalIdentityResolver,
        session_id: str = "test-session",
        agent_id: str = "hermes",
        root: Path | None = None,
        backend: PersonalMemoryBackend | None = None,
    ) -> None:
        """Pre-install handles so tests can skip ``initialize``."""
        self._user_id = user_id
        self._agent_id = agent_id
        self._session_id = session_id
        self._kg = kg
        self._engine = engine
        self._identity = identity
        self._root = root
        self._backend = backend

    # ------------------------------------------------------------------ #
    # Tool implementations (private)                                       #
    # ------------------------------------------------------------------ #

    def _tool_boot_context(self, args: dict[str, Any]) -> dict[str, Any]:
        # Wrap in structured error capture: when the host runtime hits
        # an exception (Hermes 2026-04-27 reported "unhashable type:
        # 'slice'" surfacing through this path), it should still see
        # an actionable response shape rather than a bare traceback.
        try:
            boot = compile_individual_boot_context(
                user_id=self._require_user_id(),
                agent_id=self._agent_id,
                kg=self._require_kg(),
                engine=self._engine,
                identity_resolver=self._identity,
                task_hint=args.get("task_hint"),
                token_budget=int(args.get("token_budget", 4000)),
            )
            payload = boot.model_dump(mode="json")
            payload["render"] = boot.render()
            payload["aspect_counts"] = boot.aspect_counts
            return payload
        except Exception as exc:  # noqa: BLE001 - explicit structured surface
            import traceback

            return {
                "error": "boot_context_failed",
                "exception_type": type(exc).__name__,
                "detail": str(exc),
                "traceback": traceback.format_exc(),
                "render": "",
                "aspect_counts": {
                    "active_threads": 0,
                    "commitments": 0,
                    "preferences": 0,
                    "recent_decisions": 0,
                    "relevant_entities": 0,
                    "project_status": 0,
                },
            }

    def _tool_list_threads(self) -> list[dict[str, Any]]:
        kg = self._require_kg()
        triples = kg.query_relationship(THREAD_STATUS_PREDICATE)
        out: list[dict[str, Any]] = []
        for t in triples:
            if t.valid_to is not None:
                continue
            if t.object not in {"active", "in_progress", "blocked", "deferred"}:
                continue
            out.append(
                {
                    "thread_id": t.subject,
                    "status": t.object,
                    "last_signal_at": (t.valid_from.isoformat() if t.valid_from else None),
                    "source_closet": t.source_closet,
                    "source_file": t.source_file,
                }
            )
        out.sort(key=lambda x: x["last_signal_at"] or "", reverse=True)
        return out

    def _tool_thread_status(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        thread_id = str(args["thread_id"])
        status = str(args["status"])
        if status not in {"active", "in_progress", "blocked", "deferred", "completed"}:
            raise ValueError(
                "status must be one of: active, in_progress, blocked, deferred, completed"
            )
        _validate_source(args.get("source_closet"), args.get("source_file"))
        valid_from = _parse_date(args.get("valid_from"))
        for prior in kg.query_entity(thread_id, direction="outgoing"):
            if prior.valid_to is None and prior.predicate == THREAD_STATUS_PREDICATE:
                kg.invalidate(prior.subject, prior.predicate, prior.object, ended=valid_from)
                break
        triple = kg.add_triple(
            thread_id,
            THREAD_STATUS_PREDICATE,
            status,
            valid_from=valid_from,
            source_closet=str(args["source_closet"]),
            source_file=str(args["source_file"]),
        )
        return triple.model_dump(mode="json")

    def _tool_record_commitment(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        commitment_id = str(args["commitment_id"])
        description = str(args["description"])
        _validate_source(args.get("source_closet"), args.get("source_file"))
        source_closet = str(args["source_closet"])
        source_file = str(args["source_file"])
        due_by = _parse_date(args.get("due_by"))
        triples: list[dict[str, Any]] = []
        triples.append(
            kg.add_triple(
                commitment_id,
                COMMITMENT_STATUS_PREDICATE,
                "open",
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        triples.append(
            kg.add_triple(
                commitment_id,
                COMMITMENT_DESCRIPTION_PREDICATE,
                description,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        if due_by is not None:
            triples.append(
                kg.add_triple(
                    commitment_id,
                    COMMITMENT_DUE_PREDICATE,
                    due_by.isoformat(),
                    source_closet=source_closet,
                    source_file=source_file,
                ).model_dump(mode="json")
            )
        return {"commitment_id": commitment_id, "triples": triples}

    def _tool_record_preference(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        predicate = str(args["predicate"])
        value = str(args["value"])
        if not predicate.startswith(PREFERENCE_PREDICATE_PREFIX):
            raise ValueError(f"predicate must start with {PREFERENCE_PREDICATE_PREFIX!r}")
        _validate_source(args.get("source_closet"), args.get("source_file"))
        subject = str(args.get("subject") or self._require_user_id())
        for prior in kg.query_entity(subject, direction="outgoing"):
            if prior.valid_to is None and prior.predicate == predicate:
                kg.invalidate(prior.subject, prior.predicate, prior.object)
                break
        triple = kg.add_triple(
            subject,
            predicate,
            value,
            source_closet=str(args["source_closet"]),
            source_file=str(args["source_file"]),
        )
        return triple.model_dump(mode="json")

    def _tool_record_decision(self, args: dict[str, Any]) -> dict[str, Any]:
        engine = self._require_engine()
        _validate_source(args.get("source_closet"), args.get("source_file"))
        decided_at = _parse_date(args.get("decided_at"))
        page = new_decision_page(
            slug=str(args["slug"]),
            title=str(args["title"]),
            summary=str(args["summary"]),
            decided_at=decided_at,
            sources=[f"{args['source_closet']}:{args['source_file']}"],
        )
        engine.put_page(page, plane="personal", employee_id=self._require_user_id())
        return {
            "slug": str(args["slug"]),
            "title": str(args["title"]),
            "decided_at": decided_at.isoformat() if decided_at else None,
        }

    def _tool_query_entity(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        kg = self._require_kg()
        direction = str(args.get("direction", "outgoing"))
        if direction not in {"outgoing", "incoming", "both"}:
            raise ValueError("direction must be one of: outgoing, incoming, both")
        as_of = _parse_date(args.get("as_of"))
        triples = kg.query_entity(
            str(args["name"]),
            as_of=as_of,
            direction=direction,  # type: ignore[arg-type]
        )
        if as_of is None:
            triples = [t for t in triples if t.valid_to is None]
        return [t.model_dump(mode="json") for t in triples]

    def _tool_observe(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        kg = self._require_kg()
        subject_arg = args.get("subject")
        predicate_arg = args.get("predicate")
        since = _parse_date(args.get("since"))
        observations = kg.query_observations(
            subject=str(subject_arg) if subject_arg is not None else None,
            predicate=str(predicate_arg) if predicate_arg is not None else None,
            since=since,
        )
        return [obs.model_dump(mode="json") for obs in observations]

    def _tool_resolve_entity(self, args: dict[str, Any]) -> dict[str, Any]:
        """Resolve a typed identifier or bare entity name to canonical form.

        Behavior:
        - If ``name`` is registered as a typed identifier in the
          ``IdentityResolver`` (``email:...``, ``linkedin:...``, etc.),
          returns ``{entity_name, identity_id, canonical_name, identifiers}``.
        - Otherwise returns ``{entity_name, identity_id: None,
          canonical_name: None, identifiers: []}`` — a valid pass-through
          since KG triples are indexed by entity name directly.
        """
        name = str(args.get("name", "")).strip()
        if not name:
            raise ValueError("name must be a non-empty string")
        if self._identity is None:
            raise RuntimeError("identity resolver not initialized")

        # ``LocalIdentityResolver.lookup`` requires typed-identifier form
        # (``type:value``). Bare names (``memory-mission``, ``sven``) aren't
        # registered identifiers — return the pass-through shape directly.
        if ":" not in name:
            return {
                "entity_name": name,
                "identity_id": None,
                "canonical_name": None,
                "identifiers": [],
            }

        identity_id = self._identity.lookup(name)
        if identity_id is None:
            return {
                "entity_name": name,
                "identity_id": None,
                "canonical_name": None,
                "identifiers": [],
            }
        identity = self._identity.get_identity(identity_id)
        bindings = self._identity.bindings(identity_id)
        return {
            "entity_name": name,
            "identity_id": identity_id,
            "canonical_name": identity.canonical_name if identity else None,
            "identifiers": list(bindings),
        }

    def _tool_search_recall(self, args: dict[str, Any]) -> dict[str, Any]:
        if self._backend is None:
            return {
                "error": "no_recall_backend",
                "detail": (
                    "search_recall requires a PersonalMemoryBackend (e.g. "
                    "MemPalaceAdapter). Wire one via initialize(..., backend=...)."
                ),
                "hits": [],
            }
        hits = self._backend.query(
            question=str(args["query"]),
            employee_id=self._require_user_id(),
            limit=int(args.get("limit", 10)),
        )
        return {"hits": [h.model_dump(mode="json") for h in hits]}

    def _tool_create_task(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        title = str(args["title"])
        _validate_source(args.get("source_closet"), args.get("source_file"))
        source_closet = str(args["source_closet"])
        source_file = str(args["source_file"])
        owner = args.get("owner")
        owner_str = str(owner) if owner else self._require_user_id()
        due_at = _parse_date(args.get("due_at"))
        linked_thread = args.get("linked_thread")
        linked_str = str(linked_thread) if linked_thread else None

        task_id = f"task_{uuid.uuid4().hex}"
        triples: list[dict[str, Any]] = []
        triples.append(
            kg.add_triple(
                task_id,
                TASK_STATUS_PREDICATE,
                "open",
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        triples.append(
            kg.add_triple(
                task_id,
                TASK_TITLE_PREDICATE,
                title,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        triples.append(
            kg.add_triple(
                task_id,
                TASK_OWNER_PREDICATE,
                owner_str,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        if due_at is not None:
            triples.append(
                kg.add_triple(
                    task_id,
                    TASK_DUE_PREDICATE,
                    due_at.isoformat(),
                    source_closet=source_closet,
                    source_file=source_file,
                ).model_dump(mode="json")
            )
        if linked_str is not None:
            triples.append(
                kg.add_triple(
                    task_id,
                    TASK_LINKED_THREAD_PREDICATE,
                    linked_str,
                    source_closet=source_closet,
                    source_file=source_file,
                ).model_dump(mode="json")
            )
        return {"task_id": task_id, "owner": owner_str, "triples": triples}

    def _tool_update_task_status(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        task_id = str(args["task_id"])
        new_status = str(args["new_status"])
        if new_status not in TASK_STATUS_VALUES:
            raise ValueError("new_status must be one of: " + ", ".join(sorted(TASK_STATUS_VALUES)))
        _validate_source(args.get("source_closet"), args.get("source_file"))
        valid_from = _parse_date(args.get("valid_from"))
        for prior in kg.query_entity(task_id, direction="outgoing"):
            if prior.predicate == TASK_STATUS_PREDICATE and prior.valid_to is None:
                kg.invalidate(prior.subject, prior.predicate, prior.object, ended=valid_from)
                break
        triple = kg.add_triple(
            task_id,
            TASK_STATUS_PREDICATE,
            new_status,
            valid_from=valid_from,
            source_closet=str(args["source_closet"]),
            source_file=str(args["source_file"]),
        )
        return triple.model_dump(mode="json")

    def _tool_complete_task(self, args: dict[str, Any]) -> dict[str, Any]:
        kg = self._require_kg()
        task_id = str(args["task_id"])
        _validate_source(args.get("source_closet"), args.get("source_file"))
        source_closet = str(args["source_closet"])
        source_file = str(args["source_file"])
        completed_on = _parse_date(args.get("completed_at")) or date.today()
        outcome = args.get("outcome")
        outcome_str = str(outcome) if outcome else None

        for prior in kg.query_entity(task_id, direction="outgoing"):
            if prior.predicate == TASK_STATUS_PREDICATE and prior.valid_to is None:
                kg.invalidate(prior.subject, prior.predicate, prior.object, ended=completed_on)
                break
        triples: list[dict[str, Any]] = []
        triples.append(
            kg.add_triple(
                task_id,
                TASK_STATUS_PREDICATE,
                "completed",
                valid_from=completed_on,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        triples.append(
            kg.add_triple(
                task_id,
                TASK_COMPLETED_AT_PREDICATE,
                completed_on.isoformat(),
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
        if outcome_str is not None:
            triples.append(
                kg.add_triple(
                    task_id,
                    TASK_OUTCOME_PREDICATE,
                    outcome_str,
                    source_closet=source_closet,
                    source_file=source_file,
                ).model_dump(mode="json")
            )
        return {
            "task_id": task_id,
            "completed_at": completed_on.isoformat(),
            "triples": triples,
        }

    def _tool_list_tasks(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        kg = self._require_kg()
        status = args.get("status")
        owner = args.get("owner")
        linked_thread = args.get("linked_thread")
        due_before = _parse_date(args.get("due_before"))
        since = _parse_date(args.get("since"))

        if status is not None and status != "active" and status not in TASK_STATUS_VALUES:
            raise ValueError(
                "status must be one of: active, " + ", ".join(sorted(TASK_STATUS_VALUES))
            )

        status_triples = [
            t for t in kg.query_relationship(TASK_STATUS_PREDICATE) if t.valid_to is None
        ]
        if status == "active":
            status_triples = [t for t in status_triples if t.object in TASK_ACTIVE_STATUSES]
        elif status is not None:
            status_triples = [t for t in status_triples if t.object == status]

        out: list[dict[str, Any]] = []
        for st in status_triples:
            task_id = st.subject
            triples = [
                t for t in kg.query_entity(task_id, direction="outgoing") if t.valid_to is None
            ]
            title = next((t.object for t in triples if t.predicate == TASK_TITLE_PREDICATE), "")
            task_owner = next(
                (t.object for t in triples if t.predicate == TASK_OWNER_PREDICATE),
                None,
            )
            due_raw = next(
                (t.object for t in triples if t.predicate == TASK_DUE_PREDICATE),
                None,
            )
            completed_at_raw = next(
                (t.object for t in triples if t.predicate == TASK_COMPLETED_AT_PREDICATE),
                None,
            )
            linked = next(
                (t.object for t in triples if t.predicate == TASK_LINKED_THREAD_PREDICATE),
                None,
            )
            next_act = next(
                (t.object for t in triples if t.predicate == TASK_NEXT_ACTION_PREDICATE),
                None,
            )
            outcome_v = next(
                (t.object for t in triples if t.predicate == TASK_OUTCOME_PREDICATE),
                None,
            )

            if owner is not None and task_owner != str(owner):
                continue
            if linked_thread is not None and linked != str(linked_thread):
                continue
            due_at = date.fromisoformat(due_raw) if due_raw else None
            if due_before is not None:
                if due_at is None or due_at >= due_before:
                    continue
            if since is not None:
                if st.valid_from is None or st.valid_from < since:
                    continue

            task = Task(
                task_id=task_id,
                title=title,
                status=st.object,  # type: ignore[arg-type]
                owner=task_owner,
                due_at=due_at,
                completed_at=date.fromisoformat(completed_at_raw) if completed_at_raw else None,
                linked_thread=linked,
                next_action=next_act,
                outcome=outcome_v,
                last_signal_at=st.valid_from,
                source_closet=st.source_closet,
                source_file=st.source_file,
            )
            out.append(task.model_dump(mode="json"))

        out.sort(key=lambda t: t["last_signal_at"] or "", reverse=True)
        out.sort(key=lambda t: (t["due_at"] is None, t["due_at"] or ""))
        return out

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _require_kg(self) -> PersonalKnowledgeGraph:
        if self._kg is None:
            raise RuntimeError(
                "MemoryMissionProvider not initialized — call initialize(session_id, ...) first"
            )
        return self._kg

    def _require_engine(self) -> BrainEngine:
        if self._engine is None:
            raise RuntimeError("MemoryMissionProvider engine not initialized")
        return self._engine

    def _require_user_id(self) -> str:
        if self._user_id is None:
            raise RuntimeError("MemoryMissionProvider user_id not set")
        return self._user_id


def register(ctx: Any) -> None:
    """Hermes plugin discovery entrypoint.

    Hermes calls this from ``plugins/memory/memory-mission/__init__.py``
    (or equivalent) at startup. ``ctx`` is the Hermes plugin context;
    we duck-type-call ``register_memory_provider``.
    """
    ctx.register_memory_provider(MemoryMissionProvider())


# ---------- helpers ----------


def _validate_source(source_closet: object, source_file: object) -> None:
    if not isinstance(source_closet, str) or not source_closet.strip():
        raise ValueError("source_closet is required (use 'conversational' if no document)")
    if not isinstance(source_file, str) or not source_file.strip():
        raise ValueError("source_file is required (use the session id if conversational)")


def _parse_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


__all__ = [
    "TOOL_BOOT_CONTEXT",
    "TOOL_COMPLETE_TASK",
    "TOOL_CREATE_TASK",
    "TOOL_LIST_TASKS",
    "TOOL_LIST_THREADS",
    "TOOL_OBSERVE",
    "TOOL_QUERY_ENTITY",
    "TOOL_RECORD_COMMITMENT",
    "TOOL_RECORD_DECISION",
    "TOOL_RECORD_PREFERENCE",
    "TOOL_RESOLVE_ENTITY",
    "TOOL_SEARCH_RECALL",
    "TOOL_THREAD_STATUS",
    "TOOL_UPDATE_TASK_STATUS",
    "MemoryMissionProvider",
    "register",
]
