"""Tests for the extraction layer (step 9)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_mission.extraction import (
    EXTRACTION_PROMPT,
    EventFact,
    ExtractionReport,
    ExtractionWriter,
    IdentityFact,
    OpenQuestion,
    PreferenceFact,
    RelationshipFact,
    TierCrossing,
    UpdateFact,
    ingest_facts,
)
from memory_mission.ingestion import MentionTracker

# ---------- Helpers ----------


def _identity(name: str, **kw) -> IdentityFact:
    return IdentityFact(
        confidence=kw.pop("confidence", 0.9),
        support_quote=kw.pop("support_quote", f"mention of {name}"),
        entity_name=name,
        entity_type=kw.pop("entity_type", "person"),
        properties=kw.pop("properties", {}),
    )


def _relationship(subj: str, pred: str, obj: str, **kw) -> RelationshipFact:
    return RelationshipFact(
        confidence=kw.pop("confidence", 0.9),
        support_quote=kw.pop("support_quote", f"{subj} {pred} {obj}"),
        subject=subj,
        predicate=pred,
        object=obj,
    )


def _sample_report(
    *,
    source: str = "gmail",
    source_id: str = "msg-1",
    target_plane: str = "personal",
    employee_id: str | None = "alice",
) -> ExtractionReport:
    return ExtractionReport(
        source=source,
        source_id=source_id,
        target_plane=target_plane,
        employee_id=employee_id,
        extracted_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        facts=[
            _identity("sarah-chen", entity_type="person"),
            _identity("acme-corp", entity_type="company"),
            _relationship("sarah-chen", "works_at", "acme-corp"),
        ],
    )


# ---------- Fact schema: common fields ----------


def test_fact_confidence_must_be_in_range() -> None:
    for bad in (-0.1, 1.1, 2.0):
        with pytest.raises(ValidationError):
            IdentityFact(
                confidence=bad,
                support_quote="q",
                entity_name="x",
                entity_type="person",
            )


def test_fact_requires_nonempty_support_quote() -> None:
    with pytest.raises(ValidationError):
        IdentityFact(
            confidence=0.5,
            support_quote="",
            entity_name="x",
            entity_type="person",
        )


def test_fact_is_frozen() -> None:
    fact = _identity("x")
    with pytest.raises(ValidationError):
        fact.entity_name = "y"  # type: ignore[misc]


def test_fact_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IdentityFact(
            confidence=0.5,
            support_quote="q",
            entity_name="x",
            entity_type="person",
            unexpected="oops",  # type: ignore[call-arg]
        )


# ---------- Discriminated-union parsing ----------


def test_report_parses_all_six_fact_kinds_from_json() -> None:
    payload = {
        "source": "gmail",
        "source_id": "msg-1",
        "target_plane": "personal",
        "employee_id": "alice",
        "facts": [
            {
                "kind": "identity",
                "confidence": 0.9,
                "support_quote": "Sarah",
                "entity_name": "sarah",
                "entity_type": "person",
            },
            {
                "kind": "relationship",
                "confidence": 0.9,
                "support_quote": "Sarah works at Acme",
                "subject": "sarah",
                "predicate": "works_at",
                "object": "acme",
            },
            {
                "kind": "preference",
                "confidence": 0.8,
                "support_quote": "prefers direct",
                "subject": "sarah",
                "preference": "direct communication",
            },
            {
                "kind": "event",
                "confidence": 0.95,
                "support_quote": "closed Series B",
                "entity_name": "acme",
                "event_date": "2026-03-15",
                "description": "Closed Series B",
            },
            {
                "kind": "update",
                "confidence": 0.85,
                "support_quote": "switched to clari",
                "subject": "acme",
                "predicate": "uses_tool",
                "new_object": "clari",
                "supersedes_object": "gong",
                "effective_date": None,
            },
            {
                "kind": "open_question",
                "confidence": 0.3,
                "support_quote": "unclear if mark is still cfo",
                "question": "Is Mark still CFO at Acme?",
                "hypothesis": "probably departed",
            },
        ],
    }
    report = ExtractionReport.model_validate(payload)
    kinds = [f.kind for f in report.facts]
    assert kinds == [
        "identity",
        "relationship",
        "preference",
        "event",
        "update",
        "open_question",
    ]
    # Type narrowing works per the discriminator.
    event = next(f for f in report.facts if isinstance(f, EventFact))
    assert event.event_date == date(2026, 3, 15)
    update = next(f for f in report.facts if isinstance(f, UpdateFact))
    assert update.supersedes_object == "gong"
    oq = next(f for f in report.facts if isinstance(f, OpenQuestion))
    assert oq.hypothesis == "probably departed"


def test_report_rejects_unknown_fact_kind() -> None:
    with pytest.raises(ValidationError):
        ExtractionReport.model_validate(
            {
                "source": "gmail",
                "source_id": "msg-1",
                "target_plane": "personal",
                "employee_id": "alice",
                "facts": [
                    {
                        "kind": "made-up",
                        "confidence": 0.5,
                        "support_quote": "q",
                    }
                ],
            }
        )


def test_report_firm_plane_allows_null_employee_id() -> None:
    report = ExtractionReport.model_validate(
        {
            "source": "drive",
            "source_id": "doc-1",
            "target_plane": "firm",
            "employee_id": None,
            "facts": [],
        }
    )
    assert report.employee_id is None


# ---------- entity_names + tier mention ----------


def test_entity_names_dedupes_across_facts() -> None:
    report = ExtractionReport(
        source="gmail",
        source_id="msg-1",
        target_plane="personal",
        employee_id="alice",
        facts=[
            _identity("sarah-chen"),
            _relationship("sarah-chen", "works_at", "acme-corp"),
            _relationship("sarah-chen", "knows", "bob"),
            PreferenceFact(
                confidence=0.8,
                support_quote="prefers slack",
                subject="sarah-chen",
                preference="Slack",
            ),
            EventFact(
                confidence=0.9,
                support_quote="board meeting",
                entity_name="acme-corp",
                event_date=None,
                description="board meeting",
            ),
        ],
    )
    # sarah-chen appears many times, acme-corp twice, bob once
    assert set(report.entity_names()) == {"sarah-chen", "acme-corp", "bob"}


def test_entity_names_open_question_contributes_nothing() -> None:
    report = ExtractionReport(
        source="gmail",
        source_id="msg-1",
        target_plane="personal",
        employee_id="alice",
        facts=[
            OpenQuestion(
                confidence=0.3,
                support_quote="not sure",
                question="Did it happen?",
                hypothesis=None,
            ),
        ],
    )
    assert report.entity_names() == []


# ---------- ExtractionWriter ----------


def test_writer_personal_requires_employee_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="personal target_plane requires employee_id"):
        ExtractionWriter(wiki_root=tmp_path, source="gmail", target_plane="personal")


def test_writer_firm_rejects_employee_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="firm target_plane must not carry"):
        ExtractionWriter(
            wiki_root=tmp_path,
            source="drive",
            target_plane="firm",
            employee_id="alice",
        )


def test_writer_rejects_bad_source(tmp_path: Path) -> None:
    for bad in ["", "../", "two words", "a/b"]:
        with pytest.raises(ValueError, match="source"):
            ExtractionWriter(wiki_root=tmp_path, source=bad, target_plane="firm")


def test_writer_rejects_bad_source_id(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    report = _sample_report()
    for bad in ["../escape", "with space", "a/b"]:
        with pytest.raises(ValueError, match="source_id"):
            writer.write(report.model_copy(update={"source_id": bad}))


def test_writer_write_lands_under_facts_dir(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    path = writer.write(_sample_report())
    expected = tmp_path / "staging/personal/alice/.facts/gmail/msg-1.json"
    assert path == expected
    assert expected.exists()
    payload = json.loads(expected.read_text())
    assert payload["source"] == "gmail"
    assert payload["source_id"] == "msg-1"
    assert len(payload["facts"]) == 3


def test_writer_firm_plane_path(tmp_path: Path) -> None:
    writer = ExtractionWriter(wiki_root=tmp_path, source="drive", target_plane="firm")
    report = _sample_report(
        source="drive",
        source_id="doc-1",
        target_plane="firm",
        employee_id=None,
    )
    path = writer.write(report)
    assert path == tmp_path / "staging/firm/.facts/drive/doc-1.json"


def test_writer_rejects_mismatched_report_source(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    with pytest.raises(ValueError, match="source"):
        writer.write(_sample_report(source="granola"))


def test_writer_rejects_mismatched_report_target_plane(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    with pytest.raises(ValueError, match="target_plane"):
        writer.write(_sample_report(target_plane="firm", employee_id=None))


def test_writer_rejects_mismatched_report_employee_id(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    with pytest.raises(ValueError, match="employee_id"):
        writer.write(_sample_report(employee_id="bob"))


def test_writer_read_round_trips(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    original = _sample_report()
    writer.write(original)
    loaded = writer.read("msg-1")
    assert loaded is not None
    assert loaded.source_id == original.source_id
    assert len(loaded.facts) == len(original.facts)


def test_writer_read_missing_returns_none(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    assert writer.read("missing") is None


def test_writer_iter_reports_yields_all(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    for i in range(3):
        writer.write(_sample_report(source_id=f"msg-{i}"))
    reports = list(writer.iter_reports())
    assert {r.source_id for r in reports} == {"msg-0", "msg-1", "msg-2"}


def test_writer_remove_deletes_report(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    writer.write(_sample_report())
    assert writer.remove("msg-1") is True
    assert writer.remove("msg-1") is False  # idempotent
    assert writer.read("msg-1") is None


def test_writer_overwrites_existing_report(tmp_path: Path) -> None:
    writer = ExtractionWriter(
        wiki_root=tmp_path,
        source="gmail",
        target_plane="personal",
        employee_id="alice",
    )
    writer.write(_sample_report())
    updated = _sample_report().model_copy(update={"facts": [_identity("new-guy")]})
    writer.write(updated)
    loaded = writer.read("msg-1")
    assert loaded is not None
    assert [f.kind for f in loaded.facts] == ["identity"]
    assert isinstance(loaded.facts[0], IdentityFact)
    assert loaded.facts[0].entity_name == "new-guy"


# ---------- ingest_facts ----------


def test_ingest_writes_report_without_tracker(tmp_path: Path) -> None:
    result = ingest_facts(_sample_report(), wiki_root=tmp_path)
    assert result.report_path.exists()
    assert set(result.entity_names) == {"sarah-chen", "acme-corp"}
    assert result.tier_crossings == []


def test_ingest_updates_mention_tracker(tmp_path: Path) -> None:
    tracker = MentionTracker(tmp_path / "mentions.db")
    result = ingest_facts(_sample_report(), wiki_root=tmp_path, mention_tracker=tracker)
    # First mention of each entity crosses none → stub.
    assert len(result.tier_crossings) == 2
    assert all(c.previous_tier == "none" for c in result.tier_crossings)
    assert all(c.new_tier == "stub" for c in result.tier_crossings)
    assert all(c.is_promotion for c in result.tier_crossings)
    tracker.close()


def test_ingest_records_one_mention_per_entity_per_report(
    tmp_path: Path,
) -> None:
    """Even if sarah-chen appears in 5 facts within one report, count once."""
    tracker = MentionTracker(tmp_path / "mentions.db")
    report = ExtractionReport(
        source="gmail",
        source_id="msg-1",
        target_plane="personal",
        employee_id="alice",
        facts=[
            _identity("sarah-chen"),
            _relationship("sarah-chen", "works_at", "acme-corp"),
            _relationship("sarah-chen", "knows", "bob"),
            PreferenceFact(
                confidence=0.8,
                support_quote="prefers slack",
                subject="sarah-chen",
                preference="slack",
            ),
        ],
    )
    result = ingest_facts(report, wiki_root=tmp_path, mention_tracker=tracker)
    sarah_record = tracker.get("sarah-chen")
    assert sarah_record is not None
    assert sarah_record.count == 1
    assert {c.entity_name for c in result.tier_crossings} == {
        "sarah-chen",
        "acme-corp",
        "bob",
    }
    tracker.close()


def test_ingest_tier_crossings_detect_thresholds(tmp_path: Path) -> None:
    """Three reports about acme-corp should cross stub→enrich on the 3rd."""
    tracker = MentionTracker(tmp_path / "mentions.db")
    for i in range(3):
        report = ExtractionReport(
            source="gmail",
            source_id=f"msg-{i}",
            target_plane="personal",
            employee_id="alice",
            facts=[_identity("acme-corp", entity_type="company")],
        )
        result = ingest_facts(report, wiki_root=tmp_path, mention_tracker=tracker)
        acme = next(c for c in result.tier_crossings if c.entity_name == "acme-corp")
        if i == 0:
            assert (acme.previous_tier, acme.new_tier) == ("none", "stub")
        elif i == 1:
            assert (acme.previous_tier, acme.new_tier) == ("stub", "stub")
            assert acme.is_promotion is False
        else:  # i == 2, third mention crosses into enrich
            assert (acme.previous_tier, acme.new_tier) == ("stub", "enrich")
            assert acme.is_promotion is True
    tracker.close()


# ---------- TierCrossing ----------


def test_tier_crossing_is_promotion_true_on_tier_up() -> None:
    crossing = TierCrossing(entity_name="acme", previous_tier="stub", new_tier="enrich")
    assert crossing.is_promotion is True


def test_tier_crossing_is_promotion_false_on_same_tier() -> None:
    crossing = TierCrossing(entity_name="acme", previous_tier="stub", new_tier="stub")
    assert crossing.is_promotion is False


def test_tier_crossing_is_frozen() -> None:
    crossing = TierCrossing(entity_name="x", previous_tier="none", new_tier="stub")
    with pytest.raises(ValidationError):
        crossing.new_tier = "enrich"  # type: ignore[misc]


# ---------- Prompt template ----------


def test_extraction_prompt_contains_all_six_kinds() -> None:
    for kind in (
        "identity",
        "relationship",
        "preference",
        "event",
        "update",
        "open_question",
    ):
        assert kind in EXTRACTION_PROMPT


def test_extraction_prompt_states_no_quote_no_fact_rule() -> None:
    assert "support_quote" in EXTRACTION_PROMPT
    assert "No quote, no fact" in EXTRACTION_PROMPT


def test_extraction_prompt_shows_venture_firm_example() -> None:
    """The worked example should be venture-flavored, not wealth-specific."""
    assert "Series B" in EXTRACTION_PROMPT
    assert "post-money" in EXTRACTION_PROMPT
