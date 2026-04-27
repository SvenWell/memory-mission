"""Context-farming coverage primitives (ADR-0012).

Five named pure-function aggregates over the existing substrate
(``BrainEngine`` + ``KnowledgeGraph`` + ``ProposalStore``) that
operationalize the Context Farmer role. Each primitive returns a
typed Pydantic aggregate the caller can render or pipe into a
dashboard.

The Bases dashboard at ``memory/templates/dashboard.farming.base``
covers the page-level views Bases can compute natively (per-domain
coverage, decay flags). The primitives here cover the cross-cutting
analytics that need joins across pages + KG + proposals (which
Bases can't do).

Together: Bases is the always-on operator UX; this module is the
programmatic surface a workflow skill or admin script consumes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.knowledge_graph import Triple
from memory_mission.memory.tiers import Tier, is_at_least

if TYPE_CHECKING:
    from memory_mission.memory.engine import BrainEngine
    from memory_mission.memory.knowledge_graph import KnowledgeGraph
    from memory_mission.memory.pages import Page
    from memory_mission.memory.schema import Plane
    from memory_mission.promotion.proposals import ProposalStore


# ---------- 1. Per-domain coverage ----------


class DomainCoverage(BaseModel):
    """Page count for one domain, broken down by tier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    page_count: int
    by_tier: dict[Tier, int] = Field(default_factory=dict)


def compute_domain_coverage(
    engine: BrainEngine,
    *,
    plane: Plane | None = None,
    employee_id: str | None = None,
) -> list[DomainCoverage]:
    """Page count per domain, broken down by tier.

    Returns one ``DomainCoverage`` per domain that has at least one
    page. Sorted by page count descending so heaviest domains
    surface first. Operator action: notice under-promoted domains
    (lots of decision-tier content, no doctrine).
    """
    pages = engine.list_pages(plane=plane, employee_id=employee_id)
    by_domain: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        by_domain[page.frontmatter.domain].append(page)

    result: list[DomainCoverage] = []
    for domain, group in by_domain.items():
        tier_counts: dict[Tier, int] = defaultdict(int)
        for page in group:
            tier_counts[page.frontmatter.tier] += 1
        result.append(
            DomainCoverage(
                domain=domain,
                page_count=len(group),
                by_tier=dict(tier_counts),
            )
        )
    result.sort(key=lambda c: c.page_count, reverse=True)
    return result


# ---------- 2. Decay flags ----------


class DecayedPage(BaseModel):
    """A page of tier ≥ floor that hasn't been touched in N+ days."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    domain: str
    tier: Tier
    age_days: int
    reviewed_at: datetime | None = None


def find_decayed_pages(
    engine: BrainEngine,
    *,
    plane: Plane | None = None,
    employee_id: str | None = None,
    min_age_days: int = 90,
    min_tier: Tier = "doctrine",
    now: datetime | None = None,
) -> list[DecayedPage]:
    """Pages of tier ≥ ``min_tier`` not touched in ``min_age_days``+.

    "Touched" = ``reviewed_at`` if set, else page's ``valid_from`` if
    set, else 0 days old (so a brand-new page never decays). Doctrine
    pages are supposed to be reviewed periodically; constitution even
    more so. Operator action: schedule a doctrine review pass; amend
    or invalidate stale doctrine.
    """
    pages = engine.list_pages(plane=plane, employee_id=employee_id)
    now_dt = now or datetime.now(UTC)
    threshold = timedelta(days=min_age_days)

    out: list[DecayedPage] = []
    for page in pages:
        if not is_at_least(page.frontmatter.tier, min_tier):
            continue
        last_touched = _last_touched(page, now=now_dt)
        if last_touched is None:
            continue
        age = now_dt - last_touched
        if age < threshold:
            continue
        out.append(
            DecayedPage(
                slug=page.frontmatter.slug,
                domain=page.frontmatter.domain,
                tier=page.frontmatter.tier,
                age_days=age.days,
                reviewed_at=page.frontmatter.reviewed_at,
            )
        )
    out.sort(key=lambda d: d.age_days, reverse=True)
    return out


# ---------- 3. Missing page coverage ----------


class MissingPageCoverage(BaseModel):
    """An entity referenced repeatedly without an existing doctrine page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_name: str
    triple_mention_count: int = 0
    proposal_mention_count: int = 0
    has_existing_page: bool = False


def find_missing_page_coverage(
    engine: BrainEngine,
    kg: KnowledgeGraph,
    store: ProposalStore | None = None,
    *,
    plane: Plane | None = None,
    employee_id: str | None = None,
    min_triple_mentions: int = 3,
    min_proposal_mentions: int = 0,
    count_objects: bool = False,
) -> list[MissingPageCoverage]:
    """Entities mentioned often but with no doctrine-tier page.

    Counts entity mentions across (a) currently-true KG triples and
    (b) pending+approved proposals' ``target_entity`` filtered to
    ``plane`` / ``employee_id`` so a firm-plane farming report does
    not surface personal-plane proposal targets.

    By default only **subjects** are counted in (a) because objects
    are commonly literals (``portfolio``, ``active``, ``$20m``) that
    would produce false missing-page work. Set ``count_objects=True``
    to additionally count objects that are known KG entities (each is
    verified via ``kg.get_entity``); literal objects are still
    skipped.

    Operator action: create + propose a person/company/concept page;
    promote stub pages to doctrine after the first N corroborated
    facts land.
    """
    # Index existing pages by slug + aliases for cheap "has page?" lookup.
    pages = engine.list_pages(plane=plane, employee_id=employee_id)
    page_keys: set[str] = set()
    for page in pages:
        if not is_at_least(page.frontmatter.tier, "doctrine"):
            continue
        page_keys.add(page.frontmatter.slug)
        for alias in page.frontmatter.aliases:
            page_keys.add(alias)

    triple_counts: dict[str, int] = defaultdict(int)
    # Two-pass when counting objects: first pass collects entity-like
    # names (anything appearing as subject, plus anything explicitly
    # registered via ``add_entity``); second pass counts subject + (if
    # gated) object mentions. ``add_triple`` does NOT auto-register
    # entities, so a strict ``kg.get_entity`` gate would miss almost
    # everything; subjects-of-some-triple is the practical signal.
    valid_triples = [t for t in kg.timeline() if t.valid_to is None]
    if count_objects:
        entity_names: set[str] = {t.subject for t in valid_triples}
        # Cache per-object explicit-entity lookup so a 100k-triple KG
        # hits ``kg.get_entity`` at most once per distinct object name.
        explicit_lookup: dict[str, bool] = {}

        def _is_entity_like(name: str) -> bool:
            if name in entity_names:
                return True
            if name not in explicit_lookup:
                explicit_lookup[name] = kg.get_entity(name) is not None
            return explicit_lookup[name]

    for triple in valid_triples:
        triple_counts[triple.subject] += 1
        if count_objects and _is_entity_like(triple.object):
            triple_counts[triple.object] += 1

    proposal_counts: dict[str, int] = defaultdict(int)
    if store is not None:
        for status in ("pending", "approved"):
            for proposal in store.list(status=status, target_plane=plane):
                if employee_id is not None and proposal.target_employee_id != employee_id:
                    continue
                proposal_counts[proposal.target_entity] += 1

    out: list[MissingPageCoverage] = []
    candidates = set(triple_counts) | set(proposal_counts)
    for entity_name in candidates:
        triple_n = triple_counts.get(entity_name, 0)
        proposal_n = proposal_counts.get(entity_name, 0)
        if triple_n < min_triple_mentions and proposal_n < min_proposal_mentions:
            continue
        has_page = entity_name in page_keys
        if has_page:
            continue
        out.append(
            MissingPageCoverage(
                entity_name=entity_name,
                triple_mention_count=triple_n,
                proposal_mention_count=proposal_n,
                has_existing_page=has_page,
            )
        )
    out.sort(
        key=lambda m: m.triple_mention_count + m.proposal_mention_count,
        reverse=True,
    )
    return out


# ---------- 4. Source-attribution debt ----------


class AttributionDebt(BaseModel):
    """A currently-true triple without sufficient provenance.

    Identified by ``(subject, predicate, object)`` since ``Triple`` is
    frozen Pydantic without a row-id field. Operators trace back to
    the proposal that wrote the triple via the
    ``triple_sources`` table when remediating.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    object: str
    has_source_closet: bool
    has_source_file: bool


def find_attribution_debt(kg: KnowledgeGraph) -> list[AttributionDebt]:
    """Currently-true triples missing ``source_closet`` or ``source_file``.

    Provenance is mandatory ("no quote, no fact"). Triples that
    slipped through without provenance are debt: correctness-grade
    truth without compliance-grade attribution. Operator action:
    trace back to proposal, attach source post-hoc, OR invalidate.
    """
    out: list[AttributionDebt] = []
    for triple in kg.timeline():
        if triple.valid_to is not None:
            continue
        has_closet = bool(triple.source_closet)
        has_file = bool(triple.source_file)
        if has_closet and has_file:
            continue
        out.append(
            AttributionDebt(
                subject=triple.subject,
                predicate=triple.predicate,
                object=triple.object,
                has_source_closet=has_closet,
                has_source_file=has_file,
            )
        )
    return out


# ---------- 5. Low-corroboration concentrations ----------


class LowCorroborationCluster(BaseModel):
    """Entity with N+ currently-true triples below a confidence floor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_name: str
    weak_triple_count: int
    weakest_confidence: float
    weak_triples: list[Triple] = Field(default_factory=list)


def find_low_corroboration_clusters(
    kg: KnowledgeGraph,
    *,
    confidence_floor: float = 0.7,
    min_cluster_size: int = 3,
) -> list[LowCorroborationCluster]:
    """Entities with N+ currently-true triples below ``confidence_floor``.

    Bayesian corroboration (ADR-0001) means a sustained low-confidence
    cluster indicates either the entity needs more evidence (run more
    ingestion) or the existing evidence is genuinely uncertain.
    Operator action: trigger targeted re-extraction; or promote a
    coherence warning surfacing the uncertainty in downstream queries.
    """
    weak_by_entity: dict[str, list[Triple]] = defaultdict(list)
    for triple in kg.timeline():
        if triple.valid_to is not None:
            continue
        if triple.confidence >= confidence_floor:
            continue
        # Count both subject and object — uncertainty around either
        # is grounds for the cluster.
        weak_by_entity[triple.subject].append(triple)
        weak_by_entity[triple.object].append(triple)

    out: list[LowCorroborationCluster] = []
    for entity_name, weak in weak_by_entity.items():
        if len(weak) < min_cluster_size:
            continue
        weakest = min(t.confidence for t in weak)
        out.append(
            LowCorroborationCluster(
                entity_name=entity_name,
                weak_triple_count=len(weak),
                weakest_confidence=weakest,
                weak_triples=weak,
            )
        )
    out.sort(key=lambda c: c.weakest_confidence)
    return out


# ---------- helpers ----------


def _last_touched(page: Page, *, now: datetime) -> datetime | None:
    """Most recent "touched" datetime for a page, or None.

    Order of precedence:
    1. ``reviewed_at`` — operator-asserted last review.
    2. ``valid_from`` — implicit creation/last-rewrite.
    3. None — never touched (caller treats as not-decayed).
    """
    fm = page.frontmatter
    if fm.reviewed_at is not None:
        return fm.reviewed_at
    if fm.valid_from is not None:
        return datetime.combine(fm.valid_from, datetime.min.time(), tzinfo=UTC)
    return None


__all__ = [
    "AttributionDebt",
    "DecayedPage",
    "DomainCoverage",
    "LowCorroborationCluster",
    "MissingPageCoverage",
    "compute_domain_coverage",
    "find_attribution_debt",
    "find_decayed_pages",
    "find_low_corroboration_clusters",
    "find_missing_page_coverage",
]
