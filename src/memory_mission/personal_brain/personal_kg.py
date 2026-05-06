"""Per-employee temporal knowledge graph (ADR-0013).

Wraps a per-employee instance of ``memory.knowledge_graph.KnowledgeGraph``
with employee-scope auto-application + identity-resolver bridge to the
firm-wide ``IdentityResolver``. The personal KG sits alongside MemPalace
(ADR-0004): MemPalace handles vector recall + citations; the personal
KG handles temporal entity-state + Bayesian corroboration on personal
facts.

Cross-employee leak is structurally blocked at two layers:

1. **Path isolation.** Each employee has their own SQLite DB file at
   ``<firm_root>/personal/<employee_id>/personal_kg.db``. Validated via
   ``validate_employee_id`` at construction (rejects ``../escape``,
   ``/tmp/escape``, etc. before any side effect — same hardening as
   MemPalaceAdapter).

2. **Scope auto-application.** Every write applies
   ``scope=f"employee_{employee_id}"`` automatically; every read
   applies ``viewer_scopes={f"employee_{employee_id}"}``. Even if a
   future consolidation ever puts multiple employees in one DB,
   the scope filter would still block cross-leak.

Layer 1 alone is sufficient today; layer 2 is defense in depth.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from memory_mission.identity.base import IdentityResolver
from memory_mission.memory.knowledge_graph import (
    Direction,
    Entity,
    KnowledgeGraph,
    Triple,
)
from memory_mission.memory.schema import validate_employee_id
from memory_mission.memory.tiers import DEFAULT_TIER, Tier

if TYPE_CHECKING:
    from memory_mission.personal_brain.observations import PersonalObservation


def employee_scope(employee_id: str) -> str:
    """Return the canonical personal-plane scope string for an employee.

    Format: ``employee_<validated_employee_id>``. Validated via
    ``validate_employee_id`` so unsafe shapes (path traversal,
    null bytes, etc.) are rejected before any DB or filesystem
    side effect.
    """
    return f"employee_{validate_employee_id(employee_id)}"


class PersonalKnowledgeGraph:
    """Temporal KG instance scoped to a single employee.

    Wraps the existing ``KnowledgeGraph`` substrate. All writes are
    auto-tagged with the employee's scope; all reads filter by the
    same scope. The underlying DB file is per-employee; cross-employee
    queries are structurally impossible.

    Construct via :py:meth:`for_employee` rather than the raw
    constructor when working from a firm root — that helper computes
    the standard path layout (`firm/personal/<emp>/personal_kg.db`)
    and validates the employee_id.
    """

    def __init__(
        self,
        *,
        db_path: Path | str,
        employee_id: str,
        identity_resolver: IdentityResolver,
    ) -> None:
        self._employee_id = validate_employee_id(employee_id)
        self._scope = f"employee_{self._employee_id}"
        self._scope_set: frozenset[str] = frozenset({self._scope})
        self._kg = KnowledgeGraph(db_path)
        self._identity_resolver = identity_resolver

    @classmethod
    def for_employee(
        cls,
        *,
        firm_root: Path | str,
        employee_id: str,
        identity_resolver: IdentityResolver,
    ) -> PersonalKnowledgeGraph:
        """Construct from a firm root + employee_id, using the standard path layout.

        Path: ``<firm_root>/personal/<validated_employee_id>/personal_kg.db``.
        """
        safe_employee_id = validate_employee_id(employee_id)
        db_path = Path(firm_root) / "personal" / safe_employee_id / "personal_kg.db"
        return cls(
            db_path=db_path,
            employee_id=safe_employee_id,
            identity_resolver=identity_resolver,
        )

    # ---------- Identity ----------

    @property
    def employee_id(self) -> str:
        """The validated employee_id this KG is scoped to."""
        return self._employee_id

    @property
    def scope(self) -> str:
        """The canonical scope string used on every triple in this KG."""
        return self._scope

    @property
    def identity_resolver(self) -> IdentityResolver:
        """The firm-wide IdentityResolver, shared across personal + firm planes."""
        return self._identity_resolver

    # ---------- Lifecycle ----------

    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        self._kg.close()

    def __enter__(self) -> PersonalKnowledgeGraph:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self.close()

    # ---------- Entity ops (delegates) ----------

    def add_entity(
        self,
        name: str,
        *,
        entity_type: str = "unknown",
        properties: dict[str, Any] | None = None,
    ) -> Entity:
        """Insert / update an entity in the per-employee KG.

        Entities are not scope-tagged in the underlying schema (entities
        are global; scope lives on triples). The per-employee KG file
        isolation is what keeps entities-from-employee-A separate from
        entities-from-employee-B.
        """
        return self._kg.add_entity(name, entity_type=entity_type, properties=properties)

    # ---------- Triple ops (auto-scoped) ----------

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        valid_from: date | None = None,
        valid_to: date | None = None,
        confidence: float = 1.0,
        source_closet: str | None = None,
        source_file: str | None = None,
        tier: Tier = DEFAULT_TIER,
    ) -> Triple:
        """Insert a personal triple. Scope is auto-applied as employee_<id>.

        ``scope`` is intentionally NOT a parameter — personal-KG triples
        cannot escape their employee scope by construction. Cross-employee
        promotion routes through the standard ``create_proposal`` →
        ``review-proposals`` → firm KG bridge, not by re-scoping a
        personal triple.
        """
        return self._kg.add_triple(
            subject,
            predicate,
            obj,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            source_closet=source_closet,
            source_file=source_file,
            tier=tier,
            scope=self._scope,
        )

    def corroborate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        confidence: float,
        source_closet: str | None = None,
        source_file: str | None = None,
    ) -> Triple | None:
        """Bayesian corroborate a currently-true personal triple.

        Returns ``None`` if no matching currently-true triple exists
        (caller falls back to ``add_triple``). Raises ``ValueError`` if
        an existing triple's scope is somehow not this employee's scope
        — that would indicate a cross-leak the wrapper is designed to
        block, so we fail loudly.
        """
        return self._kg.corroborate(
            subject,
            predicate,
            obj,
            confidence=confidence,
            source_closet=source_closet,
            source_file=source_file,
            scope=self._scope,
        )

    def find_current_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
    ) -> Triple | None:
        """Return the currently-true personal triple, or None.

        Defends against cross-leak: if the underlying KG returns a
        triple whose scope isn't this employee's scope, returns
        ``None`` (i.e., behaves as if no match exists). This can only
        happen if the per-employee DB has been polluted by a path-
        isolation bug — in normal operation every triple in this DB
        already carries the employee scope.
        """
        triple = self._kg.find_current_triple(subject, predicate, obj)
        if triple is None:
            return None
        if triple.scope != self._scope:
            return None
        return triple

    def invalidate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        ended: date | None = None,
    ) -> int:
        """End validity for a currently-true personal triple.

        Underlying ``invalidate`` matches on (subject, predicate, obj)
        without a scope filter; safe in this wrapper because the
        per-employee DB only contains this employee's triples. Returns
        the number of triples updated.
        """
        return self._kg.invalidate(subject, predicate, obj, ended=ended)

    # ---------- Queries (auto-filtered by employee scope) ----------

    def query_entity(
        self,
        name: str,
        *,
        as_of: date | None = None,
        direction: Direction = "outgoing",
    ) -> list[Triple]:
        """Triples involving ``name`` in this employee's plane.

        ``viewer_scopes`` is auto-applied as ``{employee_<id>}`` —
        callers cannot accidentally widen the scope beyond their own
        employee.
        """
        return self._kg.query_entity(
            name,
            as_of=as_of,
            direction=direction,
            viewer_scopes=self._scope_set,
        )

    def query_relationship(
        self,
        predicate: str,
        *,
        as_of: date | None = None,
    ) -> list[Triple]:
        """All personal triples using ``predicate``."""
        return self._kg.query_relationship(
            predicate,
            as_of=as_of,
            viewer_scopes=self._scope_set,
        )

    def query_observations(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        since: date | None = None,
        now: datetime | None = None,
    ) -> list[PersonalObservation]:
        """Currently-true observations on this employee's plane (ADR-0016).

        Auto-applies the per-employee viewer scope so callers cannot
        accidentally widen beyond the employee's own data.
        """
        return self._kg.query_observations(
            subject=subject,
            predicate=predicate,
            since=since,
            viewer_scopes=self._scope_set,
            now=now,
        )

    def timeline(self, entity_name: str | None = None) -> list[Triple]:
        """Personal triples ordered by ``valid_from`` (NULLs first)."""
        return self._kg.timeline(entity_name, viewer_scopes=self._scope_set)


@contextmanager
def open_personal_kg(
    *,
    firm_root: Path | str,
    employee_id: str,
    identity_resolver: IdentityResolver,
) -> Iterator[PersonalKnowledgeGraph]:
    """Context manager that opens + closes a per-employee KG."""
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=firm_root,
        employee_id=employee_id,
        identity_resolver=identity_resolver,
    )
    try:
        yield pkg
    finally:
        pkg.close()


__all__ = [
    "PersonalKnowledgeGraph",
    "employee_scope",
    "open_personal_kg",
]
