"""Tests for components 0.1 + 0.2 — Memory Layer (Step 6a)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.memory import (
    CORE_DOMAINS,
    BrainEngine,
    InMemoryEngine,
    Page,
    PageFrontmatter,
    TimelineEntry,
    curated_root,
    is_valid_domain,
    new_page,
    page_path,
    parse_page,
    raw_sidecar_path,
    render_page,
    validate_domain,
)
from memory_mission.observability import (
    ObservabilityLogger,
    RetrievalEvent,
    observability_scope,
)

# ---------- Page parsing / serialization ----------


SAMPLE_PAGE = """---
slug: sarah-chen
title: Sarah Chen
domain: people
aliases:
  - Sarah
  - S. Chen
sources:
  - interaction-1
  - interaction-2
valid_from: 2024-01-01
confidence: 0.9
---

Sarah is the CEO of [[acme-corp]]. She took over from the founder in 2023
and prefers direct, numbers-heavy communication.

---

2026-04-15 [interaction-2]: Confirmed CEO role in board meeting transcript
2026-04-10 [interaction-1]: First mention as "CEO of Acme" in Granola call
"""


def test_parse_page_extracts_frontmatter() -> None:
    page = parse_page(SAMPLE_PAGE)
    assert page.slug == "sarah-chen"
    assert page.frontmatter.title == "Sarah Chen"
    assert page.domain == "people"
    assert page.frontmatter.aliases == ["Sarah", "S. Chen"]
    assert page.frontmatter.sources == ["interaction-1", "interaction-2"]
    assert page.frontmatter.valid_from == date(2024, 1, 1)
    assert page.frontmatter.valid_to is None
    assert page.frontmatter.confidence == 0.9


def test_parse_page_extracts_compiled_truth() -> None:
    page = parse_page(SAMPLE_PAGE)
    assert "Sarah is the CEO" in page.compiled_truth
    assert "---" not in page.compiled_truth


def test_parse_page_extracts_timeline_entries() -> None:
    page = parse_page(SAMPLE_PAGE)
    assert len(page.timeline) == 2
    newest, oldest = page.timeline
    assert newest.entry_date == date(2026, 4, 15)
    assert newest.source_id == "interaction-2"
    assert "Confirmed CEO role" in newest.text
    assert oldest.source_id == "interaction-1"


def test_parse_page_extracts_wikilinks() -> None:
    page = parse_page(SAMPLE_PAGE)
    assert page.wikilinks() == ["acme-corp"]


def test_wikilinks_dedupe_and_strip_display_text() -> None:
    page = new_page(
        slug="test",
        title="Test",
        domain="concepts",
        compiled_truth=("[[acme-corp]] and [[acme-corp|Acme]] plus [[other|see also]]."),
    )
    assert page.wikilinks() == ["acme-corp", "other"]


def test_parse_page_without_timeline_zone() -> None:
    raw = """---
slug: idea-1
title: Idea
domain: concepts
---

Just a concept with no evidence yet.
"""
    page = parse_page(raw)
    assert page.compiled_truth.startswith("Just a concept")
    assert page.timeline == []


def test_parse_page_ignores_malformed_timeline_lines() -> None:
    raw = """---
slug: t
title: T
domain: concepts
---

Body.

---

2026-04-15 [src]: good entry
garbage line should be skipped
2026-04-14 [src2]: another good one
"""
    page = parse_page(raw)
    assert len(page.timeline) == 2
    assert page.timeline[0].source_id == "src"
    assert page.timeline[1].source_id == "src2"


def test_parse_page_missing_frontmatter_raises() -> None:
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        parse_page("just a body, no fence")


def test_parse_page_unterminated_frontmatter_raises() -> None:
    with pytest.raises(ValueError, match="no closing"):
        parse_page("---\nslug: x\n")


def test_render_page_round_trip() -> None:
    page = parse_page(SAMPLE_PAGE)
    rendered = render_page(page)
    reparsed = parse_page(rendered)
    assert reparsed.slug == page.slug
    assert reparsed.frontmatter.aliases == page.frontmatter.aliases
    assert reparsed.compiled_truth == page.compiled_truth
    assert reparsed.timeline == page.timeline


def test_render_page_preserves_extra_frontmatter_fields() -> None:
    """Unknown frontmatter keys survive round-trip (hand-edited pages)."""
    raw = """---
slug: t
title: T
domain: concepts
custom_field: custom_value
nested:
  a: 1
  b: 2
---

Body here.
"""
    page = parse_page(raw)
    rendered = render_page(page)
    assert "custom_field: custom_value" in rendered
    assert "a: 1" in rendered


def test_with_timeline_entry_prepends_newest() -> None:
    page = parse_page(SAMPLE_PAGE)
    new = page.with_timeline_entry(
        TimelineEntry(
            entry_date=date(2026, 4, 20),
            source_id="interaction-3",
            text="Third confirmation",
        )
    )
    assert len(new.timeline) == 3
    assert new.timeline[0].entry_date == date(2026, 4, 20)
    # Original unchanged (immutable-by-copy).
    assert len(page.timeline) == 2


def test_frontmatter_confidence_must_be_in_range() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        PageFrontmatter(slug="t", title="T", domain="people", confidence=1.5)


def test_frontmatter_slug_rejects_bad_shapes() -> None:
    for bad in ["", "Sarah", "sarah chen", "../escape", "sarah/chen"]:
        with pytest.raises(ValidationError):
            PageFrontmatter(slug=bad, title="T", domain="people")


def test_frontmatter_slug_accepts_valid_shapes() -> None:
    for good in ["sarah-chen", "acme-corp", "q3-2026", "c1"]:
        fm = PageFrontmatter(slug=good, title="T", domain="people")
        assert fm.slug == good


def test_timeline_entry_render_format() -> None:
    entry = TimelineEntry(entry_date=date(2026, 4, 15), source_id="src-1", text="hello")
    assert entry.render() == "2026-04-15 [src-1]: hello"


# ---------- MECE schema ----------


def test_core_domains_is_vertical_neutral() -> None:
    """GBrain base taxonomy only — verticals extend via config, not by editing core."""
    assert set(CORE_DOMAINS) == {
        "people",
        "companies",
        "deals",
        "meetings",
        "concepts",
        "sources",
        "inbox",
        "archive",
    }


def test_is_valid_domain() -> None:
    assert is_valid_domain("people")
    assert not is_valid_domain("clients")  # vertical-specific, not in core
    assert not is_valid_domain("invalid")
    assert not is_valid_domain("")


def test_validate_domain_raises_for_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        validate_domain("foobar")


def test_page_path_firm_plane() -> None:
    p = page_path("firm", "companies", "acme-corp")
    assert str(p) == "firm/companies/acme-corp.md"


def test_page_path_personal_plane() -> None:
    """Personal plane curated pages live under the four-layer ``semantic/`` subdir."""
    p = page_path("personal", "people", "sarah-chen", employee_id="sarah")
    assert str(p) == "personal/sarah/semantic/people/sarah-chen.md"


def test_page_path_personal_requires_employee_id() -> None:
    with pytest.raises(ValueError, match="personal plane requires employee_id"):
        page_path("personal", "people", "sarah-chen")


def test_page_path_firm_rejects_employee_id() -> None:
    with pytest.raises(ValueError, match="firm plane must not carry"):
        page_path("firm", "people", "sarah-chen", employee_id="sarah")


def test_raw_sidecar_path_firm_plane() -> None:
    p = raw_sidecar_path("firm", "people", "sarah-chen")
    assert str(p) == "firm/people/.raw/sarah-chen.json"


def test_raw_sidecar_path_personal_plane() -> None:
    p = raw_sidecar_path("personal", "people", "sarah-chen", employee_id="sarah")
    assert str(p) == "personal/sarah/semantic/people/.raw/sarah-chen.json"


def test_page_path_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        page_path("firm", "not-a-domain", "x")


def test_curated_root_personal_includes_semantic_layer() -> None:
    """Personal plane curated content lives under the four-layer ``semantic/`` subdir."""
    assert str(curated_root("personal", employee_id="alice")) == "personal/alice/semantic"


def test_curated_root_firm_is_flat() -> None:
    assert str(curated_root("firm")) == "firm"


def test_curated_root_personal_requires_employee_id() -> None:
    with pytest.raises(ValueError, match="personal plane requires employee_id"):
        curated_root("personal")


def test_page_path_rejects_bad_employee_id() -> None:
    for bad in ["", "../escape", "with space", "/abs"]:
        with pytest.raises(ValueError):
            page_path("personal", "people", "x", employee_id=bad)


# ---------- InMemoryEngine ----------


def _sample_page(
    slug: str = "sarah-chen", domain: str = "people", truth: str | None = None
) -> Page:
    return new_page(
        slug=slug,
        title=slug.replace("-", " ").title(),
        domain=domain,
        compiled_truth=truth or f"Notes about {slug}.",
    )


def test_in_memory_satisfies_brain_engine_protocol() -> None:
    assert isinstance(InMemoryEngine(), BrainEngine)


# ---------- Firm plane CRUD ----------


def test_put_and_get_page_firm_plane() -> None:
    engine = InMemoryEngine()
    page = _sample_page()
    engine.put_page(page, plane="firm")
    assert engine.get_page("sarah-chen", plane="firm") == page


def test_get_page_missing_returns_none() -> None:
    assert InMemoryEngine().get_page("nobody", plane="firm") is None


def test_put_page_rejects_unknown_domain() -> None:
    engine = InMemoryEngine()
    bad = Page(
        frontmatter=PageFrontmatter(slug="x", title="X", domain="invalid"),
    )
    with pytest.raises(ValueError, match="Unknown domain"):
        engine.put_page(bad, plane="firm")


def test_delete_page_idempotent() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page(), plane="firm")
    engine.delete_page("sarah-chen", plane="firm")
    engine.delete_page("sarah-chen", plane="firm")  # second call must not raise
    assert engine.get_page("sarah-chen", plane="firm") is None


# ---------- Personal plane CRUD + isolation ----------


def test_personal_plane_requires_employee_id_on_put() -> None:
    engine = InMemoryEngine()
    with pytest.raises(ValueError, match="personal plane requires employee_id"):
        engine.put_page(_sample_page(), plane="personal")


def test_firm_plane_rejects_employee_id_on_put() -> None:
    engine = InMemoryEngine()
    with pytest.raises(ValueError, match="firm plane must not carry"):
        engine.put_page(_sample_page(), plane="firm", employee_id="sarah")


def test_personal_pages_are_isolated_across_employees() -> None:
    """Same slug in different employees' personal planes stays separate."""
    engine = InMemoryEngine()
    alice_note = _sample_page("note-1", "concepts", "Alice's private note")
    bob_note = _sample_page("note-1", "concepts", "Bob's private note")
    engine.put_page(alice_note, plane="personal", employee_id="alice")
    engine.put_page(bob_note, plane="personal", employee_id="bob")

    fetched_a = engine.get_page("note-1", plane="personal", employee_id="alice")
    fetched_b = engine.get_page("note-1", plane="personal", employee_id="bob")
    assert fetched_a is not None and "Alice" in fetched_a.compiled_truth
    assert fetched_b is not None and "Bob" in fetched_b.compiled_truth


def test_same_slug_coexists_across_planes() -> None:
    """Firm, Alice-personal, Bob-personal can all hold slug='acme' simultaneously."""
    engine = InMemoryEngine()
    engine.put_page(_sample_page("acme", "companies", "Firm canonical page"), plane="firm")
    engine.put_page(
        _sample_page("acme", "companies", "Alice's notes"),
        plane="personal",
        employee_id="alice",
    )
    engine.put_page(
        _sample_page("acme", "companies", "Bob's notes"),
        plane="personal",
        employee_id="bob",
    )

    firm = engine.get_page("acme", plane="firm")
    alice = engine.get_page("acme", plane="personal", employee_id="alice")
    bob = engine.get_page("acme", plane="personal", employee_id="bob")
    assert firm is not None and "canonical" in firm.compiled_truth
    assert alice is not None and "Alice" in alice.compiled_truth
    assert bob is not None and "Bob" in bob.compiled_truth


def test_delete_on_one_plane_doesnt_affect_other() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("x"), plane="firm")
    engine.put_page(_sample_page("x"), plane="personal", employee_id="alice")
    engine.delete_page("x", plane="firm")
    assert engine.get_page("x", plane="firm") is None
    assert engine.get_page("x", plane="personal", employee_id="alice") is not None


# ---------- List pages ----------


def test_list_pages_filters_by_plane() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("a"), plane="firm")
    engine.put_page(_sample_page("b"), plane="personal", employee_id="alice")
    engine.put_page(_sample_page("c"), plane="personal", employee_id="bob")

    firm_slugs = {p.slug for p in engine.list_pages(plane="firm")}
    assert firm_slugs == {"a"}

    alice_slugs = {p.slug for p in engine.list_pages(plane="personal", employee_id="alice")}
    assert alice_slugs == {"b"}


def test_list_pages_filters_by_domain() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people"), plane="firm")
    engine.put_page(_sample_page("acme-corp", "companies"), plane="firm")
    engine.put_page(_sample_page("bob", "people"), plane="firm")
    all_slugs = {p.slug for p in engine.list_pages()}
    assert all_slugs == {"sarah-chen", "acme-corp", "bob"}
    people_slugs = {p.slug for p in engine.list_pages(domain="people")}
    assert people_slugs == {"sarah-chen", "bob"}


def test_list_pages_combines_plane_and_domain_filters() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people"), plane="firm")
    engine.put_page(_sample_page("bob", "people"), plane="personal", employee_id="alice")
    engine.put_page(_sample_page("acme-corp", "companies"), plane="firm")
    result = engine.list_pages(plane="firm", domain="people")
    assert {p.slug for p in result} == {"sarah-chen"}


def test_list_pages_unknown_domain_raises() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        InMemoryEngine().list_pages(domain="invalid")


def test_list_pages_employee_id_without_plane_raises() -> None:
    with pytest.raises(ValueError, match="only meaningful when plane"):
        InMemoryEngine().list_pages(employee_id="alice")


# ---------- Engine search ----------


def test_search_matches_title_and_compiled_truth(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people", "Leads revenue strategy"), plane="firm")
    engine.put_page(_sample_page("acme-corp", "companies", "Struggling revenue"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("revenue")

    assert {h.slug for h in hits} == {"sarah-chen", "acme-corp"}


def test_search_scope_filter_isolates_planes(tmp_path: Path) -> None:
    """Searching plane='firm' must not surface personal pages, and vice versa."""
    engine = InMemoryEngine()
    engine.put_page(_sample_page("firm-page", "concepts", "shared keyword"), plane="firm")
    engine.put_page(
        _sample_page("alice-page", "concepts", "shared keyword"),
        plane="personal",
        employee_id="alice",
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        firm_hits = engine.search("keyword", plane="firm")
        alice_hits = engine.search("keyword", plane="personal", employee_id="alice")
        global_hits = engine.search("keyword")

    assert {h.slug for h in firm_hits} == {"firm-page"}
    assert {h.slug for h in alice_hits} == {"alice-page"}
    assert {h.slug for h in global_hits} == {"firm-page", "alice-page"}


def test_search_personal_scope_isolates_across_employees(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(
        _sample_page("p1", "concepts", "secret keyword"),
        plane="personal",
        employee_id="alice",
    )
    engine.put_page(
        _sample_page("p2", "concepts", "secret keyword"),
        plane="personal",
        employee_id="bob",
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        alice_only = engine.search("keyword", plane="personal", employee_id="alice")

    assert {h.slug for h in alice_only} == {"p1"}


def test_search_empty_query_returns_no_hits(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page(), plane="firm")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        assert engine.search("   ") == []


def test_search_logs_retrieval_event(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people", "CEO of Acme"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme", employee_id="sarah"):
        engine.search("Acme", tier="navigate")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, RetrievalEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.query == "Acme"
    assert event.tier == "navigate"
    assert event.pages_loaded == ["sarah-chen"]


def test_search_limit_respected(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    for i in range(5):
        engine.put_page(
            _sample_page(f"page-{i}", "concepts", "shared keyword here"),
            plane="firm",
        )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("keyword", limit=2)
    assert len(hits) == 2


def test_search_ranks_truth_matches_above_title_matches(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="foo-page", title="Revenue Notes", domain="concepts"),
            compiled_truth="unrelated body",
        ),
        plane="firm",
    )
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="bar-page", title="Topic", domain="concepts"),
            compiled_truth="revenue revenue revenue",
        ),
        plane="firm",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("revenue")
    assert hits[0].slug == "bar-page"


def test_search_hit_carries_plane_and_employee_id(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(
        _sample_page("p", "concepts", "hello world"),
        plane="personal",
        employee_id="alice",
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("hello")
    assert len(hits) == 1
    assert hits[0].plane == "personal"
    assert hits[0].employee_id == "alice"


# ---------- Graph ----------


def test_links_from_returns_wikilinks_of_page() -> None:
    engine = InMemoryEngine()
    engine.put_page(
        _sample_page(
            "sarah-chen",
            "people",
            "CEO of [[acme-corp]] and board member at [[beta-fund]].",
        ),
        plane="firm",
    )
    assert sorted(engine.links_from("sarah-chen", plane="firm")) == [
        "acme-corp",
        "beta-fund",
    ]


def test_links_from_missing_page_returns_empty() -> None:
    assert InMemoryEngine().links_from("nobody", plane="firm") == []


def test_links_to_finds_incoming_links_within_same_scope() -> None:
    engine = InMemoryEngine()
    engine.put_page(
        _sample_page("sarah-chen", "people", "Runs [[acme-corp]] product org."),
        plane="firm",
    )
    engine.put_page(_sample_page("acme-corp", "companies"), plane="firm")
    engine.put_page(
        _sample_page("bob", "people", "Friends with [[sarah-chen]]."),
        plane="firm",
    )
    assert engine.links_to("sarah-chen", plane="firm") == ["bob"]
    assert engine.links_to("acme-corp", plane="firm") == ["sarah-chen"]
    assert engine.links_to("nobody", plane="firm") == []


def test_links_to_does_not_cross_planes() -> None:
    """A personal-plane wikilink must not surface as an incoming link on the firm plane."""
    engine = InMemoryEngine()
    engine.put_page(_sample_page("acme-corp", "companies"), plane="firm")
    engine.put_page(
        _sample_page("alice-private", "concepts", "Refers to [[acme-corp]]."),
        plane="personal",
        employee_id="alice",
    )
    assert engine.links_to("acme-corp", plane="firm") == []
    assert engine.links_to("acme-corp", plane="personal", employee_id="alice") == ["alice-private"]


# ---------- Lifecycle + stats ----------


def test_connect_disconnect_toggles_state() -> None:
    engine = InMemoryEngine()
    assert engine.stats().connected is False
    engine.connect()
    assert engine.stats().connected is True
    engine.disconnect()
    assert engine.stats().connected is False


def test_stats_counts_pages_by_plane_and_domain() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("a", "people"), plane="firm")
    engine.put_page(_sample_page("b", "people"), plane="firm")
    engine.put_page(_sample_page("c", "companies"), plane="personal", employee_id="alice")
    stats = engine.stats()
    assert stats.page_count == 3
    assert stats.pages_by_domain == {"people": 2, "companies": 1}
    assert stats.pages_by_plane == {"firm": 2, "personal": 1}
