"""Staging writer — pulled items go here for human review before promotion.

Backfill loops, real-time extraction, and any future "pull from external"
agent writes to staging FIRST. A reviewer (human or curator agent) decides
later whether to promote a staged item into a memory plane.

Layout (per source + target plane):

    <wiki_root>/staging/personal/<employee_id>/<source>/.raw/<item_id>.json
    <wiki_root>/staging/personal/<employee_id>/<source>/<item_id>.md
    <wiki_root>/staging/firm/<source>/.raw/<item_id>.json
    <wiki_root>/staging/firm/<source>/<item_id>.md

``target_plane`` tells the writer where a promoted item would live. Personal
staging belongs to one employee; firm staging is shared. The promotion
pipeline (Step 10) reads from these directories and uses ``target_plane``
to pick the destination root.

The raw sidecar preserves the connector response verbatim. The markdown
file carries minimal frontmatter (source, source_id, ingested_at,
target_plane, plus caller-supplied extras) and the body the agent
extracted from the raw payload.

Why a separate `staging` zone instead of writing directly into MECE
domains: staged items don't have a canonical home yet (an email might be
about a person, a company, a deal — promotion is the moment that decision
gets made). Staging stays out of the wiki search surface so extraction
artifacts don't accidentally surface as truth before review.

This module ports GBrain's "raw sidecar + curated page" pattern into the
backfill flow. The curated page lands later, written by the extraction /
promotion pipeline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from memory_mission.ingestion.roles import NormalizedSourceItem
from memory_mission.memory.schema import (
    Plane,
    staging_source_dir,
    validate_employee_id,
)

# Path-safe component for our own identifiers (source labels). Tight by
# design — these are operator-controlled config strings.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")

# External system identifiers (Gmail message ids, Google Calendar event
# ids, including recurring-event instance suffixes that append
# _<UTC datetime>Z) routinely run 200+ chars. Bound at 246 — sized
# for ext4's 255-byte filename limit minus our 9-byte .json.tmp
# suffix — tight enough to guard against pathological inputs.
_SAFE_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,245}$")


class StagedItem(BaseModel):
    """Pointer to a staged item on disk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    source: str
    target_plane: Plane
    employee_id: str | None
    raw_path: Path
    markdown_path: Path


def _validate_segment(
    value: str,
    *,
    name: str,
    pattern: re.Pattern[str] = _SAFE_PATH_SEGMENT,
) -> None:
    if not value or not pattern.match(value):
        max_chars = 246 if pattern is _SAFE_EXTERNAL_ID else 128
        raise ValueError(
            f"{name} {value!r} must match {pattern.pattern} "
            f"(alphanumerics + ._- only, 1-{max_chars} chars, no path separators)"
        )


class StagingWriter:
    """Writes pulled items to the staging zone for review.

    Scoped to a single ``source`` label (``"gmail"``, ``"granola"``, etc.)
    and a single ``target_plane``. For personal targets, the writer is
    also scoped to one ``employee_id``. The ``wiki_root`` directory is the
    firm's content root — staging lives alongside the eventual curated
    pages.
    """

    def __init__(
        self,
        *,
        wiki_root: Path,
        source: str,
        target_plane: Plane,
        employee_id: str | None = None,
    ) -> None:
        _validate_segment(source, name="source")
        if target_plane == "personal":
            if not employee_id:
                raise ValueError("personal target_plane requires employee_id")
            validate_employee_id(employee_id)
        elif target_plane == "firm":
            if employee_id is not None:
                raise ValueError("firm target_plane must not carry an employee_id")
        else:
            raise ValueError(f"unknown target_plane: {target_plane!r}")

        self._source = source
        self._target_plane: Plane = target_plane
        self._employee_id = employee_id
        self._wiki_root = Path(wiki_root)
        self._source_dir = self._wiki_root / staging_source_dir(
            target_plane=target_plane, source=source, employee_id=employee_id
        )
        self._raw_dir = self._source_dir / ".raw"

    @property
    def source(self) -> str:
        return self._source

    @property
    def target_plane(self) -> Plane:
        return self._target_plane

    @property
    def employee_id(self) -> str | None:
        return self._employee_id

    @property
    def source_dir(self) -> Path:
        return self._source_dir

    def write(
        self,
        *,
        item_id: str,
        raw: dict[str, Any],
        markdown_body: str,
        frontmatter_extras: dict[str, Any] | None = None,
    ) -> StagedItem:
        """Write one item: raw JSON sidecar + frontmatter-headed markdown.

        ``item_id`` is validated as a safe path segment. Both files are
        written atomically (temp file + rename) so a crash mid-write can't
        leave a half-written sidecar that future reads would parse.
        """
        _validate_segment(item_id, name="item_id", pattern=_SAFE_EXTERNAL_ID)
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._source_dir.mkdir(parents=True, exist_ok=True)

        raw_path = self._raw_dir / f"{item_id}.json"
        md_path = self._source_dir / f"{item_id}.md"

        _atomic_write_text(raw_path, json.dumps(raw, indent=2, sort_keys=True))
        _atomic_write_text(
            md_path,
            _render_staging_markdown(
                source=self._source,
                item_id=item_id,
                target_plane=self._target_plane,
                employee_id=self._employee_id,
                body=markdown_body,
                extras=frontmatter_extras or {},
            ),
        )

        return self._make_staged_item(item_id, raw_path, md_path)

    def write_envelope(self, item: NormalizedSourceItem) -> StagedItem:
        """Write a ``NormalizedSourceItem`` envelope to staging (P2).

        The writer is constructed for one (concrete_app, target_plane,
        employee_id) tuple. Mismatching envelopes are rejected
        immediately so a Gmail-bound writer never silently writes a
        Granola transcript, and a personal-bound writer never receives
        a firm-plane envelope.

        Frontmatter carries the envelope's structural fields
        (``source_role``, ``external_object_type``, ``target_scope``,
        ``container_id``, ``url``, ``modified_at``) so reviewers and
        downstream extraction see the firm-shaped scope without parsing
        the raw payload again.
        """
        if item.target_plane != self._target_plane:
            raise ValueError(
                f"envelope target_plane={item.target_plane!r} does not match "
                f"writer target_plane={self._target_plane!r}"
            )
        if item.concrete_app != self._source:
            raise ValueError(
                f"envelope concrete_app={item.concrete_app!r} does not match "
                f"writer source={self._source!r}"
            )
        return self.write(
            item_id=item.external_id,
            raw=item.raw,
            markdown_body=_render_envelope_body(item),
            frontmatter_extras=_envelope_frontmatter_extras(item),
        )

    def get(self, item_id: str) -> StagedItem | None:
        """Return a pointer to the staged item if both files exist."""
        _validate_segment(item_id, name="item_id", pattern=_SAFE_EXTERNAL_ID)
        raw_path = self._raw_dir / f"{item_id}.json"
        md_path = self._source_dir / f"{item_id}.md"
        if not raw_path.exists() or not md_path.exists():
            return None
        return self._make_staged_item(item_id, raw_path, md_path)

    def list_pending(self) -> list[StagedItem]:
        """List currently-staged items for this source, sorted by item_id."""
        if not self._source_dir.exists():
            return []
        items: list[StagedItem] = []
        for md_path in sorted(self._source_dir.glob("*.md")):
            item_id = md_path.stem
            raw_path = self._raw_dir / f"{item_id}.json"
            if raw_path.exists():
                items.append(self._make_staged_item(item_id, raw_path, md_path))
        return items

    def remove(self, item_id: str) -> bool:
        """Drop a staged item. Returns True if anything was removed."""
        _validate_segment(item_id, name="item_id", pattern=_SAFE_EXTERNAL_ID)
        raw_path = self._raw_dir / f"{item_id}.json"
        md_path = self._source_dir / f"{item_id}.md"
        removed = False
        for path in (raw_path, md_path):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def iter_raw(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Iterate ``(item_id, raw_payload)`` tuples — useful for re-extraction."""
        for staged in self.list_pending():
            with staged.raw_path.open() as f:
                yield staged.item_id, json.load(f)

    def _make_staged_item(self, item_id: str, raw_path: Path, md_path: Path) -> StagedItem:
        return StagedItem(
            item_id=item_id,
            source=self._source,
            target_plane=self._target_plane,
            employee_id=self._employee_id,
            raw_path=raw_path,
            markdown_path=md_path,
        )


# ---------- Helpers ----------


def _render_staging_markdown(
    *,
    source: str,
    item_id: str,
    target_plane: Plane,
    employee_id: str | None,
    body: str,
    extras: dict[str, Any],
) -> str:
    """Produce ``---\\n<yaml>\\n---\\n\\n<body>\\n``."""
    fm: dict[str, Any] = {
        "source": source,
        "source_id": item_id,
        "target_plane": target_plane,
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    if employee_id is not None:
        fm["employee_id"] = employee_id
    for key, value in extras.items():
        if key in fm:
            continue  # caller can't override canonical fields
        fm[key] = value
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm_yaml}\n---\n\n{body.rstrip()}\n"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via temp file + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _render_envelope_body(item: NormalizedSourceItem) -> str:
    """Render a NormalizedSourceItem as markdown ready for the staging file."""
    title = item.title.strip()
    body = item.body.strip()
    if title and body:
        return f"# {title}\n\n{body}"
    return title or body


def _envelope_frontmatter_extras(item: NormalizedSourceItem) -> dict[str, Any]:
    """Pick the envelope fields that should land in staging frontmatter.

    Skips fields the base ``write()`` already canonicalizes
    (``source``, ``source_id``, ``target_plane``, ``ingested_at``,
    ``employee_id``).
    """
    extras: dict[str, Any] = {
        "source_role": item.source_role.value,
        "external_object_type": item.external_object_type,
        "target_scope": item.target_scope,
        "modified_at": item.modified_at.isoformat(),
    }
    if item.container_id is not None:
        extras["container_id"] = item.container_id
    if item.url is not None:
        extras["url"] = item.url
    return extras


__all__ = ["StagedItem", "StagingWriter"]
