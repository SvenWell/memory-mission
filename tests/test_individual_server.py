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


# ---------- compile_agent_context / render_agent_context (parity with firm-mode) ----------


def test_compile_agent_context_returns_packet_for_personal_plane(installed_ctx, kg) -> None:
    """Smoke test: structured packet contains role + task + attendee context."""
    kg.add_triple("alice", "works_at", "acme", source_closet="gmail", source_file="m1")
    kg.add_triple("alice", "role", "CEO", source_closet="gmail", source_file="m1")

    out = server.compile_agent_context(
        role="meeting-prep",
        task="Discovery call with Alice from Acme",
        attendees=["alice"],
    )
    assert out["role"] == "meeting-prep"
    assert "Alice" in out["task"] or "alice" in out["task"]
    assert len(out["attendees"]) == 1
    alice_ctx = out["attendees"][0]
    # Triples about alice should surface in the per-attendee context.
    outgoing_predicates = {t["predicate"] for t in alice_ctx.get("outgoing_triples", [])}
    assert "works_at" in outgoing_predicates
    assert "role" in outgoing_predicates


def test_render_agent_context_returns_markdown(installed_ctx, kg) -> None:
    """render_agent_context returns the same data as compile, formatted as markdown."""
    kg.add_triple("bob", "lives_in", "cape-town", source_closet="conversational", source_file="s1")

    rendered = server.render_agent_context(
        role="email-draft",
        task="Reply to Bob",
        attendees=["bob"],
    )
    assert isinstance(rendered, str)
    assert "email-draft" in rendered
    assert "bob" in rendered.lower()
    # Triples should appear in the rendered form.
    assert "lives_in" in rendered or "cape-town" in rendered


def test_compile_agent_context_respects_tier_floor(installed_ctx) -> None:
    """tier_floor=None ⇒ no doctrine section. We just verify the packet is well-formed."""
    out = server.compile_agent_context(
        role="meeting-prep",
        task="Cold outreach",
        attendees=["unknown-prospect"],
        tier_floor=None,
    )
    # Doctrine should be empty when no engine pages + no tier_floor.
    assert out["doctrine"]["pages"] == []


def test_compile_agent_context_with_unknown_attendee_returns_empty_context(installed_ctx) -> None:
    """An attendee we know nothing about returns empty triple lists, not an error."""
    out = server.compile_agent_context(
        role="meeting-prep",
        task="Intro",
        attendees=["ghost-of-future-past"],
    )
    assert len(out["attendees"]) == 1
    ghost = out["attendees"][0]
    assert ghost["outgoing_triples"] == []
    assert ghost["incoming_triples"] == []


# ---------- query_entity conflict surfacing ----------


def test_query_entity_returns_plain_list_when_no_conflicts(installed_ctx, kg) -> None:
    """Backwards-compat: triples without conflicts have no conflicts_with key."""
    kg.add_triple("uniqueco", "founded", "2024", source_closet="conversational", source_file="s")
    out = server.query_entity("uniqueco")
    assert len(out) == 1
    assert "conflicts_with" not in out[0]


def test_query_entity_annotates_conflicting_triples(installed_ctx, kg) -> None:
    """Two currently-true triples with same (subject, predicate) but different object."""
    kg.add_triple(
        "sara", "works_at", "acme", confidence=0.95, source_closet="gmail", source_file="msg-1"
    )
    kg.add_triple(
        "sara", "works_at", "beta", confidence=0.7, source_closet="gmail", source_file="msg-2"
    )
    out = server.query_entity("sara")
    assert len(out) == 2
    # Each triple should carry the OTHER as a conflict peer.
    for t in out:
        assert "conflicts_with" in t
        assert len(t["conflicts_with"]) == 1
        peer = t["conflicts_with"][0]
        assert peer["object"] != t["object"]
        assert "confidence" in peer
        assert "source_closet" in peer
        assert "source_file" in peer


def test_query_entity_corroboration_is_not_a_conflict(installed_ctx, kg) -> None:
    """Same (subject, predicate, object) corroborated must NOT surface as conflict."""
    kg.add_triple("vendor", "sells", "widgets", source_closet="gmail", source_file="msg-a")
    kg.corroborate(
        "vendor", "sells", "widgets", confidence=0.9, source_closet="gmail", source_file="msg-b"
    )
    out = server.query_entity("vendor")
    assert len(out) == 1
    assert "conflicts_with" not in out[0]


def test_query_entity_three_way_conflict_lists_all_peers(installed_ctx, kg) -> None:
    """Three live objects on (s, p) — each lists the other two as peers, conf-desc."""
    kg.add_triple(
        "startup",
        "lead_investor",
        "fund-a",
        confidence=0.9,
        source_closet="gmail",
        source_file="m1",
    )
    kg.add_triple(
        "startup",
        "lead_investor",
        "fund-b",
        confidence=0.6,
        source_closet="gmail",
        source_file="m2",
    )
    kg.add_triple(
        "startup",
        "lead_investor",
        "fund-c",
        confidence=0.8,
        source_closet="gmail",
        source_file="m3",
    )
    out = server.query_entity("startup")
    fund_a = next(t for t in out if t["object"] == "fund-a")
    assert len(fund_a["conflicts_with"]) == 2
    # Peers ordered by confidence desc.
    peer_objects = [p["object"] for p in fund_a["conflicts_with"]]
    assert peer_objects == ["fund-c", "fund-b"]


def test_query_entity_filters_invalidated_triples_from_conflicts(installed_ctx, kg) -> None:
    """Invalidated (valid_to set) triples shouldn't surface as a conflict against the live one."""
    kg.add_triple("joe", "role", "engineer", source_closet="conversational", source_file="s1")
    kg.invalidate("joe", "role", "engineer", ended=date(2026, 4, 1))
    kg.add_triple("joe", "role", "manager", source_closet="conversational", source_file="s2")
    out = server.query_entity("joe")
    # Only one currently-true triple → no conflict annotation.
    assert len(out) == 1
    assert out[0]["object"] == "manager"
    assert "conflicts_with" not in out[0]


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
