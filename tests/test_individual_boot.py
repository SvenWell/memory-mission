"""Tests for ``compile_individual_boot_context`` (ADR-0015).

Covers:

- Empty substrate produces a render-able context with all aspects empty.
- Active threads filtered to active/in_progress/blocked/deferred states;
  completed/unknown statuses dropped.
- Commitments joined with description + due_by; only ``open`` ones
  surface in the boot context (completed ones don't bloat boot).
- Preferences pulled from any predicate matching ``prefers_*``.
- Recent decisions from personal-plane pages tier=decision within the
  recency window.
- Relevant entities ranked by mention count × recency; ``task_hint``
  substring match boosts ranking.
- Project status from personal-plane pages domain=concepts +
  extras.type=project, joined with the current ``status`` triple.
- Token budget enforcement drops lower-priority aspects first and
  records ``truncated_aspects``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.memory.pages import Page, PageFrontmatter, new_page
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.synthesis.individual_boot import (
    IndividualBootContext,
    compile_individual_boot_context,
)


def _project_page(slug: str, title: str, *, valid_from: date | None = None) -> Page:
    """Construct a Page that survives the project-page filter (extras.type=project)."""
    now = datetime.now(UTC)
    fm = PageFrontmatter(
        slug=slug,
        title=title,
        domain="concepts",
        tier="doctrine",
        valid_from=valid_from,
        created=now,
        updated=now,
        type="project",  # frontmatter extra discriminator
    )
    return Page(frontmatter=fm, compiled_truth="")


@pytest.fixture
def firm_root(tmp_path: Path) -> Path:
    return tmp_path / "firm"


@pytest.fixture
def resolver(tmp_path: Path) -> LocalIdentityResolver:
    return LocalIdentityResolver(tmp_path / "identity.sqlite3")


@pytest.fixture
def kg(firm_root: Path, resolver: LocalIdentityResolver) -> PersonalKnowledgeGraph:
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root,
        employee_id="sven",
        identity_resolver=resolver,
    )
    yield pkg
    pkg.close()


@pytest.fixture
def engine() -> InMemoryEngine:
    return InMemoryEngine()


# ---------- Empty substrate ----------


def test_empty_substrate_returns_renderable_context(kg: PersonalKnowledgeGraph) -> None:
    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    assert isinstance(ctx, IndividualBootContext)
    counts = ctx.aspect_counts
    assert counts == {
        "active_threads": 0,
        "commitments": 0,
        "preferences": 0,
        "recent_decisions": 0,
        "relevant_entities": 0,
        "project_status": 0,
    }
    rendered = ctx.render()
    assert "sven" in rendered
    assert "hermes" in rendered
    assert ctx.truncated_aspects == []


# ---------- Active threads ----------


def test_active_threads_filter_to_known_statuses(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("thread-deal-acme", "thread_status", "active", valid_from=date(2026, 4, 20))
    kg.add_triple("thread-xai-feeds", "thread_status", "deferred", valid_from=date(2026, 4, 18))
    kg.add_triple("thread-loom", "thread_status", "blocked", valid_from=date(2026, 4, 15))
    # Unknown status — dropped by the filter.
    kg.add_triple("thread-mystery", "thread_status", "unknown", valid_from=date(2026, 4, 10))

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    ids = {t.thread_id for t in ctx.active_threads}
    assert ids == {"thread-deal-acme", "thread-xai-feeds", "thread-loom"}
    # Newest first
    assert ctx.active_threads[0].thread_id == "thread-deal-acme"


def test_active_threads_drop_invalidated(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple(
        "thread-old",
        "thread_status",
        "active",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 4, 1),
    )
    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    assert ctx.active_threads == []


# ---------- Commitments ----------


def test_commitments_join_description_and_due_by(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("commit-ship-mm", "commitment_status", "open", valid_from=date(2026, 4, 25))
    kg.add_triple(
        "commit-ship-mm",
        "commitment_description",
        "Ship Memory Mission Individual mode this week",
    )
    kg.add_triple("commit-ship-mm", "commitment_due_by", "2026-05-04")

    # Completed commitment — should NOT surface in boot context.
    kg.add_triple("commit-old", "commitment_status", "completed", valid_from=date(2026, 4, 1))

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    assert len(ctx.commitments) == 1
    c = ctx.commitments[0]
    assert c.commitment_id == "commit-ship-mm"
    assert c.description == "Ship Memory Mission Individual mode this week"
    assert c.due_by == date(2026, 5, 4)


def test_commitments_sort_by_earliest_due_by_first(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("commit-later", "commitment_status", "open", valid_from=date(2026, 4, 1))
    kg.add_triple("commit-later", "commitment_due_by", "2026-06-01")
    kg.add_triple("commit-soon", "commitment_status", "open", valid_from=date(2026, 4, 1))
    kg.add_triple("commit-soon", "commitment_due_by", "2026-04-30")

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    assert [c.commitment_id for c in ctx.commitments] == ["commit-soon", "commit-later"]


# ---------- Preferences ----------


def test_preferences_match_prefers_prefix(kg: PersonalKnowledgeGraph) -> None:
    kg.add_triple("sven", "prefers_reply_style", "conversational telegram, not bullet-heavy")
    kg.add_triple("sven", "prefers_tooling", "uv over pip")
    # Non-prefers predicate — must not surface.
    kg.add_triple("sven", "knows", "memory-mission")

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    predicates = {p.predicate for p in ctx.preferences}
    assert predicates == {"prefers_reply_style", "prefers_tooling"}


# ---------- Recent decisions ----------


def test_recent_decisions_pulled_from_personal_pages(
    kg: PersonalKnowledgeGraph, engine: InMemoryEngine
) -> None:
    today = date(2026, 4, 27)
    engine.put_page(
        new_page(
            slug="adopted-uv",
            title="Adopted uv as default Python toolchain",
            domain="concepts",
            tier="decision",
            valid_from=today - timedelta(days=10),
            compiled_truth="Standardize on uv across all repos. Better lockfile semantics.",
        ),
        plane="personal",
        employee_id="sven",
    )
    # Stale decision — outside 60-day recency window
    engine.put_page(
        new_page(
            slug="dropped-pipenv",
            title="Dropped pipenv",
            domain="concepts",
            tier="decision",
            valid_from=today - timedelta(days=120),
            compiled_truth="Pipenv was abandoned for uv.",
        ),
        plane="personal",
        employee_id="sven",
    )

    ctx = compile_individual_boot_context(
        user_id="sven",
        agent_id="hermes",
        kg=kg,
        engine=engine,
        as_of=today,
    )
    slugs = [d.slug for d in ctx.recent_decisions]
    assert slugs == ["adopted-uv"]
    assert "uv" in ctx.recent_decisions[0].summary.lower()


# ---------- Relevant entities ----------


def test_relevant_entities_rank_by_count_and_recency(kg: PersonalKnowledgeGraph) -> None:
    # Three mentions of "memory-mission", two of "loom".
    kg.add_triple("memory-mission", "is_a", "project", valid_from=date(2026, 4, 1))
    kg.add_triple(
        "memory-mission", "current_phase", "individual mode", valid_from=date(2026, 4, 25)
    )
    kg.add_triple("memory-mission", "owner", "sven", valid_from=date(2026, 4, 1))
    kg.add_triple("loom", "is_a", "side-project", valid_from=date(2026, 3, 15))
    kg.add_triple("loom", "current_phase", "deferred", valid_from=date(2026, 4, 20))

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    ids = [e.entity_id for e in ctx.relevant_entities]
    assert ids[0] == "memory-mission"
    assert "loom" in ids


def test_task_hint_boosts_matching_entity(kg: PersonalKnowledgeGraph) -> None:
    # Three mentions each — count tied. Hint should break the tie.
    for i in range(3):
        kg.add_triple("loom", f"fact_{i}", f"v{i}", valid_from=date(2026, 4, 1 + i))
    for i in range(3):
        kg.add_triple("xai-feeds", f"fact_{i}", f"v{i}", valid_from=date(2026, 4, 1 + i))

    ctx = compile_individual_boot_context(
        user_id="sven",
        agent_id="hermes",
        kg=kg,
        task_hint="loom debugging",
    )
    assert ctx.relevant_entities[0].entity_id == "loom"


def test_relevant_entities_skip_aspect_predicates(kg: PersonalKnowledgeGraph) -> None:
    """Predicates already covered by other aspects must not inflate entity ranking."""
    kg.add_triple("commit-x", "commitment_status", "open", valid_from=date(2026, 4, 25))
    kg.add_triple("thread-y", "thread_status", "active", valid_from=date(2026, 4, 25))
    kg.add_triple("sven", "prefers_x", "value", valid_from=date(2026, 4, 25))
    # Plain entity-fact triple — should be the only thing in entities.
    kg.add_triple("real-entity", "knows", "thing", valid_from=date(2026, 4, 25))

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg)
    ids = {e.entity_id for e in ctx.relevant_entities}
    assert ids == {"real-entity"}


# ---------- Project status ----------


def test_project_status_pulls_current_status_triple(
    kg: PersonalKnowledgeGraph, engine: InMemoryEngine
) -> None:
    engine.put_page(
        _project_page("memory-mission", "Memory Mission", valid_from=date(2026, 4, 1)),
        plane="personal",
        employee_id="sven",
    )
    # Non-project concepts page should NOT surface in project_status.
    engine.put_page(
        new_page(
            slug="just-a-concept",
            title="Just a Concept",
            domain="concepts",
            tier="doctrine",
            compiled_truth="A non-project concept.",
        ),
        plane="personal",
        employee_id="sven",
    )
    kg.add_triple(
        "memory-mission", "status", "individual-mode-shipping", valid_from=date(2026, 4, 27)
    )

    ctx = compile_individual_boot_context(user_id="sven", agent_id="hermes", kg=kg, engine=engine)
    assert len(ctx.project_status) == 1
    ps = ctx.project_status[0]
    assert ps.slug == "memory-mission"
    assert ps.status == "individual-mode-shipping"


# ---------- Token budget ----------


def test_token_budget_truncates_lower_priority_aspects_first(
    kg: PersonalKnowledgeGraph,
) -> None:
    # Lots of entities (lower-priority drop target).
    for i in range(10):
        for j in range(3):
            kg.add_triple(
                f"entity-{i:02d}", f"fact_{j}", f"value_{j}", valid_from=date(2026, 4, 25)
            )
    # Crucial commitment.
    kg.add_triple("commit-critical", "commitment_status", "open", valid_from=date(2026, 4, 25))
    kg.add_triple("commit-critical", "commitment_description", "DO NOT DROP THIS")

    # Tight budget forces truncation.
    ctx = compile_individual_boot_context(
        user_id="sven", agent_id="hermes", kg=kg, token_budget=120
    )
    # Commitment must survive (highest priority); entities should be hit first.
    commit_ids = {c.commitment_id for c in ctx.commitments}
    assert "commit-critical" in commit_ids
    assert "relevant_entities" in ctx.truncated_aspects
    # And there should be FEWER entities than the 10 we stuffed in.
    assert len(ctx.relevant_entities) < 10


def test_aspects_are_frozen_pydantic() -> None:
    """All aspect models must be frozen (no mutation post-construction)."""
    from memory_mission.synthesis.individual_boot import (
        ActiveThread,
        BootPreference,
        Commitment,
        EntityState,
        IndividualBootContext,
        ProjectStatus,
        RecentDecision,
    )

    samples = [
        ActiveThread(thread_id="t1", status="active"),
        Commitment(commitment_id="c1", status="open"),
        BootPreference(predicate="prefers_x", value="y"),
        RecentDecision(slug="s", title="t", summary="x"),
        EntityState(entity_id="e1"),
        ProjectStatus(slug="s", title="t"),
        IndividualBootContext(
            user_id="u",
            agent_id="a",
            token_budget=1000,
            generated_at=datetime.now(UTC),
        ),
    ]
    for sample in samples:
        with pytest.raises(Exception):  # noqa: B017, PT011
            sample.thread_id = "mutated"  # type: ignore[attr-defined,misc]
