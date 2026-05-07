"""Dry-run extraction previews for narrow staged-source pilots.

This module is intentionally LLM-free. A host agent selects staged
items, runs its own model, validates the response as ``ExtractionReport``,
then calls these helpers to turn reports into a reviewable JSONL preview.

Dry-run previews are evidence-review artifacts. They never write fact
staging, proposals, pages, or KG triples.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory_mission.extraction.schema import (
    EventFact,
    ExtractedFact,
    ExtractionReport,
    IdentityFact,
    OpenQuestion,
    PreferenceFact,
    RelationshipFact,
    UpdateFact,
)
from memory_mission.ingestion.staging import StagedItem, StagingWriter
from memory_mission.memory.schema import Plane, plane_root
from memory_mission.path_safety import validate_path_segment

# Same external-id envelope as staging/extraction writers: long enough
# for Gmail / Granola ids, still a single safe path segment.
_SAFE_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,245}$")


class StagingSliceFilter(BaseModel):
    """Filter for selecting a narrow slice of staged source items.

    At least one selector must be present. ``max_items`` is a guardrail
    for pilot workflows; callers who truly want a larger slice must make
    that visible by raising the limit explicitly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_ids: tuple[str, ...] = ()
    meeting_ids: tuple[str, ...] = ()
    filter_tag: str | None = None
    filter_entity: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    max_items: int = Field(default=25, ge=1)

    @model_validator(mode="after")
    def _validate_filter(self) -> StagingSliceFilter:
        if not self.has_selector:
            raise ValueError("at least one slice selector is required")
        if (
            self.from_date is not None
            and self.to_date is not None
            and self.from_date > self.to_date
        ):
            raise ValueError("from_date must be <= to_date")
        for value in (*self.item_ids, *self.meeting_ids):
            _validate_external_id(value, name="slice id")
        return self

    @property
    def has_selector(self) -> bool:
        return bool(
            self.item_ids
            or self.meeting_ids
            or self.filter_tag
            or self.filter_entity
            or self.from_date
            or self.to_date
        )


class DryRunCandidate(BaseModel):
    """One reviewable fact candidate emitted to dry-run JSONL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    source: str
    source_id: str
    target_plane: Plane
    employee_id: str | None = None
    meeting_id: str | None = None
    meeting_date: date | None = None
    meeting_path: str
    title: str = ""
    fact_index: int
    fact_kind: str
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    support_quote: str
    fact: dict[str, Any]


class DryRunReport(BaseModel):
    """Summary for a dry-run JSONL write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    path: Path
    selected_count: int
    candidate_count: int
    dropped_low_confidence: int = 0
    dropped_missing_quote: int = 0
    selected_source_ids: list[str] = Field(default_factory=list)


def select_staged_items(writer: StagingWriter, filter: StagingSliceFilter) -> list[StagedItem]:
    """Return staged items matching ``filter``, bounded by ``max_items``.

    Matching is AND across populated selectors: for example, a tag +
    date range returns items with that tag within the date range.
    """

    selected: list[StagedItem] = []
    for item in writer.list_pending():
        metadata = _load_item_metadata(item)
        if _matches_filter(item, metadata, filter):
            selected.append(item)
    selected.sort(key=lambda item: item.item_id)
    if len(selected) > filter.max_items:
        raise ValueError(
            f"slice matched {len(selected)} staged items, exceeding max_items={filter.max_items}"
        )
    return selected


def dry_run_candidates_from_report(
    report: ExtractionReport,
    staged_item: StagedItem,
    *,
    run_id: str,
    wiki_root: Path,
    min_confidence: float = 0.6,
) -> tuple[list[DryRunCandidate], int, int]:
    """Convert one validated extraction report into JSONL candidates.

    Returns ``(candidates, dropped_low_confidence, dropped_missing_quote)``.
    """

    validate_path_segment(run_id, name="run_id")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    if report.source != staged_item.source:
        raise ValueError("report source does not match staged item source")
    if report.source_id != staged_item.item_id:
        raise ValueError("report source_id does not match staged item item_id")
    if report.target_plane != staged_item.target_plane:
        raise ValueError("report target_plane does not match staged item target_plane")
    if report.employee_id != staged_item.employee_id:
        raise ValueError("report employee_id does not match staged item employee_id")

    metadata = _load_item_metadata(staged_item)
    dropped_low = 0
    dropped_quote = 0
    candidates: list[DryRunCandidate] = []
    for index, fact in enumerate(report.facts):
        if fact.confidence < min_confidence:
            dropped_low += 1
            continue
        if not fact.support_quote.strip():
            dropped_quote += 1
            continue
        candidates.append(
            _candidate_from_fact(
                fact,
                fact_index=index,
                run_id=run_id,
                report=report,
                staged_item=staged_item,
                wiki_root=wiki_root,
                metadata=metadata,
            )
        )
    return candidates, dropped_low, dropped_quote


def write_dry_run_jsonl(
    candidates: list[DryRunCandidate],
    *,
    wiki_root: Path,
    source: str,
    target_plane: Plane,
    employee_id: str | None = None,
    run_id: str,
    selected_count: int,
    dropped_low_confidence: int = 0,
    dropped_missing_quote: int = 0,
    selected_source_ids: list[str] | None = None,
) -> DryRunReport:
    """Write candidates to ``staging/<plane>/<source>/.dry_run/<run_id>.jsonl``."""

    validate_path_segment(run_id, name="run_id")
    validate_path_segment(source, name="source")
    if target_plane == "personal" and not employee_id:
        raise ValueError("personal dry-run output requires employee_id")
    if target_plane == "firm" and employee_id is not None:
        raise ValueError("firm dry-run output must not carry employee_id")

    dry_run_dir = (
        Path(wiki_root) / "staging" / plane_root(target_plane, employee_id) / source / ".dry_run"
    )
    dry_run_dir.mkdir(parents=True, exist_ok=True)
    path = dry_run_dir / f"{run_id}.jsonl"
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for candidate in candidates:
            f.write(candidate.model_dump_json(exclude_none=True))
            f.write("\n")
    tmp.replace(path)
    return DryRunReport(
        run_id=run_id,
        path=path,
        selected_count=selected_count,
        candidate_count=len(candidates),
        dropped_low_confidence=dropped_low_confidence,
        dropped_missing_quote=dropped_missing_quote,
        selected_source_ids=selected_source_ids
        if selected_source_ids is not None
        else sorted({candidate.source_id for candidate in candidates}),
    )


def write_extraction_dry_run(
    reports: list[tuple[ExtractionReport, StagedItem]],
    *,
    wiki_root: Path,
    run_id: str,
    min_confidence: float = 0.6,
) -> DryRunReport:
    """Write a dry-run JSONL for already-extracted staged reports.

    All reports must share source + target plane. This is the helper a
    host-agent pilot calls after it has run the LLM over a selected
    staging slice.
    """

    validate_path_segment(run_id, name="run_id")
    if not reports:
        raise ValueError("at least one extraction report is required")

    first_report, first_item = reports[0]
    source = first_report.source
    target_plane = first_report.target_plane
    employee_id = first_report.employee_id
    candidates: list[DryRunCandidate] = []
    dropped_low = 0
    dropped_quote = 0
    for report, item in reports:
        if report.source != source or report.target_plane != target_plane:
            raise ValueError("all dry-run reports must share source and target_plane")
        if report.employee_id != employee_id:
            raise ValueError("all dry-run reports must share employee_id")
        new_candidates, low, quote = dry_run_candidates_from_report(
            report,
            item,
            run_id=run_id,
            wiki_root=wiki_root,
            min_confidence=min_confidence,
        )
        candidates.extend(new_candidates)
        dropped_low += low
        dropped_quote += quote

    return write_dry_run_jsonl(
        candidates,
        wiki_root=wiki_root,
        source=source,
        target_plane=target_plane,
        employee_id=employee_id,
        run_id=run_id,
        selected_count=len(reports),
        dropped_low_confidence=dropped_low,
        dropped_missing_quote=dropped_quote,
        selected_source_ids=[item.item_id for _, item in reports],
    )


def _candidate_from_fact(
    fact: ExtractedFact,
    *,
    fact_index: int,
    run_id: str,
    report: ExtractionReport,
    staged_item: StagedItem,
    wiki_root: Path,
    metadata: dict[str, Any],
) -> DryRunCandidate:
    subject, predicate, obj = _fact_review_fields(fact)
    meeting_date = _metadata_date(metadata)
    return DryRunCandidate(
        run_id=run_id,
        source=report.source,
        source_id=report.source_id,
        target_plane=report.target_plane,
        employee_id=report.employee_id,
        meeting_id=_metadata_meeting_id(staged_item, metadata),
        meeting_date=meeting_date,
        meeting_path=_relative_path(staged_item.markdown_path, wiki_root),
        title=str(metadata.get("title") or ""),
        fact_index=fact_index,
        fact_kind=fact.kind,
        subject=subject,
        predicate=predicate,
        object=obj,
        confidence=fact.confidence,
        support_quote=fact.support_quote,
        fact=fact.model_dump(mode="json"),
    )


def _fact_review_fields(fact: ExtractedFact) -> tuple[str | None, str | None, str | None]:
    if isinstance(fact, IdentityFact):
        return fact.entity_name, "identity", fact.entity_type
    if isinstance(fact, RelationshipFact):
        return fact.subject, fact.predicate, fact.object
    if isinstance(fact, PreferenceFact):
        return fact.subject, "preference", fact.preference
    if isinstance(fact, EventFact):
        return fact.entity_name, "event", fact.description
    if isinstance(fact, UpdateFact):
        return fact.subject, fact.predicate, fact.new_object
    if isinstance(fact, OpenQuestion):
        return None, "open_question", fact.question
    return None, None, None


def _matches_filter(
    item: StagedItem,
    metadata: dict[str, Any],
    filter: StagingSliceFilter,
) -> bool:
    if filter.item_ids and item.item_id not in filter.item_ids:
        return False
    if filter.meeting_ids and _metadata_meeting_id(item, metadata) not in filter.meeting_ids:
        return False
    if filter.filter_tag and filter.filter_tag.lower() not in _metadata_tags(metadata):
        return False
    item_date = _metadata_date(metadata)
    if filter.from_date and (item_date is None or item_date < filter.from_date):
        return False
    if filter.to_date and (item_date is None or item_date > filter.to_date):
        return False
    if filter.filter_entity:
        needle = filter.filter_entity.lower()
        if needle not in _metadata_search_text(metadata).lower():
            return False
    return True


def _load_item_metadata(item: StagedItem) -> dict[str, Any]:
    raw = json.loads(item.raw_path.read_text(encoding="utf-8"))
    frontmatter, body = _read_frontmatter_and_body(item.markdown_path)
    title = (
        raw.get("title")
        or raw.get("subject")
        or raw.get("name")
        or frontmatter.get("title")
        or _first_markdown_heading(body)
        or ""
    )
    return {
        "raw": raw,
        "frontmatter": frontmatter,
        "body": body,
        "title": title,
    }


def _read_frontmatter_and_body(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    _, rest = text.split("---\n", 1)
    if "---\n" not in rest:
        return {}, text
    fm_text, body = rest.split("---\n", 1)
    loaded = yaml.safe_load(fm_text) or {}
    if not isinstance(loaded, dict):
        return {}, body
    return dict(loaded), body


def _metadata_meeting_id(item: StagedItem, metadata: dict[str, Any]) -> str:
    raw = metadata["raw"]
    frontmatter = metadata["frontmatter"]
    for value in (
        raw.get("meeting_id"),
        raw.get("transcript_id"),
        raw.get("id"),
        frontmatter.get("container_id"),
        frontmatter.get("source_id"),
        item.item_id,
    ):
        if isinstance(value, str) and value:
            return value
    return item.item_id


def _metadata_tags(metadata: dict[str, Any]) -> set[str]:
    raw = metadata["raw"]
    frontmatter = metadata["frontmatter"]
    tags: set[str] = set()
    for value in (
        raw.get("labels"),
        raw.get("tags"),
        frontmatter.get("labels"),
        frontmatter.get("tags"),
    ):
        if isinstance(value, str):
            tags.add(value.lower())
        elif isinstance(value, list):
            tags.update(str(item).lower() for item in value)
    return tags


def _metadata_date(metadata: dict[str, Any]) -> date | None:
    raw = metadata["raw"]
    frontmatter = metadata["frontmatter"]
    for value in (
        raw.get("started_at"),
        raw.get("created_at"),
        raw.get("modified_at"),
        raw.get("date"),
        frontmatter.get("modified_at"),
        frontmatter.get("ingested_at"),
    ):
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _metadata_search_text(metadata: dict[str, Any]) -> str:
    raw = metadata["raw"]
    parts = [
        str(metadata.get("title") or ""),
        str(metadata.get("body") or ""),
        str(raw.get("transcript") or ""),
        str(raw.get("body") or ""),
        str(raw.get("summary") or ""),
    ]
    return "\n".join(parts)


def _first_markdown_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _validate_external_id(value: str, *, name: str) -> None:
    if not value or not _SAFE_EXTERNAL_ID.match(value):
        raise ValueError(
            f"{name} {value!r} must match {_SAFE_EXTERNAL_ID.pattern} "
            "(alphanumerics + ._- only, 1-246 chars, no path separators)"
        )


__all__ = [
    "DryRunCandidate",
    "DryRunReport",
    "StagingSliceFilter",
    "dry_run_candidates_from_report",
    "select_staged_items",
    "write_dry_run_jsonl",
    "write_extraction_dry_run",
]
