"""Provider contract tests — pin the public API any consumer integrates against.

Distinct from ``test_hermes_provider.py`` (which exercises behavior).
This file pins the **shape** Hermes / Codex / Cursor / future agent
runtimes consume so substrate-internal refactors fail loudly when
they break the contract.

If a test in this file starts failing, that's a **breaking change
signal** — bump the major version (or revert), don't just update the
test.

Pinned surfaces:

1. Provider name (``memory_mission`` — underscored, matches Hermes
   plugin directory + config dispatch).
2. Required ``MemoryProvider`` ABC method names + signatures
   (Hermes calls these).
3. Optional hook method names (Hermes-aware consumers may consume).
4. The 8 ``mm_*`` tool names + their JSON-schema parameter shapes.
5. ``register(ctx)`` plugin discovery entrypoint.
6. ``compile_individual_boot_context`` primitive surface (consumers
   importing it directly without going through the provider).
7. Provenance contract: every write tool requires ``source_closet``
   and ``source_file``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from memory_mission.integrations import hermes_provider
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
    register,
)

# ---------- 1. Provider name ----------


def test_provider_name_is_memory_mission_underscore() -> None:
    """The string Hermes config references in ``provider: <name>``."""
    assert MemoryMissionProvider().name == "memory_mission"


# ---------- 2. Required Hermes MemoryProvider ABC methods ----------

REQUIRED_METHODS: tuple[str, ...] = (
    "name",
    "is_available",
    "initialize",
    "get_tool_schemas",
    "handle_tool_call",
    "get_config_schema",
    "save_config",
)


@pytest.mark.parametrize("method_name", REQUIRED_METHODS)
def test_required_method_exists(method_name: str) -> None:
    """Every Hermes-required method must be callable on the provider."""
    p = MemoryMissionProvider()
    member = getattr(p, method_name, None)
    assert member is not None, f"missing required member: {method_name}"
    # name is a property — accessing it returns a string. Everything
    # else must be callable.
    if method_name != "name":
        assert callable(member), f"{method_name} must be callable"


def test_initialize_signature_accepts_session_id_positional() -> None:
    """Hermes calls ``initialize(session_id, **kwargs)``."""
    sig = inspect.signature(MemoryMissionProvider.initialize)
    params = list(sig.parameters.values())
    # First param is self.
    assert params[0].name == "self"
    # Second param is session_id (positional or positional-or-keyword).
    assert params[1].name == "session_id"
    # **kwargs is accepted (Hermes may pass extra context).
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)


def test_handle_tool_call_signature() -> None:
    """``handle_tool_call(name: str, args: dict)`` is the dispatch contract."""
    sig = inspect.signature(MemoryMissionProvider.handle_tool_call)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 2
    assert params[0].name == "name"
    assert params[1].name == "args"


def test_get_config_schema_returns_list_of_dicts() -> None:
    schema = MemoryMissionProvider().get_config_schema()
    assert isinstance(schema, list)
    assert all(isinstance(field, dict) for field in schema)
    # Each entry must declare key + description + required + env_var.
    for field in schema:
        assert {"key", "description", "required", "env_var"} <= field.keys()


def test_get_config_schema_lists_user_id_and_root_as_required() -> None:
    schema = MemoryMissionProvider().get_config_schema()
    by_key = {f["key"]: f for f in schema}
    assert by_key["user_id"]["required"] is True
    assert by_key["root"]["required"] is True
    # MM_USER_ID is the canonical env var, MM_PROFILE remains a legacy alias.
    assert by_key["user_id"]["env_var"] == "MM_USER_ID"
    assert by_key["root"]["env_var"] == "MM_ROOT"


# ---------- 3. Optional hook methods ----------

OPTIONAL_HOOKS: tuple[str, ...] = (
    "system_prompt_block",
    "prefetch",
    "sync_turn",
    "on_session_end",
    "on_pre_compress",
    "on_memory_write",
    "shutdown",
)


@pytest.mark.parametrize("hook_name", OPTIONAL_HOOKS)
def test_optional_hook_exists(hook_name: str) -> None:
    p = MemoryMissionProvider()
    member = getattr(p, hook_name, None)
    assert member is not None, f"missing optional hook: {hook_name}"
    assert callable(member)


# ---------- 4. Tool surface ----------

EXPECTED_TOOL_NAMES: tuple[str, ...] = (
    TOOL_BOOT_CONTEXT,
    TOOL_LIST_THREADS,
    TOOL_THREAD_STATUS,
    TOOL_RECORD_COMMITMENT,
    TOOL_RECORD_PREFERENCE,
    TOOL_RECORD_DECISION,
    TOOL_QUERY_ENTITY,
    TOOL_SEARCH_RECALL,
)


def test_tool_count_is_eight() -> None:
    """Adding/removing tools is a contract change. This test makes that intentional."""
    schemas = MemoryMissionProvider().get_tool_schemas()
    assert len(schemas) == 8


def test_tool_names_are_pinned() -> None:
    """Every tool name follows the mm_* convention and matches the EXPECTED set."""
    names = tuple(s["name"] for s in MemoryMissionProvider().get_tool_schemas())
    assert names == EXPECTED_TOOL_NAMES
    assert all(n.startswith("mm_") for n in names)


def test_every_tool_schema_declares_object_parameters() -> None:
    """JSON-schema-shaped parameters dict per Hermes' tool-registration spec."""
    for schema in MemoryMissionProvider().get_tool_schemas():
        assert schema["parameters"]["type"] == "object"
        assert "properties" in schema["parameters"]


@pytest.mark.parametrize(
    "tool_name",
    [
        TOOL_THREAD_STATUS,
        TOOL_RECORD_COMMITMENT,
        TOOL_RECORD_PREFERENCE,
        TOOL_RECORD_DECISION,
    ],
)
def test_write_tool_requires_provenance(tool_name: str) -> None:
    """Provenance contract: every write tool requires source_closet + source_file.

    This is the substrate-level invariant from ADR-0015 §5: simple-
    write-policy drops the proposal gate but provenance stays
    mandatory. Removing source_closet / source_file from any required
    list is a breaking governance change.
    """
    schema = next(s for s in MemoryMissionProvider().get_tool_schemas() if s["name"] == tool_name)
    required = set(schema["parameters"].get("required", []))
    assert "source_closet" in required, f"{tool_name}: source_closet must be required"
    assert "source_file" in required, f"{tool_name}: source_file must be required"


# ---------- 5. Plugin discovery ----------


def test_register_is_module_level_function() -> None:
    """``register(ctx)`` is the Hermes plugin discovery entrypoint."""
    assert callable(register)
    sig = inspect.signature(register)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "ctx"


def test_register_forwards_a_provider_to_register_memory_provider() -> None:
    received: list[object] = []

    class FakeCtx:
        def register_memory_provider(self, provider: object) -> None:
            received.append(provider)

    register(FakeCtx())
    assert len(received) == 1
    assert isinstance(received[0], MemoryMissionProvider)


def test_module_exports_expected_public_names() -> None:
    """``__all__`` pins what consumers can import without reaching into internals."""
    expected = {
        "MemoryMissionProvider",
        "register",
        "TOOL_BOOT_CONTEXT",
        "TOOL_LIST_THREADS",
        "TOOL_QUERY_ENTITY",
        "TOOL_RECORD_COMMITMENT",
        "TOOL_RECORD_DECISION",
        "TOOL_RECORD_PREFERENCE",
        "TOOL_SEARCH_RECALL",
        "TOOL_THREAD_STATUS",
    }
    actual = set(hermes_provider.__all__)
    missing = expected - actual
    assert not missing, f"hermes_provider.__all__ missing: {missing}"


# ---------- 6. Boot-context primitive direct API ----------


def test_compile_individual_boot_context_is_importable_from_synthesis() -> None:
    """Consumers importing the primitive directly get the canonical entry point."""
    from memory_mission.synthesis import compile_individual_boot_context

    assert callable(compile_individual_boot_context)


def test_compile_individual_boot_context_signature_pinned() -> None:
    """Signature pinned: keyword-only kwargs Hermes / direct callers depend on."""
    from memory_mission.synthesis import compile_individual_boot_context

    sig = inspect.signature(compile_individual_boot_context)
    params = sig.parameters
    # All caller-facing args must remain keyword-only (no positional gotchas).
    for required in ("user_id", "agent_id", "kg"):
        assert required in params, f"missing pinned kwarg: {required}"
        assert params[required].kind == inspect.Parameter.KEYWORD_ONLY
    # Optional kwargs that consumers depend on for biasing/recency.
    for optional in (
        "engine",
        "identity_resolver",
        "task_hint",
        "token_budget",
        "as_of",
    ):
        assert optional in params, f"missing pinned optional kwarg: {optional}"


# ---------- 7. Boot-context aspect contract ----------


def test_individual_boot_context_aspect_names_pinned() -> None:
    """The 6 aspect field names are part of the contract.

    Hermes parses ``compile_individual_boot_context(...).aspect_counts``
    and the rendered markdown headers. Renaming an aspect breaks every
    downstream evaluator.
    """
    from memory_mission.synthesis import IndividualBootContext

    fields = set(IndividualBootContext.model_fields.keys())
    pinned_aspects = {
        "active_threads",
        "commitments",
        "preferences",
        "recent_decisions",
        "relevant_entities",
        "project_status",
    }
    missing = pinned_aspects - fields
    assert not missing, f"IndividualBootContext lost pinned aspect: {missing}"


def test_individual_boot_context_aspect_count_keys_match_fields() -> None:
    """``aspect_counts`` keys mirror the aspect field names exactly."""
    from datetime import UTC, datetime

    from memory_mission.synthesis import IndividualBootContext

    empty = IndividualBootContext(
        user_id="u",
        agent_id="a",
        token_budget=4000,
        generated_at=datetime.now(UTC),
    )
    expected = {
        "active_threads",
        "commitments",
        "preferences",
        "recent_decisions",
        "relevant_entities",
        "project_status",
    }
    assert set(empty.aspect_counts.keys()) == expected


# ---------- 8. Hashability (Hermes downstream caches) ----------


def test_individual_boot_context_is_hashable() -> None:
    """Frozen-with-list-fields would otherwise raise; consumers cache responses."""
    from datetime import UTC, datetime

    from memory_mission.synthesis import IndividualBootContext

    ctx = IndividualBootContext(
        user_id="u",
        agent_id="a",
        token_budget=4000,
        generated_at=datetime.now(UTC),
    )
    # Must not raise. Must be deterministic across calls on the same model.
    assert hash(ctx) == hash(ctx)


# ---------- Helper: contract surface inventory snapshot ----------


def _public_callables(module: object) -> dict[str, Callable[..., Any]]:
    """Return all module-level callables that don't start with underscore."""
    return {
        name: obj for name, obj in inspect.getmembers(module, callable) if not name.startswith("_")
    }


def test_hermes_provider_module_does_not_leak_private_callables_via_all() -> None:
    """``__all__`` should never include underscore-prefixed names."""
    leaked = [n for n in hermes_provider.__all__ if n.startswith("_")]
    assert not leaked, f"hermes_provider.__all__ leaks private names: {leaked}"


# ---------- 9. Package version sync ----------


def test_package_version_matches_pyproject() -> None:
    """``memory_mission.__version__`` must match the installed dist version.

    Hermes flagged 2026-04-27 that the package metadata said 0.1.1
    while ``memory_mission.__version__`` still reported 0.1.0 — internal
    constant drift during a release. The fix: read ``__version__`` from
    ``importlib.metadata`` so it tracks the wheel's metadata
    automatically. This test pins that wiring so the bug can't recur.
    """
    from importlib.metadata import version as _pkg_version

    import memory_mission

    assert memory_mission.__version__ == _pkg_version("memory-mission")
