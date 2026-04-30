"""Shared path-safety validators.

Four files in this codebase had independently-defined copies of the same
``r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$"`` regex (``_SAFE_PATH_SEGMENT``
in ``ingestion/staging.py`` and ``extraction/ingest.py``;
``_SAFE_EMPLOYEE_ID`` in ``memory/schema.py``; ``_SAFE_FIRM_ID`` in
``observability/logger.py``). Whenever the constraint changed (e.g.
the 128 → 246 char ceiling needed for Google Calendar recurring-event
ids), all four copies had to be hunted down and patched in lock-step.

This module centralises the pattern + a small validator helper. The
old per-file constants stay as thin aliases for backward compatibility
within their modules; new code should import from here.
"""

from __future__ import annotations

import re
from typing import Final

# 1-128-char path-safe segment. Used for source labels, firm ids,
# employee ids, and any identifier that becomes a single path segment
# in our wiki / staging / observability layout.
SAFE_PATH_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$"
)


def validate_path_segment(value: str, *, name: str = "value") -> str:
    """Reject empty / overlong / path-unsafe identifiers.

    Returns ``value`` unchanged on success so the helper composes into
    fluent-style code (``foo = validate_path_segment(raw, name="foo")``).

    Raises ``ValueError`` with a descriptive message on failure — the
    same shape the four pre-existing inline validators produced.
    """
    if not value or not SAFE_PATH_SEGMENT_PATTERN.match(value):
        raise ValueError(
            f"{name} {value!r} must match {SAFE_PATH_SEGMENT_PATTERN.pattern} "
            "(alphanumerics + ._- only, 1-128 chars, no path separators)"
        )
    return value


__all__ = [
    "SAFE_PATH_SEGMENT_PATTERN",
    "validate_path_segment",
]
