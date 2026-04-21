"""Hybrid search — RRF fusion of vector + keyword + cosine re-scoring.

TODO (Step 6): Port from GBrain src/core/search/hybrid.ts:
- Query expansion (optional callback)
- Keyword search (always runs, tsvector-based)
- Vector search (pgvector HNSW, when embeddings present)
- RRF fusion: score = sum(1 / (RRF_K + rank)) across lists
  - RRF_K = 60 (tunable)
  - COMPILED_TRUTH_BOOST = 2.0
- Cosine re-scoring: final = 0.7 * RRF + 0.3 * cosine(query, chunk)
- Four-layer deduplication
- Auto-detect detail level based on query intent

Three modes:
- search(query) — keyword only, fastest, no embeddings required
- query(question) — hybrid, semantic + keyword
- get(slug) — direct retrieval by known slug
"""
