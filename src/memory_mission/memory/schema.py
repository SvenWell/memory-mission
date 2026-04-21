"""MECE directory schema for memory pages.

MECE = Mutually Exclusive, Collectively Exhaustive: every entity has exactly
one home domain, preventing the drift-apart problem where the same person
exists under `people/sarah-chen.md` AND `clients/sarah-chen.md` with
divergent facts.

This is the vertical-neutral core taxonomy. Verticals (wealth, CRM, legal,
etc.) extend it via their own config — they do NOT modify this list. The
project happens to deploy first into wealth management; the infrastructure
is general.

Core domains (ported from GBrain):

- ``people``: Individuals — employees, contacts, advisors, clients
- ``companies``: Organizations
- ``deals``: Deals, transactions, negotiations
- ``meetings``: Meeting records (calls, boards, 1:1s)
- ``concepts``: Ideas, frameworks, strategies
- ``sources``: Reference material, citations, research
- ``inbox``: Unsorted pending items awaiting triage
- ``archive``: Retired / historical pages

Slugs are lowercase kebab-case, validated by ``PageFrontmatter``. Conflicts
are resolved by disambiguation suffix (``sarah-chen-meridian`` vs
``sarah-chen-bain``).

Raw API responses live in ``<domain>/.raw/<slug>.json`` sidecars alongside
the curated page — the page is the distilled view; the sidecar preserves
provenance for audit and re-enrichment.
"""

from __future__ import annotations

from pathlib import PurePosixPath

CORE_DOMAINS: tuple[str, ...] = (
    "people",
    "companies",
    "deals",
    "meetings",
    "concepts",
    "sources",
    "inbox",
    "archive",
)

_DOMAIN_SET = frozenset(CORE_DOMAINS)


def is_valid_domain(domain: str) -> bool:
    """Return True if ``domain`` is one of the MECE core domains."""
    return domain in _DOMAIN_SET


def validate_domain(domain: str) -> str:
    """Raise ``ValueError`` if ``domain`` is not a recognized core domain."""
    if not is_valid_domain(domain):
        raise ValueError(f"Unknown domain {domain!r}. Valid: {list(CORE_DOMAINS)}")
    return domain


def page_path(domain: str, slug: str) -> PurePosixPath:
    """Return the repo-relative page path (``<domain>/<slug>.md``).

    Returns a ``PurePosixPath`` so the path is filesystem-agnostic and can be
    used as a wiki-root-relative key by storage backends. Callers bind to a
    concrete root (``wiki_root``) when writing to disk.
    """
    validate_domain(domain)
    return PurePosixPath(domain) / f"{slug}.md"


def raw_sidecar_path(domain: str, slug: str) -> PurePosixPath:
    """Return the raw-sidecar path (``<domain>/.raw/<slug>.json``).

    Raw sidecars preserve complete API responses with fetch timestamps,
    alongside the curated page. The page holds distilled facts; the sidecar
    preserves everything upstream handed us.
    """
    validate_domain(domain)
    return PurePosixPath(domain) / ".raw" / f"{slug}.json"
