"""Working-memory page helpers (ADR-0015).

The personal-brain working layer was deleted on ADR-0004 adoption (it
had zero production callers). ADR-0015 brings it back as **first-class
markdown pages** with the standard frontmatter shape — same parser,
same renderer, same Obsidian-native format. Workflow code constructs
them through the helpers here so the convention stays consistent
across callers.

Convention:

- ``domain="concepts"`` — CORE_DOMAINS is intentionally locked at the
  substrate level (verticals extend via config, not by adding to the
  list). Working pages live alongside other concepts pages on disk.
- ``frontmatter.extra`` carries the discriminator: ``type=project``,
  ``type=thread``, ``type=working_memory``, ``type=preference_set``,
  etc. The boot-context compiler keys on these markers.
- ``tier`` reflects the substrate's authority hierarchy:
  ``decision`` for individual decisions, ``doctrine`` for stable
  reference pages, ``policy`` for working agreements, ``working``
  is NOT a substrate tier (substrate Tier is locked too) — instead
  use ``tier="decision"`` for ephemeral working-memory entries and
  promote to ``doctrine`` when a fact stabilizes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from memory_mission.memory.pages import Page, PageFrontmatter, TimelineEntry
from memory_mission.memory.tiers import Tier

WORKING_MEMORY_TYPE = "working_memory"
PROJECT_TYPE = "project"
THREAD_TYPE = "thread"
PREFERENCE_SET_TYPE = "preference_set"


def new_working_memory_page(
    *,
    slug: str,
    title: str,
    page_type: str = WORKING_MEMORY_TYPE,
    compiled_truth: str = "",
    timeline: Iterable[TimelineEntry] = (),
    aliases: Iterable[str] = (),
    sources: Iterable[str] = (),
    valid_from: date | None = None,
    valid_to: date | None = None,
    confidence: float = 0.9,
    tier: Tier = "decision",
    extras: dict[str, object] | None = None,
) -> Page:
    """Construct a personal-plane working-memory page with the standard shape.

    Args:
        slug: Lowercase kebab-case page slug. Must be unique within the
            user's personal plane.
        title: Human-readable title.
        page_type: Frontmatter ``extra: type`` discriminator the boot-
            context compiler keys on. Default ``working_memory``;
            ``project`` for project pages; ``thread`` for thread state;
            ``preference_set`` for grouped preference pages.
        compiled_truth: Distilled body content — the "what an agent
            should know" zone. Defaults to empty for stub pages.
        timeline: Initial timeline entries; usually empty at creation.
        aliases: Alternate names the entity is known by.
        sources: Source artefacts that support this page (file paths
            or external URIs). Provenance is mandatory in the substrate;
            empty here is OK for *individual* working notes that are
            self-attested via the conversational session.
        valid_from / valid_to: Temporal validity window. Default open.
        confidence: Per-page confidence (0–1). Default 0.9 — working
            pages are typically operator-asserted, not extracted.
        tier: Substrate tier — ``decision`` for ephemeral working
            entries (default); ``doctrine`` for stable reference;
            ``policy`` for explicit working agreements.
        extras: Additional frontmatter ``extra`` fields as a dict.
            Common keys: ``status`` (``active`` / ``deferred``),
            ``due_by`` (ISO date string), ``thread_id`` (link from the
            boot-context aggregator). These ride through
            ``PageFrontmatter`` because its ``model_config`` is
            ``extra="allow"``. Pass ``None`` if no extras.

    Returns:
        ``Page`` ready to feed to ``BrainEngine.put_page(plane="personal",
        employee_id=user_id)``.
    """
    now = datetime.now(UTC)
    # PageFrontmatter has ``extra="allow"``, so unrecognized keys ride
    # through as frontmatter extras. We round-trip through
    # ``model_validate`` so the extras dict is type-checkable as a dict
    # rather than being unpacked as named kwargs (mypy can't statically
    # confirm the dict doesn't shadow named params).
    payload: dict[str, object] = {
        "slug": slug,
        "title": title,
        "domain": "concepts",
        "aliases": list(aliases),
        "sources": list(sources),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "confidence": confidence,
        "created": now,
        "updated": now,
        "tier": tier,
        "type": page_type,
    }
    if extras:
        payload.update(extras)
    fm = PageFrontmatter.model_validate(payload)
    return Page(
        frontmatter=fm,
        compiled_truth=compiled_truth,
        timeline=list(timeline),
    )


def new_project_page(
    *,
    slug: str,
    title: str,
    compiled_truth: str = "",
    aliases: Iterable[str] = (),
    sources: Iterable[str] = (),
    tier: Tier = "doctrine",
    extras: dict[str, object] | None = None,
) -> Page:
    """Project-shaped working page (``type=project``, ``tier=doctrine``).

    The boot-context compiler aggregates these into ``project_status``
    when the user's KG holds a currently-true ``(slug, status, *)`` triple.
    """
    return new_working_memory_page(
        slug=slug,
        title=title,
        page_type=PROJECT_TYPE,
        compiled_truth=compiled_truth,
        aliases=aliases,
        sources=sources,
        tier=tier,
        extras=extras,
    )


def new_decision_page(
    *,
    slug: str,
    title: str,
    summary: str,
    decided_at: date | None = None,
    sources: Iterable[str] = (),
    extras: dict[str, object] | None = None,
) -> Page:
    """A ``tier=decision`` page recording an individual decision.

    The boot-context compiler surfaces these in ``recent_decisions``
    when ``valid_from`` falls inside the recency window (60 days by
    default). ``summary`` lands as ``compiled_truth`` so the renderer
    has something concise to show.
    """
    return new_working_memory_page(
        slug=slug,
        title=title,
        page_type="decision",
        compiled_truth=summary,
        sources=sources,
        valid_from=decided_at,
        tier="decision",
        confidence=1.0,
        extras=extras,
    )


__all__ = [
    "PREFERENCE_SET_TYPE",
    "PROJECT_TYPE",
    "THREAD_TYPE",
    "WORKING_MEMORY_TYPE",
    "new_decision_page",
    "new_project_page",
    "new_working_memory_page",
]
