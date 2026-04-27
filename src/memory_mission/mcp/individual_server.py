"""Individual-mode MCP server (ADR-0015).

Distinct from the Step 18 firm-mode server (``mcp/server.py``). Exposes
the tool surface Hermes named for native-feeling personal-agent
integration:

- ``get_boot_context(task_hint?)`` — render the agent boot context
- ``list_active_threads()`` — current operating threads
- ``upsert_thread_status(thread_id, status, ...)`` — set/change a thread state
- ``record_commitment(commitment_id, description, ...)`` — open a commitment
- ``record_preference(predicate, value, ...)`` — durable user preference
- ``record_decision(slug, title, summary, ...)`` — log a decision page
- ``query_entity(name, ...)`` — currently-true triples about an entity
- ``search_recall(query, ...)`` — evidence-layer recall via the personal backend

Personal-plane writes use the simple-write policy from ADR-0015 — no
proposal gate, but provenance (``source_closet`` + ``source_file``) is
mandatory. The conversational session itself is a valid source: pass
``source_closet="conversational"`` and ``source_file="<session_id>"``
when no document caused the write.

Launch CLI:

    python -m memory_mission.mcp.individual_server \\
        --root ~/.memory-mission \\
        --user-id sven \\
        --agent-id hermes
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

import structlog
import typer
from mcp.server.fastmcp import FastMCP

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp.individual_context import IndividualMcpContext
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

_log = structlog.get_logger(__name__)

_context: IndividualMcpContext | None = None

# Versioned name lets a future contract change ride alongside the v1
# surface without forcing every Hermes/Codex client to migrate at once.
mcp: FastMCP = FastMCP("memory-mission-individual/v1")


# ---------- Context lifecycle ----------


def _ctx() -> IndividualMcpContext:
    """Return the active context or raise if uninitialized."""
    if _context is None:
        raise RuntimeError(
            "Individual MCP server context not initialized — "
            "call initialize() or initialize_from_handles() first"
        )
    return _context


def initialize(
    *,
    root: Path,
    user_id: str,
    agent_id: str = "individual-agent",
) -> IndividualMcpContext:
    """Open per-user handles from disk and install the context.

    Layout under ``root``:

    - ``identity.sqlite3`` — per-user identity resolver
    - ``personal/<user_id>/personal_kg.db`` — per-user temporal KG
    - ``.observability/`` — append-only audit log root
    """
    validate_employee_id(user_id)
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    identity = LocalIdentityResolver(root / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=root,
        employee_id=user_id,
        identity_resolver=identity,
    )
    engine: BrainEngine = InMemoryEngine()
    engine.connect()

    obs_root = root / ".observability"
    obs_root.mkdir(parents=True, exist_ok=True)

    ctx = IndividualMcpContext(
        user_id=user_id,
        agent_id=agent_id,
        kg=kg,
        engine=engine,
        identity=identity,
        observability_root=obs_root,
    )
    install(ctx)
    return ctx


def initialize_from_handles(
    *,
    user_id: str,
    agent_id: str,
    kg: PersonalKnowledgeGraph,
    engine: BrainEngine,
    identity: LocalIdentityResolver,
    observability_root: Path,
    backend: Any | None = None,
) -> IndividualMcpContext:
    """Install a context from pre-built handles. Used by tests + embedding hosts."""
    ctx = IndividualMcpContext(
        user_id=user_id,
        agent_id=agent_id,
        kg=kg,
        engine=engine,
        identity=identity,
        observability_root=observability_root,
        backend=backend,
    )
    install(ctx)
    return ctx


def install(ctx: IndividualMcpContext) -> None:
    """Replace the module-level context. Tools read from this slot."""
    global _context
    _context = ctx


def reset() -> None:
    """Clear the module-level context. Tests use this between cases."""
    global _context
    _context = None


# ---------- Provenance validation ----------


def _validate_source(source_closet: str, source_file: str) -> None:
    if not source_closet or not source_closet.strip():
        raise ValueError("source_closet is required (use 'conversational' if no document)")
    if not source_file or not source_file.strip():
        raise ValueError("source_file is required (use the session id if conversational)")


# ---------- Boot context ----------


@mcp.tool()
def get_boot_context(
    task_hint: str | None = None,
    token_budget: int = 4000,
) -> dict[str, Any]:
    """Compile the individual agent boot context (ADR-0015).

    Returns a dict with both structured aspects and a rendered
    markdown ``render`` key suitable for system-prompt injection.
    """
    ctx = _ctx()
    boot = compile_individual_boot_context(
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        kg=ctx.kg,
        engine=ctx.engine,
        identity_resolver=ctx.identity,
        task_hint=task_hint,
        token_budget=token_budget,
    )
    payload = boot.model_dump(mode="json")
    payload["render"] = boot.render()
    payload["aspect_counts"] = boot.aspect_counts
    return payload


# ---------- Threads ----------


@mcp.tool()
def list_active_threads() -> list[dict[str, Any]]:
    """List active threads (currently-true ``thread_status`` triples)."""
    ctx = _ctx()
    triples = ctx.kg.query_relationship(THREAD_STATUS_PREDICATE)
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
                "last_signal_at": t.valid_from.isoformat() if t.valid_from else None,
                "source_closet": t.source_closet,
                "source_file": t.source_file,
            }
        )
    out.sort(key=lambda x: x["last_signal_at"] or "", reverse=True)
    return out


@mcp.tool()
def upsert_thread_status(
    thread_id: str,
    status: str,
    source_closet: Annotated[str, "Provenance closet — 'conversational' if no doc"],
    source_file: Annotated[str, "Provenance file — session id if conversational"],
    valid_from: date | None = None,
) -> dict[str, Any]:
    """Set or change a thread's status. Invalidates the prior status if any."""
    ctx = _ctx()
    if status not in {"active", "in_progress", "blocked", "deferred", "completed"}:
        raise ValueError("status must be one of: active, in_progress, blocked, deferred, completed")
    _validate_source(source_closet, source_file)

    # Invalidate the prior currently-true status, if any.
    prior = ctx.kg.query_entity(thread_id, direction="outgoing")
    for p in prior:
        if p.valid_to is None and p.predicate == THREAD_STATUS_PREDICATE:
            ctx.kg.invalidate(p.subject, p.predicate, p.object, ended=valid_from)
            break

    triple = ctx.kg.add_triple(
        thread_id,
        THREAD_STATUS_PREDICATE,
        status,
        valid_from=valid_from,
        source_closet=source_closet,
        source_file=source_file,
    )
    return triple.model_dump(mode="json")


# ---------- Commitments ----------


@mcp.tool()
def record_commitment(
    commitment_id: str,
    description: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    due_by: date | None = None,
    status: str = "open",
) -> dict[str, Any]:
    """Open a commitment (status + description + optional due_by).

    Writes three triples atomically (status, description, optional
    due_by). Returns the IDs of all writes for confirmation.
    """
    ctx = _ctx()
    if status not in {"open", "completed", "blocked", "cancelled"}:
        raise ValueError("status must be one of: open, completed, blocked, cancelled")
    _validate_source(source_closet, source_file)

    written: list[dict[str, Any]] = []
    written.append(
        ctx.kg.add_triple(
            commitment_id,
            COMMITMENT_STATUS_PREDICATE,
            status,
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    written.append(
        ctx.kg.add_triple(
            commitment_id,
            COMMITMENT_DESCRIPTION_PREDICATE,
            description,
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    if due_by is not None:
        written.append(
            ctx.kg.add_triple(
                commitment_id,
                COMMITMENT_DUE_PREDICATE,
                due_by.isoformat(),
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
    return {"commitment_id": commitment_id, "triples": written}


# ---------- Preferences ----------


@mcp.tool()
def record_preference(
    predicate: str,
    value: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    subject: str | None = None,
) -> dict[str, Any]:
    """Record a durable user preference. Replaces prior value for the same predicate.

    ``predicate`` must start with ``prefers_`` (e.g. ``prefers_reply_style``).
    ``subject`` defaults to the user_id when not supplied.
    """
    ctx = _ctx()
    if not predicate.startswith(PREFERENCE_PREDICATE_PREFIX):
        raise ValueError(
            f"predicate must start with {PREFERENCE_PREDICATE_PREFIX!r} (e.g. prefers_reply_style)"
        )
    _validate_source(source_closet, source_file)
    subj = subject or ctx.user_id

    # Invalidate the prior preference triple for the same (subject, predicate).
    for p in ctx.kg.query_entity(subj, direction="outgoing"):
        if p.valid_to is None and p.predicate == predicate:
            ctx.kg.invalidate(p.subject, p.predicate, p.object)
            break

    triple = ctx.kg.add_triple(
        subj,
        predicate,
        value,
        source_closet=source_closet,
        source_file=source_file,
    )
    return triple.model_dump(mode="json")


# ---------- Decisions ----------


@mcp.tool()
def record_decision(
    slug: str,
    title: str,
    summary: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    decided_at: date | None = None,
) -> dict[str, Any]:
    """Log a tier=decision page on the personal plane.

    The boot-context compiler surfaces these in ``recent_decisions``
    for 60 days. Source provenance is stored in the page's ``sources``
    frontmatter list as ``<closet>:<file>``.
    """
    ctx = _ctx()
    _validate_source(source_closet, source_file)

    page = new_decision_page(
        slug=slug,
        title=title,
        summary=summary,
        decided_at=decided_at,
        sources=[f"{source_closet}:{source_file}"],
    )
    ctx.engine.put_page(page, plane="personal", employee_id=ctx.user_id)
    return {
        "slug": slug,
        "title": title,
        "decided_at": decided_at.isoformat() if decided_at else None,
    }


# ---------- Entity queries ----------


@mcp.tool()
def query_entity(
    name: str,
    direction: str = "outgoing",
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Currently-true triples about ``name`` on the personal plane.

    ``direction``: ``outgoing`` / ``incoming`` / ``both``.
    """
    ctx = _ctx()
    if direction not in {"outgoing", "incoming", "both"}:
        raise ValueError("direction must be one of: outgoing, incoming, both")
    triples = ctx.kg.query_entity(
        name,
        as_of=as_of,
        direction=direction,  # type: ignore[arg-type]  # validated above
    )
    # Default to currently-true filtering when no as_of supplied — the
    # MCP caller almost always wants "what's true now" rather than the
    # full history.
    if as_of is None:
        triples = [t for t in triples if t.valid_to is None]
    return [t.model_dump(mode="json") for t in triples]


# ---------- Recall (evidence layer) ----------


@mcp.tool()
def search_recall(query: str, limit: int = 10) -> dict[str, Any]:
    """Search the evidence-layer (MemPalace) recall index, if attached.

    Returns ``{"hits": [...]}`` when a personal backend is wired up;
    otherwise a structured error pointing at the ADR. Individual mode
    is usable without MemPalace — recall just isn't available.
    """
    ctx = _ctx()
    if ctx.backend is None:
        return {
            "error": "no_recall_backend",
            "detail": (
                "search_recall requires a PersonalMemoryBackend (e.g. MemPalaceAdapter) "
                "wired into the server context. See ADR-0015 §1 + ADR-0004."
            ),
            "hits": [],
        }
    hits = ctx.backend.query(question=query, limit=limit, employee_id=ctx.user_id)
    return {"hits": [h.model_dump(mode="json") for h in hits]}


# ---------- Identity resolution ----------


@mcp.tool()
def resolve_entity(name: str) -> dict[str, Any]:
    """Resolve a typed identifier or bare entity name to canonical form.

    For typed identifiers (``email:foo@bar.com``, ``linkedin:abc``):
    returns the bound ``identity_id``, ``canonical_name``, and the full
    set of identifiers attached to that identity.

    For bare names not registered in the resolver: returns the name as
    ``entity_name`` with ``identity_id=None`` — KG triples are indexed
    by entity name directly so the pass-through is valid.

    Use as STEP 1 of any retrieval planner that wants to disambiguate
    'sven' vs 'email:sven@example.com' before querying KG state.
    """
    ctx = _ctx()
    name = name.strip()
    if not name:
        raise ValueError("name must be a non-empty string")

    # ``LocalIdentityResolver.lookup`` requires typed-identifier form
    # (``type:value``). Bare names pass through as entity names.
    if ":" not in name:
        return {
            "entity_name": name,
            "identity_id": None,
            "canonical_name": None,
            "identifiers": [],
        }

    identity_id = ctx.identity.lookup(name)
    if identity_id is None:
        return {
            "entity_name": name,
            "identity_id": None,
            "canonical_name": None,
            "identifiers": [],
        }
    identity = ctx.identity.get_identity(identity_id)
    bindings = ctx.identity.bindings(identity_id)
    return {
        "entity_name": name,
        "identity_id": identity_id,
        "canonical_name": identity.canonical_name if identity else None,
        "identifiers": list(bindings),
    }


# ---------- CLI ----------


cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.command()
def serve(
    root: Path = typer.Option(  # noqa: B008 - typer pattern requires Option in defaults
        ...,
        "--root",
        help="Memory Mission root directory (e.g. ~/.memory-mission)",
    ),
    user_id: str = typer.Option(  # noqa: B008
        ..., "--user-id", help="The personal-plane user id"
    ),
    agent_id: str = typer.Option(  # noqa: B008
        "individual-agent",
        "--agent-id",
        help="Identifier of the agent runtime that will receive the context",
    ),
) -> None:  # pragma: no cover - CLI entrypoint
    """Launch the individual-mode MCP server over stdio."""
    initialize(root=root, user_id=user_id, agent_id=agent_id)
    _log.info("individual_mcp_server_starting", user_id=user_id, agent_id=agent_id)
    mcp.run()


def app() -> None:  # pragma: no cover - CLI entrypoint
    cli()


__all__ = [
    "IndividualMcpContext",
    "app",
    "initialize",
    "initialize_from_handles",
    "install",
    "mcp",
    "reset",
]
