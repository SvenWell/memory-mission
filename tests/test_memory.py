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


def test_page_path_is_posix_and_under_domain() -> None:
    p = page_path("companies", "acme-corp")
    assert str(p) == "companies/acme-corp.md"


def test_raw_sidecar_path_lives_in_dot_raw() -> None:
    p = raw_sidecar_path("people", "sarah-chen")
    assert str(p) == "people/.raw/sarah-chen.json"


def test_page_path_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        page_path("not-a-domain", "x")


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


def test_put_and_get_page() -> None:
    engine = InMemoryEngine()
    page = _sample_page()
    engine.put_page(page)
    assert engine.get_page("sarah-chen") == page


def test_get_page_missing_returns_none() -> None:
    engine = InMemoryEngine()
    assert engine.get_page("nobody") is None


def test_put_page_rejects_unknown_domain() -> None:
    engine = InMemoryEngine()
    bad = Page(
        frontmatter=PageFrontmatter(slug="x", title="X", domain="invalid"),
    )
    with pytest.raises(ValueError, match="Unknown domain"):
        engine.put_page(bad)


def test_delete_page_idempotent() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page())
    engine.delete_page("sarah-chen")
    engine.delete_page("sarah-chen")  # second call must not raise
    assert engine.get_page("sarah-chen") is None


def test_list_pages_filters_by_domain() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people"))
    engine.put_page(_sample_page("acme-corp", "companies"))
    engine.put_page(_sample_page("bob", "people"))
    assert {p.slug for p in engine.list_pages("people")} == {"sarah-chen", "bob"}
    assert {p.slug for p in engine.list_pages()} == {
        "sarah-chen",
        "acme-corp",
        "bob",
    }


def test_list_pages_unknown_domain_raises() -> None:
    with pytest.raises(ValueError, match="Unknown domain"):
        InMemoryEngine().list_pages("invalid")


# ---------- Engine search ----------


def test_search_matches_title_and_compiled_truth(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people", "Leads revenue strategy"))
    engine.put_page(_sample_page("acme-corp", "companies", "Struggling revenue"))

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("revenue")

    assert {h.slug for h in hits} == {"sarah-chen", "acme-corp"}


def test_search_empty_query_returns_no_hits(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page())
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        assert engine.search("   ") == []


def test_search_logs_retrieval_event(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people", "CEO of Acme"))

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
        engine.put_page(_sample_page(f"page-{i}", "concepts", "shared keyword here"))
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("keyword", limit=2)
    assert len(hits) == 2


def test_search_ranks_truth_matches_above_title_matches(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    # Title-only match (no truth match).
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="foo-page", title="Revenue Notes", domain="concepts"),
            compiled_truth="unrelated body",
        )
    )
    # Truth match (no title match).
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="bar-page", title="Topic", domain="concepts"),
            compiled_truth="revenue revenue revenue",
        )
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.search("revenue")
    assert hits[0].slug == "bar-page"


# ---------- Graph ----------


def test_links_from_returns_wikilinks_of_page() -> None:
    engine = InMemoryEngine()
    engine.put_page(
        _sample_page(
            "sarah-chen",
            "people",
            "CEO of [[acme-corp]] and board member at [[beta-fund]].",
        )
    )
    assert sorted(engine.links_from("sarah-chen")) == ["acme-corp", "beta-fund"]


def test_links_from_missing_page_returns_empty() -> None:
    assert InMemoryEngine().links_from("nobody") == []


def test_links_to_finds_incoming_links() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("sarah-chen", "people", "Runs [[acme-corp]] product org."))
    engine.put_page(_sample_page("acme-corp", "companies"))
    engine.put_page(_sample_page("bob", "people", "Friends with [[sarah-chen]]."))
    assert engine.links_to("sarah-chen") == ["bob"]
    assert engine.links_to("acme-corp") == ["sarah-chen"]
    assert engine.links_to("nobody") == []


# ---------- Lifecycle + stats ----------


def test_connect_disconnect_toggles_state() -> None:
    engine = InMemoryEngine()
    assert engine.stats().connected is False
    engine.connect()
    assert engine.stats().connected is True
    engine.disconnect()
    assert engine.stats().connected is False


def test_stats_counts_pages_by_domain() -> None:
    engine = InMemoryEngine()
    engine.put_page(_sample_page("a", "people"))
    engine.put_page(_sample_page("b", "people"))
    engine.put_page(_sample_page("c", "companies"))
    stats = engine.stats()
    assert stats.page_count == 3
    assert stats.pages_by_domain == {"people": 2, "companies": 1}
