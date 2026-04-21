"""Component 2.1 — Meeting Prep Agent.

TODO (Step 11, Phase 2): Before a client meeting, generate a brief combining
personal memory (employee level) + firm knowledge (wiki level).

GBrain brain-first lookup pattern:
1. Load employee's personal history with this client (from custom employee memory)
2. Navigate firm wiki -> clients/<slug>/profile.md (compiled truth)
3. Follow links -> related deals, market themes
4. Load firm thesis/sector view if relevant
5. Compose brief merging personal + firm context
Token budget: ~8K firm context + employee memory.
"""
