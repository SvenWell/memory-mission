"""Per-employee personal-plane substrate.

Public surface (post ADR-0004 adoption):

- ``backend`` — ``PersonalMemoryBackend`` Protocol + Pydantic types
  (``PersonalHit`` / ``Citation`` / ``EntityRef`` / ``WorkingContext`` /
  ``CandidateFact`` / ``IngestResult``). Substrate-agnostic contract every
  personal-plane impl satisfies.
- ``mempalace_adapter`` — ``MemPalaceAdapter`` implementing the Protocol
  over per-employee MemPalace palaces at
  ``firm/personal/<employee_id>/mempalace/``. Ships chromadb-backed
  hybrid retrieval + closet/drawer storage. Imported lazily so the
  Chroma/protobuf transitive deps don't load when only the Protocol +
  per-employee KG are needed (e.g. plain ``pytest`` collection).

The earlier four-layer model (``working`` / ``episodic`` / ``lessons`` /
``preferences``) was deleted on adoption — those modules were never
wired to a production caller, and the substrate that subsumed them
(MemPalace) has equivalent or better primitives. See ADR-0004 for the
decision history.
"""

from typing import TYPE_CHECKING, Any

from memory_mission.personal_brain.backend import (
    CandidateFact,
    Citation,
    EntityRef,
    IngestResult,
    PersonalHit,
    PersonalMemoryBackend,
    WorkingContext,
)

if TYPE_CHECKING:
    from memory_mission.personal_brain.mempalace_adapter import MemPalaceAdapter

__all__ = [
    "CandidateFact",
    "Citation",
    "EntityRef",
    "IngestResult",
    "MemPalaceAdapter",
    "PersonalHit",
    "PersonalMemoryBackend",
    "WorkingContext",
]


def __getattr__(name: str) -> Any:
    """Lazy import for heavy submodules.

    ``MemPalaceAdapter`` pulls chromadb + opentelemetry + protobuf at
    import time. Code that only needs the Protocol (e.g. typing,
    in-memory test fakes, the per-employee KG) shouldn't pay that cost
    or be exposed to the protobuf descriptor mismatch under stock
    .venv installs.
    """
    if name == "MemPalaceAdapter":
        from memory_mission.personal_brain.mempalace_adapter import MemPalaceAdapter

        return MemPalaceAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
