"""Tests for ``MemoryMissionProvider`` (ADR-0015 §4 + Hermes integration).

Exercise the Hermes ``MemoryProvider`` ABC contract via duck-typing —
we don't depend on Hermes itself. Tests cover:

- name/is_available/get_config_schema/save_config (required surface)
- get_tool_schemas (correct shape + names)
- handle_tool_call dispatch for each of the 8 tools
- prefetch returns the rendered boot context
- system_prompt_block names the provider when initialized
- shutdown closes handles
- initialize errors when no user_id/root supplied
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.integrations.hermes_provider import (
    TOOL_BOOT_CONTEXT,
    TOOL_LIST_THREADS,
    TOOL_QUERY_ENTITY,
    TOOL_RECORD_COMMITMENT,
    TOOL_RECORD_DECISION,
    TOOL_RECORD_PREFERENCE,
    TOOL_SEARCH_RECALL,
    TOOL_THREAD_STATUS,
    MemoryMissionProvider,
)
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph


@pytest.fixture
def provider(tmp_path: Path) -> MemoryMissionProvider:
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path / "firm",
        employee_id="sven",
        identity_resolver=resolver,
    )
    engine = InMemoryEngine()
    engine.connect()
    p = MemoryMissionProvider()
    p.install_handles_for_test(
        user_id="sven",
        kg=kg,
        engine=engine,
        identity=resolver,
        root=tmp_path,
    )
    yield p
    kg.close()


# ---------- Required-surface contract ----------


def test_name_is_memory_mission() -> None:
    assert MemoryMissionProvider().name == "memory-mission"


def test_is_available_true_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MM_PROFILE", "sven")
    monkeypatch.setenv("MM_ROOT", "/tmp/mm")
    assert MemoryMissionProvider().is_available() is True


def test_is_available_false_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_PROFILE", raising=False)
    monkeypatch.delenv("MM_ROOT", raising=False)
    assert MemoryMissionProvider().is_available() is False


def test_get_config_schema_lists_user_id_and_root() -> None:
    keys = {field["key"] for field in MemoryMissionProvider().get_config_schema()}
    assert {"user_id", "root"} <= keys


def test_save_config_writes_json_file(tmp_path: Path) -> None:
    MemoryMissionProvider().save_config(
        {"user_id": "sven", "root": str(tmp_path)},
        hermes_home=tmp_path,
    )
    payload = json.loads((tmp_path / "memory-mission.json").read_text())
    assert payload["user_id"] == "sven"


def test_initialize_errors_without_user_or_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_PROFILE", raising=False)
    monkeypatch.delenv("MM_ROOT", raising=False)
    with pytest.raises(ValueError, match="user_id"):
        MemoryMissionProvider().initialize("session-x")


def test_initialize_with_explicit_kwargs(tmp_path: Path) -> None:
    p = MemoryMissionProvider()
    p.initialize("session-x", user_id="sven", root=tmp_path)
    assert p.name == "memory-mission"
    p.shutdown()


# ---------- Tool schemas ----------


def test_get_tool_schemas_lists_eight_mm_tools(provider: MemoryMissionProvider) -> None:
    names = [s["name"] for s in provider.get_tool_schemas()]
    assert names == [
        TOOL_BOOT_CONTEXT,
        TOOL_LIST_THREADS,
        TOOL_THREAD_STATUS,
        TOOL_RECORD_COMMITMENT,
        TOOL_RECORD_PREFERENCE,
        TOOL_RECORD_DECISION,
        TOOL_QUERY_ENTITY,
        TOOL_SEARCH_RECALL,
    ]
    # Every schema names a tool starting with the mm_ prefix to avoid
    # collision with other Hermes providers' tool names.
    assert all(n.startswith("mm_") for n in names)


# ---------- Tool dispatch ----------


def test_boot_context_tool_returns_render_and_aspects(provider: MemoryMissionProvider) -> None:
    out = provider.handle_tool_call(TOOL_BOOT_CONTEXT, {})
    assert "render" in out
    assert "aspect_counts" in out
    assert out["aspect_counts"]["active_threads"] == 0


def test_thread_status_tool_writes_then_lists(provider: MemoryMissionProvider) -> None:
    provider.handle_tool_call(
        TOOL_THREAD_STATUS,
        {
            "thread_id": "thread-x",
            "status": "active",
            "source_closet": "conversational",
            "source_file": "session-1",
        },
    )
    threads = provider.handle_tool_call(TOOL_LIST_THREADS, {})
    assert len(threads) == 1
    assert threads[0]["status"] == "active"


def test_thread_status_tool_invalidates_prior(provider: MemoryMissionProvider) -> None:
    for status in ("active", "blocked"):
        provider.handle_tool_call(
            TOOL_THREAD_STATUS,
            {
                "thread_id": "thread-y",
                "status": status,
                "source_closet": "conversational",
                "source_file": "session-1",
            },
        )
    threads = provider.handle_tool_call(TOOL_LIST_THREADS, {})
    assert len(threads) == 1
    assert threads[0]["status"] == "blocked"


def test_record_commitment_writes_three_triples(provider: MemoryMissionProvider) -> None:
    out = provider.handle_tool_call(
        TOOL_RECORD_COMMITMENT,
        {
            "commitment_id": "commit-1",
            "description": "Ship Memory Mission Individual",
            "due_by": "2026-05-04",
            "source_closet": "conversational",
            "source_file": "session-1",
        },
    )
    assert out["commitment_id"] == "commit-1"
    assert len(out["triples"]) == 3


def test_record_preference_replaces_prior(provider: MemoryMissionProvider) -> None:
    args = {
        "predicate": "prefers_reply_style",
        "value": "concise",
        "source_closet": "conversational",
        "source_file": "session-1",
    }
    provider.handle_tool_call(TOOL_RECORD_PREFERENCE, args)
    provider.handle_tool_call(
        TOOL_RECORD_PREFERENCE,
        {**args, "value": "conversational", "source_file": "session-2"},
    )
    boot = provider.handle_tool_call(TOOL_BOOT_CONTEXT, {})
    matching = [p for p in boot["preferences"] if p["predicate"] == "prefers_reply_style"]
    assert len(matching) == 1
    assert matching[0]["value"] == "conversational"


def test_record_preference_rejects_non_prefers_predicate(
    provider: MemoryMissionProvider,
) -> None:
    with pytest.raises(ValueError, match="must start with"):
        provider.handle_tool_call(
            TOOL_RECORD_PREFERENCE,
            {
                "predicate": "knows",
                "value": "x",
                "source_closet": "conversational",
                "source_file": "s",
            },
        )


def test_record_decision_surfaces_in_boot_context(provider: MemoryMissionProvider) -> None:
    provider.handle_tool_call(
        TOOL_RECORD_DECISION,
        {
            "slug": "adopted-uv",
            "title": "Adopted uv",
            "summary": "Standardize on uv.",
            "decided_at": "2026-04-20",
            "source_closet": "conversational",
            "source_file": "session-1",
        },
    )
    boot = provider.handle_tool_call(TOOL_BOOT_CONTEXT, {})
    decisions = boot["recent_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["slug"] == "adopted-uv"


def test_query_entity_returns_currently_true_only(provider: MemoryMissionProvider) -> None:
    kg = provider._kg  # noqa: SLF001 - tests verify provider's underlying state
    assert kg is not None
    kg.add_triple("sven", "owns", "memory-mission", valid_from=date(2026, 4, 1))
    kg.add_triple(
        "sven",
        "owned",
        "loom",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 4, 1),
    )
    triples = provider.handle_tool_call(
        TOOL_QUERY_ENTITY, {"name": "sven", "direction": "outgoing"}
    )
    objects = {t["object"] for t in triples}
    assert "memory-mission" in objects
    assert "loom" not in objects


def test_search_recall_without_backend_returns_structured_error(
    provider: MemoryMissionProvider,
) -> None:
    out = provider.handle_tool_call(TOOL_SEARCH_RECALL, {"query": "anything"})
    assert out["error"] == "no_recall_backend"
    assert out["hits"] == []


def test_unknown_tool_raises(provider: MemoryMissionProvider) -> None:
    with pytest.raises(ValueError, match="Unknown Memory Mission tool"):
        provider.handle_tool_call("mm_does_not_exist", {})


# ---------- Optional hooks ----------


def test_prefetch_returns_rendered_boot_context(provider: MemoryMissionProvider) -> None:
    out = provider.prefetch("resume work")
    assert "Boot context" in out


def test_system_prompt_block_names_provider_when_initialized(
    provider: MemoryMissionProvider,
) -> None:
    block = provider.system_prompt_block()
    assert "Memory Mission" in block
    assert "sven" in block


def test_system_prompt_block_empty_when_uninitialized() -> None:
    assert MemoryMissionProvider().system_prompt_block() == ""


def test_sync_turn_is_no_op_in_v1(provider: MemoryMissionProvider) -> None:
    """V1 contract: sync_turn returns None and doesn't crash."""
    assert provider.sync_turn("user msg", "assistant reply") is None


def test_shutdown_closes_kg(provider: MemoryMissionProvider) -> None:
    provider.shutdown()
    # After shutdown, calling a tool that needs the KG raises.
    with pytest.raises(RuntimeError, match="not initialized"):
        provider.handle_tool_call(TOOL_LIST_THREADS, {})


# ---------- register() entrypoint ----------


def test_register_calls_register_memory_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Hermes plugin discovery hook receives a context with a register
    method; verify our register() forwards a fresh provider instance."""
    from memory_mission.integrations.hermes_provider import register

    received: list[object] = []

    class FakeCtx:
        def register_memory_provider(self, provider: object) -> None:
            received.append(provider)

    register(FakeCtx())
    assert len(received) == 1
    assert isinstance(received[0], MemoryMissionProvider)
