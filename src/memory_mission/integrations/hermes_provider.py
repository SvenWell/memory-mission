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
    THREAD_STATUS_PREDICATE,
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
    "TOOL_LIST_THREADS",
    "TOOL_QUERY_ENTITY",
    "TOOL_RECORD_COMMITMENT",
    "TOOL_RECORD_DECISION",
    "TOOL_RECORD_PREFERENCE",
    "TOOL_SEARCH_RECALL",
    "TOOL_THREAD_STATUS",
    "MemoryMissionProvider",
    "register",
]
