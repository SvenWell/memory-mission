"""Components 0.1 (Employee Memory) + 0.2 (Firm Wiki).

Ships in Phase 1 Step 6.

Storage model:
- Filesystem (git-backed .md files) = source of truth. Obsidian-compatible.
- PGLite/Postgres + pgvector = derived retrieval index, synced from filesystem.

Adopts GBrain's patterns (ported from TypeScript):
- Compiled truth + timeline page format
- MECE directory schema
- BrainEngine interface (pluggable PGLite <-> Postgres)
- Hybrid search with RRF fusion (vector + keyword)
- Enrichment tiers (mention-frequency auto-escalation)

Integrates MemPalace's temporal knowledge graph for entity-relationship triples.
"""
