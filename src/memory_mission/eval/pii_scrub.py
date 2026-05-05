"""Conservative PII scrubbing for eval-capture args.

Free-text user-content fields (``task_hint``, ``query``) get redacted
to ``{"_redacted": True, "length": N, "hash": <16-char sha256 prefix>}``.
The hash lets replay match the SAME redacted query across runs without
storing the original text.

Entity-name args (``name``, ``thread_id``, ``commitment_id``, etc.)
pass through unchanged — they ARE the queries, and the eval_captures
store lives in the same per-employee fence as the production KG that
already references those names. Hashing them would break replay.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Args that contain free-text user content. Redact to length + hash.
_FREE_TEXT_FIELDS = frozenset({"task_hint", "query", "summary", "description"})


def scrub_args_for_capture(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``args`` with free-text fields redacted.

    Pass-through fields (entity names, statuses, dates, IDs) keep their
    original values so replay can re-run the same query.
    """
    del tool_name  # reserved for future per-tool overrides
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key in _FREE_TEXT_FIELDS and isinstance(value, str):
            out[key] = _redact_free_text(value)
        else:
            out[key] = value
    return out


def _redact_free_text(text: str) -> dict[str, Any]:
    """Replace free-text content with length + 16-char hash."""
    return {
        "_redacted": True,
        "length": len(text),
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }
