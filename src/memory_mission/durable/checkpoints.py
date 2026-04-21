"""Super-step checkpointing for durable execution.

TODO (Step 3): Implement:
- Checkpoint table schema (thread_id, step_number, state, timestamp)
- `with checkpoint("step_name") as cp:` context manager
- Worker lease management for crash recovery
- Resume-from-checkpoint API
- Forkable checkpoints (enables time-travel debugging in 0.4)
"""
