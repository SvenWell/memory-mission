"""Tests for narrow-slice extraction dry-run previews."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from memory_mission.extraction import (
    ExtractionReport,
    IdentityFact,
    RelationshipFact,
    StagingSliceFilter,
    dry_run_candidates_from_report,
    select_staged_items,
    write_extraction_dry_run,
)
from memory_mission.ingestion import StagedItem, StagingWriter


def _writer(tmp_path: Path) -> StagingWriter:
    return StagingWriter(
        wiki_root=tmp_path / "wiki",
        source="granola",
        target_plane="personal",
        employee_id="alice",
    )


def _stage(
    writer: StagingWriter,
    item_id: str,
    *,
    meeting_id: str,
    title: str,
    transcript: str,
    started_at: str,
    labels: list[str] | None = None,
) -> StagedItem:
    return writer.write(
        item_id=item_id,
        raw={
            "id": item_id,
            "meeting_id": meeting_id,
            "title": title,
            "transcript": transcript,
            "started_at": started_at,
            "labels": labels or [],
        },
        markdown_body=transcript,
        frontmatter_extras={"modified_at": started_at, "container_id": meeting_id},
    )


def _report(
    source_id: str,
    *,
    confidence: float = 0.85,
) -> ExtractionReport:
    return ExtractionReport(
        source="granola",
        source_id=source_id,
        target_plane="personal",
        employee_id="alice",
        facts=[
            RelationshipFact(
                confidence=confidence,
                support_quote="Wealthpoint needs SOC2 before rollout.",
                subject="wealthpoint",
                predicate="blocker",
                object="soc2-review",
            ),
            IdentityFact(
                confidence=0.95,
                support_quote="Alice from Wealthpoint joined.",
                entity_name="alice",
                entity_type="person",
            ),
        ],
    )


def test_slice_filter_requires_selector() -> None:
    with pytest.raises(ValueError, match="at least one slice selector"):
        StagingSliceFilter()


def test_slice_filter_rejects_path_unsafe_ids() -> None:
    with pytest.raises(ValueError, match="slice id"):
        StagingSliceFilter(item_ids=("../escape",))


def test_select_staged_items_by_explicit_item_ids(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint diligence",
        started_at="2026-04-10T09:00:00Z",
    )
    _stage(
        writer,
        "granola-2",
        meeting_id="m-2",
        title="Other",
        transcript="Other meeting",
        started_at="2026-04-11T09:00:00Z",
    )

    selected = select_staged_items(writer, StagingSliceFilter(item_ids=("granola-2",)))

    assert [item.item_id for item in selected] == ["granola-2"]


def test_select_staged_items_by_meeting_id(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    _stage(
        writer,
        "granola-1",
        meeting_id="meeting-alpha",
        title="Alpha",
        transcript="alpha",
        started_at="2026-04-10T09:00:00Z",
    )
    _stage(
        writer,
        "granola-2",
        meeting_id="meeting-beta",
        title="Beta",
        transcript="beta",
        started_at="2026-04-11T09:00:00Z",
    )

    selected = select_staged_items(
        writer,
        StagingSliceFilter(meeting_ids=("meeting-alpha",)),
    )

    assert [item.item_id for item in selected] == ["granola-1"]


def test_select_staged_items_by_tag_date_and_entity(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint diligence",
        transcript="Wealthpoint needs SOC2 before rollout.",
        started_at="2026-04-10T09:00:00Z",
        labels=["wealth-ai"],
    )
    _stage(
        writer,
        "granola-2",
        meeting_id="m-2",
        title="Wealthpoint old",
        transcript="Wealthpoint old meeting.",
        started_at="2026-03-01T09:00:00Z",
        labels=["wealth-ai"],
    )
    _stage(
        writer,
        "granola-3",
        meeting_id="m-3",
        title="Other",
        transcript="SOC2 but not the entity.",
        started_at="2026-04-12T09:00:00Z",
        labels=["wealth-ai"],
    )

    selected = select_staged_items(
        writer,
        StagingSliceFilter(
            filter_tag="wealth-ai",
            filter_entity="Wealthpoint",
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 30),
        ),
    )

    assert [item.item_id for item in selected] == ["granola-1"]


def test_select_staged_items_enforces_max_items(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    for i in range(3):
        _stage(
            writer,
            f"granola-{i}",
            meeting_id=f"m-{i}",
            title="Wealthpoint",
            transcript="Wealthpoint",
            started_at="2026-04-10T09:00:00Z",
        )

    with pytest.raises(ValueError, match="exceeding max_items=2"):
        select_staged_items(writer, StagingSliceFilter(filter_entity="Wealthpoint", max_items=2))


def test_dry_run_candidates_drop_low_confidence(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )
    report = _report("granola-1", confidence=0.59)

    candidates, dropped_low, dropped_quote = dry_run_candidates_from_report(
        report,
        item,
        run_id="run-1",
        wiki_root=tmp_path / "wiki",
    )

    assert [candidate.fact_kind for candidate in candidates] == ["identity"]
    assert dropped_low == 1
    assert dropped_quote == 0


def test_dry_run_candidates_include_review_fields_and_full_fact(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )

    candidates, _, _ = dry_run_candidates_from_report(
        _report("granola-1"),
        item,
        run_id="run-1",
        wiki_root=tmp_path / "wiki",
    )

    rel = candidates[0]
    assert rel.source == "granola"
    assert rel.source_id == "granola-1"
    assert rel.meeting_id == "m-1"
    assert rel.meeting_date == date(2026, 4, 10)
    assert rel.meeting_path == "staging/personal/alice/granola/granola-1.md"
    assert rel.title == "Wealthpoint"
    assert rel.subject == "wealthpoint"
    assert rel.predicate == "blocker"
    assert rel.object == "soc2-review"
    assert rel.fact["kind"] == "relationship"


def test_dry_run_rejects_report_item_mismatch(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )

    with pytest.raises(ValueError, match="source_id"):
        dry_run_candidates_from_report(
            _report("other-id"),
            item,
            run_id="run-1",
            wiki_root=tmp_path / "wiki",
        )


def test_write_extraction_dry_run_writes_jsonl_without_fact_staging(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )

    summary = write_extraction_dry_run(
        [(_report("granola-1"), item)],
        wiki_root=tmp_path / "wiki",
        run_id="run-1",
    )

    assert summary.path == tmp_path / "wiki/staging/personal/alice/granola/.dry_run/run-1.jsonl"
    assert summary.selected_count == 1
    assert summary.candidate_count == 2
    assert summary.selected_source_ids == ["granola-1"]
    lines = summary.path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["fact"]["kind"] == "relationship"
    assert first["support_quote"] == "Wealthpoint needs SOC2 before rollout."
    assert not (tmp_path / "wiki/staging/personal/alice/.facts").exists()


def test_write_extraction_dry_run_preserves_selected_ids_when_all_candidates_drop(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )

    summary = write_extraction_dry_run(
        [(_report("granola-1", confidence=0.1), item)],
        wiki_root=tmp_path / "wiki",
        run_id="run-1",
        min_confidence=0.99,
    )

    assert summary.selected_count == 1
    assert summary.candidate_count == 0
    assert summary.dropped_low_confidence == 2
    assert summary.selected_source_ids == ["granola-1"]
    assert summary.path.read_text() == ""


def test_write_extraction_dry_run_rejects_path_unsafe_run_id(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    item = _stage(
        writer,
        "granola-1",
        meeting_id="m-1",
        title="Wealthpoint",
        transcript="Wealthpoint needs SOC2.",
        started_at="2026-04-10T09:00:00Z",
    )

    with pytest.raises(ValueError, match="run_id"):
        write_extraction_dry_run(
            [(_report("granola-1"), item)],
            wiki_root=tmp_path / "wiki",
            run_id="../escape",
        )
