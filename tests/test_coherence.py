"""Step 15b tests — tier coherence check + observability + policy flag.

The eval strategy in ``docs/EVALS.md`` section 2.7 calls for
structured, deterministic warnings that can be labeled and graded
without an LLM judge. These tests exercise that deterministic layer:
every scenario has a single correct answer, the KG produces
structured ``CoherenceWarning`` objects, and the promotion pipeline
either surfaces them (advisory) or blocks on them (constitutional
mode).

Fixtures in this file double as the seed corpus for the eval set
when distillation coherence (Step 17) lands — the labeled
``(pre-state, new fact, expected warnings, expected blocked)``
scenarios are exactly what section 2.7 asks for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_mission.extraction import (
    IdentityFact,
    PreferenceFact,
    RelationshipFact,
    UpdateFact,
)
from memory_mission.memory import CoherenceWarning, KnowledgeGraph
from memory_mission.observability import (
    CoherenceWarningEvent,
    ObservabilityLogger,
    observability_scope,
)
from memory_mission.permissions.policy import Policy
from memory_mission.promotion import (
    CoherenceBlockedError,
    ProposalStore,
    create_proposal,
    promote,
)

# ---------- Fixtures ----------


@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph(tmp_path / "kg.sqlite3")


@pytest.fixture
def store(tmp_path: Path) -> ProposalStore:
    return ProposalStore(tmp_path / "proposals.sqlite3")


def _identity(name: str, entity_type: str = "person") -> IdentityFact:
    return IdentityFact(
        confidence=0.9,
        support_quote=f"mention of {name}",
        entity_name=name,
        entity_type=entity_type,
    )


def _rel(s: str, p: str, o: str) -> RelationshipFact:
    return RelationshipFact(
        confidence=0.9, support_quote=f"{s} {p} {o}", subject=s, predicate=p, object=o
    )


# ---------- KG.check_coherence — deterministic layer ----------


def test_check_coherence_no_match_returns_empty(kg: KnowledgeGraph) -> None:
    assert kg.check_coherence("sarah", "works_at", "acme") == []


def test_check_coherence_same_triple_is_not_a_conflict(kg: KnowledgeGraph) -> None:
    """Same subject-predicate-object is corroboration, not conflict."""
    kg.add_triple("sarah", "works_at", "acme")
    warnings = kg.check_coherence("sarah", "works_at", "acme")
    assert warnings == []


def test_check_coherence_different_object_produces_warning(
    kg: KnowledgeGraph,
) -> None:
    kg.add_triple("sarah", "works_at", "acme", tier="policy")
    warnings = kg.check_coherence("sarah", "works_at", "beta", new_tier="decision")
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, CoherenceWarning)
    assert w.subject == "sarah"
    assert w.predicate == "works_at"
    assert w.new_object == "beta"
    assert w.new_tier == "decision"
    assert w.conflicting_object == "acme"
    assert w.conflicting_tier == "policy"
    assert w.conflict_type == "same_predicate_different_object"
    assert w.higher_tier == "policy"
    assert w.lower_tier == "decision"


def test_check_coherence_ignores_invalidated_triples(kg: KnowledgeGraph) -> None:
    from datetime import date

    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2020, 1, 1))
    kg.invalidate("sarah", "works_at", "acme", ended=date(2024, 1, 1))
    # New fact on the same subject/predicate with a different object: no
    # conflict, because the old triple is no longer currently true.
    assert kg.check_coherence("sarah", "works_at", "beta") == []


def test_check_coherence_returns_one_warning_per_conflict(
    kg: KnowledgeGraph,
) -> None:
    """Multiple currently-true triples on the same (subject, predicate) all surface."""
    # Two distinct currently-true "works_at" claims that were never
    # invalidated is unusual, but the check should flag each one.
    kg.add_triple("sarah", "works_at", "acme", tier="decision")
    kg.add_triple("sarah", "works_at", "beta", tier="policy")
    warnings = kg.check_coherence("sarah", "works_at", "gamma")
    assert len(warnings) == 2
    conflicting = {(w.conflicting_object, w.conflicting_tier) for w in warnings}
    assert conflicting == {("acme", "decision"), ("beta", "policy")}


def test_check_coherence_preserves_tier_on_both_sides(kg: KnowledgeGraph) -> None:
    kg.add_triple("firm", "mission", "preserve-capital", tier="constitution")
    warnings = kg.check_coherence("firm", "mission", "maximize-returns", new_tier="doctrine")
    assert len(warnings) == 1
    assert warnings[0].higher_tier == "constitution"
    assert warnings[0].lower_tier == "doctrine"


# ---------- Promotion integration — advisory mode ----------


def _create_conflict_setup(store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path) -> str:
    """Seed the KG with a doctrine-tier triple and queue a conflicting proposal."""
    kg.add_triple("firm", "thesis", "buy-durable-compounders", tier="doctrine")
    proposal = create_proposal(
        store,
        target_plane="firm",
        target_entity="firm",
        facts=[
            _identity("firm", "organization"),
            _rel("firm", "thesis", "buy-momentum"),
        ],
        source_report_path="/tmp/conflict.json",
        proposer_agent_id="extract-from-staging-v1",
        proposer_employee_id="alice",
    )
    return proposal.proposal_id


def test_promote_logs_coherence_warning_event_advisory(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Advisory mode: warning event lands, promotion still succeeds."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        pid = _create_conflict_setup(store, kg, tmp_path)
        result = promote(store, kg, pid, reviewer_id="a", rationale="go")
    assert result.status == "approved"

    # Warning event recorded
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert len(coherence_events) == 1
    event = coherence_events[0]
    assert event.subject == "firm"
    assert event.predicate == "thesis"
    assert event.new_object == "buy-momentum"
    assert event.conflicting_object == "buy-durable-compounders"
    assert event.conflicting_tier == "doctrine"
    assert event.blocked is False


def test_promote_advisory_still_applies_facts(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Advisory mode writes to the KG despite the warning."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        pid = _create_conflict_setup(store, kg, tmp_path)
        promote(store, kg, pid, reviewer_id="a", rationale="go")

    # Both triples now currently true — advisory mode does not invalidate
    # the conflicting one. Reviewer is expected to clean up explicitly.
    theses = kg.query_relationship("thesis")
    objects = {t.object for t in theses}
    assert objects == {"buy-durable-compounders", "buy-momentum"}


def test_promote_no_conflict_emits_no_warning(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Clean promotions: no coherence warning events."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah",
            facts=[_identity("sarah"), _rel("sarah", "knows", "bob")],
            source_report_path="/tmp/clean.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="ok")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert coherence_events == []


def test_promote_corroboration_is_not_a_conflict(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Re-promoting the same exact fact must not trigger a coherence warning."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        p1 = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah",
            facts=[_identity("sarah"), _rel("sarah", "knows", "bob")],
            source_report_path="/tmp/one.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, p1.proposal_id, reviewer_id="a", rationale="first")

        p2 = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah",
            facts=[_identity("sarah"), _rel("sarah", "knows", "bob")],
            source_report_path="/tmp/two.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="bob",
        )
        promote(store, kg, p2.proposal_id, reviewer_id="a", rationale="second")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert coherence_events == []


def test_update_fact_does_not_conflict_with_its_own_supersedes(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """UpdateFact invalidates the prior value; the scan must exclude it."""
    from datetime import date

    kg.add_triple("sarah", "works_at", "acme", valid_from=date(2024, 1, 1))
    update_fact = UpdateFact(
        confidence=0.9,
        support_quote="sarah moved to beta",
        subject="sarah",
        predicate="works_at",
        new_object="beta",
        supersedes_object="acme",
        effective_date=date(2026, 3, 15),
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah",
            facts=[_identity("sarah"), update_fact],
            source_report_path="/tmp/move.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="move")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert coherence_events == []


# ---------- Constitutional mode — blocking ----------


def test_promote_blocks_when_constitutional_mode_on(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Strict mode: warnings raise CoherenceBlockedError; proposal stays pending."""
    policy = Policy(firm_id="acme", constitutional_mode=True)
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        pid = _create_conflict_setup(store, kg, tmp_path)
        with pytest.raises(CoherenceBlockedError) as exc_info:
            promote(
                store,
                kg,
                pid,
                reviewer_id="a",
                rationale="this should not land",
                policy=policy,
            )
    err = exc_info.value
    assert len(err.warnings) == 1
    assert err.warnings[0].conflicting_object == "buy-durable-compounders"

    # Proposal stayed pending; new triple was NOT written.
    pending = store.get(pid)
    assert pending is not None
    assert pending.status == "pending"
    theses = kg.query_relationship("thesis")
    assert {t.object for t in theses} == {"buy-durable-compounders"}


def test_constitutional_mode_logs_blocked_true(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """Blocked warnings carry ``blocked=True`` on the event."""
    policy = Policy(firm_id="acme", constitutional_mode=True)
    with (
        observability_scope(observability_root=tmp_path, firm_id="acme"),
        pytest.raises(CoherenceBlockedError),
    ):
        pid = _create_conflict_setup(store, kg, tmp_path)
        promote(store, kg, pid, reviewer_id="a", rationale="no", policy=policy)

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert len(coherence_events) == 1
    assert coherence_events[0].blocked is True


def test_policy_off_falls_back_to_advisory(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """constitutional_mode=False behaves identically to no policy."""
    policy = Policy(firm_id="acme", constitutional_mode=False)
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        pid = _create_conflict_setup(store, kg, tmp_path)
        result = promote(store, kg, pid, reviewer_id="a", rationale="go", policy=policy)
    assert result.status == "approved"


def test_preference_conflict_emits_warning(
    store: ProposalStore, kg: KnowledgeGraph, tmp_path: Path
) -> None:
    """PreferenceFact participates in coherence checks too."""
    kg.add_triple("sarah", "prefers", "morning-meetings", tier="policy")
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        proposal = create_proposal(
            store,
            target_plane="firm",
            target_entity="sarah",
            facts=[
                _identity("sarah"),
                PreferenceFact(
                    confidence=0.9,
                    support_quote="sarah prefers afternoon meetings",
                    subject="sarah",
                    preference="afternoon-meetings",
                ),
            ],
            source_report_path="/tmp/pref.json",
            proposer_agent_id="extract-from-staging-v1",
            proposer_employee_id="alice",
        )
        promote(store, kg, proposal.proposal_id, reviewer_id="a", rationale="advisory")

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    coherence_events = [e for e in logger.read_all() if isinstance(e, CoherenceWarningEvent)]
    assert len(coherence_events) == 1
    assert coherence_events[0].predicate == "prefers"
