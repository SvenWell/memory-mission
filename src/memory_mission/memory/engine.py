"""``BrainEngine`` Protocol + ``InMemoryEngine`` concrete impl.

The engine is the pluggable storage layer for memory pages. Ports GBrain's
interface pattern: one ``BrainEngine`` Protocol, swappable concrete
implementations. V1 ships with an in-memory engine — good enough for tests,
early dogfood, and for Workflow agents to build against. Production backends
(pgvector via Postgres, SQLite + sqlite-vec) plug in behind the same
Protocol without touching callers.

Operations covered here (Step 6a):

- **Lifecycle** — ``connect`` / ``disconnect`` (no-ops for in-memory)
- **Page CRUD** — ``get_page`` / ``put_page`` / ``delete_page`` / ``list_pages``
- **Graph** — ``links_from`` / ``links_to`` via wikilink extraction
- **Keyword search** — substring match, logs a ``RetrievalEvent``
- **Stats** — ``stats()``

Vector search, hybrid search, compiled-truth boosting, and RRF fusion land
in Step 6b on top of this interface.

Every search call logs a ``RetrievalEvent`` to the active
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

    Search is naive keyword match over the compiled-truth zone; vector
    search and RRF fusion layer in via Step 6b on top of this interface.
    """

    def __init__(self) -> None:
        self._pages: dict[str, Page] = {}
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

    def delete_page(self, slug: str) -> None:
        self._pages.pop(slug, None)

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


def _keyword_score(page: Page, query_lower: str) -> float:
    """Score a page against a pre-lowercased query.

    Compiled-truth matches weight more than title matches because the truth
    zone is the thing curators and agents actually consume; title is a
    navigation aid. The ``COMPILED_TRUTH_BOOST`` constant from GBrain's
    hybrid search lives there; here we use a small coefficient difference
    since 6a is keyword-only.
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
