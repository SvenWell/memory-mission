"""Tests for the individual-mode MCP server (ADR-0015).

Exercises the tool surface directly via the module-level functions —
the FastMCP wrappers route to these. Tests use ``initialize_from_handles``
+ ``reset()`` between cases per the existing MCP test pattern.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.mcp import individual_server as server
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


@pytest.fixture
def kg(tmp_path: Path) -> PersonalKnowledgeGraph:
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path / "firm",
        employee_id="sven",
        identity_resolver=resolver,
    )
    yield pkg
    pkg.close()


@pytest.fixture
def installed_ctx(tmp_path: Path, kg: PersonalKnowledgeGraph):
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    engine = InMemoryEngine()
    engine.connect()
    obs = tmp_path / "observability"
    obs.mkdir()
    server.initialize_from_handles(
        user_id="sven",
        agent_id="hermes",
        kg=kg,
        engine=engine,
        identity=resolver,
        observability_root=obs,
    )
    yield server._ctx()
    server.reset()


# ---------- Boot context ----------


def test_get_boot_context_returns_render_and_structure(installed_ctx) -> None:
    out = server.get_boot_context()
    assert "render" in out
    assert "sven" in out["render"]
    assert "hermes" in out["render"]
    assert out["aspect_counts"] == {
        "active_threads": 0,
        "commitments": 0,
        "preferences": 0,
        "recent_decisions": 0,
        "relevant_entities": 0,
        "project_status": 0,
    }


def test_get_boot_context_with_task_hint_passes_through(installed_ctx, kg) -> None:
    kg.add_triple("memory-mission", "is_a", "project", valid_from=date(2026, 4, 1))
    kg.add_triple("loom", "is_a", "side-project", valid_from=date(2026, 4, 1))
    out = server.get_boot_context(task_hint="memory mission individual")
    assert out["task_hint"] == "memory mission individual"
    # The task_hint biases relevant_entities toward "memory-mission".
    entity_ids = [e["entity_id"] for e in out["relevant_entities"]]
    assert entity_ids[0] == "memory-mission"


# ---------- Threads ----------


def test_list_active_threads_filters_to_known_states(installed_ctx, kg) -> None:
    kg.add_triple(
        "thread-deal",
        "thread_status",
        "active",
        valid_from=date(2026, 4, 25),
        source_closet="conversational",
        source_file="session-1",
    )
    kg.add_triple(
        "thread-mystery",
        "thread_status",
        "unknown",
        valid_from=date(2026, 4, 25),
        source_closet="conversational",
        source_file="session-1",
    )
    threads = server.list_active_threads()
    ids = {t["thread_id"] for t in threads}
    assert ids == {"thread-deal"}


def test_upsert_thread_status_invalidates_prior(installed_ctx, kg) -> None:
    server.upsert_thread_status(
        thread_id="thread-x",
        status="active",
        source_closet="conversational",
        source_file="session-1",
    )
    server.upsert_thread_status(
        thread_id="thread-x",
        status="blocked",
        source_closet="conversational",
        source_file="session-1",
    )
    threads = server.list_active_threads()
    assert len(threads) == 1
    assert threads[0]["status"] == "blocked"


def test_upsert_thread_status_rejects_invalid_status(installed_ctx) -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        server.upsert_thread_status(
            thread_id="t",
            status="bogus",
            source_closet="conversational",
            source_file="session-1",
        )


def test_upsert_thread_status_requires_source(installed_ctx) -> None:
    with pytest.raises(ValueError, match="source_closet"):
        server.upsert_thread_status(
            thread_id="t",
            status="active",
            source_closet="",
            source_file="x",
        )
    with pytest.raises(ValueError, match="source_file"):
        server.upsert_thread_status(
            thread_id="t",
            status="active",
            source_closet="x",
            source_file="",
        )


# ---------- Commitments ----------


def test_record_commitment_writes_status_and_description_and_due(installed_ctx) -> None:
    out = server.record_commitment(
        commitment_id="commit-ship",
        description="Ship Memory Mission Individual mode",
        source_closet="conversational",
        source_file="session-1",
        due_by=date(2026, 5, 4),
    )
    assert out["commitment_id"] == "commit-ship"
    assert len(out["triples"]) == 3  # status + description + due_by
    boot = server.get_boot_context()
    assert len(boot["commitments"]) == 1
    c = boot["commitments"][0]
    assert c["description"] == "Ship Memory Mission Individual mode"
    assert c["due_by"] == "2026-05-04"


def test_record_commitment_without_due_by_writes_two_triples(installed_ctx) -> None:
    out = server.record_commitment(
        commitment_id="commit-x",
        description="Some commitment",
        source_closet="conversational",
        source_file="session-1",
    )
    assert len(out["triples"]) == 2  # no due_by


# ---------- Preferences ----------


def test_record_preference_replaces_prior(installed_ctx) -> None:
    server.record_preference(
        predicate="prefers_reply_style",
        value="concise",
        source_closet="conversational",
        source_file="session-1",
    )
    server.record_preference(
        predicate="prefers_reply_style",
        value="conversational",
        source_closet="conversational",
        source_file="session-2",
    )
    boot = server.get_boot_context()
    matching = [p for p in boot["preferences"] if p["predicate"] == "prefers_reply_style"]
    assert len(matching) == 1
    assert matching[0]["value"] == "conversational"


def test_record_preference_rejects_non_prefers_predicate(installed_ctx) -> None:
    with pytest.raises(ValueError, match="must start with"):
        server.record_preference(
            predicate="knows",
            value="memory-mission",
            source_closet="conversational",
            source_file="session-1",
        )


# ---------- Decisions ----------


def test_record_decision_writes_page_visible_in_boot_context(installed_ctx) -> None:
    out = server.record_decision(
        slug="adopted-uv",
        title="Adopted uv",
        summary="Standardize on uv across all repos.",
        decided_at=date(2026, 4, 20),
        source_closet="conversational",
        source_file="session-1",
    )
    assert out["slug"] == "adopted-uv"
    boot = server.get_boot_context()
    decisions = boot["recent_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["slug"] == "adopted-uv"


# ---------- Entity queries ----------


def test_query_entity_returns_currently_true_triples(installed_ctx, kg) -> None:
    kg.add_triple("sven", "owns", "memory-mission", valid_from=date(2026, 4, 1))
    kg.add_triple(
        "sven",
        "owned",
        "loom",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 4, 1),
    )
    triples = server.query_entity("sven", direction="outgoing")
    objects = {t["object"] for t in triples}
    # ``query_entity`` returns currently-true triples; loom is invalidated.
    assert "memory-mission" in objects
    assert "loom" not in objects


def test_query_entity_rejects_bad_direction(installed_ctx) -> None:
    with pytest.raises(ValueError, match="direction"):
        server.query_entity("sven", direction="sideways")


# ---------- Recall ----------


def test_search_recall_without_backend_returns_structured_error(installed_ctx) -> None:
    out = server.search_recall("anything")
    assert out["error"] == "no_recall_backend"
    assert out["hits"] == []


# ---------- resolve_entity ----------


def test_resolve_entity_passthrough_for_unknown_name(installed_ctx) -> None:
    """Bare names not registered as typed identifiers pass through unchanged."""
    out = server.resolve_entity("memory-mission")
    assert out == {
        "entity_name": "memory-mission",
        "identity_id": None,
        "canonical_name": None,
        "identifiers": [],
    }


def test_resolve_entity_resolves_typed_identifier(installed_ctx) -> None:
    identity_id = installed_ctx.identity.resolve(
        identifiers={"email:sven@example.com", "linkedin:sven-w-123"},
        entity_type="person",
        canonical_name="Sven Wellmann",
    )
    out = server.resolve_entity("email:sven@example.com")
    assert out["identity_id"] == identity_id
    assert out["canonical_name"] == "Sven Wellmann"
    assert set(out["identifiers"]) == {
        "email:sven@example.com",
        "linkedin:sven-w-123",
    }


def test_resolve_entity_rejects_empty_name(installed_ctx) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        server.resolve_entity("   ")


# ---------- CLI bootstrap: stdio-safe logging ----------


def test_configure_stdio_safe_logging_pins_factory_to_stderr() -> None:
    """MCP stdio servers must keep stdout reserved for JSON-RPC frames.

    The default structlog.PrintLoggerFactory writes to stdout — any log
    line emitted before / during mcp.run() would mix with the protocol
    stream and cause strict MCP clients to refuse the connection. This
    bootstrap helper must re-pin the factory so the very next log line
    lands on stderr, not stdout.
    """
    import sys

    import structlog

    server._configure_stdio_safe_logging()

    cfg = structlog.get_config()
    factory = cfg["logger_factory"]
    assert isinstance(factory, structlog.PrintLoggerFactory)
    # PrintLoggerFactory stashes the file as a private attr; allow the
    # public-vs-private rename without breaking by checking both shapes.
    file_attr = getattr(factory, "_file", None) or getattr(factory, "file", None)
    assert file_attr is sys.stderr, f"expected logger factory to write to stderr; got {file_attr!r}"
