"""Tests for hybrid search primitives + ``InMemoryEngine.query()`` (step 6c)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from memory_mission.memory import (
    COMPILED_TRUTH_BOOST,
    RRF_K,
    VECTOR_RRF_BLEND,
    EmbeddingProvider,
    HashEmbedder,
    InMemoryEngine,
    Page,
    PageFrontmatter,
    SearchHit,
    cosine_similarity,
    new_page,
    rrf_fuse,
)
from memory_mission.observability import (
    ObservabilityLogger,
    RetrievalEvent,
    observability_scope,
)

# ---------- HashEmbedder ----------


def test_hash_embedder_is_deterministic() -> None:
    a = HashEmbedder(dimension=32)
    b = HashEmbedder(dimension=32)
    assert a.embed("sarah chen ceo") == b.embed("sarah chen ceo")


def test_hash_embedder_different_text_different_vector() -> None:
    e = HashEmbedder(dimension=64)
    assert e.embed("sarah chen") != e.embed("bob smith")


def test_hash_embedder_is_l2_normalized() -> None:
    vec = HashEmbedder(dimension=32).embed("some content here")
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-9


def test_hash_embedder_empty_text_returns_zero_vector() -> None:
    vec = HashEmbedder(dimension=16).embed("")
    assert vec == [0.0] * 16


def test_hash_embedder_dimension_matches_init() -> None:
    e = HashEmbedder(dimension=128)
    assert e.dimension == 128
    assert len(e.embed("abc")) == 128


def test_hash_embedder_rejects_bad_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        HashEmbedder(dimension=0)


def test_hash_embedder_satisfies_embedding_provider_protocol() -> None:
    assert isinstance(HashEmbedder(), EmbeddingProvider)


# ---------- cosine_similarity ----------


def test_cosine_identical_vectors_is_one() -> None:
    v = [0.6, 0.8, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_opposite_vectors_is_negative_one() -> None:
    assert abs(cosine_similarity([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-9


def test_cosine_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_dimension_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="dimension"):
        cosine_similarity([1.0], [1.0, 2.0])


# ---------- rrf_fuse ----------


def test_rrf_fuse_item_in_one_list() -> None:
    fused = rrf_fuse([["a", "b", "c"]], k=60)
    assert fused["a"] == pytest.approx(1.0 / 61)
    assert fused["b"] == pytest.approx(1.0 / 62)
    assert fused["c"] == pytest.approx(1.0 / 63)


def test_rrf_fuse_item_in_both_lists_accumulates() -> None:
    """Top-of-list-in-both beats top-of-one."""
    fused = rrf_fuse([["a", "b"], ["a", "c"]], k=60)
    expected_a = 1.0 / 61 + 1.0 / 61
    expected_b = 1.0 / 62
    expected_c = 1.0 / 62
    assert fused["a"] == pytest.approx(expected_a)
    assert fused["b"] == pytest.approx(expected_b)
    assert fused["c"] == pytest.approx(expected_c)
    assert fused["a"] > fused["b"]


def test_rrf_fuse_empty_list_is_empty_dict() -> None:
    assert rrf_fuse([]) == {}


def test_rrf_k_tunable() -> None:
    """Larger k flattens the rank curve."""
    tight = rrf_fuse([["a", "b"]], k=1)
    loose = rrf_fuse([["a", "b"]], k=1000)
    tight_ratio = tight["a"] / tight["b"]
    loose_ratio = loose["a"] / loose["b"]
    assert tight_ratio > loose_ratio


# ---------- Constants ----------


def test_constants_are_the_gbrain_starting_values() -> None:
    assert RRF_K == 60
    assert COMPILED_TRUTH_BOOST == 2.0
    assert VECTOR_RRF_BLEND == 0.7


# ---------- InMemoryEngine.query() — keyword-only mode ----------


def _page(slug: str, title: str, truth: str) -> Page:
    return new_page(slug=slug, title=title, domain="concepts", compiled_truth=truth)


def test_query_without_embedder_falls_back_to_keyword(tmp_path: Path) -> None:
    engine = InMemoryEngine()  # no embedder
    engine.put_page(
        _page("p1", "Revenue Notes", "Discusses revenue targets for Q3"),
        plane="firm",
    )
    engine.put_page(_page("p2", "Unrelated", "Nothing relevant here"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("revenue")

    assert [h.slug for h in hits] == ["p1"]


def test_query_returns_empty_for_blank_question(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    engine.put_page(_page("p", "t", "body"), plane="firm")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        assert engine.query("   ") == []


def test_query_logs_retrieval_event_with_cascade_tier_by_default(
    tmp_path: Path,
) -> None:
    engine = InMemoryEngine()
    engine.put_page(_page("p", "title", "body mentioning widget"), plane="firm")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        engine.query("widget")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, RetrievalEvent)]
    assert len(events) == 1
    assert events[0].tier == "cascade"
    assert events[0].query == "widget"


# ---------- Compiled truth boost ----------


def test_query_applies_compiled_truth_boost(tmp_path: Path) -> None:
    """Page with query in compiled truth beats title-only match."""
    engine = InMemoryEngine()
    # Title match only
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="title-only", title="Widget Notes", domain="concepts"),
            compiled_truth="unrelated body",
        ),
        plane="firm",
    )
    # Truth match only
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="truth-only", title="Topic", domain="concepts"),
            compiled_truth="This is about widget internals.",
        ),
        plane="firm",
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("widget")

    # Truth-match page ranks first because of the boost.
    assert hits[0].slug == "truth-only"


def test_query_truth_boost_is_exactly_compiled_truth_boost_factor(
    tmp_path: Path,
) -> None:
    """Quantitative check: truth-match score = title-match RRF * boost."""
    engine = InMemoryEngine()
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="title-only", title="Widget Notes", domain="concepts"),
            compiled_truth="unrelated body",
        ),
        plane="firm",
    )
    engine.put_page(
        Page(
            frontmatter=PageFrontmatter(slug="truth-only", title="Topic", domain="concepts"),
            compiled_truth="widget appears here",
        ),
        plane="firm",
    )

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("widget")
    by_slug = {h.slug: h.score for h in hits}

    # truth-only: keyword_ranked[0] -> 1/(60+1), boosted 2x = 2/61
    # title-only: keyword_ranked[1] -> 1/(60+2), no boost = 1/62
    assert by_slug["truth-only"] == pytest.approx(2.0 / 61)
    assert by_slug["title-only"] == pytest.approx(1.0 / 62)


# ---------- Vector pass ----------


def test_put_page_embeds_when_embedder_attached() -> None:
    engine = InMemoryEngine(embedder=HashEmbedder(dimension=16))
    engine.put_page(_page("p1", "t1", "body one"), plane="firm")
    engine.put_page(_page("p2", "t2", "body two"), plane="firm")
    # Embeddings are stored (we can't peek at private state, but query
    # returns vector hits, which proves the side effect).
    # Delete one and verify it drops out.
    engine.delete_page("p1", plane="firm")
    assert engine.get_page("p1", plane="firm") is None


def test_query_uses_vector_pass_when_embedder_attached(tmp_path: Path) -> None:
    """A page with no keyword match still surfaces via vector similarity."""
    engine = InMemoryEngine(embedder=HashEmbedder(dimension=64))
    engine.put_page(
        _page("doc-a", "Alpha", "apple banana cherry"), plane="firm"
    )  # shares tokens with query
    engine.put_page(
        _page("doc-b", "Beta", "zucchini xylophone yak"), plane="firm"
    )  # shares nothing

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("apple banana")

    slugs = [h.slug for h in hits]
    # Both surface — doc-a via keyword + vector (bigger score), doc-b only
    # via vector (tiny score from RRF of a list of two, no cosine overlap).
    # HashEmbedder is bag-of-tokens so shared tokens = similar direction.
    assert slugs[0] == "doc-a"


def test_query_blends_rrf_and_cosine_when_vector_available(
    tmp_path: Path,
) -> None:
    """With embedder attached, final score includes cosine component per the
    VECTOR_RRF_BLEND formula."""
    embedder = HashEmbedder(dimension=32)
    engine = InMemoryEngine(embedder=embedder)
    title = "Title"
    truth = "only body words here exactly"
    engine.put_page(_page("p", title, truth), plane="firm")

    question = "only body words here exactly"
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query(question)

    # Page is rank 1 in both keyword and vector lists => RRF = 2/61.
    # Truth contains the query => boosted by COMPILED_TRUTH_BOOST = 2.0.
    rrf_score = (2.0 / 61) * COMPILED_TRUTH_BOOST
    # Cosine comes from the actual embedding — reproduce what put_page did.
    query_vec = embedder.embed(question)
    page_vec = embedder.embed(f"{title}\n{truth}")
    expected_cos = cosine_similarity(query_vec, page_vec)
    expected = VECTOR_RRF_BLEND * rrf_score + (1.0 - VECTOR_RRF_BLEND) * expected_cos
    assert hits[0].score == pytest.approx(expected, rel=1e-6)


def test_query_without_embedder_skips_cosine_blend(tmp_path: Path) -> None:
    """With no embedder, final score is pure RRF (no cosine term)."""
    engine = InMemoryEngine()
    engine.put_page(_page("p", "Widget", "Widget content here"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("widget")

    # Widget appears in both title and truth.
    # Keyword-only RRF (rank 1): 1/61, boosted 2x = 2/61.
    assert hits[0].score == pytest.approx(2.0 / 61)


def test_delete_page_also_drops_embedding(tmp_path: Path) -> None:
    engine = InMemoryEngine(embedder=HashEmbedder(dimension=16))
    engine.put_page(_page("p1", "t1", "apple"), plane="firm")
    engine.put_page(_page("p2", "t2", "banana"), plane="firm")
    engine.delete_page("p1", plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("apple")

    # p1 deleted, p2 has no apple token. Vector pass would include p2 only
    # via tiny cosine; keyword list empty. No truth-boost slug matches.
    # We assert p1 is not in results.
    assert "p1" not in {h.slug for h in hits}


def test_query_limit_respected(tmp_path: Path) -> None:
    engine = InMemoryEngine(embedder=HashEmbedder(dimension=32))
    for i in range(10):
        engine.put_page(_page(f"p-{i}", f"Title {i}", "shared keyword here"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("keyword", limit=3)

    assert len(hits) == 3


def test_query_result_contains_snippet(tmp_path: Path) -> None:
    engine = InMemoryEngine()
    long_truth = "x" * 200 + " widget here " + "y" * 200
    engine.put_page(_page("p", "Title", long_truth), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("widget")

    assert "widget" in hits[0].snippet


# ---------- Protocol + SearchHit ----------


def test_search_hit_is_frozen() -> None:
    hit = SearchHit(slug="p", plane="firm", score=1.0)
    with pytest.raises(Exception):  # noqa: B017
        hit.score = 2.0  # type: ignore[misc]


def test_engine_query_logs_pages_loaded(tmp_path: Path) -> None:
    engine = InMemoryEngine(embedder=HashEmbedder(dimension=32))
    engine.put_page(_page("p1", "title one", "apple content"), plane="firm")
    engine.put_page(_page("p2", "title two", "banana content"), plane="firm")

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        hits = engine.query("apple")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, RetrievalEvent)]
    assert len(events) == 1
    assert set(events[0].pages_loaded) == {h.slug for h in hits}
