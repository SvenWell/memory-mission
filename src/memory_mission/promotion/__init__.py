"""Component 4.2 — Promotion Pipeline (V1 centerpiece).

PR-model review for every fact that would touch a memory plane. The
review-proposals skill surfaces pending proposals to a human; the
human approves or rejects WITH RATIONALE; approved proposals apply
atomically to the KnowledgeGraph with full provenance.

Public surface:
- ``Proposal`` / ``DecisionEntry`` / ``ProposalStatus`` (data model)
- ``ProposalStore`` (per-firm SQLite queue)
- ``generate_proposal_id`` (deterministic id from inputs)
- ``create_proposal`` / ``promote`` / ``reject`` / ``reopen`` (pipeline
  functions — they combine store updates with KG writes and
  observability events)
- ``ProposalStateError`` (raised on wrong-status operations)
"""

from memory_mission.promotion.pipeline import (
    ProposalStateError,
    create_proposal,
    promote,
    reject,
    reopen,
)
from memory_mission.promotion.proposals import (
    DecisionEntry,
    Proposal,
    ProposalStatus,
    ProposalStore,
    generate_proposal_id,
)

__all__ = [
    "DecisionEntry",
    "Proposal",
    "ProposalStateError",
    "ProposalStatus",
    "ProposalStore",
    "create_proposal",
    "generate_proposal_id",
    "promote",
    "reject",
    "reopen",
]
