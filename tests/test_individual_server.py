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


# ---------- record_facts / invalidate_fact ----------


def test_record_facts_single_inserts_triple_with_provenance(installed_ctx, kg) -> None:
    out = server.record_facts(
        entity_name="Acme Corp",
        entity_type="organization",
        facts=[
            {"predicate": "founded", "object": "2024", "confidence": 0.95},
        ],
        source_closet="conversational",
        source_file="session-abc",
    )
    assert out["entity_id"] == "acme-corp"
    assert out["created_entity"] is True
    assert out["inserted_count"] == 1
    assert out["corroborated_count"] == 0
    assert out["facts"][0]["status"] == "inserted"

    triples = kg.query_entity("acme-corp", direction="outgoing")
    assert any(
        t.predicate == "founded" and t.object == "2024" and t.source_closet == "conversational"
        for t in triples
    )


def test_record_facts_upsert_re_run_corroborates_not_duplicates(installed_ctx, kg) -> None:
    args = dict(
        entity_name="Beta Fund",
        entity_type="organization",
        facts=[{"predicate": "located_in", "object": "London"}],
        source_closet="conversational",
        source_file="session-xyz",
    )
    out_a = server.record_facts(**args)
    out_b = server.record_facts(**args)

    assert out_a["facts"][0]["status"] == "inserted"
    assert out_b["facts"][0]["status"] == "corroborated"

    triples = [
        t for t in kg.query_entity("beta-fund", direction="outgoing") if t.predicate == "located_in"
    ]
    # Corroboration → confidence bumps on the same triple, not a new row.
    assert len(triples) == 1


def test_record_facts_supersede_invalidates_prior(installed_ctx, kg) -> None:
    server.record_facts(
        entity_name="Carol",
        entity_type="person",
        facts=[{"predicate": "role", "object": "engineer"}],
        source_closet="conversational",
        source_file="s1",
    )
    out = server.record_facts(
        entity_name="Carol",
        entity_type="person",
        facts=[{"predicate": "role", "object": "manager", "write_mode": "supersede"}],
        source_closet="conversational",
        source_file="s2",
    )
    assert out["facts"][0]["status"] == "superseded"
    assert out["superseded_count"] == 1

    triples = kg.query_entity("carol", direction="outgoing")
    role_triples = [t for t in triples if t.predicate == "role"]
    # One currently-true ('manager'), one invalidated ('engineer').
    current = [t for t in role_triples if t.valid_to is None]
    invalidated = [t for t in role_triples if t.valid_to is not None]
    assert len(current) == 1 and current[0].object == "manager"
    assert len(invalidated) == 1 and invalidated[0].object == "engineer"


def test_record_facts_dry_run_does_not_write(installed_ctx, kg) -> None:
    out = server.record_facts(
        entity_name="Delta Co",
        entity_type="organization",
        facts=[{"predicate": "uses", "object": "rust"}],
        source_closet="conversational",
        source_file="s",
        dry_run=True,
    )
    assert out["dry_run"] is True
    assert out["facts"][0]["status"] == "would_insert"
    # KG must remain empty for this entity.
    assert kg.query_entity("delta-co", direction="outgoing") == []


def test_record_facts_object_typed_as_entity_registers_object_entity(installed_ctx, kg) -> None:
    out = server.record_facts(
        entity_name="Echo",
        entity_type="person",
        facts=[
            {
                "predicate": "works_at",
                "object": {"value": "Foxtrot Inc", "type": "entity", "entity_type": "company"},
            }
        ],
        source_closet="conversational",
        source_file="s",
    )
    # Object should be slugified and registered.
    assert out["facts"][0]["object"] == "foxtrot-inc"
    assert out["facts"][0]["object_type"] == "entity"

    # Verify the object entity exists in the entities table by querying.
    # Easiest path: outgoing triples on "echo" should reference "foxtrot-inc".
    triples = kg.query_entity("echo", direction="outgoing")
    assert any(t.predicate == "works_at" and t.object == "foxtrot-inc" for t in triples)


def test_record_facts_batched_writes_independently_track_outcomes(installed_ctx, kg) -> None:
    out = server.record_facts(
        entity_name="Gamma",
        entity_type="person",
        facts=[
            {"predicate": "role", "object": "founder"},
            {"predicate": "lives_in", "object": "Cape Town"},
            {"predicate": "uses_tool", "object": "Linear"},
        ],
        source_closet="conversational",
        source_file="s",
    )
    assert out["inserted_count"] == 3
    assert len(out["facts"]) == 3
    triples = kg.query_entity("gamma", direction="outgoing")
    assert {(t.predicate, t.object) for t in triples} >= {
        ("role", "founder"),
        ("lives_in", "Cape Town"),
        ("uses_tool", "Linear"),
    }


def test_record_facts_skips_fact_missing_predicate(installed_ctx) -> None:
    out = server.record_facts(
        entity_name="Hotel",
        facts=[
            {"object": "no predicate here"},
            {"predicate": "valid", "object": "yes"},
        ],
        source_closet="conversational",
        source_file="s",
    )
    statuses = [f["status"] for f in out["facts"]]
    assert "skipped" in statuses
    assert "inserted" in statuses
    assert out["skipped_count"] == 1
    assert out["inserted_count"] == 1


def test_record_facts_clamps_out_of_range_confidence(installed_ctx) -> None:
    out = server.record_facts(
        entity_name="India",
        facts=[{"predicate": "weird_metric", "object": "x", "confidence": 5.0}],
        source_closet="conversational",
        source_file="s",
    )
    assert out["facts"][0]["confidence"] == 1.0
    assert any("clamped" in w for w in out["warnings"])


def test_record_facts_requires_at_least_one_fact(installed_ctx) -> None:
    with pytest.raises(ValueError, match="at least one fact"):
        server.record_facts(
            entity_name="Juliet",
            facts=[],
            source_closet="conversational",
            source_file="s",
        )


def test_record_facts_requires_source(installed_ctx) -> None:
    with pytest.raises(ValueError, match="source_closet"):
        server.record_facts(
            entity_name="Kilo",
            facts=[{"predicate": "x", "object": "y"}],
            source_closet="",
            source_file="s",
        )


def test_invalidate_fact_marks_triple_ended(installed_ctx, kg) -> None:
    kg.add_triple(
        "lima",
        "role",
        "intern",
        valid_from=date(2026, 1, 1),
        source_closet="conversational",
        source_file="s",
    )
    out = server.invalidate_fact(
        subject="lima",
        predicate="role",
        object="intern",
        rationale="user corrected: lima is now a full-time hire",
        ended=date(2026, 4, 1),
    )
    assert out["invalidated_count"] == 1
    triples = [t for t in kg.query_entity("lima", direction="outgoing") if t.predicate == "role"]
    assert all(t.valid_to is not None for t in triples)


def test_invalidate_fact_requires_rationale(installed_ctx) -> None:
    with pytest.raises(ValueError, match="rationale"):
        server.invalidate_fact(
            subject="mike",
            predicate="role",
            object="x",
            rationale="",
        )


def test_record_facts_event_time_falls_back_to_valid_from(installed_ctx, kg) -> None:
    server.record_facts(
        entity_name="November",
        entity_type="person",
        facts=[
            {
                "predicate": "met_with",
                "object": "Oscar",
                "event_time": "2026-03-15T14:30:00+00:00",
            }
        ],
        source_closet="conversational",
        source_file="s",
    )
    triples = [
        t for t in kg.query_entity("november", direction="outgoing") if t.predicate == "met_with"
    ]
    assert len(triples) == 1
    # event_time → valid_from when valid_from not explicit
    assert triples[0].valid_from == date(2026, 3, 15)
