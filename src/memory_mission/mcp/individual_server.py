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

import time
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
import typer
from mcp.server.fastmcp import FastMCP

from memory_mission import __version__
from memory_mission.eval.captures import captures_path_for, record_eval_capture
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp.individual_context import IndividualMcpContext
from memory_mission.memory.engine import BrainEngine, FileSystemEngine
from memory_mission.memory.schema import validate_employee_id
from memory_mission.memory.tiers import Tier
from memory_mission.observability import ObservabilityLogger, PersonalFactWriteEvent
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.personal_brain.working_pages import new_decision_page
from memory_mission.synthesis.compile import compile_agent_context as _compile_synthesis
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
    # FileSystemEngine makes record_decision durable across short-lived
    # Hermes/Codex MCP subprocesses while keeping the BrainEngine Protocol.
    engine: BrainEngine = FileSystemEngine(root)
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
        eval_captures_path=captures_path_for(root=root, user_id=user_id),
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
    eval_captures_path: Path | None = None,
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
        eval_captures_path=eval_captures_path,
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


def _audit_personal_fact_write(
    ctx: IndividualMcpContext,
    *,
    action: Literal["record_facts", "invalidate_fact"],
    subject: str,
    facts: list[dict[str, object]],
    outcome: dict[str, object],
    source_closet: str | None = None,
    source_file: str | None = None,
    source_quote: str | None = None,
    rationale: str | None = None,
) -> None:
    logger = ObservabilityLogger(ctx.observability_root, firm_id=ctx.user_id)
    logger.write(
        PersonalFactWriteEvent(
            firm_id=ctx.user_id,
            employee_id=ctx.user_id,
            action=action,
            subject=subject,
            source_closet=source_closet,
            source_file=source_file,
            source_quote=source_quote,
            rationale=rationale,
            facts=facts,
            outcome=outcome,
        )
    )


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
    started = time.perf_counter()
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
    record_eval_capture(
        captures_path=ctx.eval_captures_path,
        user_id=ctx.user_id,
        tool_name="mm_boot_context",
        args={"task_hint": task_hint, "token_budget": token_budget},
        result=payload,
        latency_ms=int((time.perf_counter() - started) * 1000),
        mm_version=__version__,
    )
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


# ---------- Tasks (Phase A — durable obligation, never deleted on completion) ----------


@mcp.tool()
def create_task(
    title: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    owner: str | None = None,
    due_at: date | None = None,
    linked_thread: str | None = None,
) -> dict[str, Any]:
    """Open a new task. Status defaults to ``open``.

    A task is a durable obligation — it never gets deleted when
    completed; ``mm_complete_task`` changes its state. ``owner``
    defaults to the current ``user_id``. Returns the generated
    ``task_id`` plus the triples written for confirmation.
    """
    ctx = _ctx()
    _validate_source(source_closet, source_file)
    task_id = f"task_{uuid.uuid4().hex}"
    resolved_owner = owner or ctx.user_id

    written: list[dict[str, Any]] = []
    written.append(
        ctx.kg.add_triple(
            task_id,
            TASK_STATUS_PREDICATE,
            "open",
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    written.append(
        ctx.kg.add_triple(
            task_id,
            TASK_TITLE_PREDICATE,
            title,
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    written.append(
        ctx.kg.add_triple(
            task_id,
            TASK_OWNER_PREDICATE,
            resolved_owner,
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    if due_at is not None:
        written.append(
            ctx.kg.add_triple(
                task_id,
                TASK_DUE_PREDICATE,
                due_at.isoformat(),
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
    if linked_thread is not None:
        written.append(
            ctx.kg.add_triple(
                task_id,
                TASK_LINKED_THREAD_PREDICATE,
                linked_thread,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
    return {"task_id": task_id, "owner": resolved_owner, "triples": written}


@mcp.tool()
def update_task_status(
    task_id: str,
    new_status: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    valid_from: date | None = None,
) -> dict[str, Any]:
    """Transition a task to a new status. Invalidates the prior status.

    For state changes that complete the task, prefer ``mm_complete_task``
    — it also writes the completion timestamp + optional outcome. This
    tool handles non-completion transitions: open -> in_progress,
    open -> waiting, in_progress -> blocked, etc.
    """
    if new_status not in TASK_STATUS_VALUES:
        raise ValueError("new_status must be one of: " + ", ".join(sorted(TASK_STATUS_VALUES)))
    _validate_source(source_closet, source_file)
    ctx = _ctx()
    for prior in ctx.kg.query_entity(task_id, direction="outgoing"):
        if prior.predicate == TASK_STATUS_PREDICATE and prior.valid_to is None:
            ctx.kg.invalidate(prior.subject, prior.predicate, prior.object, ended=valid_from)
            break
    triple = ctx.kg.add_triple(
        task_id,
        TASK_STATUS_PREDICATE,
        new_status,
        valid_from=valid_from,
        source_closet=source_closet,
        source_file=source_file,
    )
    return triple.model_dump(mode="json")


@mcp.tool()
def complete_task(
    task_id: str,
    source_closet: Annotated[str, "Provenance closet"],
    source_file: Annotated[str, "Provenance file"],
    outcome: str | None = None,
    completed_at: date | None = None,
) -> dict[str, Any]:
    """Mark a task completed. Never deletes; sets status + completed_at + optional outcome.

    Per the design brief: "a task is not removed when completed. It
    changes state." This invalidates the prior ``task_status`` triple,
    writes ``task_status="completed"``, writes ``task_completed_at``,
    and (if provided) writes ``task_outcome``. The task remains
    queryable as completed history.
    """
    _validate_source(source_closet, source_file)
    ctx = _ctx()
    completed_on = completed_at or date.today()
    for prior in ctx.kg.query_entity(task_id, direction="outgoing"):
        if prior.predicate == TASK_STATUS_PREDICATE and prior.valid_to is None:
            ctx.kg.invalidate(prior.subject, prior.predicate, prior.object, ended=completed_on)
            break
    written: list[dict[str, Any]] = []
    written.append(
        ctx.kg.add_triple(
            task_id,
            TASK_STATUS_PREDICATE,
            "completed",
            valid_from=completed_on,
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    written.append(
        ctx.kg.add_triple(
            task_id,
            TASK_COMPLETED_AT_PREDICATE,
            completed_on.isoformat(),
            source_closet=source_closet,
            source_file=source_file,
        ).model_dump(mode="json")
    )
    if outcome is not None:
        written.append(
            ctx.kg.add_triple(
                task_id,
                TASK_OUTCOME_PREDICATE,
                outcome,
                source_closet=source_closet,
                source_file=source_file,
            ).model_dump(mode="json")
        )
    return {
        "task_id": task_id,
        "completed_at": completed_on.isoformat(),
        "triples": written,
    }


@mcp.tool()
def list_tasks(
    status: str | None = None,
    owner: str | None = None,
    linked_thread: str | None = None,
    due_before: date | None = None,
    since: date | None = None,
) -> list[dict[str, Any]]:
    """List currently-true tasks filtered by status / owner / linked_thread / due / since.

    All filters optional. Default (no filters) returns every
    currently-true task. Special status value ``"active"`` filters to
    open / in_progress / waiting / blocked / deferred (the not-yet-
    finished bucket).

    Sorted: due_at ASC nulls last, then last_signal_at DESC.
    """
    if status is not None and status != "active" and status not in TASK_STATUS_VALUES:
        raise ValueError("status must be one of: active, " + ", ".join(sorted(TASK_STATUS_VALUES)))
    ctx = _ctx()
    status_triples = [
        t for t in ctx.kg.query_relationship(TASK_STATUS_PREDICATE) if t.valid_to is None
    ]
    if status == "active":
        status_triples = [t for t in status_triples if t.object in TASK_ACTIVE_STATUSES]
    elif status is not None:
        status_triples = [t for t in status_triples if t.object == status]

    out: list[dict[str, Any]] = []
    for st in status_triples:
        task_id = st.subject
        triples = [
            t for t in ctx.kg.query_entity(task_id, direction="outgoing") if t.valid_to is None
        ]
        title = next(
            (t.object for t in triples if t.predicate == TASK_TITLE_PREDICATE),
            "",
        )
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

        if owner is not None and task_owner != owner:
            continue
        if linked_thread is not None and linked != linked_thread:
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
            status=st.object,  # type: ignore[arg-type]  # Literal narrowed by query filter
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

    # Sort: stable two-pass — last_signal_at DESC first, then due_at
    # ASC (None last). Stable sort means due_at is the primary key.
    out.sort(key=lambda t: t["last_signal_at"] or "", reverse=True)
    out.sort(key=lambda t: (t["due_at"] is None, t["due_at"] or ""))
    return out


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


# ---------- Structured fact write surface ----------


_OBJECT_TYPE_TO_ENTITY_KIND: dict[str, str] = {
    "person": "person",
    "organization": "organization",
    "company": "organization",
    "firm": "organization",
    "fund": "organization",
    "team": "organization",
    "project": "project",
    "product": "product",
    "tool": "tool",
    "topic": "topic",
    "deal": "deal",
}


def _slugify(s: str) -> str:
    """Kebab-case a free-text name. Conservative — only alnum + hyphen."""
    import re as _re

    s = (s or "").strip().lower()
    s = _re.sub(r"[^\w\s-]", "", s, flags=_re.UNICODE)
    s = _re.sub(r"[\s_]+", "-", s)
    s = _re.sub(r"-+", "-", s).strip("-")
    return s or "unnamed"


def _entity_exists(ctx: IndividualMcpContext, entity_id: str) -> bool:
    """Best-effort entity existence check without creating anything."""
    raw_kg = getattr(ctx.kg, "_kg", None)
    get_entity = getattr(raw_kg, "get_entity", None)
    if callable(get_entity) and get_entity(entity_id) is not None:
        return True
    return bool(ctx.kg.query_entity(entity_id, direction="both"))


def _resolve_subject(
    *,
    entity_name: str,
    entity_type: str,
    identifiers: list[str],
    properties: dict[str, str] | None,
    ctx: IndividualMcpContext,
    create_if_missing: bool,
    dry_run: bool,
) -> tuple[str, bool]:
    """Resolve an entity to its canonical id.

    Returns ``(canonical_id, created)``. When identifiers are supplied,
    routes through ``IdentityResolver.resolve`` so the same person reached
    via different channels collapses to one stable id. When no identifiers,
    falls back to a slug derived from ``entity_name``.
    """
    if identifiers:
        kind = _OBJECT_TYPE_TO_ENTITY_KIND.get(entity_type, "person")
        resolver_kind = "organization" if kind == "organization" else "person"
        existing_ids: set[str] = set()
        for ident in identifiers:
            existing = ctx.identity.lookup(ident)
            if existing is not None:
                existing_ids.add(existing)
        if len(existing_ids) > 1:
            raise ValueError("identifiers resolve to multiple existing identities")
        existing = next(iter(existing_ids), None)
        if existing is None:
            if not create_if_missing:
                raise ValueError("entity not found and create_if_missing is false")
            if dry_run:
                return (_slugify(entity_name), True)
        elif dry_run:
            return existing, False
        canonical_id = ctx.identity.resolve(
            set(identifiers),
            entity_type=resolver_kind,  # type: ignore[arg-type]
            canonical_name=_slugify(entity_name),
        )
        created = existing is None
    else:
        canonical_id = _slugify(entity_name)
        existing_entity = _entity_exists(ctx, canonical_id)
        if not existing_entity and not create_if_missing:
            raise ValueError("entity not found and create_if_missing is false")
        created = not existing_entity

    if not dry_run:
        ctx.kg.add_entity(
            canonical_id,
            entity_type=entity_type or "unknown",
            properties=properties or {},
        )
    return canonical_id, created


def _normalise_object(
    raw: Any,
    *,
    ctx: IndividualMcpContext,
    dry_run: bool,
) -> tuple[str, str]:
    """Normalise a fact's ``object`` to ``(slug_or_literal, object_type)``.

    The wire shape accepts either a bare string (treated as ``literal``) or
    a dict with ``value`` + ``type`` + optional ``entity_type``. When
    ``type == "entity"``, ensures ``add_entity`` is called for the object
    side so subsequent queries against it return rows from the entities table.
    """
    if isinstance(raw, str):
        return raw, "literal"
    if not isinstance(raw, dict):
        raise ValueError(f"object must be string or dict, got {type(raw).__name__}")
    value = str(raw.get("value", "")).strip()
    if not value:
        raise ValueError("object.value is required")
    obj_type = str(raw.get("type", "literal")).lower()
    if obj_type == "entity":
        slug = _slugify(value)
        if not dry_run:
            ctx.kg.add_entity(
                slug,
                entity_type=raw.get("entity_type", "unknown"),
                properties=raw.get("properties") or {},
            )
        return slug, obj_type
    return value, obj_type


def _parse_iso_date(value: Any, *, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    try:
        return date.fromisoformat(s.split("T", 1)[0])
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date or datetime") from exc


@mcp.tool()
def record_facts(
    entity_name: Annotated[
        str,
        "Free-text entity name. Will be canonicalised via IdentityResolver "
        "when ``identifiers`` is provided; otherwise stored as a slug.",
    ],
    facts: Annotated[
        list[dict[str, Any]],
        "List of fact dicts. Each must have ``predicate`` and ``object``. "
        "Optional: ``confidence`` (0-1, default 0.85), ``valid_from``/``valid_to`` "
        "(ISO dates), ``event_time`` (ISO datetime — used as ``valid_from`` if "
        "``valid_from`` not given), ``write_mode`` (``upsert`` (default) or "
        "``supersede``). ``object`` may be a bare string or "
        "``{value, type, entity_type?}`` — when ``type=='entity'`` the object "
        "side is registered in the entities table too.",
    ],
    source_closet: Annotated[
        str,
        "Provenance closet — e.g. 'granola', 'gmail', 'conversational', 'whatsapp'.",
    ],
    source_file: Annotated[
        str,
        "Provenance file id — meeting/message id, or session id when source_closet is "
        "'conversational'.",
    ],
    entity_type: Annotated[str, "Entity kind: person, organization, project, etc."] = "unknown",
    identifiers: Annotated[
        list[str] | None,
        "Typed identifiers (``email:foo@bar.com``, ``linkedin:...``) — bind the "
        "entity to one canonical id across channels.",
    ] = None,
    properties: Annotated[
        dict[str, str] | None,
        "Extra entity properties to register (role, location, founded date, etc.).",
    ] = None,
    create_if_missing: bool = True,
    source_quote: Annotated[
        str | None,
        "Optional verbatim excerpt that supports the facts; recorded for audit.",
    ] = None,
    dry_run: Annotated[
        bool,
        "When true, return what would be written without touching the KG.",
    ] = False,
) -> dict[str, Any]:
    """Record structured facts about an entity on the personal plane.

    The single agent-callable entry point for "the user told me X about Y".
    Walks the framework's existing primitives (IdentityResolver,
    add_entity, corroborate / add_triple, invalidate) so callers get
    canonicalisation, idempotency, and provenance for free.

    For each fact:

    - ``upsert`` (default) — if the same ``(subject, predicate, object)`` is
      currently true, ``corroborate`` (Noisy-OR confidence bump + appended
      provenance). Otherwise ``add_triple``.
    - ``supersede`` — invalidate any currently-true triple with the same
      ``(subject, predicate)`` and a different object, then ``add_triple`` the
      new value. For mutable facts like ``role`` or ``works_at``.

    Returns a per-fact outcome list plus aggregate counts. With ``dry_run``,
    no writes happen — useful for previewing what an agent would change.
    """
    ctx = _ctx()
    _validate_source(source_closet, source_file)
    if not facts:
        raise ValueError("at least one fact required")
    for raw_fact in facts:
        if isinstance(raw_fact, dict):
            _parse_iso_date(raw_fact.get("valid_from"), field_name="valid_from")
            _parse_iso_date(raw_fact.get("valid_to"), field_name="valid_to")
            _parse_iso_date(raw_fact.get("event_time"), field_name="event_time")

    subject, created_entity = _resolve_subject(
        entity_name=entity_name,
        entity_type=entity_type,
        identifiers=list(identifiers or []),
        properties=properties,
        ctx=ctx,
        create_if_missing=create_if_missing,
        dry_run=dry_run,
    )

    outcomes: list[dict[str, Any]] = []
    inserted = corroborated = superseded = skipped = 0
    warnings: list[str] = []

    for raw_fact in facts:
        if not isinstance(raw_fact, dict):
            outcomes.append({"status": "skipped", "reason": "fact must be a dict"})
            skipped += 1
            continue

        predicate = str(raw_fact.get("predicate", "")).strip()
        if not predicate:
            outcomes.append({"status": "skipped", "reason": "missing predicate"})
            skipped += 1
            continue

        try:
            obj_value, obj_type = _normalise_object(
                raw_fact.get("object"),
                ctx=ctx,
                dry_run=dry_run,
            )
        except ValueError as exc:
            outcomes.append({"predicate": predicate, "status": "skipped", "reason": str(exc)})
            skipped += 1
            continue

        confidence = float(raw_fact.get("confidence", 0.85))
        if not 0.0 <= confidence <= 1.0:
            warnings.append(f"clamped confidence {confidence!r} to [0,1]")
            confidence = max(0.0, min(1.0, confidence))

        valid_from = _parse_iso_date(raw_fact.get("valid_from"), field_name="valid_from")
        if valid_from is None and raw_fact.get("event_time"):
            valid_from = _parse_iso_date(raw_fact.get("event_time"), field_name="event_time")
        valid_to = _parse_iso_date(raw_fact.get("valid_to"), field_name="valid_to")
        write_mode = str(raw_fact.get("write_mode", "upsert")).lower()

        outcome: dict[str, Any] = {
            "predicate": predicate,
            "object": obj_value,
            "object_type": obj_type,
            "confidence": confidence,
        }

        if dry_run:
            existing = ctx.kg.find_current_triple(subject, predicate, obj_value)
            outcome["status"] = "would_corroborate" if existing else "would_insert"
            outcomes.append(outcome)
            continue

        if write_mode == "supersede":
            invalidated = 0
            for prior in ctx.kg.query_entity(subject, direction="outgoing"):
                if (
                    prior.predicate == predicate
                    and prior.object != obj_value
                    and prior.valid_to is None
                ):
                    invalidated += ctx.kg.invalidate(
                        prior.subject, prior.predicate, prior.object, ended=valid_from
                    )
            ctx.kg.add_triple(
                subject,
                predicate,
                obj_value,
                valid_from=valid_from,
                valid_to=valid_to,
                confidence=confidence,
                source_closet=source_closet,
                source_file=source_file,
            )
            outcome["status"] = "superseded" if invalidated else "inserted"
            outcome["invalidated_priors"] = invalidated
            if invalidated:
                superseded += 1
            else:
                inserted += 1
        else:
            existing = ctx.kg.corroborate(
                subject,
                predicate,
                obj_value,
                confidence=confidence,
                source_closet=source_closet,
                source_file=source_file,
            )
            if existing is not None:
                outcome["status"] = "corroborated"
                corroborated += 1
            else:
                ctx.kg.add_triple(
                    subject,
                    predicate,
                    obj_value,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    confidence=confidence,
                    source_closet=source_closet,
                    source_file=source_file,
                )
                outcome["status"] = "inserted"
                inserted += 1

        outcomes.append(outcome)

    result = {
        "entity_id": subject,
        "created_entity": created_entity,
        "facts": outcomes,
        "inserted_count": inserted,
        "corroborated_count": corroborated,
        "superseded_count": superseded,
        "skipped_count": skipped,
        "warnings": warnings,
        "dry_run": dry_run,
    }
    if not dry_run:
        _audit_personal_fact_write(
            ctx,
            action="record_facts",
            subject=subject,
            source_closet=source_closet,
            source_file=source_file,
            source_quote=source_quote,
            facts=outcomes,
            outcome={
                "inserted_count": inserted,
                "corroborated_count": corroborated,
                "superseded_count": superseded,
                "skipped_count": skipped,
                "warnings": warnings,
            },
        )
    return result


@mcp.tool()
def invalidate_fact(
    subject: Annotated[str, "Entity name / canonical id whose triple is being invalidated."],
    predicate: Annotated[str, "Predicate of the triple to invalidate."],
    object: Annotated[str, "Object of the triple to invalidate (must match exactly)."],
    rationale: Annotated[
        str,
        "Why this fact is being invalidated. Required — recorded for audit.",
    ],
    ended: date | None = None,
) -> dict[str, Any]:
    """Invalidate a currently-true triple. For corrections.

    Sets ``valid_to`` on the triple so it stops appearing in
    ``query_entity`` and friends, but keeps the row + provenance for the
    audit trail. ``rationale`` is required and surfaced in observability.
    """
    ctx = _ctx()
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required for invalidate_fact")
    n = ctx.kg.invalidate(subject, predicate, object, ended=ended)
    result = {
        "subject": subject,
        "predicate": predicate,
        "object": object,
        "invalidated_count": n,
        "rationale": rationale,
        "ended": ended.isoformat() if ended else None,
    }
    _audit_personal_fact_write(
        ctx,
        action="invalidate_fact",
        subject=subject,
        rationale=rationale,
        facts=[
            {
                "predicate": predicate,
                "object": object,
                "ended": ended.isoformat() if ended else None,
            }
        ],
        outcome={"invalidated_count": n},
    )
    return result


# ---------- Entity queries ----------


@mcp.tool()
def query_entity(
    name: str,
    direction: str = "outgoing",
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Currently-true triples about ``name`` on the personal plane.

    ``direction``: ``outgoing`` / ``incoming`` / ``both``.

    When two or more currently-true triples share the same
    ``(subject, predicate)`` but disagree on ``object`` (e.g. two
    ``works_at`` triples with different employers), each conflicting
    triple is annotated with a ``conflicts_with`` field listing the
    other live objects + their confidences + provenance. This lets
    the agent reason about contradictions on read instead of treating
    equal-rank triples as independent answers.

    The annotation is additive — existing callers iterating the returned
    list see the same triple shape they always have, plus an optional
    ``conflicts_with`` key when relevant. ADR-0001 corroboration is
    NOT a conflict (same subject + predicate + object) and never surfaces.
    """
    from collections import defaultdict

    ctx = _ctx()
    if direction not in {"outgoing", "incoming", "both"}:
        raise ValueError("direction must be one of: outgoing, incoming, both")
    started = time.perf_counter()
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

    # Group by (subject, predicate) to detect intra-result contradictions.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for t in triples:
        groups[(t.subject, t.predicate)].append(t)

    out: list[dict[str, Any]] = []
    for t in triples:
        payload = t.model_dump(mode="json")
        peers = [other for other in groups[(t.subject, t.predicate)] if other.object != t.object]
        if peers:
            payload["conflicts_with"] = [
                {
                    "object": other.object,
                    "confidence": other.confidence,
                    "source_closet": other.source_closet,
                    "source_file": other.source_file,
                }
                for other in sorted(peers, key=lambda x: -x.confidence)
            ]
        out.append(payload)
    record_eval_capture(
        captures_path=ctx.eval_captures_path,
        user_id=ctx.user_id,
        tool_name="mm_query_entity",
        args={
            "name": name,
            "direction": direction,
            "as_of": as_of.isoformat() if as_of else None,
        },
        result=out,
        latency_ms=int((time.perf_counter() - started) * 1000),
        mm_version=__version__,
    )
    return out


# ---------- Observations (ADR-0016 — read-shape over Triple + triple_sources) ----------


@mcp.tool()
def observe(
    subject: str | None = None,
    predicate: str | None = None,
    since: date | None = None,
) -> list[dict[str, Any]]:
    """Currently-true observations matching the filters (ADR-0016).

    Returns evidence-backed beliefs as a flat list of dicts. Each dict
    carries the underlying triple plus aggregated provenance:

    - ``proof_count`` — number of independent sources backing the claim
    - ``freshness_trend`` — one of ``new`` / ``strengthening`` /
      ``stable`` / ``weakening`` / ``stale`` / ``contradicted``
    - ``last_corroborated_at`` — ISO timestamp of most recent source
    - ``history`` — full ordered source list with timestamps

    Filters:
    - ``subject`` — equality match on the underlying triple's subject
    - ``predicate`` — equality match on the predicate
    - ``since`` — drop observations whose latest corroboration is
      before this date

    All filters are optional; absent filter means "any." Use this when
    the agent needs "what do we believe about X, and is the picture
    strengthening or going stale?" — distinct from ``query_entity``
    which returns raw triples without aggregation.
    """
    ctx = _ctx()
    started = time.perf_counter()
    observations = ctx.kg.query_observations(
        subject=subject,
        predicate=predicate,
        since=since,
    )
    out = [obs.model_dump(mode="json") for obs in observations]
    record_eval_capture(
        captures_path=ctx.eval_captures_path,
        user_id=ctx.user_id,
        tool_name="mm_observe",
        args={
            "subject": subject,
            "predicate": predicate,
            "since": since.isoformat() if since else None,
        },
        result=out,
        latency_ms=int((time.perf_counter() - started) * 1000),
        mm_version=__version__,
    )
    return out


# ---------- Agent-context compile / render (parity with firm-mode tools) ----------


@mcp.tool()
def compile_agent_context(
    role: Annotated[
        str,
        "What the context is for — e.g. 'meeting-prep', 'email-draft'. "
        "Stored on the output and threaded into the rendered header.",
    ],
    task: Annotated[
        str,
        "Free-form description of the specific task. Shown to the host-agent "
        "LLM verbatim in the rendered context.",
    ],
    attendees: Annotated[
        list[str],
        "Stable entity IDs (``p_<token>`` / ``o_<token>``) or raw entity names "
        "for the people / orgs the workflow is scoped to.",
    ],
    tier_floor: Annotated[
        Tier | None,
        "Restrict doctrine pages to this tier or higher (``decision`` / "
        "``policy`` / ``doctrine`` / ``constitution``). ``None`` = no doctrine "
        "section — most callers want at least ``policy``.",
    ] = None,
    as_of: Annotated[
        date | None,
        "Time-travel date. When supplied, KG triples are filtered to those "
        "valid on that date. ``None`` = currently-true only.",
    ] = None,
) -> dict[str, Any]:
    """Compile the distilled context package for a workflow task.

    Parity-tool with the firm-mode ``compile_agent_context``: same
    primitive (``synthesis.compile_agent_context``), scoped to the
    individual user's personal plane. Returns the structured
    ``AgentContext`` as JSON. For the rendered markdown form, call
    ``render_agent_context`` with the same args.

    Personal-mode specifics:
    - ``plane="personal"`` and ``employee_id`` = this server's user_id
      are baked in — individual mode is single-employee by definition.
    - No ``viewer_id`` / ``policy`` filtering — the user IS the viewer
      and the only one with access to their personal plane.
    - The personal KG is read directly; doctrine pages come from the
      same per-user wiki the engine is rooted at.
    """
    ctx = _ctx()
    packet = _compile_synthesis(
        role=role,
        task=task,
        attendees=attendees,
        kg=ctx.kg._kg,  # noqa: SLF001 — PersonalKnowledgeGraph wraps an unscoped KG
        engine=ctx.engine,
        plane="personal",
        employee_id=ctx.user_id,
        tier_floor=tier_floor,
        as_of=as_of,
        identity_resolver=ctx.identity,
    )
    return packet.model_dump(mode="json")


@mcp.tool()
def render_agent_context(
    role: Annotated[str, "Same as compile_agent_context."],
    task: Annotated[str, "Same as compile_agent_context."],
    attendees: Annotated[list[str], "Same as compile_agent_context."],
    tier_floor: Tier | None = None,
    as_of: date | None = None,
) -> str:
    """Compile and render the distilled context package as markdown.

    Shortcut for ``compile_agent_context`` followed by ``.render()``.
    Returns the markdown string ready to drop into a host-agent prompt.
    """
    ctx = _ctx()
    packet = _compile_synthesis(
        role=role,
        task=task,
        attendees=attendees,
        kg=ctx.kg._kg,  # noqa: SLF001 — see compile_agent_context note above
        engine=ctx.engine,
        plane="personal",
        employee_id=ctx.user_id,
        tier_floor=tier_floor,
        as_of=as_of,
        identity_resolver=ctx.identity,
    )
    return packet.render()


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


def _configure_stdio_safe_logging() -> None:  # pragma: no cover - CLI bootstrap
    """Pin structlog to stderr.

    MCP stdio reserves stdout for JSON-RPC framing. The default
    PrintLoggerFactory writes to stdout, which would poison the
    protocol on the very first emitted log line and cause strict MCP
    clients to refuse the connection. Reconfigure before any structlog
    call could land.
    """
    import sys

    import structlog

    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def app() -> None:  # pragma: no cover - CLI entrypoint
    _configure_stdio_safe_logging()
    cli()


__all__ = [
    "IndividualMcpContext",
    "_configure_stdio_safe_logging",
    "app",
    "initialize",
    "initialize_from_handles",
    "install",
    "mcp",
    "reset",
]


if __name__ == "__main__":
    app()
