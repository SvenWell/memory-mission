"""``BrainEngine`` Protocol + ``InMemoryEngine`` concrete impl.

The engine is the pluggable storage layer for memory pages. Ports GBrain's
interface pattern: one ``BrainEngine`` Protocol, swappable concrete
implementations. V1 ships with an in-memory engine — good enough for tests,
early dogfood, and for Workflow agents to build against. Production backends
(pgvector via Postgres, SQLite + sqlite-vec) plug in behind the same
Protocol without touching callers.

Pages live on one of two planes (Step 8 onwards):

- ``personal/<employee_id>`` — private to one employee
- ``firm`` — shared institutional truth (only via PR-model promotion)

Every page op takes the plane (and employee_id for personal) so cross-plane
leakage is impossible by construction. The same slug can coexist as a
personal page for Alice, a personal page for Bob, AND a firm page; they're
distinct pages with distinct histories.

Three search modes:

- ``search(query)`` — keyword only, fastest, no embeddings required
- ``query(question)`` — hybrid (keyword + vector, RRF-fused, cosine-blended)
- ``get_page(slug)`` — direct retrieval by known slug

All retrieval methods accept an optional plane filter so workflow agents
can say "search only the firm plane" (drafting an external-facing
summary) or "search only my personal plane" (scratchpad).

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
from memory_mission.memory.schema import (
    Plane,
    validate_domain,
    validate_employee_id,
)
from memory_mission.memory.search import (
    COMPILED_TRUTH_BOOST,
    RRF_K,
    VECTOR_RRF_BLEND,
    EmbeddingProvider,
    cosine_similarity,
    rrf_fuse,
)
from memory_mission.memory.tiers import Tier, is_at_least
from memory_mission.observability.api import log_retrieval

SearchTier = Literal["navigate", "cascade", "discover"]


class PageKey(BaseModel):
    """Composite key identifying a page's location in the two-plane model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plane: Plane
    slug: str
    employee_id: str | None = None

    def __hash__(self) -> int:
        return hash((self.plane, self.slug, self.employee_id))


class SearchHit(BaseModel):
    """One match from a keyword or hybrid search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    plane: Plane
    employee_id: str | None = None
    score: float
    snippet: str = ""


class EngineStats(BaseModel):
    """Engine health / shape snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_count: int
    pages_by_plane: dict[Plane, int] = Field(default_factory=dict)
    pages_by_domain: dict[str, int] = Field(default_factory=dict)
    connected: bool


def _validate_plane_args(plane: Plane, employee_id: str | None) -> None:
    if plane == "personal":
        if not employee_id:
            raise ValueError("personal plane requires employee_id")
        validate_employee_id(employee_id)
    elif plane == "firm":
        if employee_id is not None:
            raise ValueError("firm plane must not carry an employee_id")
    else:
        raise ValueError(f"unknown plane: {plane!r}")


@runtime_checkable
class BrainEngine(Protocol):
    """Pluggable storage + search interface for memory pages."""

    def connect(self) -> None:  # pragma: no cover - protocol shape
        ...

    def disconnect(self) -> None:  # pragma: no cover
        ...

    def get_page(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> Page | None:  # pragma: no cover
        ...

    def put_page(
        self,
        page: Page,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> None:  # pragma: no cover
        ...

    def delete_page(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> None:  # pragma: no cover
        ...

    def list_pages(
        self,
        *,
        plane: Plane | None = None,
        employee_id: str | None = None,
        domain: str | None = None,
    ) -> list[Page]:  # pragma: no cover
        ...

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tier: SearchTier = "discover",
        plane: Plane | None = None,
        employee_id: str | None = None,
        tier_floor: Tier | None = None,
    ) -> list[SearchHit]:  # pragma: no cover
        ...

    def query(
        self,
        question: str,
        *,
        limit: int = 10,
        tier: SearchTier = "cascade",
        plane: Plane | None = None,
        employee_id: str | None = None,
        tier_floor: Tier | None = None,
    ) -> list[SearchHit]:  # pragma: no cover
        ...

    def links_from(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> list[str]:  # pragma: no cover
        ...

    def links_to(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> list[str]:  # pragma: no cover
        ...

    def stats(self) -> EngineStats:  # pragma: no cover
        ...


class InMemoryEngine:
    """Dict-backed engine. Good for tests, early dogfood, small brains.

    State lives in a single process, keyed by ``PageKey(plane, slug,
    employee_id)`` so the same slug can exist independently across
    employees and the firm plane.

    Pass ``embedder`` to enable the vector pass in ``query()``. Pages are
    embedded eagerly on ``put_page`` using the title + compiled truth, and
    the embedding is stored alongside the page. When no embedder is
    attached, ``query()`` falls back to keyword-only via the same RRF +
    compiled-truth-boost scaffolding.
    """

    def __init__(self, *, embedder: EmbeddingProvider | None = None) -> None:
        self._pages: dict[PageKey, Page] = {}
        self._embeddings: dict[PageKey, list[float]] = {}
        self._embedder = embedder
        self._connected = False

    # ---------- Lifecycle ----------

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # ---------- Page CRUD ----------

    def get_page(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> Page | None:
        _validate_plane_args(plane, employee_id)
        return self._pages.get(PageKey(plane=plane, slug=slug, employee_id=employee_id))

    def put_page(
        self,
        page: Page,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> None:
        _validate_plane_args(plane, employee_id)
        validate_domain(page.domain)
        key = PageKey(plane=plane, slug=page.slug, employee_id=employee_id)
        self._pages[key] = page
        if self._embedder is not None:
            text = f"{page.frontmatter.title}\n{page.compiled_truth}"
            self._embeddings[key] = self._embedder.embed(text)

    def delete_page(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> None:
        _validate_plane_args(plane, employee_id)
        key = PageKey(plane=plane, slug=slug, employee_id=employee_id)
        self._pages.pop(key, None)
        self._embeddings.pop(key, None)

    def list_pages(
        self,
        *,
        plane: Plane | None = None,
        employee_id: str | None = None,
        domain: str | None = None,
    ) -> list[Page]:
        if plane is not None:
            _validate_plane_args(plane, employee_id)
        elif employee_id is not None:
            raise ValueError("employee_id only meaningful when plane is also given")
        if domain is not None:
            validate_domain(domain)
        out: list[Page] = []
        for key, page in self._pages.items():
            if plane is not None and key.plane != plane:
                continue
            if plane == "personal" and key.employee_id != employee_id:
                continue
            if domain is not None and page.domain != domain:
                continue
            out.append(page)
        return out

    # ---------- Search ----------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tier: SearchTier = "discover",
        plane: Plane | None = None,
        employee_id: str | None = None,
        tier_floor: Tier | None = None,
    ) -> list[SearchHit]:
        """Naive substring search over title + compiled_truth.

        Logs a ``RetrievalEvent`` with the query, tier, loaded pages, and
        measured latency. An optional ``plane`` filter restricts the search
        to one plane (with ``employee_id`` for personal).

        ``tier_floor`` restricts results to pages at or above the given
        tier. E.g., ``tier_floor="doctrine"`` returns only constitution +
        doctrine pages, hiding policy + decision. Leave ``None`` to
        return every tier (backwards-compatible default).
        """
        _validate_scope_filter(plane, employee_id)
        q = query.strip().lower()
        started = time.perf_counter()
        hits: list[SearchHit] = []
        if q:
            for key, page in self._pages.items():
                if not _in_scope(key, plane, employee_id):
                    continue
                if not _in_tier(page, tier_floor):
                    continue
                score = _keyword_score(page, q)
                if score > 0:
                    hits.append(
                        SearchHit(
                            slug=page.slug,
                            plane=key.plane,
                            employee_id=key.employee_id,
                            score=score,
                            snippet=_snippet(page.compiled_truth, q),
                        )
                    )
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:limit]
        return self._log_and_return(top, query, tier, started)

    def query(
        self,
        question: str,
        *,
        limit: int = 10,
        tier: SearchTier = "cascade",
        plane: Plane | None = None,
        employee_id: str | None = None,
        tier_floor: Tier | None = None,
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

        Optional ``plane`` / ``employee_id`` restrict the search scope.
        Optional ``tier_floor`` restricts results to pages at or above
        the given doctrinal tier (Step 15). Useful for workflow agents
        that only want authoritative pages — meeting-prep might ask for
        ``tier_floor="policy"`` to skip low-authority decisions.
        """
        _validate_scope_filter(plane, employee_id)
        q = question.strip().lower()
        started = time.perf_counter()
        if not q:
            return self._log_and_return([], question, tier, started)

        in_scope = {
            key: page
            for key, page in self._pages.items()
            if _in_scope(key, plane, employee_id) and _in_tier(page, tier_floor)
        }

        keyword_scored: list[tuple[PageKey, float]] = [
            (key, _keyword_score(page, q)) for key, page in in_scope.items()
        ]
        keyword_scored = [(k, sc) for k, sc in keyword_scored if sc > 0]
        keyword_scored.sort(key=lambda pair: pair[1], reverse=True)
        keyword_ranked = [k for k, _ in keyword_scored]

        vector_similarity: dict[PageKey, float] = {}
        vector_ranked: list[PageKey] = []
        if self._embedder is not None and in_scope:
            scoped_embeddings = {k: v for k, v in self._embeddings.items() if k in in_scope}
            if scoped_embeddings:
                query_vec = self._embedder.embed(question)
                scored = [
                    (k, cosine_similarity(query_vec, vec)) for k, vec in scoped_embeddings.items()
                ]
                scored.sort(key=lambda pair: pair[1], reverse=True)
                vector_ranked = [k for k, _ in scored]
                vector_similarity = dict(scored)

        ranked_lists = [lst for lst in (keyword_ranked, vector_ranked) if lst]
        if not ranked_lists:
            return self._log_and_return([], question, tier, started)

        # RRF takes sequences of string-ish ids; convert key to a stable
        # token + back-map for lookup after fusion.
        key_token: dict[str, PageKey] = {_key_token(k): k for k in in_scope}
        ranked_token_lists = [[_key_token(k) for k in lst] for lst in ranked_lists]
        fused_tokens = rrf_fuse(ranked_token_lists, k=RRF_K)
        fused: dict[PageKey, float] = {key_token[t]: score for t, score in fused_tokens.items()}

        # Compiled truth boost: pages whose TRUTH zone contains the query.
        for key in list(fused):
            page = in_scope[key]
            if q in page.compiled_truth.lower():
                fused[key] *= COMPILED_TRUTH_BOOST

        # Cosine blend — only meaningful when we actually ran the vector pass.
        if vector_similarity:
            for key in list(fused):
                cos = vector_similarity.get(key, 0.0)
                fused[key] = VECTOR_RRF_BLEND * fused[key] + (1.0 - VECTOR_RRF_BLEND) * cos

        hits = [
            SearchHit(
                slug=key.slug,
                plane=key.plane,
                employee_id=key.employee_id,
                score=score,
                snippet=_snippet(in_scope[key].compiled_truth, q),
            )
            for key, score in fused.items()
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return self._log_and_return(hits[:limit], question, tier, started)

    # ---------- Graph ----------

    def links_from(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> list[str]:
        """Outgoing wikilinks from the page at ``(plane, slug)``."""
        _validate_plane_args(plane, employee_id)
        page = self._pages.get(PageKey(plane=plane, slug=slug, employee_id=employee_id))
        return page.wikilinks() if page is not None else []

    def links_to(
        self,
        slug: str,
        *,
        plane: Plane,
        employee_id: str | None = None,
    ) -> list[str]:
        """Slugs of pages in the SAME scope whose compiled truth links to ``slug``.

        Wikilinks are scope-local — a firm-plane page's links resolve to
        other firm-plane slugs; a personal page's links resolve within that
        employee's plane. Cross-plane linking is out of scope for V1.
        """
        _validate_plane_args(plane, employee_id)
        return sorted(
            {
                key.slug
                for key, page in self._pages.items()
                if _in_scope(key, plane, employee_id)
                and slug in page.wikilinks()
                and key.slug != slug
            }
        )

    # ---------- Stats ----------

    def stats(self) -> EngineStats:
        by_plane: dict[Plane, int] = defaultdict(int)
        by_domain: dict[str, int] = defaultdict(int)
        for key, page in self._pages.items():
            by_plane[key.plane] += 1
            by_domain[page.domain] += 1
        return EngineStats(
            page_count=len(self._pages),
            pages_by_plane=dict(by_plane),
            pages_by_domain=dict(by_domain),
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


def _validate_scope_filter(plane: Plane | None, employee_id: str | None) -> None:
    if plane is None and employee_id is not None:
        raise ValueError("employee_id only meaningful when plane is also given")
    if plane is not None:
        _validate_plane_args(plane, employee_id)


def _in_scope(key: PageKey, plane: Plane | None, employee_id: str | None) -> bool:
    if plane is None:
        return True
    if key.plane != plane:
        return False
    if plane == "personal" and key.employee_id != employee_id:
        return False
    return True


def _in_tier(page: Page, tier_floor: Tier | None) -> bool:
    """``None`` means no filter; otherwise require page tier at or above floor."""
    if tier_floor is None:
        return True
    return is_at_least(page.frontmatter.tier, tier_floor)


def _key_token(key: PageKey) -> str:
    """Stringify a ``PageKey`` for RRF fusion + back-mapping."""
    emp = key.employee_id or ""
    return f"{key.plane}\0{emp}\0{key.slug}"


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
