"""Components 1.1 (Backfill), 1.2 (Real-Time Extraction), 1.3 (Connectors).

Ships in Phase 1 Steps 5-7. P2 adds the capability-binding manifest +
normalized source-item envelope so connectors emit a single typed shape
into staging / personal substrate / proposal review (see ADR-0007).

- Connectors (1.3): Composio-backed, harness threads observability + PII +
  durability through every call. Granola + Gmail + Drive factories in V1.
- Backfill (1.1): primitives in ``staging.py`` + ``mentions.py``. The
  backfill workflow itself lives as a Hermes skill (markdown), not Python
  code — primitives compose into agent-driven workflows.
- Extraction (1.2): 6-vector framework (Supermemory-inspired), domain-adapted.
- Capability binding (P2): ``systems_manifest.py`` loads ``firm/systems.yaml``
  and exposes ``map_visibility`` for fail-closed external-visibility → firm-
  scope mapping. ``roles.py`` defines the ``NormalizedSourceItem`` envelope.
"""

from memory_mission.ingestion.mentions import (
    TIER_THRESHOLDS,
    MentionRecord,
    MentionTracker,
    Tier,
    tier_for_count,
)
from memory_mission.ingestion.roles import ConnectorRole, NormalizedSourceItem
from memory_mission.ingestion.staging import StagedItem, StagingWriter
from memory_mission.ingestion.systems_manifest import (
    RoleBinding,
    SystemsManifest,
    VisibilityMappingError,
    VisibilityRule,
    load_systems_manifest,
    map_visibility,
)

__all__ = [
    "TIER_THRESHOLDS",
    "ConnectorRole",
    "MentionRecord",
    "MentionTracker",
    "NormalizedSourceItem",
    "RoleBinding",
    "StagedItem",
    "StagingWriter",
    "SystemsManifest",
    "Tier",
    "VisibilityMappingError",
    "VisibilityRule",
    "load_systems_manifest",
    "map_visibility",
    "tier_for_count",
]
