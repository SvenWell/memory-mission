"""Tests for component 1.3 — Connector Layer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_mission.durable import CheckpointStore, durable_run
from memory_mission.ingestion.connectors import (
    AFFINITY_ACTIONS,
    CALENDAR_ACTIONS,
    DRIVE_ACTIONS,
    GMAIL_ACTIONS,
    GRANOLA_ACTIONS,
    ONEDRIVE_ACTIONS,
    OUTLOOK_ACTIONS,
    ComposioConnector,
    Connector,
    ConnectorAction,
    ConnectorResult,
    InMemoryConnector,
    invoke,
    make_affinity_connector,
    make_calendar_connector,
    make_drive_connector,
    make_gmail_connector,
    make_granola_connector,
    make_onedrive_connector,
    make_outlook_connector,
)
from memory_mission.middleware import PIIRedactionMiddleware
from memory_mission.observability import (
    ConnectorInvocationEvent,
    ObservabilityLogger,
    observability_scope,
)

# ---------- Helpers ----------


def _read_connector_events(root: Path, firm_id: str) -> list[ConnectorInvocationEvent]:
    logger = ObservabilityLogger(observability_root=root, firm_id=firm_id)
    return [e for e in logger.read_all() if isinstance(e, ConnectorInvocationEvent)]


# ---------- Protocol shape ----------


def test_in_memory_satisfies_connector_protocol() -> None:
    c = InMemoryConnector()
    assert isinstance(c, Connector)


def test_composio_satisfies_connector_protocol() -> None:
    c = ComposioConnector(name="x", actions=())
    assert isinstance(c, Connector)


# ---------- Base types ----------


def test_connector_action_is_frozen() -> None:
    a = ConnectorAction(name="list", description="x")
    with pytest.raises(ValidationError):
        a.name = "other"  # type: ignore[misc]


def test_connector_result_preview_defaults_empty() -> None:
    r = ConnectorResult(data={"k": 1})
    assert r.preview == ""
    assert r.metadata == {}


def test_connector_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ConnectorResult(data={}, extra="nope")  # type: ignore[call-arg]


# ---------- InMemoryConnector ----------


def test_in_memory_records_invocations() -> None:
    c = InMemoryConnector(responders={"echo": ConnectorResult(data={"got": "it"}, preview="p")})
    c.invoke("echo", {"a": 1})
    c.invoke("echo", {"a": 2})
    assert c.invocations == [("echo", {"a": 1}), ("echo", {"a": 2})]


def test_in_memory_rejects_unknown_action() -> None:
    c = InMemoryConnector(responders={"x": ConnectorResult(data={})})
    with pytest.raises(ValueError, match="Unknown action 'y'"):
        c.invoke("y", {})


def test_in_memory_callable_responder_receives_params() -> None:
    def responder(params: dict[str, Any]) -> ConnectorResult:
        return ConnectorResult(data={"doubled": params["n"] * 2})

    c = InMemoryConnector(responders={"double": responder})
    result = c.invoke("double", {"n": 3})
    assert result.data == {"doubled": 6}


def test_in_memory_register_adds_responders() -> None:
    c = InMemoryConnector()
    c.register("x", ConnectorResult(data={"ok": True}))
    assert c.invoke("x", {}).data == {"ok": True}


def test_in_memory_list_actions_reflects_registered() -> None:
    c = InMemoryConnector(
        responders={
            "a": ConnectorResult(data={}),
            "b": ConnectorResult(data={}),
        }
    )
    names = {a.name for a in c.list_actions()}
    assert names == {"a", "b"}


# ---------- ComposioConnector ----------


def test_composio_requires_client_for_invoke() -> None:
    conn = ComposioConnector(
        name="granola",
        actions=(ConnectorAction(name="list_transcripts", description="x"),),
    )
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_transcripts", {})


def test_composio_rejects_unknown_action_before_client_check() -> None:
    """Unknown actions fail fast even when no client is attached."""
    conn = ComposioConnector(
        name="gmail",
        actions=(ConnectorAction(name="list_message_ids", description="x"),),
    )
    with pytest.raises(ValueError, match="Unknown action 'nope'"):
        conn.invoke("nope", {})


def test_composio_dispatches_to_client() -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            captured["action"] = action
            captured["params"] = params
            return {"title": "Hello", "transcript": "World"}

    conn = ComposioConnector(
        name="granola",
        actions=(ConnectorAction(name="get_transcript", description="x"),),
        client=FakeClient(),
    )
    result = conn.invoke("get_transcript", {"transcript_id": "t-1"})
    assert captured == {"action": "get_transcript", "params": {"transcript_id": "t-1"}}
    assert result.data == {"title": "Hello", "transcript": "World"}
    assert result.metadata == {"composio_action": "get_transcript"}


def test_composio_default_preview_stringifies_keys() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"k1": "v1", "k2": "v2"}

    conn = ComposioConnector(
        name="x",
        actions=(ConnectorAction(name="a", description="d"),),
        client=FakeClient(),
    )
    result = conn.invoke("a", {})
    assert "k1=" in result.preview
    assert "k2=" in result.preview


# ---------- Granola + Gmail factories ----------


def test_granola_exposes_list_and_get_actions() -> None:
    conn = make_granola_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {"list_transcripts", "get_transcript"}
    assert conn.name == "granola"


def test_granola_preview_uses_title_and_body() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"title": "Board meeting", "transcript": "Revenue is up."}

    conn = make_granola_connector(client=FakeClient())
    result = conn.invoke("get_transcript", {"transcript_id": "t1"})
    assert "Board meeting" in result.preview
    assert "Revenue is up" in result.preview


def test_granola_actions_match_exported_constant() -> None:
    """Factory should expose exactly the actions in ``GRANOLA_ACTIONS``."""
    assert make_granola_connector().list_actions() == list(GRANOLA_ACTIONS)


def test_gmail_exposes_list_and_get_actions() -> None:
    conn = make_gmail_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {"list_message_ids", "get_message"}
    assert conn.name == "gmail"


def test_gmail_preview_includes_sender_and_subject() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "from": "alice@example.com",
                "subject": "Q2 Review",
                "snippet": "Here is the agenda for Q2.",
            }

    conn = make_gmail_connector(client=FakeClient())
    result = conn.invoke("get_message", {"message_id": "m-1"})
    # Sender is scrubbed by harness (it's an email), but the raw preview
    # still contains it on the connector output.
    assert "alice@example.com" in result.preview
    assert "Q2 Review" in result.preview
    assert "agenda for Q2" in result.preview


def test_gmail_actions_match_exported_constant() -> None:
    assert make_gmail_connector().list_actions() == list(GMAIL_ACTIONS)


# ---------- Drive factory ----------


def test_drive_exposes_list_and_get_actions() -> None:
    conn = make_drive_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {"list_files", "get_file"}
    assert conn.name == "drive"


def test_drive_actions_match_exported_constant() -> None:
    assert make_drive_connector().list_actions() == list(DRIVE_ACTIONS)


def test_drive_preview_includes_name_and_mime() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "name": "Q3 Investment Memo",
                "mime_type": "application/vnd.google-apps.document",
                "content": "Q3 thesis: lean into vertical AI infrastructure.",
            }

    conn = make_drive_connector(client=FakeClient())
    result = conn.invoke("get_file", {"file_id": "f-1"})
    assert "Q3 Investment Memo" in result.preview
    assert "application/vnd.google-apps.document" in result.preview
    assert "vertical AI infrastructure" in result.preview


def test_drive_preview_handles_missing_fields() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {}

    conn = make_drive_connector(client=FakeClient())
    result = conn.invoke("get_file", {"file_id": "f-1"})
    # Empty fields collapse cleanly — no header dash, no body content
    assert result.preview == ""


def test_drive_requires_client_for_invoke() -> None:
    conn = make_drive_connector()
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_files", {})


# ---------- Calendar factory ----------


def test_calendar_exposes_list_and_get_actions() -> None:
    conn = make_calendar_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {"list_events", "get_event"}
    assert conn.name == "gcal"


def test_calendar_actions_match_exported_constant() -> None:
    assert make_calendar_connector().list_actions() == list(CALENDAR_ACTIONS)


def test_calendar_preview_includes_summary_start_and_attendee_count() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "summary": "Q3 partner sync",
                "start": {"dateTime": "2026-09-01T15:00:00Z"},
                "attendees": [
                    {"email": "alice@example.com"},
                    {"email": "bob@example.com"},
                ],
            }

    conn = make_calendar_connector(client=FakeClient())
    result = conn.invoke("get_event", {"event_id": "ev-1"})
    assert "Q3 partner sync" in result.preview
    assert "2026-09-01T15:00:00Z" in result.preview
    assert "2 attendees" in result.preview


def test_calendar_preview_handles_all_day_event() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "summary": "Off-site",
                "start": {"date": "2026-09-15"},
                "attendees": [{"email": "alice@example.com"}],
            }

    conn = make_calendar_connector(client=FakeClient())
    result = conn.invoke("get_event", {"event_id": "ev-2"})
    assert "Off-site" in result.preview
    assert "2026-09-15" in result.preview
    assert "1 attendee" in result.preview


def test_calendar_preview_handles_no_attendees() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "summary": "Solo focus block",
                "start": {"dateTime": "2026-09-02T09:00:00Z"},
            }

    conn = make_calendar_connector(client=FakeClient())
    result = conn.invoke("get_event", {"event_id": "ev-3"})
    assert "0 attendees" in result.preview


def test_calendar_requires_client_for_invoke() -> None:
    conn = make_calendar_connector()
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_events", {})


# ---------- Affinity factory ----------


def test_affinity_exposes_read_actions() -> None:
    conn = make_affinity_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {
        "list_organizations",
        "get_organization",
        "list_persons",
        "get_person",
        "list_opportunities",
        "get_opportunity",
        "list_lists",
        "get_list_metadata",
        "list_list_entries",
    }
    assert conn.name == "affinity"


def test_affinity_actions_match_exported_constant() -> None:
    assert make_affinity_connector().list_actions() == list(AFFINITY_ACTIONS)


def test_affinity_preview_uses_organization_name_and_domain() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"id": 42, "name": "Northpoint Capital", "domain": "northpoint.fund"}

    conn = make_affinity_connector(client=FakeClient())
    result = conn.invoke("get_organization", {"organization_id": 42})
    assert "Northpoint Capital" in result.preview
    assert "northpoint.fund" in result.preview


def test_affinity_preview_uses_person_name_and_email() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": 7,
                "first_name": "Sarah",
                "last_name": "Chen",
                "primary_email": "sarah@example.com",
            }

    conn = make_affinity_connector(client=FakeClient())
    result = conn.invoke("get_person", {"person_id": 7})
    assert "Sarah Chen" in result.preview
    assert "sarah@example.com" in result.preview


def test_affinity_requires_client_for_invoke() -> None:
    conn = make_affinity_connector()
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_organizations", {})


# ---------- Outlook factory ----------


def test_outlook_exposes_read_actions() -> None:
    conn = make_outlook_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {
        "list_messages",
        "get_message",
        "list_mail_folders",
        "search_messages",
        "get_mail_delta",
    }
    assert conn.name == "outlook"


def test_outlook_actions_match_exported_constant() -> None:
    assert make_outlook_connector().list_actions() == list(OUTLOOK_ACTIONS)


def test_outlook_preview_includes_sender_and_subject() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "from": {"emailAddress": {"address": "alice@northpoint.fund"}},
                "subject": "Q3 Review",
                "body_preview": "Here's the Q3 agenda.",
            }

    conn = make_outlook_connector(client=FakeClient())
    result = conn.invoke("get_message", {"message_id": "AAMkAD..."})
    assert "alice@northpoint.fund" in result.preview
    assert "Q3 Review" in result.preview
    assert "Q3 agenda" in result.preview


def test_outlook_requires_client_for_invoke() -> None:
    conn = make_outlook_connector()
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_messages", {})


# ---------- OneDrive / SharePoint factory ----------


def test_onedrive_exposes_read_actions() -> None:
    conn = make_onedrive_connector()
    names = {a.name for a in conn.list_actions()}
    assert names == {
        "list_drive_items",
        "get_item",
        "list_recent_items",
        "search_items",
        "get_item_metadata",
        "get_item_permissions",
        "get_sharepoint_site_details",
        "list_site_subsites",
        "get_sharepoint_list_items",
        "get_sharepoint_site_page_content",
    }
    assert conn.name == "one_drive"


def test_onedrive_actions_match_exported_constant() -> None:
    assert make_onedrive_connector().list_actions() == list(ONEDRIVE_ACTIONS)


def test_onedrive_preview_includes_name_and_mime() -> None:
    class FakeClient:
        def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "name": "Q4 LP Update.docx",
                "file": {
                    "mimeType": (
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"
                    )
                },
                "content": "Q4 thesis: lean into vertical AI infrastructure.",
            }

    conn = make_onedrive_connector(client=FakeClient())
    result = conn.invoke("get_item", {"item_id": "01ABC..."})
    assert "Q4 LP Update.docx" in result.preview
    assert "vertical AI infrastructure" in result.preview


def test_onedrive_requires_client_for_invoke() -> None:
    conn = make_onedrive_connector()
    with pytest.raises(NotImplementedError, match="no client attached"):
        conn.invoke("list_drive_items", {})


# ---------- Harness invoke() ----------


def test_invoke_returns_raw_result_from_connector(tmp_path: Path) -> None:
    conn = InMemoryConnector(
        responders={"x": ConnectorResult(data={"raw": True}, preview="preview")}
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        result = invoke(conn, "x", {"p": 1})
    assert result.data == {"raw": True}


def test_invoke_logs_connector_invocation_event(tmp_path: Path) -> None:
    conn = InMemoryConnector(
        responders={"ping": ConnectorResult(data={"ok": True}, preview="pong")}
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme", employee_id="sarah"):
        invoke(conn, "ping", {"x": 1})

    events = _read_connector_events(tmp_path, "acme")
    assert len(events) == 1
    event = events[0]
    assert event.connector_name == "in-memory"
    assert event.action == "ping"
    assert event.success is True
    assert event.error is None
    assert event.firm_id == "acme"
    assert event.employee_id == "sarah"
    assert event.trace_id is not None
    assert event.latency_ms >= 0


def test_invoke_scrubs_pii_from_preview_before_logging(tmp_path: Path) -> None:
    conn = InMemoryConnector(
        responders={
            "get": ConnectorResult(
                data={"body": "secret"},
                preview="Contact alice@example.com about account 123-45-6789-0123",
            )
        }
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "get", {})

    events = _read_connector_events(tmp_path, "acme")
    assert len(events) == 1
    event = events[0]
    assert "alice@example.com" not in event.preview
    assert "[EMAIL]" in event.preview
    assert event.preview_redactions.get("email", 0) >= 1


def test_invoke_truncates_long_preview_before_logging(tmp_path: Path) -> None:
    long_body = "x" * 2000
    conn = InMemoryConnector(responders={"get": ConnectorResult(data={}, preview=long_body)})
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "get", {}, preview_chars=100)

    event = _read_connector_events(tmp_path, "acme")[0]
    assert len(event.preview) <= 100


def test_invoke_captures_latency_on_slow_connector(tmp_path: Path) -> None:
    import time

    def slow(params: dict[str, Any]) -> ConnectorResult:
        time.sleep(0.02)
        return ConnectorResult(data={}, preview="")

    conn = InMemoryConnector(responders={"slow": slow})
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "slow", {})

    event = _read_connector_events(tmp_path, "acme")[0]
    assert event.latency_ms >= 10  # 20ms sleep, generous floor


def test_invoke_logs_failure_and_reraises(tmp_path: Path) -> None:
    def boom(params: dict[str, Any]) -> ConnectorResult:
        raise RuntimeError("upstream down")

    conn = InMemoryConnector(responders={"bad": boom})
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        with pytest.raises(RuntimeError, match="upstream down"):
            invoke(conn, "bad", {})

    events = _read_connector_events(tmp_path, "acme")
    assert len(events) == 1
    event = events[0]
    assert event.success is False
    assert event.error is not None
    assert "RuntimeError" in event.error
    assert "upstream down" in event.error
    assert event.preview == ""


def test_invoke_threads_trace_id_from_scope(tmp_path: Path) -> None:
    trace = uuid4()
    conn = InMemoryConnector(responders={"x": ConnectorResult(data={}, preview="")})
    with observability_scope(observability_root=tmp_path, firm_id="acme", trace_id=trace):
        invoke(conn, "x", {})

    event = _read_connector_events(tmp_path, "acme")[0]
    assert event.trace_id == trace


def test_invoke_respects_custom_redactor(tmp_path: Path) -> None:
    """A caller can pass a tighter redactor for sensitive workflows."""
    conn = InMemoryConnector(
        responders={"x": ConnectorResult(data={}, preview="Project Atlas is confidential")}
    )
    literal_redactor = PIIRedactionMiddleware(literal_redactions=["Atlas"])
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "x", {}, redactor=literal_redactor)

    event = _read_connector_events(tmp_path, "acme")[0]
    assert "Atlas" not in event.preview
    assert "[REDACTED]" in event.preview


def test_invoke_params_none_defaults_to_empty_dict(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    def responder(params: dict[str, Any]) -> ConnectorResult:
        captured.append(params)
        return ConnectorResult(data={}, preview="")

    conn = InMemoryConnector(responders={"x": responder})
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "x")

    assert captured == [{}]


# ---------- Integration: durable backfill loop ----------


def test_backfill_loop_resumes_cleanly_after_crash(tmp_path: Path) -> None:
    """Full stack test: durable_run + observability_scope + invoke()."""
    store = CheckpointStore(tmp_path / "durable.db")
    processed: list[str] = []

    # Fake Gmail connector that returns 5 message ids then fetches bodies.
    message_ids = ["m-1", "m-2", "m-3", "m-4", "m-5"]

    def fetch_body(params: dict[str, Any]) -> ConnectorResult:
        mid = params["message_id"]
        return ConnectorResult(
            data={"id": mid, "body": f"body-of-{mid}"},
            preview=f"from=x@y.com subject=Msg-{mid}",
        )

    conn = InMemoryConnector(name="gmail", responders={"get_message": fetch_body})

    def run_pass(crash_at_index: int | None) -> None:
        with observability_scope(
            observability_root=tmp_path / "obs",
            firm_id="acme",
            employee_id="sarah",
        ):
            with durable_run(
                store=store,
                thread_id="backfill-acme-sarah",
                firm_id="acme",
                employee_id="sarah",
                workflow_type="backfill-email",
            ) as run:
                for i, mid in enumerate(message_ids):
                    step = f"msg-{mid}"
                    if run.is_done(step):
                        continue
                    if crash_at_index is not None and i == crash_at_index:
                        raise RuntimeError("simulated crash")
                    invoke(conn, "get_message", {"message_id": mid})
                    processed.append(mid)
                    run.mark_done(step, state={"last": mid})
                run.complete()

    # First pass: crash after processing 2 messages.
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_pass(crash_at_index=2)
    assert processed == ["m-1", "m-2"]

    # Second pass: resume — should process only m-3, m-4, m-5.
    run_pass(crash_at_index=None)
    assert processed == ["m-1", "m-2", "m-3", "m-4", "m-5"]

    # Observability log has one entry per processed message (not per attempt).
    events = _read_connector_events(tmp_path / "obs", "acme")
    assert len(events) == 5
    assert {e.action for e in events} == {"get_message"}
    # All share the same firm_id + employee_id; trace_id is per-scope.
    assert all(e.firm_id == "acme" for e in events)
    assert all(e.employee_id == "sarah" for e in events)


def test_preview_redaction_counts_visible_in_event(tmp_path: Path) -> None:
    """Regression: the count dict must round-trip through JSONL."""
    conn = InMemoryConnector(
        responders={
            "get": ConnectorResult(
                data={},
                preview="a@b.com and c@d.com and 555-123-4567",
            )
        }
    )
    with observability_scope(observability_root=tmp_path, firm_id="acme"):
        invoke(conn, "get", {})

    event = _read_connector_events(tmp_path, "acme")[0]
    assert event.preview_redactions["email"] == 2
    assert event.preview_redactions["phone"] == 1
    # Sanity: email/phone scrubs landed in the preview.
    assert re.fullmatch(r".*\[EMAIL\].*\[EMAIL\].*\[PHONE\]", event.preview)
