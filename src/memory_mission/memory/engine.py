"""``BrainEngine`` Protocol + ``InMemoryEngine`` concrete impl.

The engine is the pluggable storage layer for memory pages. Ports GBrain's
interface pattern: one ``BrainEngine`` Protocol, swappable concrete
implementations. V1 ships with an in-memory engine — good enough for tests,
early dogfood, and for Workflow agents to build against. Production backends
(pgvector via Postgres, SQLite + sqlite-vec) plug in behind the same
Protocol without touching callers.

Three search modes:

- ``search(query)`` — keyword only, fastest, no embeddings required
- ``query(question)`` — hybrid (keyword + vector, RRF-fused, cosine-blended)
- ``get_page(slug)`` — direct retrieval by known slug

Every search / query call logs a ``RetrievalEvent`` to the active
``observability_scope`` so retrievals show up in the audit trail alongside
extractions and promotions.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.pages import Page
from memory_mission.memory.schema import validate_domain
from memory_mission.memory.search import (
    COMPILED_TRUTH_BOOST,
    RRF_K,
    VECTOR_RRF_BLEND,
    EmbeddingProvider,
    cosine_similarity,
    rrf_fuse,
)
from memory_mission.observability.api import log_retrieval

SearchTier = Literal["navigate", "cascade", "discover"]


class SearchHit(BaseModel):
    """One match from a keyword or hybrid search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    score: float
    snippet: str = ""


class EngineStats(BaseModel):
    """Engine health / shape snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_count: int
    pages_by_domain: dict[str, int] = Field(default_factory=dict)
    connected: bool


@runtime_checkable
class BrainEngine(Protocol):
    """Pluggable storage + search interface for memory pages."""

    def connect(self) -> None:  # pragma: no cover - protocol shape
        ...

    def disconnect(self) -> None:  # pragma: no cover
        ...

    def get_page(self, slug: str) -> Page | None:  # pragma: no cover
        ...

    def put_page(self, page: Page) -> None:  # pragma: no cover
        ...

    def delete_page(self, slug: str) -> None:  # pragma: no cover
        ...

    def list_pages(self, domain: str | None = None) -> list[Page]:  # pragma: no cover
        ...

    def search(
        self, query: str, *, limit: int = 10, tier: SearchTier = "discover"
    ) -> list[SearchHit]:  # pragma: no cover
        ...

    def query(
        self,
        question: str,
        *,
        limit: int = 10,
        tier: SearchTier = "cascade",
    ) -> list[SearchHit]:  # pragma: no cover
        ...

    def links_from(self, slug: str) -> list[str]:  # pragma: no cover
        ...

    def links_to(self, slug: str) -> list[str]:  # pragma: no cover
        ...

    def stats(self) -> EngineStats:  # pragma: no cover
        ...


class InMemoryEngine:
    """Dict-backed engine. Good for tests, early dogfood, small brains.

    All state lives in a single process. Thread-safe for the simple read /
    write patterns used in tests; NOT safe for multi-process use. Swap to a
    DB-backed engine when you need concurrency or persistence.

    Pass ``embedder`` to enable the vector pass in ``query()``. Pages are
    embedded eagerly on ``put_page`` using the title + compiled truth, and
    the embedding is stored alongside the page. When no embedder is
    attached, ``query()`` falls back to keyword-only (equivalent to
    ``search()``) with the same RRF + compiled-truth-boost shape so the
    scoring path stays uniform.
    """

    def __init__(self, *, embedder: EmbeddingProvider | None = None) -> None:
        self._pages: dict[str, Page] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._embedder = embedder
        self._connected = False

    # ---------- Lifecycle ----------

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # ---------- Page CRUD ----------

    def get_page(self, slug: str) -> Page | None:
        return self._pages.get(slug)

    def put_page(self, page: Page) -> None:
        validate_domain(page.domain)
        self._pages[page.slug] = page
        if self._embedder is not None:
            text = f"{page.frontmatter.title}\n{page.compiled_truth}"
            self._embeddings[page.slug] = self._embedder.embed(text)

    def delete_page(self, slug: str) -> None:
        self._pages.pop(slug, None)
        self._embeddings.pop(slug, None)

    def list_pages(self, domain: str | None = None) -> list[Page]:
        if domain is None:
            return list(self._pages.values())
        validate_domain(domain)
        return [p for p in self._pages.values() if p.domain == domain]

    # ---------- Search ----------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tier: SearchTier = "discover",
    ) -> list[SearchHit]:
        """Naive substring search over title + compiled_truth.

        Logs a ``RetrievalEvent`` with the query, tier, loaded pages, and
        measured latency. A real hybrid search replaces this body in Step 6b;
        the Protocol stays stable.
        """
        q = query.strip().lower()
        started = time.perf_counter()
        hits: list[SearchHit] = []
        if q:
            for page in self._pages.values():
                score = _keyword_score(page, q)
                if score > 0:
                    hits.append(
                        SearchHit(
                            slug=page.slug,
                            score=score,
                            snippet=_snippet(page.compiled_truth, q),
                        )
                    )
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:limit]
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_retrieval(
            query=query,
            tier=tier,
            pages_loaded=[h.slug for h in top],
            token_budget=0,
            tokens_used=0,
            latency_ms=latency_ms,
        )
        return top

    def query(
        self,
        question: str,
        *,
        limit: int = 10,
        tier: SearchTier = "cascade",
    ) -> list[SearchHit]:
        """Hybrid search: keyword + vector, RRF-fused with compiled-truth boost.

        Pipeline:

        1. Keyword pass ranks pages by token matches over title + truth.
        2. Vector pass (if an embedder is attached) ranks pages by cosine
           similarity between the query embedding and each page embedding.
        3. Lists fuse via RRF (``k = 60``).
        4. Pages whose compiled-truth zone contained the query get their
           score multiplied by ``COMPILED_TRUTH_BOOST = 2.0``.
        5. When vector scores are present, final = 0.7 * RRF + 0.3 * cosine.
        6. Top ``limit`` pages returned, event logged.

        With no embedder attached the pipeline degrades to keyword-only via
        the same RRF scaffolding — same code path, same boost, no vector
        contribution. That's the "starter mode" until a real embedding
        provider plugs in.
        """
        q = question.strip().lower()
        started = time.perf_counter()
        if not q:
            return self._log_and_return([], question, tier, started)

        # Keyword pass: rank by current keyword score.
        keyword_scored = [(page.slug, _keyword_score(page, q)) for page in self._pages.values()]
        keyword_scored = [(s, sc) for s, sc in keyword_scored if sc > 0]
        keyword_scored.sort(key=lambda pair: pair[1], reverse=True)
        keyword_ranked = [slug for slug, _ in keyword_scored]

        # Vector pass: cosine similarity vs page embeddings.
        vector_similarity: dict[str, float] = {}
        vector_ranked: list[str] = []
        if self._embedder is not None and self._embeddings:
            query_vec = self._embedder.embed(question)
            scored = [
                (slug, cosine_similarity(query_vec, vec)) for slug, vec in self._embeddings.items()
            ]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            vector_ranked = [slug for slug, _ in scored]
            vector_similarity = dict(scored)

        ranked_lists = [lst for lst in (keyword_ranked, vector_ranked) if lst]
        if not ranked_lists:
            return self._log_and_return([], question, tier, started)

        fused = rrf_fuse(ranked_lists, k=RRF_K)

        # Compiled truth boost: pages whose TRUTH zone contains the query.
        for slug in list(fused):
            page = self._pages.get(slug)
            if page is not None and q in page.compiled_truth.lower():
                fused[slug] *= COMPILED_TRUTH_BOOST

        # Cosine blend — only meaningful when we actually ran the vector pass.
        if vector_similarity:
            for slug in list(fused):
                cos = vector_similarity.get(slug, 0.0)
                fused[slug] = VECTOR_RRF_BLEND * fused[slug] + (1.0 - VECTOR_RRF_BLEND) * cos

        hits = [
            SearchHit(
                slug=slug,
                score=score,
                snippet=_snippet(self._pages[slug].compiled_truth, q),
            )
            for slug, score in fused.items()
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return self._log_and_return(hits[:limit], question, tier, started)

    # ---------- Graph ----------

    def links_from(self, slug: str) -> list[str]:
        """Outgoing wikilinks from the page at ``slug``."""
        page = self._pages.get(slug)
        return page.wikilinks() if page is not None else []

    def links_to(self, slug: str) -> list[str]:
        """Slugs of pages whose compiled truth links TO ``slug``."""
        return sorted(
            {p.slug for p in self._pages.values() if slug in p.wikilinks() and p.slug != slug}
        )

    # ---------- Stats ----------

    def stats(self) -> EngineStats:
        counts: dict[str, int] = defaultdict(int)
        for p in self._pages.values():
            counts[p.domain] += 1
        return EngineStats(
            page_count=len(self._pages),
            pages_by_domain=dict(counts),
            connected=self._connected,
        )

    # ---------- Internals ----------

    def _log_and_return(
        self,
        hits: list[SearchHit],
        query: str,
        tier: SearchTier,
        started: float,
    ) -> list[SearchHit]:
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_retrieval(
            query=query,
            tier=tier,
            pages_loaded=[h.slug for h in hits],
            token_budget=0,
            tokens_used=0,
            latency_ms=latency_ms,
        )
        return hits


def _keyword_score(page: Page, query_lower: str) -> float:
    """Score a page against a pre-lowercased query.

    Compiled-truth matches weight more than title matches because the truth
    zone is what curators and agents actually consume; title is a navigation
    aid. The full ``COMPILED_TRUTH_BOOST`` multiplier in ``search.py`` is
    applied in ``query()`` on top of this; ``search()`` uses only these
    keyword weights.
    """
    title_hits = page.frontmatter.title.lower().count(query_lower)
    truth_hits = page.compiled_truth.lower().count(query_lower)
    return 1.0 * title_hits + 2.0 * truth_hits


def _snippet(text: str, query_lower: str, *, width: int = 80) -> str:
    """Return a short window around the first match of ``query_lower``."""
    lower = text.lower()
    idx = lower.find(query_lower)
    if idx < 0:
        return text[:width]
    start = max(0, idx - width // 2)
    end = min(len(text), start + width)
    return text[start:end]
