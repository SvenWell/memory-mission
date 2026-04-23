"""Shared pytest fixtures."""

import pytest

from memory_mission.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings instance scoped to a temp directory for isolation."""
    return Settings(
        wiki_root=tmp_path / "wiki",
        observability_root=tmp_path / ".observability",
    )
