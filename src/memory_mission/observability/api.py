"""Convenience logging API.

Thin wrappers over ``ObservabilityLogger`` that read firm_id / employee_id /
trace_id from the current ``observability_scope()`` so call sites don't need
to repeat them.

Every subsequent component (memory, extraction, workflows) uses THIS module
to log, not the raw Logger class.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from memory_mission.observability.context import (
    current_employee_id,
    current_firm_id,
    current_logger,
    current_trace_id,
)
from memory_mission.observability.events import (
    CoherenceWarningEvent,
    ConnectorInvocationEvent,
    DraftEvent,
    ExtractionEvent,
    PromotionEvent,
    ProposalCreatedEvent,
    ProposalDecidedEvent,
    RetrievalEvent,
)


def log_extraction(
    *,
    source_interaction_id: str,
    source_type: Literal["email", "calendar", "transcript", "manual"],
    extracted_facts: list[dict[str, object]],
    confidence_scores: dict[str, float],
    llm_provider: str,
    llm_model: str,
    prompt_hash: str,
    latency_ms: int | None = None,
) -> ExtractionEvent:
    """Record a fact extraction. Returns the event for convenience."""
    event = ExtractionEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        source_interaction_id=source_interaction_id,
        source_type=source_type,
        extracted_facts=extracted_facts,
        confidence_scores=confidence_scores,
        llm_provider=llm_provider,
        llm_model=llm_model,
        prompt_hash=prompt_hash,
        latency_ms=latency_ms,
    )
    current_logger().write(event)
    return event


def log_promotion(
    *,
    candidate_fact: dict[str, object],
    target_page: str,
    signal_scores: dict[str, float],
    total_score: float,
    gates: dict[str, bool],
    decision: Literal["proposed", "approved", "rejected", "revised"],
    reviewer: str,
    reviewer_type: Literal["human", "agent"],
    justification: str | None = None,
) -> PromotionEvent:
    """Record a promotion pipeline decision."""
    event = PromotionEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        candidate_fact=candidate_fact,
        target_page=target_page,
        signal_scores=signal_scores,
        total_score=total_score,
        gates=gates,
        decision=decision,
        reviewer=reviewer,
        reviewer_type=reviewer_type,
        justification=justification,
    )
    current_logger().write(event)
    return event


def log_retrieval(
    *,
    query: str,
    tier: Literal["navigate", "cascade", "discover"],
    pages_loaded: list[str],
    token_budget: int,
    tokens_used: int,
    latency_ms: int,
) -> RetrievalEvent:
    """Record a memory retrieval (search/query/get)."""
    event = RetrievalEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        query=query,
        tier=tier,
        pages_loaded=pages_loaded,
        token_budget=token_budget,
        tokens_used=tokens_used,
        latency_ms=latency_ms,
    )
    current_logger().write(event)
    return event


def log_draft(
    *,
    workflow: Literal["meeting_prep", "email_draft", "crm_update"],
    context_pages: list[str],
    output_preview: str,
    output_length_chars: int,
    user_action: Literal["pending", "sent", "edited", "discarded"] | None = "pending",
) -> DraftEvent:
    """Record a workflow agent output. User action is updated via a follow-up event."""
    event = DraftEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        workflow=workflow,
        context_pages=context_pages,
        output_preview=output_preview,
        output_length_chars=output_length_chars,
        user_action=user_action,
    )
    current_logger().write(event)
    return event


def log_connector_invocation(
    *,
    connector_name: str,
    action: str,
    preview: str,
    preview_redactions: dict[str, int],
    latency_ms: int,
    success: bool,
    error: str | None = None,
) -> ConnectorInvocationEvent:
    """Record a connector invocation.

    Called by the connector harness after every ``invoke()``. The preview is
    expected to already be PII-scrubbed and truncated by the harness.
    """
    event = ConnectorInvocationEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        connector_name=connector_name,
        action=action,
        preview=preview,
        preview_redactions=preview_redactions,
        latency_ms=latency_ms,
        success=success,
        error=error,
    )
    current_logger().write(event)
    return event


def log_proposal_created(
    *,
    proposal_id: str,
    target_plane: Literal["personal", "firm"],
    target_employee_id: str | None,
    target_scope: str,
    target_entity: str,
    proposer_agent_id: str,
    proposer_employee_id: str,
    fact_count: int,
    source_report_path: str,
) -> ProposalCreatedEvent:
    """Record a new promotion proposal entering the queue."""
    event = ProposalCreatedEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        proposal_id=proposal_id,
        target_plane=target_plane,
        target_employee_id=target_employee_id,
        target_scope=target_scope,
        target_entity=target_entity,
        proposer_agent_id=proposer_agent_id,
        proposer_employee_id=proposer_employee_id,
        fact_count=fact_count,
        source_report_path=source_report_path,
    )
    current_logger().write(event)
    return event


def log_proposal_decided(
    *,
    proposal_id: str,
    decision: Literal["approved", "rejected", "reopened"],
    reviewer_id: str,
    rationale: str,
    target_plane: Literal["personal", "firm"],
    target_employee_id: str | None,
    target_entity: str,
    fact_count: int,
    rejection_count: int,
) -> ProposalDecidedEvent:
    """Record a reviewer's decision on a pending proposal."""
    event = ProposalDecidedEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        proposal_id=proposal_id,
        decision=decision,
        reviewer_id=reviewer_id,
        rationale=rationale,
        target_plane=target_plane,
        target_employee_id=target_employee_id,
        target_entity=target_entity,
        fact_count=fact_count,
        rejection_count=rejection_count,
    )
    current_logger().write(event)
    return event


def coherence_warnings_for(
    entity_id: str,
    *,
    since: datetime | None = None,
) -> list[CoherenceWarningEvent]:
    """Return unresolved coherence warnings touching ``entity_id``.

    Used by Move 3 rendering paths (``AgentContext`` / ``render_page``)
    to surface ``[!contradiction]`` callouts for an entity when an
    observability log records unresolved conflicts on it.

    Scans the current firm's append-only JSONL via ``current_logger``.
    Requires an active observability scope; returns ``[]`` if no
    matching events are found.

    V1 treats every ``CoherenceWarningEvent`` as unresolved — we do
    not yet emit a ``CoherenceResolvedEvent`` on merge / reject. That
    primitive is deferred to post-V1 (see ``docs/VISION.md`` +
    post-V1 backlog). When it lands, this helper will filter by
    subsequent resolution events on the same (subject, predicate,
    conflicting_object) triple.

    Args:
        entity_id: Subject to match on. Stable ID (``p_<token>``) or
            raw entity name. Compared exactly against ``subject``.
        since: Optional floor — only return events at or after this
            timestamp. Default ``None`` = entire log.

    Returns:
        List of ``CoherenceWarningEvent`` ordered oldest-first.
    """
    logger = current_logger()
    out: list[CoherenceWarningEvent] = []
    for event in logger.read_all():
        if not isinstance(event, CoherenceWarningEvent):
            continue
        if event.subject != entity_id:
            continue
        if since is not None and event.timestamp < since:
            continue
        out.append(event)
    return out


def log_coherence_warning(
    *,
    proposal_id: str,
    subject: str,
    predicate: str,
    new_object: str,
    new_tier: Literal["constitution", "doctrine", "policy", "decision"],
    conflicting_object: str,
    conflicting_tier: Literal["constitution", "doctrine", "policy", "decision"],
    conflict_type: Literal["same_predicate_different_object"],
    blocked: bool,
) -> CoherenceWarningEvent:
    """Record a tier coherence conflict detected during ``promote()``.

    Each warning lands as its own event so the observability log
    becomes the eval corpus for doctrinal conflicts. Callers decide
    whether the warning is advisory (``blocked=False``) or blocked the
    promotion (``blocked=True``, firm in constitutional mode).
    """
    event = CoherenceWarningEvent(
        firm_id=current_firm_id(),
        employee_id=current_employee_id(),
        trace_id=current_trace_id(),
        proposal_id=proposal_id,
        subject=subject,
        predicate=predicate,
        new_object=new_object,
        new_tier=new_tier,
        conflicting_object=conflicting_object,
        conflicting_tier=conflicting_tier,
        conflict_type=conflict_type,
        blocked=blocked,
    )
    current_logger().write(event)
    return event
