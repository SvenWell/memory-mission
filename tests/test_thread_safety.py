"""Cross-thread safety tests for SQLite-backed substrate.

Regression tests for the bug Hermes' live integration hit on
2026-04-27: ``SQLite objects created in a thread can only be used in
that same thread``. Agent runtimes (Hermes, Codex, Cursor, MCP
servers) routinely create the connection in one thread and dispatch
tool calls from another — Python's stdlib sqlite3 default rejects
that unless ``check_same_thread=False`` is set on connect.

Each test creates the SQLite-backed primitive in the test's main
thread, then exercises a real read/write from a worker thread.
Without the fix, every test below raises
``sqlite3.ProgrammingError: SQLite objects created in a thread can
only be used in that same thread``.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from queue import Queue

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.ingestion.mentions import MentionTracker
from memory_mission.integrations.hermes_provider import (
    TOOL_BOOT_CONTEXT,
    TOOL_LIST_THREADS,
    TOOL_THREAD_STATUS,
    MemoryMissionProvider,
)
from memory_mission.memory.engine import InMemoryEngine
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph
from memory_mission.promotion.proposals import Proposal, ProposalStore


def _run_in_other_thread(fn):
    """Invoke ``fn`` on a worker thread; surface its return / exception to caller."""
    out: Queue = Queue()

    def _runner() -> None:
        try:
            out.put(("ok", fn()))
        except BaseException as exc:  # noqa: BLE001 - test harness re-raises
            out.put(("err", exc))

    t = threading.Thread(target=_runner)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "worker thread hung"
    kind, value = out.get_nowait()
    if kind == "err":
        raise value
    return value


def test_knowledge_graph_can_be_used_across_threads(tmp_path: Path) -> None:
    """KG opened in main thread, written + read from worker thread."""
    kg = KnowledgeGraph(tmp_path / "kg.db")
    try:
        # Write from a different thread than the one that opened the connection.
        def _write_and_read() -> int:
            kg.add_triple(
                "alice",
                "works_at",
                "acme",
                source_closet="conversational",
                source_file="session-1",
            )
            triples = kg.query_entity("alice")
            return len(triples)

        count = _run_in_other_thread(_write_and_read)
        assert count == 1
    finally:
        kg.close()


def test_personal_kg_can_be_used_across_threads(tmp_path: Path) -> None:
    """Per-employee KG opened in main thread, written from worker thread."""
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    kg = PersonalKnowledgeGraph.for_employee(
        firm_root=tmp_path / "firm",
        employee_id="sven",
        identity_resolver=resolver,
    )
    try:

        def _write_and_read() -> int:
            kg.add_triple(
                "sven",
                "prefers_reply",
                "concise",
                source_closet="conversational",
                source_file="session-1",
            )
            return len(kg.query_entity("sven"))

        count = _run_in_other_thread(_write_and_read)
        assert count == 1
    finally:
        kg.close()


def test_local_identity_resolver_can_be_used_across_threads(tmp_path: Path) -> None:
    """Identity resolver opened in main, resolved from worker thread."""
    resolver = LocalIdentityResolver(tmp_path / "identity.sqlite3")
    identity = _run_in_other_thread(
        lambda: resolver.resolve(
            identifiers={"email:sven@example.com"},
            entity_type="person",
            canonical_name="Sven",
        )
    )
    assert identity is not None
    # And the lookup also works from a different thread.
    bound = _run_in_other_thread(lambda: resolver.lookup("email:sven@example.com"))
    assert bound == identity


def test_proposal_store_can_be_used_across_threads(tmp_path: Path) -> None:
    """Proposal store opened in main, inserted + listed from worker thread."""
    store = ProposalStore(tmp_path / "proposals.db")
    try:
        proposal = Proposal(
            proposal_id="p1",
            target_plane="firm",
            target_scope="public",
            target_entity="acme",
            proposer_agent_id="agent-1",
            proposer_employee_id="emp-1",
            facts=[],
            source_report_path="/tmp/r.md",
        )

        def _insert_and_list() -> int:
            store.insert(proposal)
            return len(store.list(status="pending"))

        count = _run_in_other_thread(_insert_and_list)
        assert count == 1
    finally:
        store.close()


def test_mention_tracker_can_be_used_across_threads(tmp_path: Path) -> None:
    """Mention tracker opened in main, written + read from worker thread."""
    tracker = MentionTracker(tmp_path / "mentions.db")
    try:
        _run_in_other_thread(lambda: tracker.record("alice"))
        record = _run_in_other_thread(lambda: tracker.get("alice"))
        assert record is not None
        assert record.count == 1
    finally:
        tracker.close()


# ---------- The Hermes-specific repro ----------


def test_hermes_provider_lifecycle_across_threads(tmp_path: Path) -> None:
    """Repro of the actual Hermes failure mode: initialize() runs in one
    thread, handle_tool_call() runs in another. Without
    ``check_same_thread=False`` on the underlying SQLite connections,
    this raises ``ProgrammingError`` from inside KG.query_relationship.
    Hermes hit this 2026-04-27.
    """
    provider = MemoryMissionProvider()
    # initialize() opens KG + identity in this thread.
    provider.initialize(
        "session-x",
        user_id="sven",
        root=tmp_path,
    )
    try:
        # Tool calls from a different thread, mimicking Hermes' tool
        # dispatcher. Boot context AND list-threads are the two methods
        # Hermes reported failing.
        boot_payload = _run_in_other_thread(
            lambda: provider.handle_tool_call(TOOL_BOOT_CONTEXT, {})
        )
        assert "render" in boot_payload
        assert "aspect_counts" in boot_payload
        assert "error" not in boot_payload, (
            f"boot_context returned structured error from worker thread: {boot_payload}"
        )

        threads = _run_in_other_thread(lambda: provider.handle_tool_call(TOOL_LIST_THREADS, {}))
        assert threads == []

        # Write tool from worker thread, then read back from yet another
        # worker thread. Both must succeed.
        _run_in_other_thread(
            lambda: provider.handle_tool_call(
                TOOL_THREAD_STATUS,
                {
                    "thread_id": "thread-from-worker",
                    "status": "active",
                    "source_closet": "conversational",
                    "source_file": "session-x",
                },
            )
        )
        threads_after_write = _run_in_other_thread(
            lambda: provider.handle_tool_call(TOOL_LIST_THREADS, {})
        )
        assert any(t["thread_id"] == "thread-from-worker" for t in threads_after_write)
    finally:
        provider.shutdown()


# ---------- Sanity: in-memory engine doesn't regress ----------


def test_in_memory_engine_can_be_used_across_threads(tmp_path: Path) -> None:
    """The InMemoryEngine doesn't use SQLite, but the test ensures we
    don't accidentally introduce thread coupling through other parts of
    the read path."""
    from memory_mission.memory.pages import new_page

    engine = InMemoryEngine()
    engine.connect()
    page = new_page(
        slug="sarah-chen",
        title="Sarah Chen",
        domain="people",
        compiled_truth="Sven's contact at Acme.",
        valid_from=date(2026, 4, 27),
    )
    _run_in_other_thread(lambda: engine.put_page(page, plane="personal", employee_id="sven"))
    pages = _run_in_other_thread(lambda: engine.list_pages(plane="personal", employee_id="sven"))
    assert len(pages) == 1
    assert pages[0].frontmatter.slug == "sarah-chen"
