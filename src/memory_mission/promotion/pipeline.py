"""Promotion pipeline — ``create_proposal`` / ``promote`` / ``reject`` / ``reopen``.

V1's centerpiece. Default deny on auto-merge: nothing lands on a plane
without an explicit human decision with rationale.

Flow:

1. An extraction step (or any upstream caller) produces an
   ``ExtractionReport``. Facts grouped by ``target_entity`` become a
   ``Proposal`` via ``create_proposal``.
2. The review-proposals skill surfaces pending proposals to a human.
   The human approves or rejects — the skill calls ``promote`` or
   ``reject``, always with a rationale.
3. ``promote()`` applies the proposal's facts to the ``KnowledgeGraph``
   atomically (all or nothing), marks the proposal ``approved``,
   appends the decision to history, and emits a ``ProposalDecidedEvent``
   with ``decision="approved"``.
4. ``reject()`` marks ``rejected``, increments rejection_count, and
   emits a ``ProposalDecidedEvent`` with ``decision="rejected"``.
5. ``reopen()`` flips a rejected proposal back to pending so a human
   with new information can reconsider.

Rationale is required on every decision — passing an empty string
raises. That structurally blocks rubber-stamp approvals.

Events flow through ``observability_scope`` so each decision lands in
the per-firm audit trail with trace_id + reviewer identity intact.

This module depends on ``KnowledgeGraph`` (for ``promote`` writes) but
nothing above memory/ingestion — no BrainEngine, no LLM. Same "pure
library" property as permissions: host-agent skills orchestrate; we
provide the primitives.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from memory_mission.extraction.schema import (
    EventFact,
    ExtractedFact,
    IdentityFact,
    OpenQuestion,
    PreferenceFact,
    RelationshipFact,
    UpdateFact,
)
from memory_mission.memory.knowledge_graph import CoherenceWarning, KnowledgeGraph
from memory_mission.memory.schema import Plane
from memory_mission.memory.tiers import DEFAULT_TIER, Tier
from memory_mission.observability.api import (
    log_coherence_warning,
    log_proposal_created,
    log_proposal_decided,
)
from memory_mission.observability.context import current_firm_id, current_logger, current_trace_id
from memory_mission.permissions.policy import Policy
from memory_mission.promotion.proposals import (
    DecisionEntry,
    Proposal,
    ProposalStore,
    generate_proposal_id,
)


class ProposalIntegrityError(Exception):
    """Raised when a proposal's stored id doesn't match its recomputed hash.

    Triggered when an identity-bearing field (plane / employee / entity /
    source_report_path / facts) was mutated between proposal creation
    and the next pipeline operation (promote / reject / reopen). The
    pipeline refuses to act on tampered proposals — modeled on SomaOS's
    ``context_hash`` binding for governed actions, where an approval is
    bound to the exact context at approval time and reuse with mutated
    context is rejected.
    """


class ProposalStateError(Exception):
    """Raised when an operation is attempted on a proposal in the wrong status."""


class ScopeConflictError(Exception):
    """Raised by ``promote()`` when target_scope conflicts with KG state.

    Collected by ``_scope_scan`` on a pre-flight pass over every fact —
    raising BEFORE any write keeps ``_apply_facts`` structurally atomic
    under scope-conflict paths (no partial-write corruption on retry).

    The structured ``conflicts`` field carries one human-readable line
    per conflicting fact so the reviewer can decide whether to split
    the proposal, adjust target_scope, or reject.
    """

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        summary = "; ".join(conflicts)
        super().__init__(f"promotion blocked by {len(conflicts)} scope conflict(s): {summary}")


class CoherenceBlockedError(Exception):
    """Raised by ``promote()`` when firm policy blocks on coherence warnings.

    Set when ``Policy.constitutional_mode`` is True and at least one
    ``CoherenceWarning`` surfaces during ``_apply_facts``. The proposal
    stays pending; the reviewer must either resolve the conflict (e.g.,
    merge entities, retire the conflicting triple, or change the
    proposal) or switch the firm off constitutional mode.

    The structured ``warnings`` field carries the full list so
    reviewer UIs can display them verbatim.
    """

    def __init__(self, warnings: list[CoherenceWarning]) -> None:
        self.warnings = warnings
        summary = "; ".join(
            f"{w.subject} {w.predicate} {w.conflicting_object} "
            f"({w.conflicting_tier}) vs new {w.new_object} ({w.new_tier})"
            for w in warnings
        )
        super().__init__(f"promotion blocked by {len(warnings)} coherence warning(s): {summary}")


def create_proposal(
    store: ProposalStore,
    *,
    target_plane: Plane,
    target_entity: str,
    facts: list[ExtractedFact],
    source_report_path: str,
    proposer_agent_id: str,
    proposer_employee_id: str,
    target_employee_id: str | None = None,
    target_scope: str = "public",
) -> Proposal:
    """Stage a new proposal. Idempotent by deterministic ``proposal_id``.

    Calling with the same inputs twice returns the existing proposal
    instead of inserting a duplicate — handy when an extraction flow
    re-runs on the same source material.
    """
    _require_observability_scope()
    if not facts:
        raise ValueError("create_proposal requires at least one fact")

    proposal_id = generate_proposal_id(
        target_plane=target_plane,
        target_employee_id=target_employee_id,
        target_entity=target_entity,
        source_report_path=source_report_path,
        facts=facts,
    )
    existing = store.get(proposal_id)
    if existing is not None:
        return existing

    proposal = Proposal(
        proposal_id=proposal_id,
        target_plane=target_plane,
        target_employee_id=target_employee_id,
        target_scope=target_scope,
        target_entity=target_entity,
        proposer_agent_id=proposer_agent_id,
        proposer_employee_id=proposer_employee_id,
        facts=facts,
        source_report_path=source_report_path,
    )
    store.insert(proposal)

    log_proposal_created(
        proposal_id=proposal.proposal_id,
        target_plane=proposal.target_plane,
        target_employee_id=proposal.target_employee_id,
        target_scope=proposal.target_scope,
        target_entity=proposal.target_entity,
        proposer_agent_id=proposal.proposer_agent_id,
        proposer_employee_id=proposal.proposer_employee_id,
        fact_count=len(proposal.facts),
        source_report_path=proposal.source_report_path,
    )
    return proposal


def promote(
    store: ProposalStore,
    knowledge_graph: KnowledgeGraph,
    proposal_id: str,
    *,
    reviewer_id: str,
    rationale: str,
    policy: Policy | None = None,
) -> Proposal:
    """Approve a pending proposal: apply its facts to the KG and record the decision.

    Atomic on success. Raises ``ProposalStateError`` if the proposal
    doesn't exist or isn't in the ``pending`` state. Requires a
    non-empty rationale — empty strings are structurally blocked.

    Coherence (Step 15): before applying facts, each triple-like fact
    is checked against currently-true triples on the same
    ``(subject, predicate)``. Conflicts (different object) surface as
    ``CoherenceWarning`` events on the observability log. If ``policy``
    is supplied and ``policy.constitutional_mode`` is True, any
    warning raises ``CoherenceBlockedError`` and the proposal stays
    pending. Advisory mode logs the warnings and proceeds.
    """
    _require_observability_scope()
    proposal = _require_pending(store, proposal_id)
    _require_rationale(rationale)

    # Apply facts FIRST so we don't mark approved on a failed write.
    _apply_facts(proposal, knowledge_graph, policy=policy)

    now = datetime.now(UTC)
    approved = proposal.model_copy(
        update={
            "status": "approved",
            "rationale": rationale,
            "reviewer_id": reviewer_id,
            "decided_at": now,
            "decision_history": [
                *proposal.decision_history,
                DecisionEntry(
                    decision="approved",
                    reviewer_id=reviewer_id,
                    rationale=rationale,
                    at=now,
                ),
            ],
        }
    )
    store.save(approved)
    log_proposal_decided(
        proposal_id=approved.proposal_id,
        decision="approved",
        reviewer_id=reviewer_id,
        rationale=rationale,
        target_plane=approved.target_plane,
        target_employee_id=approved.target_employee_id,
        target_entity=approved.target_entity,
        fact_count=len(approved.facts),
        rejection_count=approved.rejection_count,
    )
    return approved


def reject(
    store: ProposalStore,
    proposal_id: str,
    *,
    reviewer_id: str,
    rationale: str,
) -> Proposal:
    """Reject a pending proposal. Preserves decision history + bumps rejection_count."""
    _require_observability_scope()
    proposal = _require_pending(store, proposal_id)
    _require_rationale(rationale)

    now = datetime.now(UTC)
    rejected = proposal.model_copy(
        update={
            "status": "rejected",
            "rationale": rationale,
            "reviewer_id": reviewer_id,
            "decided_at": now,
            "rejection_count": proposal.rejection_count + 1,
            "decision_history": [
                *proposal.decision_history,
                DecisionEntry(
                    decision="rejected",
                    reviewer_id=reviewer_id,
                    rationale=rationale,
                    at=now,
                ),
            ],
        }
    )
    store.save(rejected)
    log_proposal_decided(
        proposal_id=rejected.proposal_id,
        decision="rejected",
        reviewer_id=reviewer_id,
        rationale=rationale,
        target_plane=rejected.target_plane,
        target_employee_id=rejected.target_employee_id,
        target_entity=rejected.target_entity,
        fact_count=len(rejected.facts),
        rejection_count=rejected.rejection_count,
    )
    return rejected


def reopen(
    store: ProposalStore,
    proposal_id: str,
    *,
    reviewer_id: str,
    rationale: str,
) -> Proposal:
    """Flip a rejected proposal back to pending for reconsideration.

    Only rejected proposals can be reopened — approved ones landed in
    the KG and can't be unwound through this path (that's what
    ``KnowledgeGraph.invalidate`` is for).
    """
    _require_observability_scope()
    proposal = store.get(proposal_id)
    if proposal is None:
        raise ProposalStateError(f"proposal {proposal_id!r} not found")
    if proposal.status != "rejected":
        raise ProposalStateError(
            f"proposal {proposal_id!r} is {proposal.status!r}; only "
            "rejected proposals can be reopened"
        )
    _require_integrity(proposal)
    _require_rationale(rationale)

    now = datetime.now(UTC)
    reopened = proposal.model_copy(
        update={
            "status": "pending",
            "decided_at": None,
            "reviewer_id": None,
            "rationale": None,
            "decision_history": [
                *proposal.decision_history,
                DecisionEntry(
                    decision="reopened",
                    reviewer_id=reviewer_id,
                    rationale=rationale,
                    at=now,
                ),
            ],
        }
    )
    store.save(reopened)
    log_proposal_decided(
        proposal_id=reopened.proposal_id,
        decision="reopened",
        reviewer_id=reviewer_id,
        rationale=rationale,
        target_plane=reopened.target_plane,
        target_employee_id=reopened.target_employee_id,
        target_entity=reopened.target_entity,
        fact_count=len(reopened.facts),
        rejection_count=reopened.rejection_count,
    )
    return reopened


# ---------- Internals ----------


def _require_observability_scope() -> None:
    """Fail before mutating store/KG if the audit context is missing."""
    current_firm_id()
    current_trace_id()
    current_logger()


def _require_pending(store: ProposalStore, proposal_id: str) -> Proposal:
    proposal = store.get(proposal_id)
    if proposal is None:
        raise ProposalStateError(f"proposal {proposal_id!r} not found")
    if proposal.status != "pending":
        raise ProposalStateError(
            f"proposal {proposal_id!r} is {proposal.status!r}; expected pending"
        )
    _require_integrity(proposal)
    return proposal


def _require_integrity(proposal: Proposal) -> None:
    """Refuse to act on a proposal whose stored id no longer matches its facts.

    Defends against post-creation tampering of identity-bearing fields.
    See ``ProposalIntegrityError`` for the threat model.
    """
    if not proposal.integrity_ok():
        raise ProposalIntegrityError(
            f"proposal {proposal.proposal_id!r} integrity check failed: "
            f"stored id does not match recomputed hash "
            f"({proposal.expected_proposal_id()!r}). "
            "Identity-bearing fields (plane / employee / entity / "
            "source_report_path / facts) were mutated after creation."
        )


def _require_rationale(rationale: str) -> None:
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required on every decision")


def _apply_facts(
    proposal: Proposal,
    kg: KnowledgeGraph,
    *,
    policy: Policy | None = None,
) -> None:
    """Apply a proposal's facts to the KG. All-or-nothing on success.

    V1 policy:
    - identity → ``add_entity`` (idempotent upsert)
    - relationship → corroborate matching triple if one is currently
      true, otherwise ``add_triple``
    - preference → same pattern with predicate ``prefers``
    - event → same pattern with predicate ``event``; ``valid_from``
      carries the event date
    - update → ``invalidate`` prior triple if ``supersedes_object``
      given, then corroborate-or-add for the new value
    - open_question → skipped (never promoted; must become a new fact
      to land in the KG)

    Corroboration uses the Bayesian independent-evidence update
    (Noisy-OR, capped at 0.99) so re-extracting the same fact from a
    new source strengthens belief without creating duplicate rows.

    Coherence (Step 15): before each triple-like fact lands, the KG
    is asked whether the new (subject, predicate, object) conflicts
    with any currently-true triple on the same (subject, predicate)
    but a different object. Each conflict surfaces as a
    ``CoherenceWarning`` logged via ``log_coherence_warning``. If
    ``policy.constitutional_mode`` is True, the collected warnings
    raise ``CoherenceBlockedError`` BEFORE any write, leaving the KG
    untouched and the proposal pending.

    ``source_closet`` + ``source_file`` carry provenance: the closet
    is ``firm`` or ``personal/<employee_id>``; the file is the
    ``ExtractionReport`` path that grounded this proposal. Every
    corroboration appends its source to ``triple_sources``.
    """
    source_closet = _source_closet(proposal)
    source_file = proposal.source_report_path
    target_scope = proposal.target_scope
    strict = bool(policy is not None and policy.constitutional_mode)

    # Pass 0: scope scan. Catch scope mismatches BEFORE any write so the
    # KG never holds partial state on a scope-conflict raise. Covers the
    # corroborate-on-different-scope path AND the UpdateFact invalidate+
    # re-add downgrade attack — both raise here, both leave KG untouched.
    scope_conflicts = _scope_scan(proposal, kg)
    if scope_conflicts:
        raise ScopeConflictError(scope_conflicts)

    # Pass 1: coherence scan. Collect every warning, log each one, and
    # raise before applying anything if the firm is in strict mode.
    warnings = _coherence_scan(proposal, kg)
    for warning in warnings:
        log_coherence_warning(
            proposal_id=proposal.proposal_id,
            subject=warning.subject,
            predicate=warning.predicate,
            new_object=warning.new_object,
            new_tier=warning.new_tier,
            conflicting_object=warning.conflicting_object,
            conflicting_tier=warning.conflicting_tier,
            conflict_type=warning.conflict_type,
            blocked=strict,
        )
    if strict and warnings:
        raise CoherenceBlockedError(warnings)

    # Pass 2: apply facts. This only runs if we didn't block above.
    for fact in proposal.facts:
        if isinstance(fact, IdentityFact):
            kg.add_entity(
                fact.entity_name,
                entity_type=fact.entity_type,
                properties=fact.properties,
            )
        elif isinstance(fact, RelationshipFact):
            # Ensure both endpoints exist as entities before linking.
            kg.add_entity(fact.subject)
            kg.add_entity(fact.object)
            _add_or_corroborate(
                kg,
                fact.subject,
                fact.predicate,
                fact.object,
                confidence=fact.confidence,
                source_closet=source_closet,
                source_file=source_file,
                scope=target_scope,
            )
        elif isinstance(fact, PreferenceFact):
            kg.add_entity(fact.subject)
            _add_or_corroborate(
                kg,
                fact.subject,
                "prefers",
                fact.preference,
                confidence=fact.confidence,
                source_closet=source_closet,
                source_file=source_file,
                scope=target_scope,
            )
        elif isinstance(fact, EventFact):
            kg.add_entity(fact.entity_name)
            _add_or_corroborate(
                kg,
                fact.entity_name,
                "event",
                fact.description,
                valid_from=fact.event_date,
                confidence=fact.confidence,
                source_closet=source_closet,
                source_file=source_file,
                scope=target_scope,
            )
        elif isinstance(fact, UpdateFact):
            kg.add_entity(fact.subject)
            if fact.supersedes_object:
                kg.invalidate(
                    fact.subject,
                    fact.predicate,
                    fact.supersedes_object,
                    ended=fact.effective_date,
                )
            _add_or_corroborate(
                kg,
                fact.subject,
                fact.predicate,
                fact.new_object,
                valid_from=fact.effective_date,
                confidence=fact.confidence,
                source_closet=source_closet,
                source_file=source_file,
                scope=target_scope,
            )
        elif isinstance(fact, OpenQuestion):
            continue  # open questions never promote


def _scope_scan(proposal: Proposal, kg: KnowledgeGraph) -> list[str]:
    """Pre-flight scope compatibility pass — returns a list of conflict strings.

    For every fact that would write to the KG, check if the currently-true
    triple at that (subject, predicate, object) has a different scope than
    ``proposal.target_scope``. For ``UpdateFact``, also check the
    ``supersedes_object`` triple — that one would otherwise be silently
    invalidated under the old scope and re-added under the new.

    Returns an empty list when every write is scope-compatible.
    """
    target = proposal.target_scope
    errors: list[str] = []
    for fact in proposal.facts:
        if isinstance(fact, RelationshipFact):
            existing = kg.find_current_triple(fact.subject, fact.predicate, fact.object)
            if existing is not None and existing.scope != target:
                errors.append(
                    f"{fact.subject} {fact.predicate} {fact.object}: existing scope "
                    f"{existing.scope!r} != proposal scope {target!r}"
                )
        elif isinstance(fact, PreferenceFact):
            existing = kg.find_current_triple(fact.subject, "prefers", fact.preference)
            if existing is not None and existing.scope != target:
                errors.append(
                    f"{fact.subject} prefers {fact.preference!r}: existing scope "
                    f"{existing.scope!r} != proposal scope {target!r}"
                )
        elif isinstance(fact, EventFact):
            existing = kg.find_current_triple(fact.entity_name, "event", fact.description)
            if existing is not None and existing.scope != target:
                errors.append(
                    f"{fact.entity_name} event {fact.description!r}: existing scope "
                    f"{existing.scope!r} != proposal scope {target!r}"
                )
        elif isinstance(fact, UpdateFact):
            if fact.supersedes_object:
                prev = kg.find_current_triple(fact.subject, fact.predicate, fact.supersedes_object)
                if prev is not None and prev.scope != target:
                    errors.append(
                        f"{fact.subject} {fact.predicate} {fact.supersedes_object} "
                        f"(supersedes): existing scope {prev.scope!r} != proposal "
                        f"scope {target!r} — invalidating would downgrade"
                    )
            new_existing = kg.find_current_triple(fact.subject, fact.predicate, fact.new_object)
            if new_existing is not None and new_existing.scope != target:
                errors.append(
                    f"{fact.subject} {fact.predicate} {fact.new_object}: existing scope "
                    f"{new_existing.scope!r} != proposal scope {target!r}"
                )
        # IdentityFact: entities carry no scope — skip.
        # OpenQuestion: never promotes — skip.
    return errors


def _coherence_scan(proposal: Proposal, kg: KnowledgeGraph) -> list[CoherenceWarning]:
    """Collect every coherence warning this proposal's facts would produce.

    Only triple-like facts participate — IdentityFact and OpenQuestion
    are never subject to tier coherence checks. Each fact contributes
    zero or more warnings; the function returns the flat list.
    """
    warnings: list[CoherenceWarning] = []
    for fact in proposal.facts:
        if isinstance(fact, RelationshipFact):
            warnings.extend(
                kg.check_coherence(
                    fact.subject,
                    fact.predicate,
                    fact.object,
                    new_tier=DEFAULT_TIER,
                )
            )
        elif isinstance(fact, PreferenceFact):
            warnings.extend(
                kg.check_coherence(fact.subject, "prefers", fact.preference, new_tier=DEFAULT_TIER)
            )
        elif isinstance(fact, EventFact):
            warnings.extend(
                kg.check_coherence(
                    fact.entity_name,
                    "event",
                    fact.description,
                    new_tier=DEFAULT_TIER,
                )
            )
        elif isinstance(fact, UpdateFact):
            # UpdateFact invalidates the prior object before adding the new
            # one, so the only coherence concern is any OTHER currently-true
            # triple on the same (subject, predicate) that isn't the one
            # being superseded.
            subj_warnings = kg.check_coherence(
                fact.subject,
                fact.predicate,
                fact.new_object,
                new_tier=DEFAULT_TIER,
            )
            if fact.supersedes_object:
                subj_warnings = [
                    w for w in subj_warnings if w.conflicting_object != fact.supersedes_object
                ]
            warnings.extend(subj_warnings)
    return warnings


def _add_or_corroborate(
    kg: KnowledgeGraph,
    subject: str,
    predicate: str,
    obj: str,
    *,
    valid_from: date | None = None,
    confidence: float,
    source_closet: str | None,
    source_file: str | None,
    tier: Tier = DEFAULT_TIER,
    scope: str = "public",
) -> None:
    """Corroborate a matching currently-true triple, or add a new one.

    Central injection point for the promotion-time Bayesian update.
    If no currently-true match exists, falls back to ``add_triple`` so
    the fact lands with its provenance seeded into ``triple_sources``.

    ``scope`` is copied from the proposal's ``target_scope`` and is
    load-bearing for access control — corroboration raises
    ``ValueError`` on scope mismatch rather than letting a restricted
    fact silently become public (or vice versa). ``_scope_scan`` runs
    pre-flight so this raise path is defense-in-depth, not the primary
    error surface.

    Idempotent by ``source_file``: if a currently-true triple for
    ``(subject, predicate, object)`` already records this source in
    ``triple_sources``, we skip — prevents double-corroboration when a
    prior ``promote()`` applied facts but then failed on ``store.save``
    and the reviewer retries.
    """
    if kg.has_triple_source(subject=subject, predicate=predicate, obj=obj, source_file=source_file):
        return
    existing = kg.find_current_triple(subject, predicate, obj)
    if existing is not None:
        kg.corroborate(
            subject,
            predicate,
            obj,
            confidence=confidence,
            source_closet=source_closet,
            source_file=source_file,
            scope=scope,
        )
        return
    kg.add_triple(
        subject,
        predicate,
        obj,
        valid_from=valid_from,
        confidence=confidence,
        source_closet=source_closet,
        source_file=source_file,
        tier=tier,
        scope=scope,
    )


def _source_closet(proposal: Proposal) -> str:
    if proposal.target_plane == "firm":
        return "firm"
    return f"personal/{proposal.target_employee_id}"


__all__ = [
    "ProposalIntegrityError",
    "ProposalStateError",
    "create_proposal",
    "promote",
    "reject",
    "reopen",
]
