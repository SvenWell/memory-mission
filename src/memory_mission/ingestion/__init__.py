"""Components 1.1 (Backfill), 1.2 (Real-Time Extraction), 1.3 (Connectors).

Ships in Phase 1 Steps 5-7.

- Connectors (1.3): Composio-backed, harness threads observability + PII +
  durability through every call. Granola + Gmail factories in V1.
- Backfill (1.1): primitives in ``staging.py`` + ``mentions.py``. The
  backfill workflow itself lives as a Hermes skill (markdown), not Python
  code — primitives compose into agent-driven workflows.
- Extraction (1.2): 6-vector framework (Supermemory-inspired), domain-adapted.
"""

from memory_mission.ingestion.mentions import (
    TIER_THRESHOLDS,
    MentionRecord,
    MentionTracker,
    Tier,
    tier_for_count,
)
from memory_mission.ingestion.staging import StagedItem, StagingWriter

__all__ = [
    "TIER_THRESHOLDS",
    "MentionRecord",
    "MentionTracker",
    "StagedItem",
    "StagingWriter",
    "Tier",
    "tier_for_count",
]
