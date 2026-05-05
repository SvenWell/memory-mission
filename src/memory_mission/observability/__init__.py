"""Component 0.4 — Observability + Audit Trail.

FOUNDATIONAL infrastructure. Every other component writes to this log.

Usage:

    from memory_mission.observability import (
        ObservabilityLogger,
        observability_scope,
        log_extraction,
        log_retrieval,
    )

    with observability_scope(
        observability_root=Path("./.observability"),
        firm_id="acme-wealth",
        employee_id="sarah-chen",
    ):
        log_extraction(
            source_interaction_id="call-2026-04-18-001",
            source_type="transcript",
            extracted_facts=[{"client": "acme-corp", "claim": "shifting 15% to alts"}],
            confidence_scores={"client": 0.95, "claim": 0.7},
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            prompt_hash="sha256:...",
        )

What gets logged:
- Extractions: source, facts, confidence, LLM + prompt hash
- Promotions: candidate fact, scores, gates, decision, reviewer
- Retrievals: query, pages loaded, tokens, latency
- Drafts: workflow, context pages, output, user action

Retention: 7 years (wealth management regulatory requirement).
Format: immutable append-only JSONL per firm.
"""

from memory_mission.observability.api import (
    coherence_warnings_for,
    log_coherence_warning,
    log_connector_invocation,
    log_draft,
    log_extraction,
    log_promotion,
    log_proposal_created,
    log_proposal_decided,
    log_retrieval,
)
from memory_mission.observability.context import (
    current_employee_id,
    current_firm_id,
    current_logger,
    current_trace_id,
    observability_scope,
)
from memory_mission.observability.events import (
    SCHEMA_VERSION,
    CoherenceWarningEvent,
    ConnectorInvocationEvent,
    DraftEvent,
    Event,
    ExtractionEvent,
    PersonalFactWriteEvent,
    PromotionEvent,
    ProposalCreatedEvent,
    ProposalDecidedEvent,
    RetrievalEvent,
)
from memory_mission.observability.logger import (
    EVENTS_FILENAME,
    ObservabilityLogger,
    parse_event_line,
    serialize_event,
)

__all__ = [
    "EVENTS_FILENAME",
    "SCHEMA_VERSION",
    "CoherenceWarningEvent",
    "ConnectorInvocationEvent",
    "DraftEvent",
    "Event",
    "ExtractionEvent",
    "ObservabilityLogger",
    "PersonalFactWriteEvent",
    "PromotionEvent",
    "ProposalCreatedEvent",
    "ProposalDecidedEvent",
    "RetrievalEvent",
    "coherence_warnings_for",
    "current_employee_id",
    "current_firm_id",
    "current_logger",
    "current_trace_id",
    "log_coherence_warning",
    "log_connector_invocation",
    "log_draft",
    "log_extraction",
    "log_promotion",
    "log_proposal_created",
    "log_proposal_decided",
    "log_retrieval",
    "observability_scope",
    "parse_event_line",
    "serialize_event",
]
