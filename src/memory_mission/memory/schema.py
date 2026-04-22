"""MECE directory schema for memory pages.

MECE = Mutually Exclusive, Collectively Exhaustive: every entity has exactly
one home domain within a given memory plane, preventing the drift-apart
problem where the same person exists under two divergent pages.

This is the vertical-neutral core taxonomy. Verticals (wealth, CRM, legal,
etc.) extend it via their own config — they do NOT modify this list.

Core domains (ported from GBrain):

- ``people``: Individuals — employees, contacts, advisors, clients
- ``companies``: Organizations
- ``deals``: Deals, transactions, negotiations
- ``meetings``: Meeting records (calls, boards, 1:1s)
- ``concepts``: Ideas, frameworks, strategies
- ``sources``: Reference material, citations, research
- ``inbox``: Unsorted pending items awaiting triage
- ``archive``: Retired / historical pages

Memory planes (Emile's governance model, Step 8):

- ``personal``: private to one employee. Path: ``personal/<employee_id>/``.
  Rich, local, never shared across employees.
- ``firm``: shared institutional truth. Path: ``firm/``. Only reached via
  PR-model promotion through the staging zone.

Staging is a separate zone (not a plane) — pulled items + proposed
promotions wait there under ``staging/personal/<emp>/<source>/`` or
``staging/firm/<source>/`` until the review skill promotes or rejects.

Slugs are lowercase kebab-case. Conflicts within one plane are resolved
by disambiguation suffix (``sarah-chen-meridian`` vs ``sarah-chen-bain``).

Raw API responses live in ``<plane_root>/<domain>/.raw/<slug>.json``
sidecars alongside the curated page — the page is the distilled view; the
sidecar preserves provenance for audit and re-enrichment.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

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

Plane = Literal["personal", "firm"]

# Same shape as observability's firm-id regex: alnum + ._- with a length
# bound, no path separators, no NUL. Employee IDs become path segments.
_SAFE_EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")


def is_valid_domain(domain: str) -> bool:
    """Return True if ``domain`` is one of the MECE core domains."""
    return domain in _DOMAIN_SET


def validate_domain(domain: str) -> str:
    """Raise ``ValueError`` if ``domain`` is not a recognized core domain."""
    if not is_valid_domain(domain):
        raise ValueError(f"Unknown domain {domain!r}. Valid: {list(CORE_DOMAINS)}")
    return domain


def validate_employee_id(employee_id: str) -> str:
    """Reject employee_ids that aren't safe path segments."""
    if not employee_id or not _SAFE_EMPLOYEE_ID.match(employee_id):
        raise ValueError(
            f"employee_id {employee_id!r} must match {_SAFE_EMPLOYEE_ID.pattern} "
            "(alphanumerics + ._- only, 1-128 chars, no path separators)"
        )
    return employee_id


def plane_root(plane: Plane, employee_id: str | None = None) -> PurePosixPath:
    """Return the repo-relative root for a memory plane.

    - ``personal`` requires ``employee_id`` and returns ``personal/<employee_id>``
    - ``firm`` rejects ``employee_id`` and returns ``firm``
    """
    if plane == "personal":
        if not employee_id:
            raise ValueError("personal plane requires employee_id")
        validate_employee_id(employee_id)
        return PurePosixPath("personal") / employee_id
    if plane == "firm":
        if employee_id is not None:
            raise ValueError(f"firm plane must not carry an employee_id (got {employee_id!r})")
        return PurePosixPath("firm")
    raise ValueError(f"unknown plane: {plane!r}")


def page_path(
    plane: Plane,
    domain: str,
    slug: str,
    *,
    employee_id: str | None = None,
) -> PurePosixPath:
    """Return the repo-relative page path.

    - Personal: ``personal/<employee_id>/<domain>/<slug>.md``
    - Firm: ``firm/<domain>/<slug>.md``

    ``PurePosixPath`` is filesystem-agnostic so storage backends bind the
    concrete root (``wiki_root``) separately.
    """
    validate_domain(domain)
    return plane_root(plane, employee_id) / domain / f"{slug}.md"


def raw_sidecar_path(
    plane: Plane,
    domain: str,
    slug: str,
    *,
    employee_id: str | None = None,
) -> PurePosixPath:
    """Return the raw-sidecar path.

    - Personal: ``personal/<employee_id>/<domain>/.raw/<slug>.json``
    - Firm: ``firm/<domain>/.raw/<slug>.json``

    Raw sidecars preserve the complete API response with fetch timestamps
    alongside the curated page. The page is the distilled view; the sidecar
    preserves everything upstream handed us.
    """
    validate_domain(domain)
    return plane_root(plane, employee_id) / domain / ".raw" / f"{slug}.json"


def staging_source_dir(
    *,
    target_plane: Plane,
    source: str,
    employee_id: str | None = None,
) -> PurePosixPath:
    """Return the staging subdirectory for a source + target plane.

    - Personal target: ``staging/personal/<employee_id>/<source>``
    - Firm target: ``staging/firm/<source>``

    Used by ``StagingWriter`` to determine where pulled items land before
    promotion. The ``target_plane`` is where the item would go if promoted.
    """
    root = PurePosixPath("staging") / plane_root(target_plane, employee_id)
    # source is validated inside StagingWriter; treat as raw segment here
    return root / source
