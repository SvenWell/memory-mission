"""Tests for component 0.4 — Observability + Audit Trail."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from memory_mission.cli import app
from memory_mission.observability import (
    EVENTS_FILENAME,
    ExtractionEvent,
    ObservabilityLogger,
    PromotionEvent,
    RetrievalEvent,
    log_extraction,
    log_promotion,
    log_retrieval,
    observability_scope,
    parse_event_line,
    serialize_event,
)

# ---------- Event schema ----------


def test_extraction_event_minimal_fields() -> None:
    """An ExtractionEvent builds with the minimal required fields and is frozen."""
    event = ExtractionEvent(
        firm_id="acme",
        source_interaction_id="email-1",
        source_type="email",
        extracted_facts=[{"claim": "x"}],
        confidence_scores={"claim": 0.8},
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        prompt_hash="sha256:abc",
    )
    assert event.event_type == "extraction"
    assert event.schema_version == 1
    assert event.firm_id == "acme"
    with pytest.raises(ValidationError):
        event.firm_id = "other"  # frozen, should raise  # type: ignore[misc]


def test_event_serialization_roundtrip() -> None:
    """Event -> JSON -> Event preserves data and type discrimination."""
    original = RetrievalEvent(
        firm_id="acme",
        employee_id="sarah",
        query="Q3 preferences",
        tier="navigate",
        pages_loaded=["clients/acme-corp/profile.md"],
        token_budget=8000,
        tokens_used=2300,
        latency_ms=120,
    )
    line = original.model_dump_json()
    parsed = parse_event_line(line)
    assert isinstance(parsed, RetrievalEvent)
    assert parsed.event_id == original.event_id
    assert parsed.query == original.query


def test_extra_fields_rejected() -> None:
    """Extra fields on an event raise ValidationError (strict schema)."""
    with pytest.raises(ValidationError):
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="email-1",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            prompt_hash="sha256:abc",
            rogue_field="not allowed",  # type: ignore[call-arg]
        )


# ---------- Logger: append-only + round-trip ----------


def test_logger_writes_and_reads(tmp_path: Path) -> None:
    """Write three events, read them back in insertion order."""
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = [
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id=f"email-{i}",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            prompt_hash="sha256:x",
        )
        for i in range(3)
    ]
    for event in events:
        logger.write(event)

    read_back = list(logger.read_all())
    assert len(read_back) == 3
    assert [e.source_interaction_id for e in read_back] == [  # type: ignore[attr-defined]
        "email-0",
        "email-1",
        "email-2",
    ]
    assert logger.count() == 3


def test_logger_append_only_not_truncating(tmp_path: Path) -> None:
    """Reopening the logger and writing more appends — doesn't truncate."""
    logger_a = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    logger_a.write(
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="first",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="x",
        )
    )

    logger_b = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    logger_b.write(
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="second",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="x",
        )
    )

    events = list(logger_b.read_all())
    assert len(events) == 2
    assert [e.source_interaction_id for e in events] == ["first", "second"]  # type: ignore[attr-defined]


def test_logger_rejects_cross_firm_write(tmp_path: Path) -> None:
    """A logger scoped to firm A refuses to write an event for firm B."""
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="firm-a")
    alien_event = ExtractionEvent(
        firm_id="firm-b",
        source_interaction_id="x",
        source_type="email",
        extracted_facts=[],
        confidence_scores={},
        llm_provider="anthropic",
        llm_model="m",
        prompt_hash="h",
    )
    with pytest.raises(ValueError, match="does not match logger"):
        logger.write(alien_event)


def test_multi_firm_isolation(tmp_path: Path) -> None:
    """Firm A logs can't leak into Firm B logs."""
    logger_a = ObservabilityLogger(observability_root=tmp_path, firm_id="firm-a")
    logger_b = ObservabilityLogger(observability_root=tmp_path, firm_id="firm-b")

    logger_a.write(
        ExtractionEvent(
            firm_id="firm-a",
            source_interaction_id="a-1",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
    )
    logger_b.write(
        ExtractionEvent(
            firm_id="firm-b",
            source_interaction_id="b-1",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
    )

    events_a = list(logger_a.read_all())
    events_b = list(logger_b.read_all())
    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_a[0].firm_id == "firm-a"
    assert events_b[0].firm_id == "firm-b"

    # Files live in separate directories.
    assert (tmp_path / "firm-a" / EVENTS_FILENAME).exists()
    assert (tmp_path / "firm-b" / EVENTS_FILENAME).exists()


def test_empty_firm_id_rejected(tmp_path: Path) -> None:
    """Empty firm_id is a programming bug — fail loudly."""
    with pytest.raises(ValueError, match="firm_id cannot be empty"):
        ObservabilityLogger(observability_root=tmp_path, firm_id="")


# ---------- Ambient context + convenience API ----------


def test_scope_requires_firm_id(tmp_path: Path) -> None:
    """Calling log_extraction outside a scope raises."""
    with pytest.raises(RuntimeError, match="No firm_id"):
        log_extraction(
            source_interaction_id="x",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )


def test_scope_binds_context_and_logs(tmp_path: Path) -> None:
    """Within a scope, convenience API logs with correct firm/employee/trace."""
    with observability_scope(
        observability_root=tmp_path,
        firm_id="acme",
        employee_id="sarah",
    ):
        event = log_extraction(
            source_interaction_id="call-1",
            source_type="transcript",
            extracted_facts=[{"claim": "x"}],
            confidence_scores={"claim": 0.9},
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            prompt_hash="h",
        )
    assert event.firm_id == "acme"
    assert event.employee_id == "sarah"
    assert event.trace_id is not None

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    events = list(logger.read_all())
    assert len(events) == 1
    assert events[0].event_id == event.event_id


def test_scope_generates_trace_id_if_missing(tmp_path: Path) -> None:
    """Scope auto-generates a trace_id when one isn't supplied."""
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        event = log_retrieval(
            query="x",
            tier="discover",
            pages_loaded=[],
            token_budget=8000,
            tokens_used=100,
            latency_ms=50,
        )
    assert event.trace_id is not None


def test_scope_honors_explicit_trace_id(tmp_path: Path) -> None:
    """An explicit trace_id propagates to events in that scope."""
    trace = uuid4()
    with observability_scope(observability_root=tmp_path, firm_id="acme", trace_id=trace):
        e1 = log_extraction(
            source_interaction_id="a",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
        e2 = log_retrieval(
            query="x",
            tier="discover",
            pages_loaded=[],
            token_budget=0,
            tokens_used=0,
            latency_ms=0,
        )
    assert e1.trace_id == trace
    assert e2.trace_id == trace


def test_nested_scopes_isolate(tmp_path: Path) -> None:
    """Nested scopes bind independently and restore on exit."""
    with observability_scope(
        observability_root=tmp_path, firm_id="firm-outer", employee_id="alice"
    ):
        e1 = log_promotion(
            candidate_fact={"x": 1},
            target_page="clients/x",
            signal_scores={},
            total_score=0.5,
            gates={},
            decision="proposed",
            reviewer="agent-1",
            reviewer_type="agent",
        )
        assert e1.firm_id == "firm-outer"
        assert e1.employee_id == "alice"

        with observability_scope(
            observability_root=tmp_path, firm_id="firm-inner", employee_id="bob"
        ):
            e2 = log_promotion(
                candidate_fact={"x": 2},
                target_page="clients/y",
                signal_scores={},
                total_score=0.5,
                gates={},
                decision="proposed",
                reviewer="agent-1",
                reviewer_type="agent",
            )
            assert e2.firm_id == "firm-inner"
            assert e2.employee_id == "bob"

        # After inner scope exits, outer context is restored.
        e3 = log_promotion(
            candidate_fact={"x": 3},
            target_page="clients/z",
            signal_scores={},
            total_score=0.5,
            gates={},
            decision="proposed",
            reviewer="agent-1",
            reviewer_type="agent",
        )
        assert e3.firm_id == "firm-outer"
        assert e3.employee_id == "alice"


# ---------- Concurrent writes ----------


def _write_batch(args: tuple[Path, str, int]) -> None:
    """Worker entrypoint for multi-process test."""
    root, firm_id, worker_id = args
    logger = ObservabilityLogger(observability_root=root, firm_id=firm_id)
    for i in range(20):
        logger.write(
            ExtractionEvent(
                firm_id=firm_id,
                source_interaction_id=f"w{worker_id}-e{i}",
                source_type="email",
                extracted_facts=[],
                confidence_scores={},
                llm_provider="anthropic",
                llm_model="m",
                prompt_hash="h",
            )
        )


def test_concurrent_writes_no_corruption(tmp_path: Path) -> None:
    """4 processes × 20 events = 80 lines, every one parseable."""
    firm_id = "acme"
    tasks = [(tmp_path, firm_id, worker_id) for worker_id in range(4)]
    with multiprocessing.Pool(processes=4) as pool:
        pool.map(_write_batch, tasks)

    logger = ObservabilityLogger(observability_root=tmp_path, firm_id=firm_id)
    # Every line must be a valid event (no torn writes).
    events = list(logger.read_all())
    assert len(events) == 80
    # Every source_interaction_id should be unique (no dupes from races).
    ids = {e.source_interaction_id for e in events}  # type: ignore[attr-defined]
    assert len(ids) == 80


# ---------- CLI ----------


def test_cli_log_count_command(tmp_path: Path) -> None:
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    logger.write(
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="x",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["log", "count", "--firm", "acme", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "1"


def test_cli_log_path_command(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["log", "path", "--firm", "acme", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert str(tmp_path / "acme" / EVENTS_FILENAME) in result.stdout


def test_cli_log_tail_command(tmp_path: Path) -> None:
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    logger.write(
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="visible-event",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["log", "tail", "--firm", "acme", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0
    # stdout should contain the JSONL representation of the event.
    parsed = json.loads(result.stdout.strip().splitlines()[-1])
    assert parsed["source_interaction_id"] == "visible-event"
    assert parsed["event_type"] == "extraction"


def test_cli_log_tail_filters_event_type(tmp_path: Path) -> None:
    logger = ObservabilityLogger(observability_root=tmp_path, firm_id="acme")
    logger.write(
        ExtractionEvent(
            firm_id="acme",
            source_interaction_id="ext-1",
            source_type="email",
            extracted_facts=[],
            confidence_scores={},
            llm_provider="anthropic",
            llm_model="m",
            prompt_hash="h",
        )
    )
    logger.write(
        PromotionEvent(
            firm_id="acme",
            candidate_fact={"x": 1},
            target_page="clients/acme",
            signal_scores={},
            total_score=0.9,
            gates={"min_score": True},
            decision="approved",
            reviewer="human:jane",
            reviewer_type="human",
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "log",
            "tail",
            "--firm",
            "acme",
            "--root",
            str(tmp_path),
            "--event-type",
            "promotion",
        ],
    )
    assert result.exit_code == 0
    lines = [line for line in result.stdout.strip().splitlines() if line]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "promotion"


# ---------- Serialization helper ----------


def test_serialize_event_returns_plain_dict(tmp_path: Path) -> None:
    event = RetrievalEvent(
        firm_id="acme",
        query="x",
        tier="discover",
        pages_loaded=[],
        token_budget=0,
        tokens_used=0,
        latency_ms=0,
    )
    data = serialize_event(event)
    assert isinstance(data, dict)
    assert data["event_type"] == "retrieval"
    # Should be JSON-serializable (e.g. UUID rendered as string).
    assert isinstance(json.dumps(data), str)
