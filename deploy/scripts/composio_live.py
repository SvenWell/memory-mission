"""Live Composio adapter for memory-mission's ComposioClient Protocol.

Translates mm action names (list_message_ids / get_message / list_events / get_event)
into Composio tool slugs and reshapes responses to the snake_case dicts mm's
envelope helpers expect.

Each instance binds to one Composio user_id (one Gmail or Calendar account).

This is the addon that wires Composio for real — memory-mission's stock
``composio.py`` ships the Protocol but raises NotImplementedError. We're the
first integration test of the Composio path in this repo.

Lives alongside the backfill scripts under deploy/scripts/; each script
adds its own dir to sys.path so `from composio_live import ...` resolves.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from composio import Composio

# Pin toolkit versions — Composio's manual execute requires explicit versions.
_TOOLKIT_VERSIONS = {
    "gmail": "20251222_02",
    "googlecalendar": "20251230_01",
    # granola_mcp deliberately omitted — MCP-style toolkits don't pin to a static
    # version; LiveGranolaClient passes dangerously_skip_version_check directly.
}

_composio: Composio | None = None


def _client() -> Composio:
    global _composio
    if _composio is None:
        _composio = Composio(toolkit_versions=_TOOLKIT_VERSIONS)
    return _composio


def _unwrap(result: Any) -> dict[str, Any]:
    """Composio responses come as {data: {...}, error: ..., successful: bool}."""
    data = result.data if hasattr(result, "data") else result
    if isinstance(data, dict) and "successful" in data and "data" in data:
        if not data.get("successful", True):
            raise RuntimeError(f"Composio call failed: {data.get('error')}")
        return data.get("data") or {}
    return data if isinstance(data, dict) else {}


def _hdr(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_addr_list(s: str) -> list[str]:
    return [a.strip() for a in (s or "").split(",") if a.strip()]


def _gmail_translate(raw: dict[str, Any]) -> dict[str, Any]:
    """Composio Gmail message envelope → mm envelope-shaped dict."""
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    msg_id = raw.get("messageId") or raw.get("id") or ""
    ts = raw.get("messageTimestamp") or ""  # keep as ISO string for JSON-safe staging
    return {
        "id": msg_id,
        "message_id": msg_id,
        "thread_id": raw.get("threadId") or raw.get("thread_id"),
        "internal_date": ts,
        "labels": list(raw.get("labelIds") or []),
        "subject": _hdr(headers, "Subject"),
        "from": _hdr(headers, "From"),
        "to": _parse_addr_list(_hdr(headers, "To")),
        "cc": _parse_addr_list(_hdr(headers, "Cc")),
        "snippet": raw.get("snippet") or "",
        "body": raw.get("messageText") or "",
        "permalink": f"https://mail.google.com/mail/u/0/#inbox/{msg_id}" if msg_id else None,
    }


def _cal_translate(raw: dict[str, Any], calendar_id: str) -> dict[str, Any]:
    """Composio Calendar event → mm envelope-shaped dict."""
    out = dict(raw)
    out.setdefault("html_link", raw.get("htmlLink"))
    out.setdefault("calendar_id", calendar_id)
    return out


class LiveGmailClient:
    """Composio adapter satisfying mm's ComposioClient Protocol — Gmail."""

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        c = _client()
        if action == "list_message_ids":
            args: dict[str, Any] = {"ids_only": True, "verbose": False}
            if "query" in params:
                args["query"] = params["query"]
            if "page_token" in params:
                args["page_token"] = params["page_token"]
            if "max_results" in params:
                args["max_results"] = params["max_results"]
            r = c.tools.execute(slug="GMAIL_FETCH_EMAILS", user_id=self._user_id, arguments=args)
            data = _unwrap(r)
            messages = data.get("messages") or []
            return {
                "messages": [
                    {"id": m.get("messageId") or m.get("id"), "thread_id": m.get("threadId")}
                    for m in messages
                ],
                "next_page_token": data.get("nextPageToken"),
            }
        if action == "get_message":
            msg_id = params["message_id"]
            r = c.tools.execute(
                slug="GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
                user_id=self._user_id,
                arguments={"message_id": msg_id, "format": "full"},
            )
            return _gmail_translate(_unwrap(r))
        raise ValueError(f"Unknown gmail action: {action}")


class LiveCalendarClient:
    """Composio adapter satisfying mm's ComposioClient Protocol — Calendar."""

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        c = _client()
        if action == "list_events":
            cal_id = params.get("calendar_id", "primary")
            args: dict[str, Any] = {"calendar_id": cal_id}
            if "time_min" in params:
                args["timeMin"] = params["time_min"]
            if "time_max" in params:
                args["timeMax"] = params["time_max"]
            if "query" in params:
                args["q"] = params["query"]
            if "page_token" in params:
                args["pageToken"] = params["page_token"]
            if "max_results" in params:
                args["maxResults"] = params["max_results"]
            args["singleEvents"] = True  # expand recurrences
            args["orderBy"] = "startTime"
            r = c.tools.execute(slug="GOOGLECALENDAR_EVENTS_LIST", user_id=self._user_id, arguments=args)
            data = _unwrap(r)
            return {
                "events": [_cal_translate(e, cal_id) for e in (data.get("items") or [])],
                "next_page_token": data.get("nextPageToken"),
            }
        if action == "get_event":
            cal_id = params.get("calendar_id", "primary")
            r = c.tools.execute(
                slug="GOOGLECALENDAR_EVENTS_GET",
                user_id=self._user_id,
                arguments={"calendar_id": cal_id, "event_id": params["event_id"]},
            )
            return _cal_translate(_unwrap(r), cal_id)
        raise ValueError(f"Unknown calendar action: {action}")


def make_live_gmail_client(*, user_id: str) -> LiveGmailClient:
    return LiveGmailClient(user_id=user_id)


def make_live_calendar_client(*, user_id: str) -> LiveCalendarClient:
    return LiveCalendarClient(user_id=user_id)


# ---------- Granola ----------


_GRANOLA_MEETING_RE = re.compile(
    r'<meeting\s+id="(?P<id>[^"]+)"\s+title="(?P<title>[^"]*)"\s+date="(?P<date>[^"]*)">'
    r'(?P<body>.*?)(?=<meeting\s+id=|</meetings_data>|\Z)',
    re.DOTALL,
)
_GRANOLA_EMAIL_RE = re.compile(r"<([^>@]+@[^>]+)>")


def _granola_parse_date(s: str) -> str:
    """Granola dates come as 'Apr 29, 2026 2:00 PM' or 'Apr 3, 2026 3:15 PM'.

    Day and hour can be 1- or 2-digit. ``strptime``'s ``%d``/``%I`` are
    zero-padded on Linux, so we zero-pad first.
    """
    if not s:
        return ""
    norm = s.strip()
    # Zero-pad single-digit day after the month name: "Apr 3," → "Apr 03,"
    norm = re.sub(r"^(\w{3,})\s+(\d),", r"\1 0\2,", norm)
    # Zero-pad single-digit hour: " 3:15 PM" → " 03:15 PM"
    norm = re.sub(r"\s(\d):(\d{2})\s(AM|PM)$", r" 0\1:\2 \3", norm)
    try:
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
            try:
                return datetime.strptime(norm, fmt).isoformat() + "+00:00"
            except ValueError:
                continue
    except Exception:
        pass
    return s


def _granola_parse_meeting_block(block: str, mid: str, title: str, date: str) -> dict[str, Any]:
    """Parse one <meeting>…</meeting> body — extract attendees + summary content."""
    attendees = _GRANOLA_EMAIL_RE.findall(block)
    return {
        "id": mid,
        "transcript_id": mid,
        "meeting_id": mid,
        "title": title,
        "created_at": _granola_parse_date(date),
        "attendees": attendees,
        "labels": [],
        "transcript": block.strip(),  # the structured notes ARE our body
        "notes": block.strip(),
    }


def _granola_extract_meetings_text(data: Any) -> str:
    """Composio MCP responses come as {data: {data: [{text: '...'}], ...}, ...}."""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list) and inner:
            return inner[0].get("text", "") if isinstance(inner[0], dict) else ""
        if isinstance(inner, dict):
            sub = inner.get("data")
            if isinstance(sub, list) and sub and isinstance(sub[0], dict):
                return sub[0].get("text", "")
    return ""


class LiveGranolaClient:
    """Composio adapter satisfying mm's ComposioClient Protocol — Granola.

    Granola's Composio integration is MCP-shaped — responses come back as
    XML-ish text blobs nested under ``data.data[0].text`` rather than
    structured JSON. We parse the meeting XML to extract id / title / date /
    participants / notes content. Transcripts (paid Granola tier only) are
    skipped — meeting notes from GET_MEETINGS are richer signal anyway.
    """

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def _exec(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call Granola MCP with rate-limit-aware retry (exp backoff up to 60s).

        The MCP integration silently returns ``"Rate limit exceeded..."`` as
        the response body rather than a 429 — surface that and retry.
        """
        import time
        c = _client()
        for attempt in range(5):
            r = c.tools.execute(
                slug=slug,
                user_id=self._user_id,
                arguments=arguments,
                dangerously_skip_version_check=True,
            )
            data = r.data if hasattr(r, "data") else r
            text = _granola_extract_meetings_text(data)
            if "rate limit exceeded" in text.lower():
                wait = min(60, 5 * (2 ** attempt))
                time.sleep(wait)
                continue
            return data
        # Final result even if rate-limited so caller sees the message
        return data

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "list_meetings":
            args: dict[str, Any] = {}
            if "time_range" in params:
                args["time_range"] = params["time_range"]
            if "custom_start" in params:
                args["custom_start"] = params["custom_start"]
            if "custom_end" in params:
                args["custom_end"] = params["custom_end"]
            data = self._exec("GRANOLA_MCP_LIST_MEETINGS", args)
            text = _granola_extract_meetings_text(data)
            meetings = []
            for m in _GRANOLA_MEETING_RE.finditer(text):
                meetings.append({
                    "id": m.group("id"),
                    "title": m.group("title"),
                    "date": m.group("date"),
                })
            return {"meetings": meetings, "raw_text_len": len(text)}

        if action == "get_meeting":
            mid = params["meeting_id"]
            data = self._exec("GRANOLA_MCP_GET_MEETINGS", {"meeting_ids": [mid]})
            text = _granola_extract_meetings_text(data)
            for m in _GRANOLA_MEETING_RE.finditer(text):
                if m.group("id") == mid:
                    return _granola_parse_meeting_block(
                        m.group("body"), mid, m.group("title"), m.group("date"),
                    )
            return _granola_parse_meeting_block("", mid, "", "")

        if action == "get_transcript":
            # Paid Granola tier only; we use meeting notes from get_meeting instead.
            return {"meeting_id": params["meeting_id"], "transcript": ""}

        raise ValueError(f"Unknown granola action: {action}")


def make_live_granola_client(*, user_id: str) -> LiveGranolaClient:
    return LiveGranolaClient(user_id=user_id)
