"""Shared text utilities: stopword-filtered tokenization + Jaccard similarity.

Single source of truth for "what counts as a content word" across the
codebase. Multiple layers (hybrid search, future promotion-pipeline
dedup, future workflow-agent retrieval) all depend on the same
definition; drift here would silently make different layers see
different words.

Ported (with light adaptation) from agentic-stack's ``harness/text.py``
under Apache 2.0 — see https://github.com/codejunkie99/agentic-stack.
The stopword list and 3+ char content-word rule survive verbatim
because they're known-good defaults from production agent memory work.
"""

from __future__ import annotations

import re
from collections.abc import Set as AbstractSet

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "should",
        "could",
        "may",
        "might",
        "must",
        "can",
        "this",
        "that",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "as",
        "if",
        "then",
        "when",
        "where",
        "how",
        "why",
        "what",
        "it",
        "its",
        "their",
        "our",
        "we",
        "you",
        "i",
        "not",
        "no",
    }
)

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")


def word_set(text: str | None) -> set[str]:
    """Lowercase content words: 3+ chars, alpha-leading, stopwords removed.

    Returns a set so callers can use set operators directly. Empty input
    returns an empty set (no exception).
    """
    if not text:
        return set()
    return {token.lower() for token in _WORD_RE.findall(text) if token.lower() not in STOPWORDS}


def jaccard(a: AbstractSet[str], b: AbstractSet[str]) -> float:
    """Jaccard similarity ``|a ∩ b| / |a ∪ b|``.

    Both empty → 1.0 (degenerate-but-equal). One empty → 0.0.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
