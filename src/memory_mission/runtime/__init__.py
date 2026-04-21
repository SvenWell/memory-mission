"""Layer 5 — Agent Orchestration Runtime.

Runtime: Hermes Agent (primary). Ironclaw + OpenClaw also available.

This module is a thin adapter that plugs our memory/middleware/observability
layers into Hermes. Hermes handles session durability, learning loop, MCP
protocol. We provide memory + observability + guardrails.
"""
