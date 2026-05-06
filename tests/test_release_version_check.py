"""Tests for scripts/check_release_version.py — guards against the v0.1.5 metadata bug."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_version.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script_module():
    return _load_module()


@pytest.fixture
def fake_pyproject(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "memory-mission"\nversion = "0.1.6"\n')
    return path


def test_matching_version_passes(script_module, fake_pyproject: Path) -> None:
    script_module.check_release_version("v0.1.6", fake_pyproject)


def test_matching_version_without_v_prefix_passes(script_module, fake_pyproject: Path) -> None:
    script_module.check_release_version("0.1.6", fake_pyproject)


def test_mismatched_version_raises(script_module, fake_pyproject: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        script_module.check_release_version("v0.1.5", fake_pyproject)
    assert "version mismatch" in str(exc.value)
    assert "v0.1.5" in str(exc.value)
    assert "0.1.6" in str(exc.value)


def test_missing_version_raises(tmp_path: Path, script_module) -> None:
    bad = tmp_path / "pyproject.toml"
    bad.write_text('[project]\nname = "memory-mission"\n')
    with pytest.raises(KeyError):
        script_module.read_pyproject_version(bad)


def test_real_pyproject_self_check(script_module) -> None:
    real_version = script_module.read_pyproject_version(REPO_ROOT / "pyproject.toml")
    assert real_version
    script_module.check_release_version(f"v{real_version}", REPO_ROOT / "pyproject.toml")


def test_main_argv_too_short_raises(script_module) -> None:
    with pytest.raises(SystemExit):
        script_module.main(["check_release_version.py"])


def test_normalize_tag_strips_v_prefix(script_module) -> None:
    assert script_module.normalize_tag("v0.1.6") == "0.1.6"
    assert script_module.normalize_tag("0.1.6") == "0.1.6"
