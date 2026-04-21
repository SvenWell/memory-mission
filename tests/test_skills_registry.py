"""Tests that the ``skills/`` registry stays internally consistent.

We're not testing skill *behavior* here — skills are markdown executed
by an agent runtime. These tests just enforce that the registry shape
is honest: every skill directory has a ``SKILL.md`` with valid YAML
frontmatter, every line in ``_manifest.jsonl`` parses and matches a
real ``SKILL.md``, and the canonical fields agree between the two.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

REQUIRED_FRONTMATTER_FIELDS = {
    "name",
    "version",
    "triggers",
    "tools",
    "preconditions",
    "constraints",
    "category",
}

VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------- Helpers ----------


def _skill_dirs() -> list[Path]:
    """Each direct child of ``skills/`` that doesn't start with ``_``."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_"))


def _parse_frontmatter(skill_md: Path) -> dict[str, Any]:
    """Return the YAML frontmatter dict from a ``SKILL.md`` file."""
    text = skill_md.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{skill_md} missing leading '---' frontmatter fence")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{skill_md} frontmatter has no closing '---' line")
    fm = yaml.safe_load(text[4:end])
    if not isinstance(fm, dict):
        raise ValueError(f"{skill_md} frontmatter did not parse to a mapping")
    return fm


def _manifest_entries() -> list[dict[str, Any]]:
    manifest = SKILLS_DIR / "_manifest.jsonl"
    if not manifest.exists():
        return []
    out: list[dict[str, Any]] = []
    for line_no, raw in enumerate(manifest.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"_manifest.jsonl line {line_no} is not valid JSON: {exc}") from exc
    return out


# ---------- Layout ----------


def test_skills_dir_exists() -> None:
    assert SKILLS_DIR.is_dir(), "skills/ directory must exist at repo root"


def test_index_md_exists() -> None:
    assert (SKILLS_DIR / "_index.md").is_file()


def test_manifest_exists_and_parses() -> None:
    entries = _manifest_entries()
    assert entries, "_manifest.jsonl must have at least one entry"


def test_writing_skills_doc_exists() -> None:
    assert (SKILLS_DIR / "_writing-skills.md").is_file()


# ---------- Per-skill SKILL.md ----------


def test_every_skill_dir_has_skill_md() -> None:
    for skill_dir in _skill_dirs():
        assert (skill_dir / "SKILL.md").is_file(), f"{skill_dir.name} is missing SKILL.md"


def test_every_skill_md_parses_valid_frontmatter() -> None:
    for skill_dir in _skill_dirs():
        fm = _parse_frontmatter(skill_dir / "SKILL.md")
        missing = REQUIRED_FRONTMATTER_FIELDS - fm.keys()
        assert not missing, f"{skill_dir.name}/SKILL.md frontmatter missing fields: {missing}"


def test_every_skill_version_is_iso_date() -> None:
    for skill_dir in _skill_dirs():
        fm = _parse_frontmatter(skill_dir / "SKILL.md")
        assert VERSION_RE.match(str(fm["version"])), (
            f"{skill_dir.name}/SKILL.md version must be YYYY-MM-DD"
        )


def test_every_skill_name_matches_dir_name() -> None:
    """Frontmatter ``name`` must match the directory name (registry consistency)."""
    for skill_dir in _skill_dirs():
        fm = _parse_frontmatter(skill_dir / "SKILL.md")
        assert fm["name"] == skill_dir.name, (
            f"{skill_dir.name}/SKILL.md declares name={fm['name']!r}; "
            "directory and frontmatter name must match"
        )


def test_every_skill_has_triggers_and_constraints() -> None:
    """Triggers + constraints must be non-empty lists of strings."""
    for skill_dir in _skill_dirs():
        fm = _parse_frontmatter(skill_dir / "SKILL.md")
        for field in ("triggers", "constraints"):
            value = fm.get(field)
            assert isinstance(value, list) and value, (
                f"{skill_dir.name}/SKILL.md {field!r} must be a non-empty list"
            )
            assert all(isinstance(x, str) for x in value), (
                f"{skill_dir.name}/SKILL.md {field!r} entries must be strings"
            )


def test_every_skill_includes_self_rewrite_hook() -> None:
    """Convention: every skill ends with a self-rewrite hook section."""
    for skill_dir in _skill_dirs():
        body = (skill_dir / "SKILL.md").read_text()
        assert "Self-rewrite hook" in body, (
            f"{skill_dir.name}/SKILL.md missing 'Self-rewrite hook' section"
        )


# ---------- Manifest ↔ skill consistency ----------


def test_manifest_covers_every_skill_dir() -> None:
    manifest_names = {entry["name"] for entry in _manifest_entries()}
    skill_names = {d.name for d in _skill_dirs()}
    missing_in_manifest = skill_names - manifest_names
    missing_on_disk = manifest_names - skill_names
    assert not missing_in_manifest, f"_manifest.jsonl missing entries for: {missing_in_manifest}"
    assert not missing_on_disk, (
        f"_manifest.jsonl entries with no SKILL.md on disk: {missing_on_disk}"
    )


def _frontmatter_by_name() -> dict[str, dict[str, Any]]:
    return {d.name: _parse_frontmatter(d / "SKILL.md") for d in _skill_dirs()}


@pytest.mark.parametrize(
    "field", ["name", "version", "triggers", "tools", "preconditions", "constraints", "category"]
)
def test_manifest_entries_match_frontmatter(field: str) -> None:
    """Every manifest field must agree with the skill's SKILL.md frontmatter."""
    by_name = _frontmatter_by_name()
    for entry in _manifest_entries():
        fm = by_name[entry["name"]]
        assert entry[field] == fm[field], (
            f"_manifest.jsonl[{entry['name']!r}].{field} != "
            f"SKILL.md frontmatter (manifest={entry[field]!r}, "
            f"frontmatter={fm[field]!r})"
        )


# ---------- Index references every skill ----------


def test_index_md_mentions_every_skill() -> None:
    """Sanity: _index.md must reference each skill by name."""
    index = (SKILLS_DIR / "_index.md").read_text()
    missing: Iterable[str] = [d.name for d in _skill_dirs() if d.name not in index]
    assert not list(missing), f"_index.md does not mention these skills: {list(missing)}"
