"""Convenience logging API.

Thin wrappers over ``ObservabilityLogger`` that read firm_id / employee_id /
trace_id from the current ``observability_scope()`` so call sites don't need
to repeat them.

Every subsequent component (memory, extraction, workflows) uses THIS module
to log, not the raw Logger class.
"""

from __future__ import annotations

from typing import Literal

from memory_mission.observability.context import (
    current_employee_id,
    current_firm_id,
    current_logger,
    current_trace_id,
)
from memory_mission.observability.events import (
    ConnectorInvocationEvent,
    DraftEvent,
    ExtractionEvent,
    PromotionEvent,
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
