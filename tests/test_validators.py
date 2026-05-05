"""Tests for the consolidated path-segment validator."""

from __future__ import annotations

import pytest

from memory_mission.path_safety import (
    SAFE_PATH_SEGMENT_PATTERN,
    validate_path_segment,
)


def test_pattern_accepts_typical_identifiers() -> None:
    for ident in ("alice", "firm-id", "employee_42", "gmail.com", "a-b.c_d", "x"):
        assert SAFE_PATH_SEGMENT_PATTERN.match(ident), ident


def test_pattern_rejects_path_separators_and_nul() -> None:
    for bad in ("a/b", "a\\b", "foo\x00bar", "../escape", "/abs", ""):
        assert not SAFE_PATH_SEGMENT_PATTERN.match(bad), bad


def test_pattern_rejects_leading_dot() -> None:
    # First-char class is [A-Za-z0-9_-], so '.' as first char is rejected.
    assert not SAFE_PATH_SEGMENT_PATTERN.match(".hidden")


def test_pattern_accepts_128_chars_max() -> None:
    assert SAFE_PATH_SEGMENT_PATTERN.match("a" * 128)


def test_pattern_rejects_129_chars() -> None:
    assert not SAFE_PATH_SEGMENT_PATTERN.match("a" * 129)


def test_validate_path_segment_returns_value_on_success() -> None:
    assert validate_path_segment("alice", name="employee_id") == "alice"


def test_validate_path_segment_raises_with_descriptive_message() -> None:
    with pytest.raises(ValueError, match="employee_id"):
        validate_path_segment("with space", name="employee_id")
    with pytest.raises(ValueError, match="alphanumerics"):
        validate_path_segment("a/b", name="whatever")
    with pytest.raises(ValueError, match="whatever"):
        validate_path_segment("", name="whatever")


def test_old_aliases_point_to_shared_pattern() -> None:
    """Each callsite re-exports the shared pattern under its old name."""
    from memory_mission.extraction.ingest import _SAFE_PATH_SEGMENT as INGEST_PAT
    from memory_mission.ingestion.staging import _SAFE_PATH_SEGMENT as STAGING_PAT
    from memory_mission.memory.schema import _SAFE_EMPLOYEE_ID
    from memory_mission.observability.logger import _SAFE_FIRM_ID

    assert _SAFE_EMPLOYEE_ID is SAFE_PATH_SEGMENT_PATTERN
    assert _SAFE_FIRM_ID is SAFE_PATH_SEGMENT_PATTERN
    assert STAGING_PAT is SAFE_PATH_SEGMENT_PATTERN
    assert INGEST_PAT is SAFE_PATH_SEGMENT_PATTERN
