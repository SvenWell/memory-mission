"""Extraction schema — the typed shape the LLM must return.

Six buckets, adapted from Supermemory's extraction taxonomy and aligned
with how we store things downstream:

- ``identity`` → ``KnowledgeGraph.add_entity`` target (name + type +
  free-form properties).
- ``relationship`` → ``KnowledgeGraph.add_triple`` target (subject,
  predicate, object).
- ``preference`` → a triple with a ``prefers_*`` predicate, OR a
  refinement to a page's compiled-truth zone.
- ``event`` → a ``TimelineEntry`` attached to the named entity's page.
- ``update`` → a pair of ``invalidate`` + ``add_triple`` — replaces a
  prior state with a new one.
- ``open_question`` → flagged for human review; never auto-promoted.

Each fact carries ``confidence`` (0.0-1.0) and ``support_quote`` — the
verbatim text from the source that grounds the claim. "No quote, no
fact" is the extraction rule.

The LLM never touches our code. The host-agent skill runs its own LLM,
gets back a JSON array, parses it with ``ExtractionReport.model_validate``,
and calls ``ingest_facts``. This module is pure schema.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.schema import Plane


class _FactBase(BaseModel):
    """Fields shared by every extracted fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    confidence: float = Field(ge=0.0, le=1.0)
    support_quote: str = Field(
        min_length=1,
        description="Verbatim text from the source that grounds this claim.",
    )


class IdentityFact(_FactBase):
    """An entity is named or described."""

    kind: Literal["identity"] = "identity"
    entity_name: str
    entity_type: str = "unknown"
    properties: dict[str, Any] = Field(default_factory=dict)


class RelationshipFact(_FactBase):
    """Two entities connected by a predicate."""

    kind: Literal["relationship"] = "relationship"
    subject: str
    predicate: str
    object: str


class PreferenceFact(_FactBase):
    """A stated or implied preference."""

    kind: Literal["preference"] = "preference"
    subject: str
    preference: str


class EventFact(_FactBase):
    """A dated thing that happened."""

    kind: Literal["event"] = "event"
    entity_name: str
    event_date: date | None = None
    description: str


class UpdateFact(_FactBase):
    """A prior state is superseded by a new one."""

    kind: Literal["update"] = "update"
    subject: str
    predicate: str
    new_object: str
    supersedes_object: str | None = None
    effective_date: date | None = None


class OpenQuestion(_FactBase):
    """The LLM spotted something but isn't confident — route to human review."""

    kind: Literal["open_question"] = "open_question"
    question: str
    hypothesis: str | None = None


ExtractedFact = Annotated[
    IdentityFact | RelationshipFact | PreferenceFact | EventFact | UpdateFact | OpenQuestion,
    Field(discriminator="kind"),
]


class ExtractionReport(BaseModel):
    """All facts extracted from one source item — one JSON file per report.

    The report lives in fact staging
    (``<wiki_root>/staging/personal/<emp>/.facts/<source>/<source_id>.json``
    or the firm equivalent) until the promotion pipeline (Step 10) reads
    it and produces ``Proposal`` objects for review.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    source_id: str
    target_plane: Plane
    employee_id: str | None = None
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    facts: list[ExtractedFact] = Field(default_factory=list)

    def entity_names(self) -> list[str]:
        """Every distinct entity name referenced by this report.

        Used by ``ingest_facts`` to drive ``MentionTracker.record()``
        calls — one call per unique entity per extraction, not per fact.
        """
        names: dict[str, None] = {}
        for fact in self.facts:
            for name in _fact_entity_names(fact):
                names.setdefault(name, None)
        return list(names)


def _fact_entity_names(fact: ExtractedFact) -> list[str]:
    """Surface every entity name a fact references, for mention tracking."""
    if isinstance(fact, IdentityFact):
        return [fact.entity_name]
    if isinstance(fact, RelationshipFact):
        return [fact.subject, fact.object]
    if isinstance(fact, PreferenceFact):
        return [fact.subject]
    if isinstance(fact, EventFact):
        return [fact.entity_name]
    if isinstance(fact, UpdateFact):
        return [fact.subject]
    # OpenQuestion: no named entity, skip
    return []


__all__ = [
    "EventFact",
    "ExtractedFact",
    "ExtractionReport",
    "IdentityFact",
    "OpenQuestion",
    "PreferenceFact",
    "RelationshipFact",
    "UpdateFact",
]
