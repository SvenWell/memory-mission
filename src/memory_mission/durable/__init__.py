"""Component 0.6 — Durable Execution + Checkpointing.

FOUNDATIONAL. Ships in Phase 1 Step 3.

Long-running operations (backfill = 24h+, dreaming loop, HITL pauses) must
survive worker crashes and deploys. Every super-step writes a checkpoint
keyed by thread_id. On crash, another worker picks up from the latest
checkpoint — no re-run from scratch.
"""
