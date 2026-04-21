"""Component 0.4 — Observability + Audit Trail.

FOUNDATIONAL. Ships in Phase 1 Step 2. Every other component writes to this log.

Records:
- Every extraction decision (source, facts, confidence, LLM prompt + model)
- Every promotion decision (candidate, score, threshold gates, reviewer)
- Every retrieval (query, pages loaded, budget, response, latency)
- Every draft/brief generated (context, output, user action)

Immutable append-only JSON Lines per firm. 7-year retention for compliance.
"""
