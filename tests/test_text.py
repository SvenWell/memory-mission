"""Tests for ``word_set`` and ``jaccard`` (ported from agentic-stack)."""

from __future__ import annotations

import pytest

from memory_mission.memory import STOPWORDS, jaccard, word_set

# ---------- word_set ----------


def test_word_set_empty_input() -> None:
    assert word_set("") == set()
    assert word_set(None) == set()


def test_word_set_lowercases() -> None:
    assert word_set("Sarah CHEN") == {"sarah", "chen"}


def test_word_set_drops_stopwords() -> None:
    """The / and / is should not survive."""
    result = word_set("the cat is on the mat")
    assert result == {"cat", "mat"}


def test_word_set_requires_3_plus_chars_alpha_leading() -> None:
    """Two-letter tokens drop. Pure-digit tokens drop. Alpha-leading 3+ wins."""
    result = word_set("ai is great 42 python wins")
    assert result == {"great", "python", "wins"}


def test_word_set_finds_alpha_substring_after_digits() -> None:
    """Regex starts at the first alpha — ``42rocks`` yields ``rocks``."""
    assert word_set("42rocks") == {"rocks"}


def test_word_set_keeps_underscores_and_hyphens() -> None:
    assert word_set("snake_case kebab-case") == {"snake_case", "kebab-case"}


def test_word_set_returns_set_not_list() -> None:
    """Same word twice → one entry."""
    assert word_set("revenue revenue revenue") == {"revenue"}


def test_stopwords_includes_common_english() -> None:
    for word in ("the", "a", "and", "is", "what", "we"):
        assert word in STOPWORDS


# ---------- jaccard ----------


def test_jaccard_identical_sets() -> None:
    s = {"a", "b", "c"}
    assert jaccard(s, s) == 1.0


def test_jaccard_disjoint_sets() -> None:
    assert jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap() -> None:
    """{a,b} ∩ {b,c} = {b}; ∪ = {a,b,c}; 1/3."""
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1.0 / 3.0)


def test_jaccard_both_empty_is_one() -> None:
    """Degenerate-but-equal returns 1.0 (matches agentic-stack)."""
    assert jaccard(set(), set()) == 1.0


def test_jaccard_one_empty_is_zero() -> None:
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a"}, set()) == 0.0


def test_jaccard_accepts_frozensets() -> None:
    """Both arguments are AbstractSet — frozenset works."""
    assert jaccard(frozenset({"a"}), frozenset({"a"})) == 1.0


# ---------- Composition ----------


def test_word_set_plus_jaccard_for_simple_relevance() -> None:
    """The intended use: cheap lexical overlap."""
    query = word_set("revenue notes for Q3")
    body = word_set("Discusses revenue targets and Q3 forecast.")
    score = jaccard(query, body)
    assert score > 0  # 'revenue' overlaps; both have other distinct words
