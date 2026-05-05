"""Shared path-safety validators.

Several modules need the same single-path-segment constraint for
operator-controlled ids that become directory or file path components.
Keep the regex in one import-light module so low-level packages such as
observability can use it without importing the memory engine package.
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
