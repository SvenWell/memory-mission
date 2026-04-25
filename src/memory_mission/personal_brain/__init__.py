"""Per-employee personal-plane substrate.

Public surface (post ADR-0004 adoption):

- ``backend`` — ``PersonalMemoryBackend`` Protocol + Pydantic types
  (``PersonalHit`` / ``Citation`` / ``EntityRef`` / ``WorkingContext`` /
  ``CandidateFact`` / ``IngestResult``). Substrate-agnostic contract every
  personal-plane impl satisfies.
- ``mempalace_adapter`` — ``MemPalaceAdapter`` implementing the Protocol
  over per-employee MemPalace palaces at
  ``firm/personal/<employee_id>/mempalace/``. Ships chromadb-backed
  hybrid retrieval + closet/drawer storage.

The earlier four-layer model (``working`` / ``episodic`` / ``lessons`` /
``preferences``) was deleted on adoption — those modules were never
wired to a production caller, and the substrate that subsumed them
(MemPalace) has equivalent or better primitives. See ADR-0004 for the
decision history.
"""

from memory_mission.personal_brain.backend import (
    CandidateFact,
    Citation,
    EntityRef,
    IngestResult,
    PersonalHit,
    PersonalMemoryBackend,
    WorkingContext,
)
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
