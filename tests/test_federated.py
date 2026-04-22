"""Step 16 tests — cross-employee pattern detector.

Covers the behaviors from ``docs/EVALS.md`` section 2.6:

- **True firm pattern** — ≥3 employees, ≥3 distinct source_files:
  fires.
- **Shared-artefact pattern** (dominant failure mode) — 3 employees
  but a single source_file: does NOT fire (independence check).
- **Below threshold** — 2 employees or 2 sources: does NOT fire.
- **Multiple groups in one scan** — each independently evaluated;
  strongest signal ranks first.
- **Proposal integration** — a candidate produces a federated-origin
  Proposal that flows through the normal promotion pipeline and
  participates in coherence checks (Step 15).

Test fixtures here seed the labeled corpus the eval doc asks for.
Each test's setup IS the labeled scenario; the assertion IS the
binary expected outcome. Add more scenarios here (same shape, new
test function) to grow the corpus toward the 50-scenario target.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.federated import (
    CandidateSource,
    FirmCandidate,
    aggregate_noisy_or,
    detect_firm_candidates,
    propose_firm_candidate,
)
from memory_mission.memory import KnowledgeGraph
from memory_mission.observability import (
    CoherenceWarningEvent,
    ObservabilityLogger,
    observability_scope,
)
from memory_mission.permissions.policy import Policy
from memory_mission.promotion import (
    CoherenceBlockedError,
    ProposalStore,
    promote,
)

# ---------- Fixtures ----------


@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph(tmp_path / "kg.sqlite3")


@pytest.fixture
def store(tmp_path: Path) -> ProposalStore:
    return ProposalStore(tmp_path / "proposals.sqlite3")


def _seed_personal_triple(
    kg: KnowledgeGraph,
    employee: str,
    subject: str,
    predicate: str,
    obj: str,
    *,
    source_file: str,
    confidence: float = 0.8,
) -> None:
    """Simulate the realistic personal-plane promote() flow.

    Each employee's own promotion pipeline runs
    ``_add_or_corroborate``: first observation creates a triple;
    subsequent observations of the same (subject, predicate, object)
    corroborate it and append a ``triple_sources`` row. Mirror that
    so the detector's scan sees production-shaped data.
    """
    closet = f"personal/{employee}"
    existing = kg.find_current_triple(subject, predicate, obj)
    if existing is not None:
        kg.corroborate(
            subject,
            predicate,
            obj,
            confidence=confidence,
            source_closet=closet,
            source_file=source_file,
        )
        return
    kg.add_triple(
        subject,
        predicate,
        obj,
        confidence=confidence,
        source_closet=closet,
        source_file=source_file,
    )


# ---------- Aggregation math ----------


def test_aggregate_noisy_or_single_value() -> None:
    assert aggregate_noisy_or([0.6]) == pytest.approx(0.6)


def test_aggregate_noisy_or_combines_independent_evidence() -> None:
    # Three employees each at 0.8 → 1 - 0.2^3 = 0.992 → capped 0.99
    result = aggregate_noisy_or([0.8, 0.8, 0.8])
    assert result == 0.99  # cap


def test_aggregate_noisy_or_two_weaker_signals() -> None:
    # Two at 0.5 → 1 - 0.5 * 0.5 = 0.75
    assert aggregate_noisy_or([0.5, 0.5]) == pytest.approx(0.75)


def test_aggregate_noisy_or_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="confidence"):
        aggregate_noisy_or([0.5, 1.5])


# ---------- Detection happy path ----------


def test_detect_fires_on_three_employees_three_sources(kg: KnowledgeGraph) -> None:
    """True firm pattern: three employees, three distinct source files."""
    for emp, src in [
        ("alice", "/tmp/alice-note.md"),
        ("bob", "/tmp/bob-email.json"),
        ("carol", "/tmp/carol-call.txt"),
    ]:
        _seed_personal_triple(kg, emp, "sarah-chen", "works_at", "acme-corp", source_file=src)

    candidates = detect_firm_candidates(kg)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, FirmCandidate)
    assert candidate.subject == "sarah-chen"
    assert candidate.predicate == "works_at"
    assert candidate.object == "acme-corp"
    assert candidate.distinct_employees == 3
    assert candidate.distinct_source_files == 3
    assert candidate.employee_ids == ["alice", "bob", "carol"]
    assert len(candidate.contributing_sources) == 3


def test_detect_does_not_fire_on_shared_single_source(kg: KnowledgeGraph) -> None:
    """Dominant failure mode: three employees share one Granola transcript."""
    shared = "/tmp/granola-team-call.md"
    for emp in ("alice", "bob", "carol"):
        _seed_personal_triple(kg, emp, "sarah-chen", "works_at", "acme-corp", source_file=shared)
    assert detect_firm_candidates(kg) == []


def test_detect_does_not_fire_below_employee_threshold(kg: KnowledgeGraph) -> None:
    for emp, src in [
        ("alice", "/tmp/a.md"),
        ("bob", "/tmp/b.md"),
    ]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)
    assert detect_firm_candidates(kg) == []


def test_detect_does_not_fire_below_source_threshold(kg: KnowledgeGraph) -> None:
    """Three employees but only two distinct source_file values."""
    _seed_personal_triple(kg, "alice", "sarah", "works_at", "acme", source_file="/a.md")
    _seed_personal_triple(kg, "bob", "sarah", "works_at", "acme", source_file="/a.md")
    _seed_personal_triple(kg, "carol", "sarah", "works_at", "acme", source_file="/b.md")
    assert detect_firm_candidates(kg) == []


def test_detect_respects_custom_thresholds(kg: KnowledgeGraph) -> None:
    """Lower thresholds make the detector more permissive."""
    for emp, src in [
        ("alice", "/a.md"),
        ("bob", "/b.md"),
    ]:
        _seed_personal_triple(kg, emp, "s", "p", "o", source_file=src)

    # Default: no fire (needs 3)
    assert detect_firm_candidates(kg) == []

    # Lowered thresholds: fires
    got = detect_firm_candidates(kg, min_employees=2, min_sources=2)
    assert len(got) == 1
    assert got[0].distinct_employees == 2


def test_detect_rejects_nonsense_thresholds(kg: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        detect_firm_candidates(kg, min_employees=0)
    with pytest.raises(ValueError, match="thresholds"):
        detect_firm_candidates(kg, min_sources=0)


# ---------- Detection: multiple groups + ranking ----------


def test_detect_scans_each_group_independently(kg: KnowledgeGraph) -> None:
    """Two distinct patterns in one scan produce two candidates."""
    # Pattern A: 3 employees assert (sarah, works_at, acme)
    for emp, src in [("alice", "/a1.md"), ("bob", "/b1.md"), ("carol", "/c1.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)
    # Pattern B: 4 employees assert (mark, role, cto) — ranks higher
    for emp, src in [
        ("alice", "/a2.md"),
        ("bob", "/b2.md"),
        ("carol", "/c2.md"),
        ("dave", "/d2.md"),
    ]:
        _seed_personal_triple(kg, emp, "mark", "role", "cto", source_file=src)

    candidates = detect_firm_candidates(kg)
    assert len(candidates) == 2
    # Higher employee count ranks first
    assert candidates[0].subject == "mark"
    assert candidates[0].distinct_employees == 4
    assert candidates[1].subject == "sarah"


def test_detect_does_not_cluster_distinct_objects(kg: KnowledgeGraph) -> None:
    """Same (subject, predicate) with different objects are distinct groups."""
    # Three employees each on "sarah works_at acme"
    for emp, src in [("alice", "/a1.md"), ("bob", "/b1.md"), ("carol", "/c1.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)
    # Just one employee on "sarah works_at beta" — does not fire
    _seed_personal_triple(kg, "dave", "sarah", "works_at", "beta", source_file="/d.md")

    candidates = detect_firm_candidates(kg)
    assert len(candidates) == 1
    assert candidates[0].object == "acme"


def test_detect_ignores_firm_plane_triples(kg: KnowledgeGraph) -> None:
    """Firm-plane triples do NOT contribute to cross-employee aggregation."""
    # A firm-plane triple exists already
    kg.add_triple(
        "sarah",
        "works_at",
        "acme",
        source_closet="firm",
        source_file="/firm/manual.md",
    )
    # Two personal-plane corroborations — below threshold
    _seed_personal_triple(kg, "alice", "sarah", "works_at", "acme", source_file="/a.md")
    _seed_personal_triple(kg, "bob", "sarah", "works_at", "acme", source_file="/b.md")

    # No fire: firm closet is excluded, only 2 personal employees
    assert detect_firm_candidates(kg) == []


def test_detect_ignores_invalidated_triples(kg: KnowledgeGraph) -> None:
    """Triples with valid_to set are excluded from the scan."""
    from datetime import date

    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)

    # Before invalidation: fires
    assert len(detect_firm_candidates(kg)) == 1

    # Invalidate alice's triple — drops below threshold
    kg.invalidate("sarah", "works_at", "acme", ended=date(2026, 3, 15))
    # invalidate() ends ALL currently-true matching triples, so count drops to 0
    assert detect_firm_candidates(kg) == []


# ---------- Confidence aggregation ----------


def test_candidate_confidence_reflects_corroborated_triple(
    kg: KnowledgeGraph,
) -> None:
    """Candidate confidence inherits the corroborated triple value.

    Three personal-plane promotions at 0.8 / 0.7 / 0.9 compose via
    Noisy-OR through ``corroborate()``: 1 - (0.2 * 0.3 * 0.1) = 0.994,
    capped at 0.99. The detector surfaces that post-corroboration
    confidence directly — re-aggregating would double-count the
    evidence.
    """
    for emp, src, conf in [
        ("alice", "/a.md", 0.8),
        ("bob", "/b.md", 0.7),
        ("carol", "/c.md", 0.9),
    ]:
        _seed_personal_triple(
            kg, emp, "sarah", "works_at", "acme", source_file=src, confidence=conf
        )

    [candidate] = detect_firm_candidates(kg)
    assert candidate.confidence == 0.99


# ---------- Proposal generation ----------


def test_propose_firm_candidate_creates_pending_proposal(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)
    [candidate] = detect_firm_candidates(kg)

    with observability_scope(observability_root=tmp_path, firm_id="acme-firm"):
        proposal = propose_firm_candidate(candidate, store=store)

    assert proposal.target_plane == "firm"
    assert proposal.target_entity == "sarah"
    assert proposal.proposer_agent_id == "federated-detector-v1"
    assert proposal.source_report_path.startswith("federated-detector://")
    assert len(proposal.facts) == 1
    fact = proposal.facts[0]
    assert fact.kind == "relationship"
    # support_quote lists contributing employees for the reviewer
    assert "alice" in fact.support_quote
    assert "bob" in fact.support_quote
    assert "carol" in fact.support_quote


def test_propose_firm_candidate_is_idempotent(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Re-running the detector + proposer returns the same pending proposal."""
    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)

    with observability_scope(observability_root=tmp_path, firm_id="acme-firm"):
        [candidate] = detect_firm_candidates(kg)
        p1 = propose_firm_candidate(candidate, store=store)
        [candidate2] = detect_firm_candidates(kg)
        p2 = propose_firm_candidate(candidate2, store=store)

    assert p1.proposal_id == p2.proposal_id


def test_propose_then_promote_corroborates_with_firm_source(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """End-to-end: detect → propose → promote corroborates, appending firm to provenance.

    Because the personal-plane triples are currently true, the firm-
    level promotion runs through ``_add_or_corroborate`` and appends
    ``source_closet='firm'`` to ``triple_sources`` rather than adding
    a duplicate triples row. Net effect: one triple, four provenance
    rows (alice / bob / carol / firm), confidence strengthened.
    """
    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src, confidence=0.8)

    with observability_scope(observability_root=tmp_path, firm_id="acme-firm"):
        [candidate] = detect_firm_candidates(kg)
        proposal = propose_firm_candidate(candidate, store=store)
        promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="partner-1",
            rationale="three employees independently saw this",
        )

    # One triple, stronger confidence, firm source appears in provenance history.
    triples = kg.query_relationship("works_at")
    assert len(triples) == 1
    assert triples[0].confidence == 0.99  # capped Noisy-OR across 4 sources
    sources = kg.triple_sources("sarah", "works_at", "acme")
    closets = {s.source_closet for s in sources}
    assert "firm" in closets
    assert {"personal/alice", "personal/bob", "personal/carol"} <= closets


def test_federated_proposal_respects_coherence_check(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Federated proposals pass through Step 15 coherence like any other."""
    # Existing firm doctrine: sarah works_at beta
    kg.add_triple(
        "sarah",
        "works_at",
        "beta",
        source_closet="firm",
        source_file="/firm/manual.md",
        tier="doctrine",
    )
    # Three employees now claim sarah works_at acme (personal plane)
    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "sarah", "works_at", "acme", source_file=src)

    policy = Policy(firm_id="acme-firm", constitutional_mode=True)
    with observability_scope(observability_root=tmp_path, firm_id="acme-firm"):
        [candidate] = detect_firm_candidates(kg)
        proposal = propose_firm_candidate(candidate, store=store)
        with pytest.raises(CoherenceBlockedError):
            promote(
                store,
                kg,
                proposal.proposal_id,
                reviewer_id="partner-1",
                rationale="try to land",
                policy=policy,
            )

    # Proposal stayed pending; firm triple did not change.
    pending = store.get(proposal.proposal_id)
    assert pending is not None
    assert pending.status == "pending"
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme-firm")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert len(coherence_events) == 1
    assert coherence_events[0].blocked is True


def test_contributing_sources_are_sorted_by_employee(kg: KnowledgeGraph) -> None:
    """employee_ids property returns stable sorted output for reviewer display."""
    for emp, src in [
        ("zoe", "/z.md"),
        ("alice", "/a.md"),
        ("mark", "/m.md"),
    ]:
        _seed_personal_triple(kg, emp, "s", "p", "o", source_file=src)
    [candidate] = detect_firm_candidates(kg)
    assert candidate.employee_ids == ["alice", "mark", "zoe"]


def test_candidate_source_carries_triple_reference(kg: KnowledgeGraph) -> None:
    """Each CandidateSource points back to a specific triples row."""
    for emp, src in [("alice", "/a.md"), ("bob", "/b.md"), ("carol", "/c.md")]:
        _seed_personal_triple(kg, emp, "s", "p", "o", source_file=src)
    [candidate] = detect_firm_candidates(kg)
    for source in candidate.contributing_sources:
        assert isinstance(source, CandidateSource)
        assert source.triple_id > 0
        assert source.source_closet.startswith("personal/")
