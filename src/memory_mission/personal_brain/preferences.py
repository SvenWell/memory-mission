"""Per-employee preferences — how this person wants their agent to behave.

PREFERENCES.md is the FIRST file the agent reads at the start of every
session (agentic-stack convention). YAML frontmatter holds typed
fields the workflow agents consume directly (timezone, communication
style, explanation depth); the markdown body is free-form for
preferences too nuanced to schematize.

The user is expected to hand-edit this file. We provide read +
typed-update helpers so skills can refine specific fields without
clobbering the rest.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.schema import plane_root, validate_employee_id

PREFERENCES_DIR = "preferences"
PREFERENCES_FILENAME = "PREFERENCES.md"

_ZONE_SEP = re.compile(r"^---\s*$", re.MULTILINE)


class Preferences(BaseModel):
    """Per-employee preferences.

    Common fields are typed; everything else lands in ``extras`` so a
    firm can add custom keys without code changes.
    """

    model_config = ConfigDict(extra="allow")

    employee_id: str
    name: str = ""
    timezone: str = ""
    communication_style: str = ""
    explanation_style: str = ""
    test_strategy: str = ""
    body: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def preferences_dir(wiki_root: Path, employee_id: str) -> Path:
    """Absolute preferences/ directory for an employee."""
    validate_employee_id(employee_id)
    return wiki_root / plane_root("personal", employee_id) / PREFERENCES_DIR


def preferences_path(wiki_root: Path, employee_id: str) -> Path:
    """Absolute path to PREFERENCES.md for an employee."""
    return preferences_dir(wiki_root, employee_id) / PREFERENCES_FILENAME


def write_preferences(wiki_root: Path, prefs: Preferences) -> Path:
    """Atomic write of PREFERENCES.md."""
    dest = preferences_path(wiki_root, prefs.employee_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = _render(prefs)
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def read_preferences(wiki_root: Path, employee_id: str) -> Preferences | None:
    """Return parsed preferences or None if PREFERENCES.md doesn't exist."""
    path = preferences_path(wiki_root, employee_id)
    if not path.exists():
        return None
    return _parse(path.read_text(encoding="utf-8"), employee_id=employee_id)


def update_preferences(
    wiki_root: Path,
    employee_id: str,
    **fields: Any,
) -> Preferences:
    """Read, apply ``fields`` updates, write back. Creates if missing."""
    existing = read_preferences(wiki_root, employee_id)
    base: dict[str, Any] = (
        existing.model_dump() if existing is not None else {"employee_id": employee_id}
    )
    base.update(fields)
    base["updated_at"] = datetime.now(UTC)
    prefs = Preferences.model_validate(base)
    write_preferences(wiki_root, prefs)
    return prefs


def _render(prefs: Preferences) -> str:
    """``Preferences`` → markdown text."""
    fm: dict[str, Any] = {
        "employee_id": prefs.employee_id,
        "updated_at": prefs.updated_at.isoformat(),
    }
    typed = ["name", "timezone", "communication_style", "explanation_style", "test_strategy"]
    for key in typed:
        value = getattr(prefs, key, "")
        if value:
            fm[key] = value
    extras = prefs.model_extra or {}
    for key, value in extras.items():
        if key in fm:
            continue
        fm[key] = value
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()

    parts = ["---", fm_yaml, "---", ""]
    if prefs.body.strip():
        parts.append(prefs.body.rstrip())
        parts.append("")
    return "\n".join(parts)


def _parse(raw: str, *, employee_id: str) -> Preferences:
    """Markdown → ``Preferences``."""
    if not raw.startswith("---"):
        raise ValueError("PREFERENCES.md is missing YAML frontmatter")
    rest = raw.split("\n", 1)[1] if "\n" in raw else ""
    match = _ZONE_SEP.search(rest)
    if match is None:
        raise ValueError("PREFERENCES.md frontmatter has no closing '---'")
    fm_text = rest[: match.start()]
    body = rest[match.end() :].lstrip("\n")
    fm_data = yaml.safe_load(fm_text) or {}
    if not isinstance(fm_data, dict):
        raise ValueError("PREFERENCES.md frontmatter must parse to a mapping")

    updated_raw = fm_data.pop("updated_at", None)
    if isinstance(updated_raw, str):
        updated_at = datetime.fromisoformat(updated_raw)
    elif isinstance(updated_raw, datetime):
        updated_at = updated_raw
    else:
        updated_at = datetime.now(UTC)

    return Preferences(
        employee_id=str(fm_data.pop("employee_id", employee_id)),
        updated_at=updated_at,
        body=body.rstrip(),
        **fm_data,
    )


__all__ = [
    "PREFERENCES_DIR",
    "PREFERENCES_FILENAME",
    "Preferences",
    "preferences_dir",
    "preferences_path",
    "read_preferences",
    "update_preferences",
    "write_preferences",
]
