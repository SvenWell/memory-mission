"""Tests for the promotion pipeline (step 10a)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from memory_mission.extraction import (
    EventFact,
    IdentityFact,
    OpenQuestion,
    PreferenceFact,
    RelationshipFact,
    UpdateFact,
)
from memory_mission.memory import KnowledgeGraph
from memory_mission.observability import (
    ObservabilityLogger,
    ProposalCreatedEvent,
    ProposalDecidedEvent,
    observability_scope,
)
from memory_mission.promotion import (
    Proposal,
    ProposalIntegrityError,
    ProposalStateError,
    ProposalStore,
    create_proposal,
    generate_proposal_id,
    promote,
    reject,
    reopen,
)

# ---------- Helpers ----------


def _identity(name: str, etype: str = "person") -> IdentityFact:
    return IdentityFact(
        confidence=0.95,
        support_quote=f"mention of {name}",
        entity_name=name,
        entity_type=etype,
    )


def _rel(s: str, p: str, o: str) -> RelationshipFact:
    return RelationshipFact(
        confidence=0.9,
        support_quote=f"{s} {p} {o}",
        subject=s,
        predicate=p,
        object=o,
    )


def _sample_facts() -> list[IdentityFact | RelationshipFact]:
    return [
        _identity("sarah-chen", "person"),
        _identity("acme-corp", "company"),
        _rel("sarah-chen", "works_at", "acme-corp"),
    ]


def _create_sample(
    store: ProposalStore,
    *,
    target_plane: str = "firm",
    target_entity: str = "sarah-chen",
    target_employee_id: str | None = None,
    source_report_path: str = "/tmp/report-1.json",
    proposer_employee_id: str = "alice",
    target_scope: str = "public",
) -> Proposal:
    return create_proposal(
        store,
        target_plane=target_plane,
        target_entity=target_entity,
        target_employee_id=target_employee_id,
        target_scope=target_scope,
        facts=_sample_facts(),
        source_report_path=source_report_path,
        proposer_agent_id="extract-from-staging-v1",
        proposer_employee_id=proposer_employee_id,
    )


# ---------- Fixtures ----------


@pytest.fixture
def store(tmp_path: Path) -> ProposalStore:
    return ProposalStore(tmp_path / "proposals.sqlite3")


@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph(tmp_path / "kg.sqlite3")


# ---------- generate_proposal_id ----------


def test_proposal_id_deterministic_for_same_inputs() -> None:
    a = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=_sample_facts(),
    )
    b = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=_sample_facts(),
    )
    assert a == b


def test_proposal_id_differs_when_plane_differs() -> None:
    a = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=_sample_facts(),
    )
    b = generate_proposal_id(
        target_plane="personal",
        target_employee_id="alice",
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=_sample_facts(),
    )
    assert a != b


def test_proposal_id_differs_when_facts_differ() -> None:
    a = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=_sample_facts(),
    )
    b = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=[_identity("sarah-chen", "person")],  # fewer facts
    )
    assert a != b


# ---------- Store round-trip ----------


def test_store_insert_then_get(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
    fetched = store.get(proposal.proposal_id)
    assert fetched is not None
    assert fetched.proposal_id == proposal.proposal_id
    assert fetched.status == "pending"
    assert [f.kind for f in fetched.facts] == [
        "identity",
        "identity",
        "relationship",
    ]


def test_store_list_filters_by_status(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = _create_sample(store, source_report_path="/tmp/1.json")
        p2 = _create_sample(store, source_report_path="/tmp/2.json")
        _create_sample(store, source_report_path="/tmp/3.json")
        promote(store, kg, p1.proposal_id, reviewer_id="alice", rationale="ok")
        reject(store, p2.proposal_id, reviewer_id="alice", rationale="no")

    assert len(store.list(status="pending")) == 1
    assert len(store.list(status="approved")) == 1
    assert len(store.list(status="rejected")) == 1


def test_store_list_filters_by_target_entity(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        _create_sample(store, target_entity="sarah-chen", source_report_path="/tmp/1.json")
        _create_sample(store, target_entity="acme-corp", source_report_path="/tmp/2.json")

    sarah = store.list(target_entity="sarah-chen")
    assert len(sarah) == 1
    assert sarah[0].target_entity == "sarah-chen"


def test_store_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "props.sqlite3"
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        with ProposalStore(db) as s1:
            proposal = _create_sample(s1)
    with ProposalStore(db) as s2:
        fetched = s2.get(proposal.proposal_id)
        assert fetched is not None


def test_store_stats_counts_by_status(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = _create_sample(store, source_report_path="/tmp/1.json")
        p2 = _create_sample(store, source_report_path="/tmp/2.json")
        promote(store, kg, p1.proposal_id, reviewer_id="alice", rationale="ok")
        reject(store, p2.proposal_id, reviewer_id="alice", rationale="no")
    assert store.stats() == {"pending": 0, "approved": 1, "rejected": 1}


def test_store_validates_plane_on_insert(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "p.sqlite3")
    with pytest.raises(ValueError, match="personal target_plane requires"):
        store.insert(
            Proposal(
                proposal_id="abc",
                target_plane="personal",
                target_employee_id=None,
                target_entity="sarah-chen",
                proposer_agent_id="x",
                proposer_employee_id="alice",
                facts=_sample_facts(),
                source_report_path="/tmp/r.json",
            )
        )


# ---------- create_proposal ----------


def test_create_proposal_emits_created_event(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, ProposalCreatedEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.proposal_id == proposal.proposal_id
    assert event.target_entity == "sarah-chen"
    assert event.fact_count == 3
    assert event.proposer_employee_id == "alice"


def test_create_proposal_is_idempotent_by_inputs(store: ProposalStore, tmp_path: Path) -> None:
    """Second call with same inputs returns the existing proposal."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        first = _create_sample(store)
        second = _create_sample(store)
    assert first.proposal_id == second.proposal_id
    # Only one pending proposal in the store.
    assert len(store.list(status="pending")) == 1


def test_create_proposal_requires_at_least_one_fact(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        with pytest.raises(ValueError, match="at least one fact"):
            create_proposal(
                store,
                target_plane="firm",
                target_entity="sarah-chen",
                facts=[],
                source_report_path="/tmp/r.json",
                proposer_agent_id="x",
                proposer_employee_id="alice",
            )


def test_create_proposal_personal_requires_employee_id(
    store: ProposalStore, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        with pytest.raises(ValueError, match="personal target_plane"):
            create_proposal(
                store,
                target_plane="personal",
                target_entity="sarah-chen",
                facts=_sample_facts(),
                source_report_path="/tmp/r.json",
                proposer_agent_id="x",
                proposer_employee_id="alice",
            )


def test_create_proposal_without_observability_scope_does_not_insert(
    store: ProposalStore,
) -> None:
    with pytest.raises(RuntimeError, match="observability_scope"):
        create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=_sample_facts(),
            source_report_path="/tmp/r.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )

    assert store.list() == []


# ---------- promote ----------


def test_promote_applies_facts_to_kg(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="Evidence holds",
        )

    # Entities landed
    assert kg.get_entity("sarah-chen") is not None
    assert kg.get_entity("acme-corp") is not None
    # Relationship landed as a triple
    triples = kg.query_entity("sarah-chen")
    assert any(
        t.subject == "sarah-chen" and t.predicate == "works_at" and t.object == "acme-corp"
        for t in triples
    )


def test_promote_records_approved_status_and_history(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        approved = promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="Evidence holds",
        )
    assert approved.status == "approved"
    assert approved.reviewer_id == "alice"
    assert approved.rationale == "Evidence holds"
    assert approved.decided_at is not None
    assert len(approved.decision_history) == 1
    assert approved.decision_history[0].decision == "approved"
    assert approved.decision_history[0].rationale == "Evidence holds"


def test_promote_emits_decided_event(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="Evidence holds",
        )

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, ProposalDecidedEvent)]
    assert len(events) == 1
    assert events[0].decision == "approved"
    assert events[0].reviewer_id == "alice"
    assert events[0].rationale == "Evidence holds"


def test_promote_requires_nonempty_rationale(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Structural block on rubber-stamping."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        for empty in ("", "   ", "\n\t"):
            with pytest.raises(ValueError, match="rationale is required"):
                promote(
                    store,
                    kg,
                    proposal.proposal_id,
                    reviewer_id="alice",
                    rationale=empty,
                )


def test_promote_raises_on_nonexistent_proposal(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        with pytest.raises(ProposalStateError, match="not found"):
            promote(
                store,
                kg,
                "missing-id",
                reviewer_id="alice",
                rationale="whatever",
            )


def test_promote_raises_on_already_approved(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")
        with pytest.raises(ProposalStateError, match="approved.*expected pending"):
            promote(store, kg, proposal.proposal_id, reviewer_id="b", rationale="ok")


def test_promote_without_observability_scope_does_not_mutate(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)

    with pytest.raises(RuntimeError, match="observability_scope"):
        promote(store, kg, proposal.proposal_id, reviewer_id="alice", rationale="ok")

    pending = store.get(proposal.proposal_id)
    assert pending is not None
    assert pending.status == "pending"
    assert kg.get_entity("sarah-chen") is None
    assert kg.get_entity("acme-corp") is None
    assert kg.query_relationship("works_at") == []


def test_promote_provenance_carries_source_closet(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Firm-plane promotion uses closet='firm'; personal uses 'personal/<emp>'.

    Under Bayesian corroboration (Step 13), the second promotion of the
    same triple does not insert a duplicate row — it bumps confidence on
    the existing triple and appends its source to ``triple_sources``. So
    both closets are present in the provenance history even though only
    one triples row exists.
    """
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        firm_proposal = _create_sample(store, target_plane="firm", source_report_path="/tmp/1.json")
        personal_proposal = _create_sample(
            store,
            target_plane="personal",
            target_employee_id="alice",
            source_report_path="/tmp/2.json",
        )
        promote(store, kg, firm_proposal.proposal_id, reviewer_id="a", rationale="ok")
        promote(
            store,
            kg,
            personal_proposal.proposal_id,
            reviewer_id="a",
            rationale="ok",
        )

    # Second promotion corroborates, so exactly one triple row exists.
    triples = kg.query_relationship("works_at")
    assert len(triples) == 1

    # Both source closets live in the full provenance history.
    sources = kg.triple_sources("sarah-chen", "works_at", "acme-corp")
    closets = {s.source_closet for s in sources}
    assert closets == {"firm", "personal/alice"}


def test_promote_applies_update_fact_correctly(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """UpdateFact.invalidate + add_triple produces the time-travel shape."""
    # Seed the prior fact
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        seed = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                _identity("sarah-chen", "person"),
                _rel("sarah-chen", "works_at", "old-co"),
            ],
            source_report_path="/tmp/seed.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )
        promote(store, kg, seed.proposal_id, reviewer_id="a", rationale="ok")

        # Now propose and promote an UpdateFact superseding the prior one
        update_proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                UpdateFact(
                    confidence=0.95,
                    support_quote="moved to new-co",
                    subject="sarah-chen",
                    predicate="works_at",
                    new_object="new-co",
                    supersedes_object="old-co",
                    effective_date=date(2026, 3, 15),
                ),
            ],
            source_report_path="/tmp/update.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )
        promote(store, kg, update_proposal.proposal_id, reviewer_id="a", rationale="ok")

    # Time-travel: Feb 2026 should still be old-co; April should be new-co
    feb = kg.query_entity("sarah-chen", as_of=date(2026, 2, 1))
    apr = kg.query_entity("sarah-chen", as_of=date(2026, 4, 1))
    assert any(t.object == "old-co" for t in feb)
    assert any(t.object == "new-co" for t in apr)


def test_promote_skips_open_question_facts(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Open questions never promote to the KG even when part of an approved proposal."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                _identity("sarah-chen", "person"),
                OpenQuestion(
                    confidence=0.3,
                    support_quote="unclear if she's CFO",
                    question="Is she CFO?",
                    hypothesis=None,
                ),
            ],
            source_report_path="/tmp/r.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")

    # Entity landed; no "question" predicate triple exists
    assert kg.get_entity("sarah-chen") is not None
    assert kg.query_relationship("question") == []


def test_promote_preference_writes_prefers_triple(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                _identity("sarah-chen", "person"),
                PreferenceFact(
                    confidence=0.9,
                    support_quote="prefers direct communication",
                    subject="sarah-chen",
                    preference="direct, numbers-heavy communication",
                ),
            ],
            source_report_path="/tmp/r.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")

    triples = kg.query_entity("sarah-chen")
    prefers = [t for t in triples if t.predicate == "prefers"]
    assert len(prefers) == 1
    assert prefers[0].object == "direct, numbers-heavy communication"


def test_promote_event_writes_dated_triple(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="acme-corp",
            facts=[
                _identity("acme-corp", "company"),
                EventFact(
                    confidence=0.95,
                    support_quote="closed Series B",
                    entity_name="acme-corp",
                    event_date=date(2026, 3, 15),
                    description="Closed Series B at $80M post-money",
                ),
            ],
            source_report_path="/tmp/r.json",
            proposer_agent_id="x",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")

    events = [t for t in kg.query_entity("acme-corp") if t.predicate == "event"]
    assert len(events) == 1
    assert events[0].valid_from == date(2026, 3, 15)
    assert events[0].object == "Closed Series B at $80M post-money"


# ---------- reject ----------


def test_reject_marks_rejected_and_bumps_count(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        rejected = reject(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="source not trustworthy",
        )
    assert rejected.status == "rejected"
    assert rejected.rejection_count == 1
    assert rejected.rationale == "source not trustworthy"
    assert rejected.reviewer_id == "alice"


def test_reject_emits_decided_event(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(store, proposal.proposal_id, reviewer_id="alice", rationale="stale")
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [e for e in logger.read_all() if isinstance(e, ProposalDecidedEvent)]
    assert len(events) == 1
    assert events[0].decision == "rejected"
    assert events[0].rejection_count == 1


def test_reject_requires_rationale(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        with pytest.raises(ValueError, match="rationale is required"):
            reject(store, proposal.proposal_id, reviewer_id="alice", rationale="")


def test_reject_raises_on_non_pending(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")
        with pytest.raises(ProposalStateError, match="approved.*expected pending"):
            reject(store, proposal.proposal_id, reviewer_id="b", rationale="oops")


def test_reject_without_observability_scope_does_not_mutate(
    store: ProposalStore, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)

    with pytest.raises(RuntimeError, match="observability_scope"):
        reject(store, proposal.proposal_id, reviewer_id="alice", rationale="no")

    pending = store.get(proposal.proposal_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.rejection_count == 0
    assert pending.decision_history == []


# ---------- reopen ----------


def test_reopen_flips_rejected_back_to_pending(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="source stale",
        )
        reopened = reopen(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="new evidence from Q3 memo",
        )
    assert reopened.status == "pending"
    assert reopened.decided_at is None
    assert reopened.reviewer_id is None
    assert reopened.rationale is None
    # Rejection count preserved, and full history retained
    assert reopened.rejection_count == 1
    assert [d.decision for d in reopened.decision_history] == [
        "rejected",
        "reopened",
    ]


def test_reopen_requires_rationale(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(store, proposal.proposal_id, reviewer_id="a", rationale="no")
        with pytest.raises(ValueError, match="rationale is required"):
            reopen(store, proposal.proposal_id, reviewer_id="a", rationale="")


def test_reopen_raises_on_pending_or_approved(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        pending = _create_sample(store, source_report_path="/tmp/pending.json")
        with pytest.raises(ProposalStateError, match="only rejected"):
            reopen(store, pending.proposal_id, reviewer_id="a", rationale="why")
        approved = _create_sample(store, source_report_path="/tmp/approved.json")
        promote(store, kg, approved.proposal_id, reviewer_id="a", rationale="ok")
        with pytest.raises(ProposalStateError, match="only rejected"):
            reopen(store, approved.proposal_id, reviewer_id="a", rationale="why")


def test_reopen_emits_decided_event(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(store, proposal.proposal_id, reviewer_id="a", rationale="no")
        reopen(store, proposal.proposal_id, reviewer_id="a", rationale="revisit")
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    reopened_events = [
        e
        for e in logger.read_all()
        if isinstance(e, ProposalDecidedEvent) and e.decision == "reopened"
    ]
    assert len(reopened_events) == 1


def test_reopen_without_observability_scope_does_not_mutate(
    store: ProposalStore, tmp_path: Path
) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(store, proposal.proposal_id, reviewer_id="alice", rationale="stale")

    with pytest.raises(RuntimeError, match="observability_scope"):
        reopen(store, proposal.proposal_id, reviewer_id="alice", rationale="retry")

    rejected = store.get(proposal.proposal_id)
    assert rejected is not None
    assert rejected.status == "rejected"
    assert rejected.rejection_count == 1
    assert [entry.decision for entry in rejected.decision_history] == ["rejected"]


# ---------- End-to-end audit trail ----------


def test_full_lifecycle_records_trace_across_events(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Created → rejected → reopened → approved produces 4 observable events."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        reject(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="stale source",
        )
        reopen(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="source re-verified",
        )
        promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="now confirmed",
        )

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    all_events = list(logger.read_all())
    created = [e for e in all_events if isinstance(e, ProposalCreatedEvent)]
    decided = [e for e in all_events if isinstance(e, ProposalDecidedEvent)]
    assert len(created) == 1
    assert [d.decision for d in decided] == ["rejected", "reopened", "approved"]

    # Final stored proposal: approved, rejection_count=1, history=[rejected, reopened, approved]
    final = store.get(proposal.proposal_id)
    assert final is not None
    assert final.status == "approved"
    assert final.rejection_count == 1
    assert [d.decision for d in final.decision_history] == [
        "rejected",
        "reopened",
        "approved",
    ]


# ---------- Step 13: Bayesian corroboration in _apply_facts ----------


def _second_sample_facts() -> list[IdentityFact | RelationshipFact]:
    """Same relationship as ``_sample_facts`` — drives corroboration paths."""
    return [
        _identity("sarah-chen", "person"),
        _identity("acme-corp", "company"),
        _rel("sarah-chen", "works_at", "acme-corp"),
    ]


def test_promote_same_relationship_twice_corroborates(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Re-promoting the same relationship bumps confidence instead of duplicating."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = _create_sample(store, source_report_path="/tmp/r1.json")
        p2 = _create_sample(
            store,
            source_report_path="/tmp/r2.json",
            proposer_employee_id="bob",
        )
        promote(store, kg, p1.proposal_id, reviewer_id="reviewer", rationale="first")
        promote(store, kg, p2.proposal_id, reviewer_id="reviewer", rationale="second")

    triples = kg.query_relationship("works_at")
    assert len(triples) == 1
    triple = triples[0]
    # Initial 0.9 confidence from ``_rel``; second promote bumps via Noisy-OR
    assert triple.confidence == pytest.approx(1.0 - (0.1 * 0.1))  # 0.99
    assert triple.corroboration_count == 1
    # Both source reports live in the provenance chain
    sources = kg.triple_sources("sarah-chen", "works_at", "acme-corp")
    files = {s.source_file for s in sources}
    assert files == {"/tmp/r1.json", "/tmp/r2.json"}


def test_promote_distinct_relationships_still_add_new_triples(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Different objects do NOT collapse — corroboration is exact-match only."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        # First proposal: sarah works_at acme
        p1 = _create_sample(store, source_report_path="/tmp/r1.json")
        promote(store, kg, p1.proposal_id, reviewer_id="a", rationale="ok")

        # Second proposal: sarah works_at beta (different object)
        p2 = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=[
                _identity("sarah-chen", "person"),
                _identity("beta-fund", "company"),
                _rel("sarah-chen", "works_at", "beta-fund"),
            ],
            source_report_path="/tmp/r2.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, p2.proposal_id, reviewer_id="a", rationale="ok")

    triples = kg.query_relationship("works_at")
    # Both currently-true — Step 13 does not auto-invalidate on corroborate;
    # that's the UpdateFact path (explicit supersedes).
    assert len(triples) == 2
    objects = {t.object for t in triples}
    assert objects == {"acme-corp", "beta-fund"}


def test_promote_corroborate_preserves_audit_trail(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Each promote() still emits a ProposalDecidedEvent — corroboration is
    orthogonal to the proposal lifecycle."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = _create_sample(store, source_report_path="/tmp/r1.json")
        p2 = _create_sample(
            store,
            source_report_path="/tmp/r2.json",
            proposer_employee_id="bob",
        )
        promote(store, kg, p1.proposal_id, reviewer_id="a", rationale="first")
        promote(store, kg, p2.proposal_id, reviewer_id="a", rationale="second")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    decided = [e for e in logger.read_all() if isinstance(e, ProposalDecidedEvent)]
    assert len(decided) == 2
    assert all(e.decision == "approved" for e in decided)


def test_promote_preference_corroborates(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """PreferenceFact runs through the corroborate path too."""
    pref_facts = [
        IdentityFact(
            confidence=0.95,
            support_quote="sarah mention",
            entity_name="sarah-chen",
            entity_type="person",
        ),
        PreferenceFact(
            confidence=0.8,
            support_quote="prefers morning meetings",
            subject="sarah-chen",
            preference="morning-meetings",
        ),
    ]
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=pref_facts,
            source_report_path="/tmp/r1.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        p2 = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah-chen",
            facts=pref_facts,
            source_report_path="/tmp/r2.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="bob",
        )
        promote(store, kg, p1.proposal_id, reviewer_id="a", rationale="ok")
        promote(store, kg, p2.proposal_id, reviewer_id="a", rationale="ok")

    prefs = kg.query_relationship("prefers")
    assert len(prefs) == 1
    assert prefs[0].corroboration_count == 1


# ---------- Scope propagation (bugfix regression) ----------


def test_promote_copies_target_scope_onto_triples(
    store: ProposalStore,
    kg: KnowledgeGraph,
    tmp_path: Path,
) -> None:
    """Approving a partner-only proposal must stamp its triples partner-only.

    Before the fix, target_scope was dropped on approval and triples
    landed at the schema default 'public', letting MCP get_triples
    return the fact to any employee with READ scope.
    """
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p = create_proposal(
            store,
            target_plane="firm",
            target_entity="ceo",
            facts=[_rel("ceo", "compensation", "7m")],
            source_report_path="/tmp/board.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
            target_scope="partner-only",
        )
        promote(store, kg, p.proposal_id, reviewer_id="reviewer", rationale="verified")

    triples = kg.query_entity("ceo")
    assert len(triples) == 1
    assert triples[0].scope == "partner-only"


def test_promote_corroborating_mismatched_scope_raises(
    store: ProposalStore,
    kg: KnowledgeGraph,
    tmp_path: Path,
) -> None:
    """A second proposal corroborating under a different scope must raise.

    Raised by the pre-flight ``_scope_scan`` before any KG write, so the
    reviewer surfaces the conflict and the KG stays at post-p1 state.
    """
    from memory_mission.promotion import ScopeConflictError

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = create_proposal(
            store,
            target_plane="firm",
            target_entity="ceo",
            facts=[_rel("ceo", "compensation", "7m")],
            source_report_path="/tmp/a.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
            target_scope="partner-only",
        )
        promote(store, kg, p1.proposal_id, reviewer_id="reviewer", rationale="source 1")

        p2 = create_proposal(
            store,
            target_plane="firm",
            target_entity="ceo",
            facts=[_rel("ceo", "compensation", "7m")],
            source_report_path="/tmp/b.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="bob",
            target_scope="public",
        )
        with pytest.raises(ScopeConflictError, match="existing scope 'partner-only'"):
            promote(store, kg, p2.proposal_id, reviewer_id="reviewer", rationale="source 2")

    # KG unchanged after the raise — proves atomicity.
    triples = kg.query_entity("ceo")
    assert len(triples) == 1
    assert triples[0].scope == "partner-only"
    assert triples[0].corroboration_count == 0  # p2 never corroborated


def test_promote_is_idempotent_after_failed_save(
    store: ProposalStore,
    kg: KnowledgeGraph,
    tmp_path: Path,
) -> None:
    """Re-promoting a proposal whose facts already landed must NOT corroborate twice.

    Mitigates the non-atomic promote() edge case: if store.save failed
    after _apply_facts succeeded on the first try, the proposal is still
    pending. A retry must not double-bump confidence on the same source.
    """
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p = create_proposal(
            store,
            target_plane="firm",
            target_entity="alice",
            facts=[_rel("alice", "works_at", "acme")],
            source_report_path="/tmp/retry.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, p.proposal_id, reviewer_id="reviewer", rationale="first run")

        # Simulate a retry: reset proposal to pending to mimic a failed-save
        # scenario, then promote again. Idempotent _add_or_corroborate must
        # detect the already-applied source and skip.
        reset = p.model_copy(update={"status": "pending", "reviewer_id": None, "decided_at": None})
        store.save(reset)
        promote(store, kg, p.proposal_id, reviewer_id="reviewer", rationale="retry")

    triples = kg.query_entity("alice")
    assert len(triples) == 1
    assert triples[0].corroboration_count == 0  # not double-bumped


def test_promote_updatefact_scope_downgrade_blocked(
    store: ProposalStore,
    kg: KnowledgeGraph,
    tmp_path: Path,
) -> None:
    """UpdateFact cannot invalidate a partner-only triple under a public proposal.

    Closes the downgrade attack: invalidate + re-add would otherwise
    bypass corroborate's scope check because the prior triple is no
    longer 'current' by the time add_triple runs.
    """
    from datetime import date as _date

    from memory_mission.promotion import ScopeConflictError

    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        # Seed partner-only triple
        seed = create_proposal(
            store,
            target_plane="firm",
            target_entity="ceo",
            facts=[_rel("ceo", "compensation", "7m")],
            source_report_path="/tmp/seed.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
            target_scope="partner-only",
        )
        promote(store, kg, seed.proposal_id, reviewer_id="reviewer", rationale="seed")

        # Try to supersede it under public scope
        update = UpdateFact(
            confidence=0.9,
            support_quote="compensation change",
            subject="ceo",
            predicate="compensation",
            supersedes_object="7m",
            new_object="8m",
            effective_date=_date(2026, 4, 1),
        )
        p = create_proposal(
            store,
            target_plane="firm",
            target_entity="ceo",
            facts=[update],
            source_report_path="/tmp/downgrade.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="bob",
            target_scope="public",
        )
        with pytest.raises(ScopeConflictError, match="invalidating would downgrade"):
            promote(store, kg, p.proposal_id, reviewer_id="reviewer", rationale="downgrade")

    # KG unchanged: the original partner-only triple still exists, not invalidated
    triples = kg.query_entity("ceo")
    assert len(triples) == 1
    assert triples[0].scope == "partner-only"
    assert triples[0].valid_to is None


def test_knowledge_graph_enables_wal(tmp_path: Path) -> None:
    """KG connection uses WAL journal mode for multi-writer safety."""
    graph = KnowledgeGraph(tmp_path / "kg.sqlite3")
    try:
        mode = graph._conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        graph.close()
    assert mode == "wal"


# ---------- Integrity verification (ProposalIntegrityError) ----------
#
# SomaOS-style invariant: a proposal's stored ``proposal_id`` is a hash
# of identity-bearing fields. Mutating any of those fields after creation
# breaks the link. The pipeline refuses to act on tampered proposals so
# an approval at one set of facts cannot be replayed against a different
# set of facts.


def test_integrity_ok_for_freshly_created_proposal(store: ProposalStore, tmp_path: Path) -> None:
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
    assert proposal.integrity_ok() is True
    assert proposal.expected_proposal_id() == proposal.proposal_id


def test_integrity_ok_false_when_facts_mutated() -> None:
    """Constructing a proposal with mismatched (id, facts) demonstrates the check."""
    facts = _sample_facts()
    real_id = generate_proposal_id(
        target_plane="firm",
        target_employee_id=None,
        target_entity="sarah-chen",
        source_report_path="/tmp/r.json",
        facts=facts,
    )
    tampered = Proposal(
        proposal_id=real_id,
        target_plane="firm",
        target_entity="sarah-chen",
        proposer_agent_id="extract-from-staging-v1",
        proposer_employee_id="alice",
        # Inject ONE extra fact relative to what real_id was hashed over.
        facts=[*facts, _identity("ghost-entity")],
        source_report_path="/tmp/r.json",
    )
    assert tampered.integrity_ok() is False
    assert tampered.expected_proposal_id() != tampered.proposal_id


def test_promote_raises_proposal_integrity_error_on_tamper(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Mutating facts after creation must block promotion."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        # Simulate post-creation tampering by overwriting the stored row
        # with a Proposal whose facts differ from what the id was hashed over.
        tampered = proposal.model_copy(update={"facts": [*proposal.facts, _identity("ghost")]})
        store.save(tampered)
        with pytest.raises(ProposalIntegrityError) as excinfo:
            promote(
                store,
                kg,
                proposal.proposal_id,
                reviewer_id="alice",
                rationale="should not land",
            )
    msg = str(excinfo.value)
    assert "integrity check failed" in msg
    assert proposal.proposal_id in msg


def test_promote_does_not_apply_facts_when_integrity_fails(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """No KG writes leak when the integrity check refuses promotion."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        tampered = proposal.model_copy(update={"facts": [_identity("ghost-only-in-tampered-row")]})
        store.save(tampered)
        with pytest.raises(ProposalIntegrityError):
            promote(
                store,
                kg,
                proposal.proposal_id,
                reviewer_id="alice",
                rationale="ignored",
            )
    # The ghost from the tampered row must NOT have been registered.
    assert kg.get_entity("ghost-only-in-tampered-row") is None
    # And the original sarah-chen entity must NOT have landed either,
    # because the pipeline halted before _apply_facts.
    assert kg.get_entity("sarah-chen") is None


def test_reject_raises_proposal_integrity_error_on_tamper(
    store: ProposalStore, tmp_path: Path
) -> None:
    """The integrity check fires for reject() too, not only promote()."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        tampered = proposal.model_copy(update={"target_entity": "different-entity"})
        store.save(tampered)
        with pytest.raises(ProposalIntegrityError):
            reject(
                store,
                proposal.proposal_id,
                reviewer_id="alice",
                rationale="ignored",
            )


def test_reopen_raises_proposal_integrity_error_on_tamper(
    store: ProposalStore, tmp_path: Path
) -> None:
    """The integrity check fires for reopen() too — covers all three transitions."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        rejected = reject(
            store,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="not yet",
        )
        # Tamper after the legitimate reject.
        tampered = rejected.model_copy(update={"source_report_path": "/tmp/different.json"})
        store.save(tampered)
        with pytest.raises(ProposalIntegrityError):
            reopen(
                store,
                proposal.proposal_id,
                reviewer_id="alice",
                rationale="reconsidering",
            )


def test_promote_succeeds_when_integrity_intact(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Sanity: untampered proposals still promote normally — guard against
    over-zealous integrity check breaking the happy path."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = _create_sample(store)
        approved = promote(
            store,
            kg,
            proposal.proposal_id,
            reviewer_id="alice",
            rationale="Evidence holds",
        )
    assert approved.status == "approved"
    assert kg.get_entity("sarah-chen") is not None
