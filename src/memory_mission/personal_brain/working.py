"""Working state — what this employee's agent is currently focused on.

The shortest-lived layer of the per-employee brain. Stored as a single
``WORKSPACE.md`` file with YAML frontmatter + free-form body. Volatile:
the ``archive_stale`` helper retires entries older than a configurable
threshold so working memory doesn't accumulate forever.

Vault-friendly format — opens cleanly in Obsidian; the user can hand-
edit at any time. Same convention as agentic-stack's ``working/``
layer adapted to the firm context.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.schema import plane_root, validate_employee_id

WORKING_DIR = "working"
WORKSPACE_FILENAME = "WORKSPACE.md"

_ZONE_SEP = re.compile(r"^---\s*$", re.MULTILINE)


class WorkingState(BaseModel):
    """Current task state for one employee's agent.

    Attribution: per-employee. ``focus`` is one short sentence ("what
    am I doing right now"); ``open_items`` is a bullet list. ``body``
    is free-form markdown the user can edit by hand.
    """

    model_config = ConfigDict(extra="allow")

    employee_id: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    focus: str = ""
    open_items: list[str] = Field(default_factory=list)
    body: str = ""


def working_dir(wiki_root: Path, employee_id: str) -> Path:
    """Return the absolute working/ directory path for an employee."""
    validate_employee_id(employee_id)
    return wiki_root / plane_root("personal", employee_id) / WORKING_DIR


def workspace_path(wiki_root: Path, employee_id: str) -> Path:
    """Return the absolute path to ``WORKSPACE.md`` for an employee."""
    return working_dir(wiki_root, employee_id) / WORKSPACE_FILENAME


def write_working_state(wiki_root: Path, state: WorkingState) -> Path:
    """Atomic write of WORKSPACE.md. Creates the working/ dir as needed."""
    dest = workspace_path(wiki_root, state.employee_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = _render(state)
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def read_working_state(wiki_root: Path, employee_id: str) -> WorkingState | None:
    """Return the current working state or None if no WORKSPACE.md exists."""
    path = workspace_path(wiki_root, employee_id)
    if not path.exists():
        return None
    return _parse(path.read_text(encoding="utf-8"), employee_id=employee_id)


def archive_stale(
    wiki_root: Path,
    employee_id: str,
    *,
    older_than: timedelta = timedelta(days=2),
    archive_dir_name: str = ".archive",
    now: datetime | None = None,
) -> Path | None:
    """Move WORKSPACE.md to working/.archive/ if it's older than ``older_than``.

    Returns the archived path, or None if there was nothing to archive.
    Defaults match the agentic-stack convention (working state archived
    after 2 days of inactivity).
    """
    state = read_working_state(wiki_root, employee_id)
    if state is None:
        return None
    current = now or datetime.now(UTC)
    if state.updated_at.tzinfo is None:
        state_ts = state.updated_at.replace(tzinfo=UTC)
    else:
        state_ts = state.updated_at
    if (current - state_ts) < older_than:
        return None

    src = workspace_path(wiki_root, employee_id)
    archive_dir = working_dir(wiki_root, employee_id) / archive_dir_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = state_ts.strftime("%Y%m%dT%H%M%SZ")
    dest = archive_dir / f"WORKSPACE-{stamp}.md"
    src.rename(dest)
    return dest


def _render(state: WorkingState) -> str:
    """``WorkingState`` → frontmatter+body markdown text."""
    fm: dict[str, object] = {
        "employee_id": state.employee_id,
        "updated_at": state.updated_at.isoformat(),
    }
    if state.focus:
        fm["focus"] = state.focus
    extras = state.model_extra or {}
    for key, value in extras.items():
        if key in fm:
            continue
        fm[key] = value
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()

    parts = ["---", fm_yaml, "---", ""]
    if state.open_items:
        parts.append("## Open items")
        parts.append("")
        for item in state.open_items:
            parts.append(f"- {item}")
        parts.append("")
    if state.body.strip():
        parts.append(state.body.rstrip())
        parts.append("")
    return "\n".join(parts)


def _parse(raw: str, *, employee_id: str) -> WorkingState:
    """Parse markdown back into ``WorkingState``."""
    if not raw.startswith("---"):
        raise ValueError("WORKSPACE.md is missing YAML frontmatter")
    rest = raw.split("\n", 1)[1] if "\n" in raw else ""
    match = _ZONE_SEP.search(rest)
    if match is None:
        raise ValueError("WORKSPACE.md frontmatter has no closing '---'")
    fm_text = rest[: match.start()]
    body = rest[match.end() :].lstrip("\n")
    fm_data = yaml.safe_load(fm_text) or {}
    if not isinstance(fm_data, dict):
        raise ValueError("WORKSPACE.md frontmatter must parse to a mapping")

    open_items, body_remaining = _split_open_items(body)
    updated_raw = fm_data.get("updated_at")
    if isinstance(updated_raw, str):
        updated_at = datetime.fromisoformat(updated_raw)
    elif isinstance(updated_raw, datetime):
        updated_at = updated_raw
    else:
        updated_at = datetime.now(UTC)

    extras = {k: v for k, v in fm_data.items() if k not in {"employee_id", "updated_at", "focus"}}
    return WorkingState(
        employee_id=str(fm_data.get("employee_id", employee_id)),
        updated_at=updated_at,
        focus=str(fm_data.get("focus", "") or ""),
        open_items=open_items,
        body=body_remaining.rstrip(),
        **extras,
    )


_OPEN_ITEMS_HEADER = re.compile(r"^##\s+Open\s+items\s*$", re.IGNORECASE)


def _split_open_items(body: str) -> tuple[list[str], str]:
    """Pull bullets under '## Open items' out of the body block."""
    lines = body.splitlines()
    items: list[str] = []
    body_lines: list[str] = []
    in_section = False
    for line in lines:
        if _OPEN_ITEMS_HEADER.match(line.strip()):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if stripped.startswith("- "):
                items.append(stripped[2:].strip())
                continue
            if not stripped:
                continue
            in_section = False
            body_lines.append(line)
        else:
            body_lines.append(line)
    return items, "\n".join(body_lines)


__all__ = [
    "WORKING_DIR",
    "WORKSPACE_FILENAME",
    "WorkingState",
    "archive_stale",
    "read_working_state",
    "working_dir",
    "workspace_path",
    "write_working_state",
]
