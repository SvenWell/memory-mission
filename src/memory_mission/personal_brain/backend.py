"""``PersonalMemoryBackend`` Protocol — the contract every personal-substrate impl must satisfy.

The active implementation is ``MemPalaceAdapter``. The Protocol keeps
the rest of the system substrate-agnostic:

- A minimal in-house impl built on the existing ``BrainEngine`` +
  ``KnowledgeGraph`` with plane scoping (``Plane="personal"`` +
  ``employee_id``) remains a fallback option if MemPalace is ever
  superseded.
- ``MemPalaceAdapter`` wraps ``mempalace`` per employee.

Either way, the rest of the system (skills, MCP tools, extraction →
proposal bridge) codes against the Protocol — never against a specific
substrate. A future substrate swap should happen behind this Protocol
without touching consumers.

Acceptance scenarios live in ``tests/fixtures/pilot_tasks/`` and the
parameterized contract tests in ``tests/test_personal_backend_contract.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.ingestion.roles import NormalizedSourceItem

if TYPE_CHECKING:
    from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


class Citation(BaseModel):
    """Source-aware breadcrumb back to the underlying source object.

    Every personal-substrate query result ships with citations so the
    host agent can ground its prose in real source artefacts (the email,
    the calendar event, the transcript line). No untraceable claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_role: str
    concrete_app: str
    external_id: str
    container_id: str | None = None
    url: str | None = None
    modified_at: datetime
    excerpt: str | None = None


class PersonalHit(BaseModel):
    """One hit returned by ``PersonalMemoryBackend.query``.

    ``hit_id`` is opaque to the caller — used by ``citations(hit_id)``
    if a follow-up call needs to fetch all source breadcrumbs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hit_id: str
    title: str
    snippet: str
    score: float
    cited_at: datetime
    citations: list[Citation] = Field(default_factory=list)


class IngestResult(BaseModel):
    """Outcome of ``PersonalMemoryBackend.ingest`` for one source item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items_ingested: int
    items_skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)


class EntityRef(BaseModel):
    """Stable entity reference — bridges to firm KG via the shared ``IdentityResolver``.

    Personal substrate references the SAME ``p_<token>`` / ``o_<token>``
    IDs the firm KG uses. Identity is per-firm infrastructure; both
    planes resolve to the same nodes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    canonical_name: str | None = None
    identifiers: list[str] = Field(default_factory=list)


class WorkingContext(BaseModel):
    """Pre-task private context for the host agent.

    The "what should I remember before this interaction?" payload —
    relevant prior hits + the employee's preferences + their open
    commitments. Composed by the personal substrate, consumed by the
    host agent's LLM.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: str
    task: str
    relevant_hits: list[PersonalHit] = Field(default_factory=list)
    employee_preferences: dict[str, str] = Field(default_factory=dict)
    open_commitments: list[str] = Field(default_factory=list)


class CandidateFact(BaseModel):
    """A fact the personal substrate surfaces upward for proposal flow.

    The bridge into firm truth: personal-substrate signal becomes a
    firm proposal candidate. ``CandidateFact.payload`` matches the
    ``ExtractedFact`` discriminated-union shape — extraction code can
    consume it without conversion.

    Surfacing is NOT promoting. The proposal pipeline still gates the
    write. ``candidate_facts()`` is signal generation, not authority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: str
    fact_kind: str
    payload: dict[str, Any]
    citations: list[Citation] = Field(default_factory=list)
    confidence: float
    surfaced_at: datetime


@runtime_checkable
class PersonalMemoryBackend(Protocol):
    """Contract for the personal-plane memory substrate.

    Two implementations target this Protocol (one wins at the P1
    binary gate). The Protocol surface is the firm pilot's stable
    contract — connectors, MCP tools, extraction bridge, and skills
    all code against this, never against a specific impl.

    Every method takes ``employee_id`` explicitly and must enforce
    employee-private isolation: a query under one ``employee_id``
    must never return data ingested under another. Tests assert.
    """

    def ingest(
        self,
        item: NormalizedSourceItem,
        *,
        employee_id: str,
    ) -> IngestResult:
        """Ingest one normalized source item into the employee's substrate.

        Connector-emitted shape lands here. Returns counts so backfill
        skills can report progress.
        """
        ...

    def query(
        self,
        question: str,
        *,
        employee_id: str,
        limit: int = 10,
    ) -> list[PersonalHit]:
        """Hybrid search the employee's prior interactions.

        Must return ``PersonalHit``s with at least one ``Citation`` per
        hit unless the substrate is intentionally surfacing a derived
        item (e.g., an open-commitment fact synthesized from multiple
        sources). Source-grounded retrieval is the contract.
        """
        ...

    def citations(
        self,
        hit_id: str,
        *,
        employee_id: str,
    ) -> list[Citation]:
        """Fetch all source breadcrumbs for a previously-returned hit."""
        ...

    def resolve_entity(
        self,
        identifiers: list[str],
        *,
        employee_id: str,
    ) -> EntityRef:
        """Resolve typed identifiers (``email:..``, ``linkedin:..``) to the stable entity ID.

        Bridges into the firm-wide ``IdentityResolver`` — both planes
        speak the same ``p_<token>`` / ``o_<token>`` vocabulary.
        """
        ...

    def working_context(
        self,
        *,
        employee_id: str,
        task: str,
    ) -> WorkingContext:
        """Pre-task private context for the host agent.

        Returns relevant prior hits + the employee's preferences + their
        open commitments. The "what should I remember before drafting
        this email / preparing this meeting / making this call?" payload.
        """
        ...

    def candidate_facts(
        self,
        *,
        employee_id: str,
        since: datetime | None = None,
    ) -> Iterable[CandidateFact]:
        """Surface candidate facts upward for the proposal flow.

        The bridge into firm truth. Returns ``CandidateFact``s whose
        ``payload`` matches ``ExtractedFact`` discriminator shape —
        extraction code consumes without conversion.

        Does NOT promote. The proposal pipeline gates every firm-plane
        write; this method only surfaces signal.
        """
        ...

    def personal_kg(self, employee_id: str) -> PersonalKnowledgeGraph:
        """Return the per-employee temporal KG (ADR-0013).

        Backends maintain one ``PersonalKnowledgeGraph`` per employee
        (lazily constructed, cached). The returned KG auto-applies
        ``scope=employee_<id>`` on every write and
        ``viewer_scopes={employee_<id>}`` on every read — cross-employee
        leak is structurally impossible.

        The KG sits alongside the substrate's retrieval layer
        (MemPalace's ChromaDB index in the default impl). Retrieval
        answers "did I see something about X?"; the KG answers "what
        do I currently believe about X, when did it become true,
        and what evidence supports it?".

        See ADR-0013 for the architectural rationale and the
        relationship to ADR-0004 (MemPalace adoption).
        """
        ...


__all__ = [
    "CandidateFact",
    "Citation",
    "EntityRef",
    "IngestResult",
    "PersonalHit",
    "PersonalMemoryBackend",
    "WorkingContext",
]
