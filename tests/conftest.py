"""Shared pytest fixtures."""

import os

# Force pure-Python protobuf parsing before any test module imports
# chromadb/opentelemetry transitively. The shipped chromadb pulls
# proto descriptors that fail under stock protoc-generated C++ bindings
# in current .venvs; the pure-Python path is the supported fallback.
# Set here so plain ``make check`` works without an env override.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import pytest  # noqa: E402

from memory_mission.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings instance scoped to a temp directory for isolation."""
    return Settings(
        wiki_root=tmp_path / "wiki",
        observability_root=tmp_path / ".observability",
    )
