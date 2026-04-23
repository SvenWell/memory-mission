"""Deterministic cross-employee pattern detector.

Scans ``KnowledgeGraph`` for personal-plane triples that appear
across enough employees AND enough distinct source documents to
warrant a firm-level proposal.

Two thresholds matter:

- ``min_employees`` — at least N distinct personal-plane closets
  (``personal/alice``, ``personal/bob``, ...) have the same
  (subject, predicate, object). Default 3 per ``docs/EVALS.md`` 2.6.
- ``min_sources`` — at least N distinct ``source_file`` values.
  Guards against the dominant failure mode (same Granola transcript
  shared to all three — one source_file, not three independent ones).
  Default 3.

Only fires when BOTH thresholds pass. Anything less is noise.

Output is a list of ``FirmCandidate`` — Pydantic records that a
downstream skill or caller turns into ``Proposal`` objects via the
normal promotion pipeline. This module writes nothing; it is a
pure read + compute layer. That keeps the detector grade-able
against fixed fixtures (eval doc 2.6 recipe).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.extraction.schema import RelationshipFact
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.memory.tiers import DEFAULT_TIER, Tier
from memory_mission.promotion import Proposal, ProposalStore, create_proposal

DEFAULT_MIN_EMPLOYEES: int = 3
DEFAULT_MIN_SOURCES: int = 3

# Confidence cap for the Noisy-OR aggregation, matching the KG's own
# corroboration cap so no agent-path fact can reach certainty.
_AGGREGATION_CAP: float = 0.99


class CandidateSource(BaseModel):
    """One contributing evidence row for a ``FirmCandidate``.

    Preserves the provenance trail so reviewers can trace a candidate
    back to specific personal-plane extractions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_closet: str  # "personal/<employee_id>"
    source_file: str
    triple_id: int
    confidence: float


class FirmCandidate(BaseModel):
    """A pattern worth proposing to the firm plane.

    Structured so reviewers and eval fixtures can reason about it
    without parsing prose. ``confidence`` is the KG's current belief
    in the fact — it already reflects Noisy-OR aggregation from the
    personal-plane corroboration path, so the detector does not
    re-aggregate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    object: str
    tier: Tier = DEFAULT_TIER
    distinct_employees: int = Field(ge=1)
    distinct_source_files: int = Field(ge=1)
    contributing_sources: list[CandidateSource]
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def employee_ids(self) -> list[str]:
        """Distinct employee IDs that contributed to this candidate, sorted."""
        ids = {s.source_closet.removeprefix("personal/") for s in self.contributing_sources}
        return sorted(ids)

    def to_relationship_fact(self) -> RelationshipFact:
        """Build a ``RelationshipFact`` representing this candidate.

        ``support_quote`` is a structured, reviewer-readable summary.
        """
        summary = (
            f"federated detector: {self.distinct_employees} employees "
            f"({', '.join(self.employee_ids)}) asserted "
            f"'{self.subject} {self.predicate} {self.object}' across "
            f"{self.distinct_source_files} distinct sources"
        )
        return RelationshipFact(
            confidence=self.confidence,
            support_quote=summary,
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
        )


# ---------- Detection ----------


def detect_firm_candidates(
    kg: KnowledgeGraph,
    *,
    min_employees: int = DEFAULT_MIN_EMPLOYEES,
    min_sources: int = DEFAULT_MIN_SOURCES,
) -> list[FirmCandidate]:
    """Return candidates that meet BOTH the employee and source thresholds.

    Deterministic, SQL-backed. Output order is stable:
    ``(-distinct_employees, -aggregate_confidence, subject, predicate,
    object)`` so callers get the strongest signals first without
    doing their own sort.

    Independence enforcement: the source threshold counts DISTINCT
    ``source_file`` strings. Three employees sharing one transcript
    register as 1 source_file — insufficient.
    """
    if min_employees < 1 or min_sources < 1:
        raise ValueError("thresholds must be >= 1")

    rows = kg.scan_triple_sources(closet_prefix="personal/")
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["subject"], row["predicate"], row["object"])
        groups[key].append(row)

    candidates: list[FirmCandidate] = []
    for (subject, predicate, obj), group_rows in groups.items():
        distinct_employees = {r["source_closet"] for r in group_rows}
        distinct_sources = {r["source_file"] for r in group_rows if r["source_file"]}

        if len(distinct_employees) < min_employees:
            continue
        if len(distinct_sources) < min_sources:
            continue

        # Dedup contributing_sources on (source_closet, source_file) so
        # reviewers see one row per employee-per-document even if
        # corroboration re-appended within that pair.
        seen: set[tuple[str, str | None]] = set()
        contributing: list[CandidateSource] = []
        for r in group_rows:
            dedup_key = (r["source_closet"], r["source_file"])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            contributing.append(
                CandidateSource(
                    source_closet=r["source_closet"],
                    source_file=r["source_file"] or "",
                    triple_id=int(r["triple_id"]),
                    confidence=float(r["confidence"]),
                )
            )

        # Canonical tier for the candidate = highest tier seen across
        # contributing personal-plane triples. Most will be decision;
        # if an employee already hand-tiered the personal copy,
        # respect that.
        tiers = [r["tier"] or DEFAULT_TIER for r in group_rows]
        tier = _highest_tier(tiers)

        # The triple's current confidence already reflects Noisy-OR
        # aggregation from personal-plane corroborations. All rows in
        # the group share the same triple → same confidence value.
        triple_confidence = float(group_rows[0]["confidence"])

        candidates.append(
            FirmCandidate(
                subject=subject,
                predicate=predicate,
                object=obj,
                tier=tier,
                distinct_employees=len(distinct_employees),
                distinct_source_files=len(distinct_sources),
                contributing_sources=contributing,
                confidence=triple_confidence,
            )
        )

    candidates.sort(
        key=lambda c: (
            -c.distinct_employees,
            -c.confidence,
            c.subject,
            c.predicate,
            c.object,
        )
    )
    return candidates


def aggregate_noisy_or(confidences: list[float]) -> float:
    """Combine independent evidence via Noisy-OR, capped at 0.99.

    Matches the ``CORROBORATION_CAP`` semantics from
    ``KnowledgeGraph.corroborate``: N independent sources compound,
    but never reach certainty without explicit human override.
    """
    product = 1.0
    for c in confidences:
        if not 0.0 <= c <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {c}")
        product *= 1.0 - c
    return min(_AGGREGATION_CAP, 1.0 - product)


# ---------- Proposal generation ----------


def propose_firm_candidate(
    candidate: FirmCandidate,
    *,
    store: ProposalStore,
    proposer_agent_id: str = "federated-detector-v1",
    proposer_employee_id: str = "admin",
    target_scope: str = "public",
) -> Proposal:
    """Stage a federated-detector candidate as a firm-plane proposal.

    Uses ``create_proposal`` so:
    - the proposal_id is deterministic (re-running the detector
      returns the same pending proposal instead of creating a dupe)
    - a ``ProposalCreatedEvent`` lands in the audit log
    - the reviewer skill's usual listing and ranking logic picks it
      up like any other proposal

    Coherence checks (Step 15) run when the proposal is promoted.
    Federated proposals can and should be blocked by
    ``constitutional_mode`` the same way extraction-sourced ones are.
    """
    fact = candidate.to_relationship_fact()
    source_report_path = _federated_report_path(candidate)
    return create_proposal(
        store,
        target_plane="firm",
        target_entity=candidate.subject,
        facts=[fact],
        source_report_path=source_report_path,
        proposer_agent_id=proposer_agent_id,
        proposer_employee_id=proposer_employee_id,
        target_scope=target_scope,
    )


# ---------- Internals ----------


def _federated_report_path(candidate: FirmCandidate) -> str:
    """Synthetic URI that flags this proposal's origin.

    Not a real file path — reviewers reading the audit log see
    ``federated-detector://`` and know to look at
    ``contributing_sources`` on the candidate (or at the support_quote
    on the fact) for per-employee provenance.
    """
    return f"federated-detector://{candidate.subject}/{candidate.predicate}/{candidate.object}"


def _highest_tier(tiers: list[str]) -> Tier:
    """Return the highest tier in the list, defaulting to decision."""
    from memory_mission.memory.tiers import tier_level

    ordered = sorted(tiers, key=lambda t: -tier_level(t))  # type: ignore[arg-type]
    if not ordered:
        return DEFAULT_TIER
    return ordered[0]  # type: ignore[return-value]
