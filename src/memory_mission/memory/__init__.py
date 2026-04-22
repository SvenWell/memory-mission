"""Components 0.1 (Employee Memory) + 0.2 (Firm Wiki).

Ships in Phase 1 Step 6.

Storage model:
- Filesystem (git-backed .md files) = source of truth. Obsidian-compatible.
- In-memory engine for V1 / tests. Postgres + pgvector backend swaps in
  later behind the same ``BrainEngine`` Protocol.

Adopts GBrain's patterns (ported from TypeScript):
- Compiled truth + timeline page format (``pages.py``)
- MECE directory schema — vertical-neutral core (``schema.py``)
- BrainEngine interface + InMemoryEngine (``engine.py``)
- Hybrid search with RRF fusion (Step 6b, on top of this interface)

Ports MemPalace's temporal knowledge graph for entity-relationship triples
(Step 6b — schema + code come from reading MemPalace, not installing it).
"""

from memory_mission.memory.engine import (
    BrainEngine,
    EngineStats,
    InMemoryEngine,
    PageKey,
    SearchHit,
    SearchTier,
)
from memory_mission.memory.knowledge_graph import (
    CORROBORATION_CAP,
    Direction,
    Entity,
    GraphStats,
    KnowledgeGraph,
    Triple,
    TripleSource,
)
from memory_mission.memory.pages import (
    Page,
    PageFrontmatter,
    TimelineEntry,
    new_page,
    parse_page,
    render_page,
)
from memory_mission.memory.salience import (
    MAX_RECENCY,
    NEUTRAL_SCORE,
    RECENCY_DECAY_PER_DAY,
    RECURRENCE_CAP,
    salience_score,
)
from memory_mission.memory.schema import (
    CORE_DOMAINS,
    Plane,
    curated_root,
    is_valid_domain,
    page_path,
    plane_root,
    raw_sidecar_path,
    staging_source_dir,
    validate_domain,
    validate_employee_id,
)
from memory_mission.memory.search import (
    COMPILED_TRUTH_BOOST,
    RRF_K,
    VECTOR_RRF_BLEND,
    EmbeddingProvider,
    HashEmbedder,
    cosine_similarity,
    rrf_fuse,
)
from memory_mission.memory.text import STOPWORDS, jaccard, word_set

__all__ = [
    "COMPILED_TRUTH_BOOST",
    "CORE_DOMAINS",
    "CORROBORATION_CAP",
    "MAX_RECENCY",
    "NEUTRAL_SCORE",
    "RECENCY_DECAY_PER_DAY",
    "RECURRENCE_CAP",
    "RRF_K",
    "STOPWORDS",
    "VECTOR_RRF_BLEND",
    "BrainEngine",
    "Direction",
    "EmbeddingProvider",
    "EngineStats",
    "Entity",
    "GraphStats",
    "HashEmbedder",
    "InMemoryEngine",
    "KnowledgeGraph",
    "Page",
    "PageFrontmatter",
    "PageKey",
    "Plane",
    "SearchHit",
    "SearchTier",
    "TimelineEntry",
    "Triple",
    "TripleSource",
    "cosine_similarity",
    "curated_root",
    "is_valid_domain",
    "jaccard",
    "new_page",
    "page_path",
    "parse_page",
    "plane_root",
    "raw_sidecar_path",
    "render_page",
    "rrf_fuse",
    "salience_score",
    "staging_source_dir",
    "validate_domain",
    "validate_employee_id",
    "word_set",
]
