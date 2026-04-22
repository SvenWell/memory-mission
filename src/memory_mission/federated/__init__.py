"""Federated cross-employee pattern detector (Step 16).

Why this exists: each employee's agent extracts facts into their
private personal plane. The firm learns only what explicit
promotions surface. But when three employees independently arrive
at the same fact — sourced from three different documents — that's
high-signal evidence the fact belongs to the firm, not just to
each individual.

Maciek's constitutional frame pushes toward governed promotion;
Emile's federated frame pushes toward cross-employee aggregation.
This module is where the two meet. Patterns get detected, proposals
get generated, and they flow through the same PR-model review that
governs every other firm-plane write.

Shape:

- ``FirmCandidate`` — a (subject, predicate, object) triple that
  appears across ≥ N employees' personal planes via ≥ N distinct
  source documents. Structured Pydantic so the shape is easy to
  label for eval work (``docs/EVALS.md`` 2.6).
- ``detect_firm_candidates(kg, *, min_employees, min_sources)`` —
  deterministic SQL scan over the firm's KG. Filters by
  ``source_closet LIKE 'personal/%'``, groups by triple, thresholds
  on distinct employees AND distinct source files.
- ``propose_firm_candidate(store, candidate, *, ...)`` — turns a
  candidate into a ``Proposal`` targeting the firm plane. Uses
  ``create_proposal`` so idempotency and observability are inherited.

Independence check: the dominant failure mode (per eval doc 2.6)
is firing on three employees each ingesting THE SAME Granola
transcript. The detector defends against this by requiring N
distinct ``source_file`` values, not just N distinct employees.
Same transcript shared to everyone = 1 source_file, insufficient.

Admin-only: the detector reads across every employee's personal-
plane provenance. Skill-level permission enforcement decides who
can invoke it. The module itself is a pure library.
"""

from memory_mission.federated.detector import (
    DEFAULT_MIN_EMPLOYEES,
    DEFAULT_MIN_SOURCES,
    CandidateSource,
    FirmCandidate,
    aggregate_noisy_or,
    detect_firm_candidates,
    propose_firm_candidate,
)

__all__ = [
    "DEFAULT_MIN_EMPLOYEES",
    "DEFAULT_MIN_SOURCES",
    "CandidateSource",
    "FirmCandidate",
    "aggregate_noisy_or",
    "detect_firm_candidates",
    "propose_firm_candidate",
]
