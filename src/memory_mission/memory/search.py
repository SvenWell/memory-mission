"""Hybrid search primitives — RRF fusion + cosine re-scoring.

Ports the search recipe from GBrain's ``src/core/search/hybrid.ts``:

1. **Keyword pass.** Token / substring match over title + compiled truth.
2. **Vector pass.** Cosine similarity of the query embedding against page
   embeddings, if an ``EmbeddingProvider`` is attached.
3. **RRF fusion.** Combine the two ranked lists via reciprocal-rank fusion
   with ``RRF_K = 60``.
4. **Compiled truth boost.** Pages whose compiled-truth zone matches the
   query get their fused score multiplied by ``COMPILED_TRUTH_BOOST = 2.0``
   — truth beats title because the curated truth zone is what agents
   actually consume.
5. **Cosine blend.** When vector scores are available, final score is
   ``VECTOR_RRF_BLEND * rrf + (1 - VECTOR_RRF_BLEND) * cosine``. The 70/30
   default matches GBrain's starting point; tune on pilot data later.

Three tunables surface as constants:

- ``RRF_K = 60`` — larger values dampen rank contribution, making the
  fusion more tolerant of out-of-order lists.
- ``COMPILED_TRUTH_BOOST = 2.0`` — multiplier applied to pages matching
  the compiled-truth zone specifically.
- ``VECTOR_RRF_BLEND = 0.7`` — weight on the RRF component; the remainder
  goes to cosine.

``HashEmbedder`` is a deterministic stand-in for a real embedder (OpenAI,
Gemini). It's NOT semantic — same text produces the same vector, but
"sarah chen" and "ceo" look as distant as any other two tokens. Useful
for verifying pipeline plumbing without calling a live API. Swap in a real
``EmbeddingProvider`` when a real flow needs semantic similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

RRF_K: int = 60
COMPILED_TRUTH_BOOST: float = 2.0
VECTOR_RRF_BLEND: float = 0.7


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface for any text embedder."""

    @property
    def dimension(self) -> int:  # pragma: no cover - protocol shape
        ...

    def embed(self, text: str) -> list[float]:  # pragma: no cover
        ...


class HashEmbedder:
    """Deterministic SHA256-based bag-of-tokens embedder.

    NOT semantic. Each token contributes to a dimension chosen by hashing
    the token with SHA256 (stable across processes, unlike Python's ``hash``).
    Vectors are L2-normalized so cosine similarity is bounded in ``[-1, 1]``.

    Use for tests and plumbing verification. Replace with OpenAI / Gemini
    when you need real semantic search.
    """

    def __init__(self, *, dimension: int = 32) -> None:
        if dimension < 1:
            raise ValueError(f"dimension must be >= 1, got {dimension}")
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokenize(text):
            idx = _token_index(token, self._dim)
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 when either vector has zero norm."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rrf_fuse(ranked_lists: Sequence[Sequence[str]], *, k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion.

    For each ranked list, each item contributes ``1 / (k + rank)`` where
    ``rank`` is 1-indexed position. Items in multiple lists accumulate.
    Returns ``{item: fused_score}``, order is insertion-order of first
    appearance.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for position, item in enumerate(ranked):
            rank = position + 1
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


# ---------- Internals ----------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _token_index(token: str, dimension: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % dimension
