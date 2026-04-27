"""Workflow-level synthesis — distilled context for agents (Step 17).

The first workflow primitive that touches the full stack. Extraction,
promotion, corroboration, identity, tier, and federated detection all
contribute to what ``compile_agent_context`` returns.

Inspired by Tolaria's Neighborhood mode (ADR-0069): the canonical shape
of "everything we know about this entity" is structured — outgoing
relationships first, inverse relationships second, backlinks third,
events and preferences surfaced as their own groups, empty groups
visible with count 0. That shape is what ``AttendeeContext`` encodes.

Consumers:

- ``skills/meeting-prep/SKILL.md`` — the canonical workflow. Given
  attendees + task, compiles a distilled package and hands the render
  to the host-agent LLM for drafting.
- Future workflow skills (email-draft, CRM-update, deal-memo) reuse
  ``compile_agent_context`` with different ``role`` values — the
  primitive is deliberately general.
- Eval harness (see ``docs/EVALS.md`` section 2.8) reads the
  structured ``AgentContext`` directly to grade binary criteria
  (attendees identified, superseded facts omitted, etc.) without
  parsing rendered prose.

``AgentContext.render()`` produces a markdown string with tier-aware
sectioning, inline provenance citations, and stable sort order so
host agents can feed it directly into their LLM prompt.
"""

from memory_mission.synthesis.compile import compile_agent_context
from memory_mission.synthesis.context import (
    AgentContext,
    AttendeeContext,
    DoctrineContext,
)
from memory_mission.synthesis.individual_boot import (
    ActiveThread,
    BootPreference,
    Commitment,
    EntityState,
    IndividualBootContext,
    ProjectStatus,
    RecentDecision,
    compile_individual_boot_context,
)

__all__ = [
    "ActiveThread",
    "AgentContext",
    "AttendeeContext",
    "BootPreference",
    "Commitment",
    "DoctrineContext",
    "EntityState",
    "IndividualBootContext",
    "ProjectStatus",
    "RecentDecision",
    "compile_agent_context",
    "compile_individual_boot_context",
]
