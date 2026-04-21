"""Components 0.1 (Employee Memory) + 0.2 (Firm Wiki).

Ships in Phase 1 Step 6.

Storage model:
- Filesystem (git-backed .md files) = source of truth. Obsidian-compatible.
- In-memory engine for V1 / tests. Postgres + pgvector backend swaps in
  later behind the same ``BrainEngine`` Protocol.

Adopts GBrain's patterns (ported from TypeScript):
- Compiled truth + timeline page format (``pages.py``)
- MECE directory schema, wealth-adapted (``schema.py``)
- BrainEngine interface + InMemoryEngine (``engine.py``)
- Hybrid search with RRF fusion (Step 6b, on top of this interface)

Ports MemPalace's temporal knowledge graph for entity-relationship triples
(Step 6b — schema + code come from reading MemPalace, not installing it).
"""

from memory_mission.memory.engine import (
    BrainEngine,
    EngineStats,
    InMemoryEngine,
    SearchHit,
    SearchTier,
)
from memory_mission.memory.pages import (
    Page,
    PageFrontmatter,
    TimelineEntry,
    new_page,
    parse_page,
    render_page,
)
from memory_mission.memory.schema import (
    CORE_DOMAINS,
    is_valid_domain,
    page_path,
    raw_sidecar_path,
    validate_domain,
)

__all__ = [
    "CORE_DOMAINS",
    "BrainEngine",
    "EngineStats",
    "InMemoryEngine",
    "Page",
    "PageFrontmatter",
    "SearchHit",
    "SearchTier",
    "TimelineEntry",
    "is_valid_domain",
    "new_page",
    "page_path",
    "parse_page",
    "raw_sidecar_path",
    "render_page",
    "validate_domain",
]
