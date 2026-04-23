"""Component 1.2 — Extraction Agent (host-run LLM, our schema + ingest).

Memory Mission ships:
- ``ExtractedFact`` discriminated union (6 buckets: Identity /
  Relationship / Preference / Event / Update / OpenQuestion)
- ``ExtractionReport`` — the JSON shape the LLM returns
- ``ingest_facts()`` — validates + writes to fact staging + updates
  mention tracker
- ``EXTRACTION_PROMPT`` — markdown template the host agent passes to
  its own LLM

What Memory Mission does NOT ship: any LLM SDK. The host agent runs
its own LLM (Claude, GPT, Gemini) with this prompt, parses the JSON,
and calls ``ingest_facts``. Same pattern as Composio for connectors.
"""

from memory_mission.extraction.ingest import (
    ExtractionWriter,
    IngestResult,
    TierCrossing,
    ingest_facts,
)
from memory_mission.extraction.prompts import EXTRACTION_PROMPT
from memory_mission.extraction.schema import (
    EventFact,
    ExtractedFact,
    ExtractionReport,
    IdentityFact,
    OpenQuestion,
    PreferenceFact,
    RelationshipFact,
    UpdateFact,
)

__all__ = [
    "EXTRACTION_PROMPT",
    "EventFact",
    "ExtractedFact",
    "ExtractionReport",
    "ExtractionWriter",
    "IdentityFact",
    "IngestResult",
    "OpenQuestion",
    "PreferenceFact",
    "RelationshipFact",
    "TierCrossing",
    "UpdateFact",
    "ingest_facts",
]
