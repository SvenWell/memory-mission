"""``compile_agent_context`` — build a distilled context package.

Reads from the KG (for structured facts) and the BrainEngine (for
curated pages). Optionally consults an IdentityResolver to fill in
canonical names for attendees referenced by stable ID.

Deliberately single-function, single-pass: this is a read-heavy
primitive that should be cheap enough to call on every meeting-prep
invocation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from memory_mission.identity.base import IdentityResolver
from memory_mission.memory.engine import BrainEngine
from memory_mission.memory.knowledge_graph import (
    CoherenceWarning,
    KnowledgeGraph,
    Triple,
)
from memory_mission.memory.pages import Page
from memory_mission.memory.schema import Plane
from memory_mission.memory.tiers import Tier
from memory_mission.observability.api import coherence_warnings_for
from memory_mission.permissions.policy import (
    PUBLIC_SCOPE,
    Policy,
    can_read,
    page_scope,
    viewer_scopes,
)
from memory_mission.synthesis.context import (
    AgentContext,
    AttendeeContext,
    DoctrineContext,
)


def compile_agent_context(
    *,
    role: str,
    task: str,
    attendees: list[str],
    kg: KnowledgeGraph,
    engine: BrainEngine | None = None,
    plane: Plane = "firm",
    employee_id: str | None = None,
    tier_floor: Tier | None = None,
    as_of: date | None = None,
    identity_resolver: IdentityResolver | None = None,
    viewer_id: str | None = None,
    policy: Policy | None = None,
) -> AgentContext:
    """Build the distilled context package for ``role`` + ``task``.

    Args:
        role: What the context is for — ``"meeting-prep"``,
            ``"email-draft"``, etc. Stored on the output and threaded
            into the rendered header.
        task: Free-form description of the specific task. Shown to the
            host-agent LLM verbatim.
        attendees: Stable entity IDs (``p_<token>`` / ``o_<token>``)
            or raw names for entities the workflow is scoped to.
        kg: The firm's knowledge graph.
        engine: Optional BrainEngine for curated page retrieval. When
            supplied and ``tier_floor`` is set, ``DoctrineContext`` is
            populated from the engine's pages at that floor or above.
            When ``None``, doctrine is empty.
        plane: Which plane the doctrine pages should come from
            (default ``"firm"``).
        employee_id: Required when ``plane == "personal"`` for the
            engine scope filter.
        tier_floor: If set, restrict doctrine pages to this tier or
            higher. ``None`` means "no doctrine section" — most
            meeting-prep calls should pass ``"policy"`` or
            ``"doctrine"`` to get authoritative context.
        as_of: Time-travel date. When supplied, the KG's temporal
            filtering applies: only triples valid on that date
            contribute. Invalidated (ended) triples are always
            excluded regardless.
        identity_resolver: When supplied, each attendee ID is
            resolved via ``get_identity`` to populate
            ``canonical_name``. Without a resolver, ``canonical_name``
            stays ``None`` and the attendee ID is the display name.
        viewer_id: The employee whose perspective is compiling this
            context. When set, KG triples and firm-plane doctrine pages
            are filtered to what this viewer is allowed to read.
            ``None`` means "internal caller" — no filtering, trusted
            context (tests, extraction pipeline, ingestion).
        policy: The firm's permissions policy. With ``viewer_id`` set:
            present → full ``can_read`` + ``viewer_scopes`` filtering;
            ``None`` → fail-closed to public-only scope. This keeps
            accidental policy removal from silently re-exposing
            previously-scoped data.

    Returns:
        ``AgentContext`` ready to render or inspect.
    """
    # Fail-closed when a viewer is set but the firm hasn't configured a
    # policy — public-only rather than unfiltered. Keeps accidental
    # policy removal from silently exposing previously-scoped data.
    scopes: frozenset[str] | None = None
    if viewer_id is not None:
        if policy is not None:
            scopes = viewer_scopes(policy, viewer_id)
        else:
            scopes = frozenset({PUBLIC_SCOPE})

    attendee_contexts = [
        _compile_attendee_context(
            attendee_id=attendee_id,
            kg=kg,
            engine=engine,
            plane=plane,
            employee_id=employee_id,
            as_of=as_of,
            identity_resolver=identity_resolver,
            viewer_id=viewer_id,
            policy=policy,
            scopes=scopes,
        )
        for attendee_id in attendees
    ]

    doctrine = _compile_doctrine_context(
        engine=engine,
        plane=plane,
        employee_id=employee_id,
        tier_floor=tier_floor,
        viewer_id=viewer_id,
        policy=policy,
    )

    return AgentContext(
        role=role,
        task=task,
        plane=plane,
        as_of=as_of,
        tier_floor=tier_floor,
        attendees=attendee_contexts,
        doctrine=doctrine,
        generated_at=datetime.now(UTC),
    )


# ---------- Internals ----------


def _compile_attendee_context(
    *,
    attendee_id: str,
    kg: KnowledgeGraph,
    engine: BrainEngine | None,
    plane: Plane,
    employee_id: str | None,
    as_of: date | None,
    identity_resolver: IdentityResolver | None,
    viewer_id: str | None,
    policy: Policy | None,
    scopes: frozenset[str] | None,
) -> AttendeeContext:
    canonical_name: str | None = None
    if identity_resolver is not None:
        identity = identity_resolver.get_identity(attendee_id)
        if identity is not None:
            canonical_name = identity.canonical_name

    outgoing_raw = kg.query_entity(
        attendee_id, direction="outgoing", as_of=as_of, viewer_scopes=scopes
    )
    incoming_raw = kg.query_entity(
        attendee_id, direction="incoming", as_of=as_of, viewer_scopes=scopes
    )

    # Filter out invalidated triples that query_entity did not already
    # drop (it only drops them when as_of is given).
    outgoing = _currently_valid(outgoing_raw, as_of=as_of)
    incoming = _currently_valid(incoming_raw, as_of=as_of)

    # Classify outgoing by predicate:
    #   "event" → events
    #   "prefers" → preferences
    #   everything else → outgoing_triples
    outgoing_triples: list[Triple] = []
    events: list[Triple] = []
    preferences: list[Triple] = []
    for t in outgoing:
        if t.predicate == "event":
            events.append(t)
        elif t.predicate == "prefers":
            preferences.append(t)
        else:
            outgoing_triples.append(t)

    # Events: newest first by valid_from (None sorts last)
    events.sort(
        key=lambda t: (t.valid_from is None, t.valid_from or date.min),
        reverse=True,
    )
    # Put triples with an explicit valid_from at the top
    events = [e for e in events if e.valid_from is not None] + [
        e for e in events if e.valid_from is None
    ]

    # Related pages: look up a curated page by slug == attendee_id if engine available
    related_pages = _related_pages_for(
        attendee_id=attendee_id,
        engine=engine,
        plane=plane,
        employee_id=employee_id,
        viewer_id=viewer_id,
        policy=policy,
    )

    # Coherence warnings (Move 3): pulled from the observability log
    # when a scope is active. Best-effort — if no scope is open, the
    # field stays empty and the render shows no callout.
    warnings: list[CoherenceWarning] = []
    try:
        events_for_entity = coherence_warnings_for(attendee_id)
    except RuntimeError:
        events_for_entity = []
    for event in events_for_entity:
        warnings.append(
            CoherenceWarning(
                subject=event.subject,
                predicate=event.predicate,
                new_object=event.new_object,
                new_tier=event.new_tier,
                conflicting_object=event.conflicting_object,
                conflicting_tier=event.conflicting_tier,
                conflict_type=event.conflict_type,
            )
        )

    return AttendeeContext(
        attendee_id=attendee_id,
        canonical_name=canonical_name,
        outgoing_triples=outgoing_triples,
        incoming_triples=incoming,
        events=events,
        preferences=preferences,
        related_pages=related_pages,
        coherence_warnings=warnings,
    )


def _compile_doctrine_context(
    *,
    engine: BrainEngine | None,
    plane: Plane,
    employee_id: str | None,
    tier_floor: Tier | None,
    viewer_id: str | None,
    policy: Policy | None,
) -> DoctrineContext:
    """Return doctrine pages at or above ``tier_floor``.

    When either ``engine`` or ``tier_floor`` is None, return empty —
    the caller explicitly opted out of doctrine context. When
    ``policy`` + ``viewer_id`` are set and ``plane == "firm"``, pages
    the viewer cannot read under ``can_read`` are dropped. The
    ``BrainEngine.list_pages`` Protocol does not accept viewer/policy,
    so filtering happens here rather than in the engine.
    """
    if engine is None or tier_floor is None:
        return DoctrineContext()

    pages = engine.list_pages(plane=plane, employee_id=employee_id)
    from memory_mission.memory.tiers import is_at_least, tier_level

    kept = [p for p in pages if is_at_least(p.frontmatter.tier, tier_floor)]
    if viewer_id is not None and plane == "firm":
        if policy is not None:
            kept = [p for p in kept if can_read(policy, viewer_id, p)]
        else:
            # Fail-closed: no policy configured → public-only doctrine.
            kept = [p for p in kept if page_scope(p) == PUBLIC_SCOPE]
    # Highest tier first, then alphabetical by slug
    kept.sort(key=lambda p: (-tier_level(p.frontmatter.tier), p.frontmatter.slug))
    return DoctrineContext(pages=kept)


def _related_pages_for(
    *,
    attendee_id: str,
    engine: BrainEngine | None,
    plane: Plane,
    employee_id: str | None,
    viewer_id: str | None,
    policy: Policy | None,
) -> list[Page]:
    """Fetch the curated page whose slug matches the attendee ID, if any.

    V1 is intentionally minimal — one page per attendee, direct slug
    match. Richer lookup (wikilink traversal, backlinks) is a later
    refinement when real meeting-prep data surfaces the need.
    """
    if engine is None:
        return []
    page = engine.get_page(
        slug=attendee_id,
        plane=plane,
        employee_id=employee_id,
        viewer_id=viewer_id,
        policy=policy,
    )
    return [page] if page is not None else []


def _currently_valid(triples: list[Triple], *, as_of: date | None) -> list[Triple]:
    """Drop invalidated triples.

    ``query_entity`` drops invalidated triples only when ``as_of`` is
    set. When it isn't, any ended triple comes back from the query —
    we filter those out here so the attendee context never contains
    facts known to be false.
    """
    if as_of is not None:
        return triples
    return [t for t in triples if t.valid_to is None]
