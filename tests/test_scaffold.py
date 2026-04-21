"""Step 1 smoke test: package imports, version is set, CLI is wired, config loads."""

from typer.testing import CliRunner

import memory_mission
from memory_mission.cli import app
from memory_mission.config import Settings


def test_package_imports_with_version() -> None:
    """Package imports cleanly and exposes a version string."""
    assert memory_mission.__version__
    assert isinstance(memory_mission.__version__, str)


def test_cli_version_command() -> None:
    """`memory-mission version` runs without error and prints the version."""
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert memory_mission.__version__ in result.stdout


def test_cli_info_command(tmp_path) -> None:
    """`memory-mission info` prints loaded config."""
    runner = CliRunner()
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "memory-mission" in result.stdout


def test_settings_defaults_load(settings: Settings) -> None:
    """Settings load with reasonable defaults and can be overridden."""
    assert settings.wiki_root.name == "wiki"
    assert settings.observability_root.name == ".observability"
    assert settings.llm_provider == "anthropic"


def test_stub_modules_import() -> None:
    """All component stubs import without errors (import-time sanity)."""
    import memory_mission.durable  # noqa: F401
    import memory_mission.ingestion  # noqa: F401
    import memory_mission.memory  # noqa: F401
    import memory_mission.middleware  # noqa: F401
    import memory_mission.observability  # noqa: F401
    import memory_mission.runtime  # noqa: F401
    import memory_mission.workflows  # noqa: F401
