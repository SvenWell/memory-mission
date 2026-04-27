"""Discovery primitives for the personal plane.

Surfaces what already exists under a Memory Mission root so a new
agent / writer can detect prior state before silently creating
another silo. Addresses the multi-agent identifier-coordination gap
documented in ``project_multi_agent_identifier_gap.md`` (2026-04-27):
two agents picked different forms of the same logical user
(``sven`` vs ``6052376253``) and the substrate accepted both.

This module is deliberately tiny — it doesn't enforce canonicality,
it just tells you what's there. Callers decide whether to consolidate
(via ``migrate_personal_kg``) or proceed.
"""

from __future__ import annotations

from pathlib import Path

from memory_mission.memory.schema import validate_employee_id


def list_personal_user_ids(root: Path | str) -> list[str]:
    """Return the set of user_ids that already have a personal KG under ``root``.

    Scans ``<root>/personal/<user_id>/personal_kg.db``. A directory
    without the SQLite file is skipped (it might be an in-progress
    init or a stale dir). Filters anything that doesn't match
    ``validate_employee_id`` (defense-in-depth — refuses to surface
    path-unsafe entries even if they ended up on disk).

    Returns the user_ids sorted lexicographically. Empty list if
    ``root`` doesn't exist or has no personal-plane content.

    Args:
        root: Memory Mission root (e.g. ``~/.memory-mission``).

    Returns:
        List of user_ids that have a populated personal KG. Use this
        before calling ``initialize(user_id=...)`` with a new id to
        check whether a logically-equivalent id already has data.
    """
    root_path = Path(root).expanduser()
    personal_dir = root_path / "personal"
    if not personal_dir.is_dir():
        return []
    out: list[str] = []
    for child in personal_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            validate_employee_id(child.name)
        except ValueError:
            continue
        if (child / "personal_kg.db").is_file():
            out.append(child.name)
    out.sort()
    return out


__all__ = ["list_personal_user_ids"]
