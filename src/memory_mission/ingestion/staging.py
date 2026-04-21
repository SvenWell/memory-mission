"""Staging writer — pulled items go here for human review before promotion.

Backfill loops, real-time extraction, and any future "pull from external"
agent writes to staging FIRST. A reviewer (human or curator agent) decides
later whether to promote a staged item into the firm wiki proper.

Layout (per source):

    <wiki_root>/staging/<source>/.raw/<item_id>.json   # connector raw payload
    <wiki_root>/staging/<source>/<item_id>.md           # distilled markdown

The raw sidecar preserves the connector response verbatim. The markdown
file carries minimal frontmatter (source, source_id, ingested_at, plus
caller-supplied extras) and the body the agent extracted from the raw
payload.

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

# Same shape as observability's firm-id regex: alnum + ._- with a length
# bound, no path separators or NUL bytes. Source labels and item ids share
# this surface because both become path segments.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")


class StagedItem(BaseModel):
    """Pointer to a staged item on disk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    source: str
    raw_path: Path
    markdown_path: Path


def _validate_segment(value: str, *, name: str) -> None:
    if not value or not _SAFE_PATH_SEGMENT.match(value):
        raise ValueError(
            f"{name} {value!r} must match {_SAFE_PATH_SEGMENT.pattern} "
            "(alphanumerics + ._- only, 1-128 chars, no path separators)"
        )


class StagingWriter:
    """Writes pulled items to ``<wiki_root>/staging/<source>/`` for review.

    One ``StagingWriter`` instance is scoped to a single ``source`` label
    (``"gmail"``, ``"granola"``, etc.). The ``wiki_root`` directory is
    treated as the firm's content root — staging lives alongside the
    eventual curated pages.
    """

    def __init__(self, *, wiki_root: Path, source: str) -> None:
        _validate_segment(source, name="source")
        self._source = source
        self._wiki_root = Path(wiki_root)
        self._source_dir = self._wiki_root / "staging" / source
        self._raw_dir = self._source_dir / ".raw"

    @property
    def source(self) -> str:
        return self._source

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
        _validate_segment(item_id, name="item_id")
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
                body=markdown_body,
                extras=frontmatter_extras or {},
            ),
        )

        return StagedItem(
            item_id=item_id,
            source=self._source,
            raw_path=raw_path,
            markdown_path=md_path,
        )

    def get(self, item_id: str) -> StagedItem | None:
        """Return a pointer to the staged item if both files exist."""
        _validate_segment(item_id, name="item_id")
        raw_path = self._raw_dir / f"{item_id}.json"
        md_path = self._source_dir / f"{item_id}.md"
        if not raw_path.exists() or not md_path.exists():
            return None
        return StagedItem(
            item_id=item_id,
            source=self._source,
            raw_path=raw_path,
            markdown_path=md_path,
        )

    def list_pending(self) -> list[StagedItem]:
        """List currently-staged items for this source, sorted by item_id."""
        if not self._source_dir.exists():
            return []
        items: list[StagedItem] = []
        for md_path in sorted(self._source_dir.glob("*.md")):
            item_id = md_path.stem
            raw_path = self._raw_dir / f"{item_id}.json"
            if raw_path.exists():
                items.append(
                    StagedItem(
                        item_id=item_id,
                        source=self._source,
                        raw_path=raw_path,
                        markdown_path=md_path,
                    )
                )
        return items

    def remove(self, item_id: str) -> bool:
        """Drop a staged item. Returns True if anything was removed."""
        _validate_segment(item_id, name="item_id")
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


# ---------- Helpers ----------


def _render_staging_markdown(
    *,
    source: str,
    item_id: str,
    body: str,
    extras: dict[str, Any],
) -> str:
    """Produce ``---\\n<yaml>\\n---\\n\\n<body>\\n``."""
    fm: dict[str, Any] = {
        "source": source,
        "source_id": item_id,
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    for key, value in extras.items():
        if key in fm:
            continue  # caller can't override the canonical fields
        fm[key] = value
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm_yaml}\n---\n\n{body.rstrip()}\n"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via temp file + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


__all__ = ["StagedItem", "StagingWriter"]
