"""BrainEngine interface — pluggable storage for memory layer.

TODO (Step 6): Port from GBrain src/core/engine.ts:
- Lifecycle: connect, disconnect, init_schema, transactions
- Page ops: CRUD with slug-based retrieval + filtering
- Search: keyword + vector + embedding retrieval
- Content: chunk upsert/retrieval/deletion tied to pages
- Graph: bidirectional linking with context + traversal
- Metadata: tag management, timeline entries
- Raw storage: sidecar persistence with source labeling
- Versioning: page versions + revert
- Monitoring: stats, health, ingest logs
- Sync: slug renames with automatic link rewriting
- Config: key-value config storage

Two implementations:
- PGLiteEngine (embedded Postgres, V1 default)
- PostgresEngine (Supabase-compatible, for scale)

Bidirectional migration: memory-mission migrate --to postgres|pglite.
"""
