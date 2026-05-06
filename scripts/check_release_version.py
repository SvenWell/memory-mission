"""Verify a candidate git tag matches the version declared in pyproject.toml.

Run as `python scripts/check_release_version.py vX.Y.Z`. Exits 0 on match,
non-zero on mismatch. Designed to be wired into a `make tag` target so a
mismatched tag (the v0.1.5 bug) cannot be cut.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def read_pyproject_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    if not isinstance(project, dict) or "version" not in project:
        raise KeyError(f"no [project].version in {pyproject_path}")
    version = project["version"]
    if not isinstance(version, str):
        raise TypeError(f"[project].version in {pyproject_path} is not a string")
    return version


def normalize_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def check_release_version(tag: str, pyproject_path: Path) -> None:
    pyproject_version = read_pyproject_version(pyproject_path)
    candidate_version = normalize_tag(tag)
    if pyproject_version != candidate_version:
        raise SystemExit(
            f"version mismatch: tag {tag!r} -> {candidate_version!r}, "
            f"but {pyproject_path.name} says {pyproject_version!r}. "
            f"Bump [project].version in {pyproject_path.name} before tagging."
        )


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: check_release_version.py vX.Y.Z")
    tag = argv[1]
    repo_root = Path(__file__).resolve().parent.parent
    check_release_version(tag, repo_root / "pyproject.toml")
    print(f"OK: tag {tag} matches pyproject.toml version")


if __name__ == "__main__":
    main(sys.argv)
