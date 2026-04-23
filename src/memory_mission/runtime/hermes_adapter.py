"""Hermes Agent adapter.

TODO: Wire Hermes to our layers:
- Hook every action to observability log (0.4)
- Trigger extraction (1.2) after interactions via Hermes periodic nudges
- Expose employee memory + firm wiki via MCP tools
- Honor access control metadata when routing queries
- Integrate middleware chain (0.7) at Hermes' LLM call sites

Hermes native MEMORY.md + USER.md maps cleanly to our concepts:
- MEMORY.md = compiled firm/environment facts (firm wiki extract per employee)
- USER.md = employee's own profile (communication style, preferences)
These become the employee agent's 'soul' injected into every session.
"""
