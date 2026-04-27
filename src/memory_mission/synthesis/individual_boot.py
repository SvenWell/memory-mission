"""Individual-mode agent boot context (ADR-0015).

Distinct from ``compile_agent_context`` (per-task briefing primitive).
This module produces a compact, multi-aspect ``IndividualBootContext``
intended for **system-prompt injection at agent spin-up time**, not
runtime tool calls. The agent boots with the right context already
loaded; it doesn't have to discover memory ad hoc mid-session.

Aspects aggregated:

- ``active_threads`` — currently-true threads in working / in_progress /
  blocked state (predicate ``thread_status``).
- ``commitments`` — open commitments (predicate ``commitment_status``)
  with optional ``due_by``.
- ``preferences`` — durable user preferences (any predicate matching
  ``prefers_*``).
- ``recent_decisions`` — pages tier=``decision`` within a recency
  window, newest first.
- ``relevant_entities`` — top-K entities by mention frequency × recency,
  biased by ``task_hint`` substring match when provided.
- ``project_status`` — pages with ``domain="concepts"`` carrying
  frontmatter extras ``type: project``, plus their currently-true
  status triple. (CORE_DOMAINS is intentionally locked at the
  substrate level; per-vertical types ride on frontmatter extras.)

Token-budgeted: render is sized by character count (``≈4 chars/token``
heuristic). When over budget, lowest-recency entries drop first per
aspect; lower-priority aspects (``relevant_entities`` then
``project_status``) drop before higher-priority ones (``commitments``,
``active_threads``, ``preferences``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.knowledge_graph import Triple

if TYPE_CHECKING:
    from memory_mission.identity.base import IdentityResolver
    from memory_mission.memory.engine import BrainEngine
    from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


# Predicate vocabulary the boot compiler recognizes. Workflow skills
# emit these on personal-plane writes; the compiler aggregates them
# back at boot time. Stable strings — changing them is a substrate
# decision, not a refactor.
THREAD_STATUS_PREDICATE = "thread_status"
COMMITMENT_STATUS_PREDICATE = "commitment_status"
COMMITMENT_DUE_PREDICATE = "commitment_due_by"
COMMITMENT_DESCRIPTION_PREDICATE = "commitment_description"
PREFERENCE_PREDICATE_PREFIX = "prefers_"
PROJECT_STATUS_PREDICATE = "status"
PROJECT_TYPE_MARKER = "project"  # frontmatter `extra: type: project`
PROJECT_TYPE_KEY = "type"

ThreadStatus = Literal["active", "in_progress", "blocked", "deferred"]
CommitmentStatus = Literal["open", "completed", "blocked", "cancelled"]


# ---------- Aspect types ----------


class ActiveThread(BaseModel):
    """A working thread with a currently-true status the agent should know about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str
    status: ThreadStatus
    last_signal_at: date | None = None
    source_closet: str | None = None
    source_file: str | None = None


class Commitment(BaseModel):
    """An open commitment captured from conversation or workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commitment_id: str
    status: CommitmentStatus
    description: str | None = None
    due_by: date | None = None
    last_signal_at: date | None = None
    source_closet: str | None = None
    source_file: str | None = None


class BootPreference(BaseModel):
    """A durable user preference the agent should honor by default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate: str
    value: str
    confirmed_at: date | None = None
    source_closet: str | None = None
    source_file: str | None = None


class RecentDecision(BaseModel):
    """A recent tier=decision page summary, newest first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    title: str
    summary: str
    decided_at: date | None = None


class EntityState(BaseModel):
    """A relevant entity with its currently-true triples.

    Ranked by mention count × recency; biased toward ``task_hint``
    substring matches when the caller supplies one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    canonical_name: str | None = None
    mention_count: int = 0
    current_facts: list[Triple] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.canonical_name or self.entity_id


class ProjectStatus(BaseModel):
    """Project page snapshot with currently-true status triple."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    title: str
    status: str | None = None
    last_updated: date | None = None


class IndividualBootContext(BaseModel):
    """Compact, multi-aspect context injected at agent BOOT time.

    Render this once at agent launch; do NOT recompile per turn unless
    the workflow explicitly requests a fresh boot context (e.g. via the
    ``memory.get_working_context`` tool).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    agent_id: str
    task_hint: str | None = None
    token_budget: int
    active_threads: list[ActiveThread] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    preferences: list[BootPreference] = Field(default_factory=list)
    recent_decisions: list[RecentDecision] = Field(default_factory=list)
    relevant_entities: list[EntityState] = Field(default_factory=list)
    project_status: list[ProjectStatus] = Field(default_factory=list)
    generated_at: datetime
    truncated_aspects: list[str] = Field(default_factory=list)

    @property
    def aspect_counts(self) -> dict[str, int]:
        return {
            "active_threads": len(self.active_threads),
            "commitments": len(self.commitments),
            "preferences": len(self.preferences),
            "recent_decisions": len(self.recent_decisions),
            "relevant_entities": len(self.relevant_entities),
            "project_status": len(self.project_status),
        }

    def render(self) -> str:
        """Markdown render suitable for system-prompt injection."""
        lines: list[str] = []
        lines.append(f"# Boot context for {self.user_id} ({self.agent_id})")
        if self.task_hint:
            lines.append(f"_Task hint:_ {self.task_hint}")
        lines.append("")

        if self.active_threads:
            lines.append("## Active threads")
            for t in self.active_threads:
                last = f" (last signal {t.last_signal_at.isoformat()})" if t.last_signal_at else ""
                lines.append(f"- **{t.thread_id}** — {t.status}{last}")
            lines.append("")

        if self.commitments:
            lines.append("## Open commitments")
            for c in self.commitments:
                desc = c.description or c.commitment_id
                due = f" — due {c.due_by.isoformat()}" if c.due_by else ""
                lines.append(f"- **{desc}** ({c.status}){due}")
            lines.append("")

        if self.preferences:
            lines.append("## Preferences")
            for p in self.preferences:
                lines.append(f"- `{p.predicate}` = {p.value}")
            lines.append("")

        if self.recent_decisions:
            lines.append("## Recent decisions")
            for d in self.recent_decisions:
                when = f" ({d.decided_at.isoformat()})" if d.decided_at else ""
                lines.append(f"- **{d.title}**{when}: {d.summary}")
            lines.append("")

        if self.project_status:
            lines.append("## Project status")
            for ps in self.project_status:
                status = ps.status or "(unset)"
                when = f" — updated {ps.last_updated.isoformat()}" if ps.last_updated else ""
                lines.append(f"- **{ps.title}** ({ps.slug}): {status}{when}")
            lines.append("")

        if self.relevant_entities:
            lines.append("## Relevant entities")
            for e in self.relevant_entities:
                facts_summary = ", ".join(f"{t.predicate}: {t.object}" for t in e.current_facts[:3])
                lines.append(
                    f"- **{e.display_name}** ({e.mention_count} mentions): {facts_summary}"
                )
            lines.append("")

        if self.truncated_aspects:
            lines.append(f"_Truncated to fit token budget: {', '.join(self.truncated_aspects)}._")

        return "\n".join(lines).rstrip()


# ---------- Compiler ----------


@dataclass(frozen=True)
class _AspectLimits:
    """Per-aspect caps applied before token budgeting (defense in depth)."""

    active_threads: int = 20
    commitments: int = 20
    preferences: int = 30
    recent_decisions: int = 10
    relevant_entities: int = 10
    project_status: int = 15


_DEFAULT_LIMITS = _AspectLimits()
_DEFAULT_TOKEN_BUDGET = 4000
_TOKENS_PER_CHAR = 0.25  # ~4 chars per token, English-ish heuristic
_RECENT_DECISION_WINDOW_DAYS = 60


def compile_individual_boot_context(
    *,
    user_id: str,
    agent_id: str,
    kg: PersonalKnowledgeGraph,
    engine: BrainEngine | None = None,
    identity_resolver: IdentityResolver | None = None,
    task_hint: str | None = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    as_of: date | None = None,
    now: datetime | None = None,
) -> IndividualBootContext:
    """Compile the agent boot-time context for a single user/agent pair.

    Args:
        user_id: The personal-plane employee/user id this context covers.
        agent_id: Identifier of the agent runtime that will receive
            the context (``"hermes"``, ``"codex"``, etc.). Stored on
            the output for observability.
        kg: The user's per-employee KG (``PersonalKnowledgeGraph``).
            Reads are auto-scoped to ``employee_<id>``.
        engine: Optional ``BrainEngine`` for page retrieval (recent
            decisions + project status). When ``None``, those aspects
            stay empty.
        identity_resolver: Optional resolver for canonical entity
            names. When ``None``, ``EntityState.canonical_name`` is
            unset and rendering falls back to ``entity_id``.
        task_hint: Optional substring used to bias ``relevant_entities``
            ranking. Entities whose names contain the hint (case-
            insensitive) get a count boost.
        token_budget: Approximate ceiling on rendered length, applied
            via the ~4 chars/token heuristic. Lower-priority aspects
            (relevant_entities, project_status) shrink first when over.
        as_of: Time-travel date forwarded to KG queries. ``None`` = now.
        now: Current time injection point for tests. ``None`` = ``datetime.now(UTC)``.

    Returns:
        ``IndividualBootContext`` — frozen, render-ready, structured
        for both LLM injection (``render()``) and programmatic
        inspection (``aspect_counts``, individual aspect lists).
    """
    now_dt = now or datetime.now(UTC)
    today = as_of or now_dt.date()

    threads = _collect_active_threads(kg, as_of=as_of, limit=_DEFAULT_LIMITS.active_threads)
    commitments = _collect_commitments(kg, as_of=as_of, limit=_DEFAULT_LIMITS.commitments)
    preferences = _collect_preferences(kg, as_of=as_of, limit=_DEFAULT_LIMITS.preferences)
    recent_decisions = _collect_recent_decisions(
        engine,
        user_id=user_id,
        today=today,
        limit=_DEFAULT_LIMITS.recent_decisions,
    )
    relevant_entities = _collect_relevant_entities(
        kg,
        identity_resolver=identity_resolver,
        task_hint=task_hint,
        as_of=as_of,
        limit=_DEFAULT_LIMITS.relevant_entities,
    )
    project_status = _collect_project_status(
        engine,
        kg,
        user_id=user_id,
        as_of=as_of,
        limit=_DEFAULT_LIMITS.project_status,
    )

    ctx = IndividualBootContext(
        user_id=user_id,
        agent_id=agent_id,
        task_hint=task_hint,
        token_budget=token_budget,
        active_threads=threads,
        commitments=commitments,
        preferences=preferences,
        recent_decisions=recent_decisions,
        relevant_entities=relevant_entities,
        project_status=project_status,
        generated_at=now_dt,
    )
    return _enforce_token_budget(ctx, token_budget=token_budget)


# ---------- Helpers ----------


_THREAD_STATUSES: frozenset[str] = frozenset(get_args(ThreadStatus))
_COMMITMENT_STATUSES: frozenset[str] = frozenset(get_args(CommitmentStatus))


def _collect_active_threads(
    kg: PersonalKnowledgeGraph,
    *,
    as_of: date | None,
    limit: int,
) -> list[ActiveThread]:
    """Pull currently-true ``thread_status`` triples filtered to active states."""
    triples = kg.query_relationship(THREAD_STATUS_PREDICATE, as_of=as_of)
    out: list[ActiveThread] = []
    for t in triples:
        if t.valid_to is not None:
            continue
        if t.object not in _THREAD_STATUSES:
            continue
        out.append(
            ActiveThread(
                thread_id=t.subject,
                status=cast(ThreadStatus, t.object),
                last_signal_at=t.valid_from,
                source_closet=t.source_closet,
                source_file=t.source_file,
            )
        )
    out.sort(key=lambda x: x.last_signal_at or date.min, reverse=True)
    return out[:limit]


def _collect_commitments(
    kg: PersonalKnowledgeGraph,
    *,
    as_of: date | None,
    limit: int,
) -> list[Commitment]:
    """Pull currently-true commitment status triples and join with description + due_by."""
    status_triples = kg.query_relationship(COMMITMENT_STATUS_PREDICATE, as_of=as_of)
    out: list[Commitment] = []
    seen: set[str] = set()
    for t in status_triples:
        if t.valid_to is not None:
            continue
        if t.object not in _COMMITMENT_STATUSES:
            continue
        if t.subject in seen:
            continue
        seen.add(t.subject)
        # Commitments default to "open" filter — completed ones don't
        # belong in boot context. Operators can query them via tools
        # if needed.
        if t.object != "open":
            continue
        description = _lookup_first_object(kg, t.subject, COMMITMENT_DESCRIPTION_PREDICATE, as_of)
        due_raw = _lookup_first_object(kg, t.subject, COMMITMENT_DUE_PREDICATE, as_of)
        due_by = _parse_iso_date(due_raw)
        out.append(
            Commitment(
                commitment_id=t.subject,
                status=cast(CommitmentStatus, t.object),
                description=description,
                due_by=due_by,
                last_signal_at=t.valid_from,
                source_closet=t.source_closet,
                source_file=t.source_file,
            )
        )
    # Sort: due-by ascending (earliest deadline first), then last_signal_at desc.
    out.sort(
        key=lambda c: (
            c.due_by or date.max,
            -(c.last_signal_at.toordinal() if c.last_signal_at else 0),
        )
    )
    return out[:limit]


def _collect_preferences(
    kg: PersonalKnowledgeGraph,
    *,
    as_of: date | None,
    limit: int,
) -> list[BootPreference]:
    """Scan for any currently-true triple with predicate prefix ``prefers_``."""
    out: list[BootPreference] = []
    seen: set[tuple[str, str]] = set()
    for t in kg.timeline():
        if t.valid_to is not None:
            continue
        if not t.predicate.startswith(PREFERENCE_PREDICATE_PREFIX):
            continue
        if as_of is not None and not t.is_valid_at(as_of):
            continue
        key = (t.predicate, t.subject)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            BootPreference(
                predicate=t.predicate,
                value=t.object,
                confirmed_at=t.valid_from,
                source_closet=t.source_closet,
                source_file=t.source_file,
            )
        )
    out.sort(key=lambda p: p.confirmed_at or date.min, reverse=True)
    return out[:limit]


def _collect_recent_decisions(
    engine: BrainEngine | None,
    *,
    user_id: str,
    today: date,
    limit: int,
) -> list[RecentDecision]:
    """Return tier=decision pages from the personal plane within the recency window."""
    if engine is None:
        return []
    cutoff = today - timedelta(days=_RECENT_DECISION_WINDOW_DAYS)
    pages = engine.list_pages(plane="personal", employee_id=user_id)
    out: list[RecentDecision] = []
    for page in pages:
        if page.frontmatter.tier != "decision":
            continue
        decided_at = page.frontmatter.valid_from
        if decided_at is not None and decided_at < cutoff:
            continue
        summary = _first_paragraph(page.compiled_truth)
        out.append(
            RecentDecision(
                slug=page.frontmatter.slug,
                title=page.frontmatter.title,
                summary=summary,
                decided_at=decided_at,
            )
        )
    out.sort(key=lambda d: d.decided_at or date.min, reverse=True)
    return out[:limit]


def _collect_relevant_entities(
    kg: PersonalKnowledgeGraph,
    *,
    identity_resolver: IdentityResolver | None,
    task_hint: str | None,
    as_of: date | None,
    limit: int,
) -> list[EntityState]:
    """Top-K entities ranked by mention count × recency, biased by task_hint."""
    counts: dict[str, int] = {}
    last_seen: dict[str, date] = {}
    triples_by_subject: dict[str, list[Triple]] = {}
    for t in kg.timeline():
        if t.valid_to is not None:
            continue
        if as_of is not None and not t.is_valid_at(as_of):
            continue
        # Skip the predicate-vocabulary entries the other aspects already cover —
        # they would dominate the entity count if left in.
        if t.predicate in {
            THREAD_STATUS_PREDICATE,
            COMMITMENT_STATUS_PREDICATE,
            COMMITMENT_DUE_PREDICATE,
            COMMITMENT_DESCRIPTION_PREDICATE,
        }:
            continue
        if t.predicate.startswith(PREFERENCE_PREDICATE_PREFIX):
            continue
        counts[t.subject] = counts.get(t.subject, 0) + 1
        triples_by_subject.setdefault(t.subject, []).append(t)
        when = t.valid_from
        if when is not None and when > last_seen.get(t.subject, date.min):
            last_seen[t.subject] = when

    if not counts:
        return []

    hint_lower = (task_hint or "").lower()

    def score(name: str) -> tuple[int, int]:
        # Higher count first, then more-recent first. Hint match bumps count.
        boost = 5 if hint_lower and hint_lower in name.lower() else 0
        recency_ord = last_seen.get(name, date.min).toordinal()
        return (counts[name] + boost, recency_ord)

    ranked = sorted(counts.keys(), key=score, reverse=True)[:limit]
    out: list[EntityState] = []
    for name in ranked:
        canonical: str | None = None
        if identity_resolver is not None:
            try:
                ident = identity_resolver.lookup(name)
                if ident is not None:
                    res = identity_resolver.get_identity(ident)
                    canonical = res.canonical_name if res is not None else None
            except Exception:  # noqa: BLE001  -- resolver failure is non-fatal at boot
                canonical = None
        out.append(
            EntityState(
                entity_id=name,
                canonical_name=canonical,
                mention_count=counts[name],
                current_facts=triples_by_subject.get(name, [])[:5],
            )
        )
    return out


def _collect_project_status(
    engine: BrainEngine | None,
    kg: PersonalKnowledgeGraph,
    *,
    user_id: str,
    as_of: date | None,
    limit: int,
) -> list[ProjectStatus]:
    """Project pages (domain=concepts, extras.type=project) with current status triple."""
    if engine is None:
        return []
    pages = engine.list_pages(plane="personal", employee_id=user_id)
    out: list[ProjectStatus] = []
    for page in pages:
        if page.frontmatter.domain != "concepts":
            continue
        extras = page.frontmatter.model_extra or {}
        if extras.get(PROJECT_TYPE_KEY) != PROJECT_TYPE_MARKER:
            continue
        # Pull currently-true status triple for the project page slug.
        # ``find_current_triple`` requires a known object; for the
        # boot context we only know subject + predicate, so iterate.
        status_value = _lookup_first_object(
            kg, page.frontmatter.slug, PROJECT_STATUS_PREDICATE, as_of
        )
        out.append(
            ProjectStatus(
                slug=page.frontmatter.slug,
                title=page.frontmatter.title,
                status=status_value,
                last_updated=page.frontmatter.reviewed_at.date()
                if page.frontmatter.reviewed_at is not None
                else page.frontmatter.valid_from,
            )
        )
    # Sort by last_updated desc.
    out.sort(key=lambda p: p.last_updated or date.min, reverse=True)
    return out[:limit]


def _lookup_first_object(
    kg: PersonalKnowledgeGraph,
    subject: str,
    predicate: str,
    as_of: date | None,
) -> str | None:
    """Return the object of the first currently-true (subject, predicate, *) triple."""
    triples = kg.query_entity(subject, as_of=as_of, direction="outgoing")
    for t in triples:
        if t.valid_to is not None:
            continue
        if t.predicate == predicate:
            return t.object
    return None


def _parse_iso_date(value: str | None) -> date | None:
    """Best-effort ISO date parse. Returns None on any failure."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph of ``text``, trimmed to ~200 chars."""
    for chunk in text.split("\n\n"):
        stripped = chunk.strip()
        if stripped:
            return stripped[:200] + ("…" if len(stripped) > 200 else "")
    return ""


def _enforce_token_budget(
    ctx: IndividualBootContext,
    *,
    token_budget: int,
) -> IndividualBootContext:
    """Shrink lower-priority aspects until rendered size fits the budget.

    Drop order: ``relevant_entities`` > ``project_status`` > ``recent_decisions``
    > ``commitments`` > ``preferences`` > ``active_threads``. Within each
    aspect, oldest entries (lowest ``last_seen`` / ``valid_from``) drop
    first. Aspects already sorted recent-first by their collectors, so
    this is a tail-trim.
    """
    drop_order = (
        "relevant_entities",
        "project_status",
        "recent_decisions",
        "commitments",
        "preferences",
        "active_threads",
    )
    truncated: list[str] = []
    current = ctx
    while _approx_tokens(current.render()) > token_budget:
        shrunk = False
        for aspect in drop_order:
            items: list[object] = list(getattr(current, aspect))
            if not items:
                continue
            items.pop()  # drop the tail (oldest after the per-collector sort)
            updates: dict[str, object] = {aspect: items}
            if aspect not in truncated:
                truncated.append(aspect)
                updates["truncated_aspects"] = truncated
            current = current.model_copy(update=updates)
            shrunk = True
            break
        if not shrunk:
            # Nothing left to trim; surface what fits.
            break
    return current


def _approx_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters of English-ish text."""
    return int(len(text) * _TOKENS_PER_CHAR)


__all__ = [
    "ActiveThread",
    "BootPreference",
    "Commitment",
    "EntityState",
    "IndividualBootContext",
    "ProjectStatus",
    "RecentDecision",
    "compile_individual_boot_context",
]
