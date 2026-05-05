"""Eval capture + replay for Memory Mission.

Inspired by GBrain's BrainBench-Real (v0.25.0). Opt-in via the
``MM_CONTRIBUTOR_MODE=1`` env var. When enabled, selected ``mm_*`` MCP
tool calls record their args + result signature + latency into a
per-employee SQLite store. The ``mm eval replay`` CLI re-runs captured
queries at HEAD and diffs them against the stored signatures so we can
detect retrieval-quality regressions across substrate changes.

Privacy posture: captures live at the same per-employee fence as the
personal KG (``<root>/personal/<user_id>/eval_captures.sqlite3``).
Free-text args (``task_hint``, ``query``) get redacted to length +
hash; entity-name args pass through because they ARE the queries.
Capture failure NEVER breaks the underlying tool path.
"""

from __future__ import annotations

from memory_mission.eval.captures import (
    CONTRIBUTOR_MODE_ENV,
    EvalCapture,
    EvalCapturesStore,
    is_capture_enabled,
    record_eval_capture,
)

__all__ = [
    "CONTRIBUTOR_MODE_ENV",
    "EvalCapture",
    "EvalCapturesStore",
    "is_capture_enabled",
    "record_eval_capture",
]
