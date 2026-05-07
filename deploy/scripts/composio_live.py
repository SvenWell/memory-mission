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
    "hubspot": "20260501_00",
    "monday": "20260429_00",
    "notion": "20260501_00",
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
    """Granola dates come in several formats — try each, return ISO string.

    Returns empty string if no format matches, so callers can fall back
    rather than propagating an unparseable raw string downstream (the
    library's `_require_datetime` would reject it).
    """
    if not s:
        return ""
    norm = s.strip()
    # Zero-pad single-digit day after the month name: "Apr 3," → "Apr 03,"
    norm = re.sub(r"^(\w{3,})\s+(\d),", r"\1 0\2,", norm)
    # Zero-pad single-digit hour: " 3:15 PM" → " 03:15 PM"
    norm = re.sub(r"\s(\d):(\d{2})\s(AM|PM)$", r" 0\1:\2 \3", norm)

    # Try Granola's prevailing string formats — abbreviated and full month,
    # with and without time-of-day.
    for fmt in (
        "%b %d, %Y %I:%M %p",   # Apr 29, 2026 02:00 PM
        "%b %d, %Y",             # Apr 29, 2026
        "%B %d, %Y %I:%M %p",   # April 29, 2026 02:00 PM
        "%B %d, %Y",             # April 29, 2026
    ):
        try:
            return datetime.strptime(norm, fmt).isoformat() + "+00:00"
        except ValueError:
            continue

    # Last resort: ISO 8601 — handles "2026-04-29T15:00:00Z", "2026-04-29", etc.
    try:
        parsed = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (ValueError, TypeError):
        pass

    return ""


def _granola_parse_meeting_block(block: str, mid: str, title: str, date: str) -> dict[str, Any]:
    """Parse one <meeting>…</meeting> body — extract attendees + summary content.

    If Granola returns a date we can't parse (drafts, older records, format
    drift), fall back to the current UTC time so ingestion still succeeds.
    Better to have an approximate timestamp than to lose the meeting.
    """
    import sys as _sys

    attendees = _GRANOLA_EMAIL_RE.findall(block)
    created_at = _granola_parse_date(date)
    if not created_at:
        _sys.stderr.write(
            f"[granola] WARN: meeting {mid} date={date!r} unparseable; "
            "using current UTC time as fallback\n"
        )
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": mid,
        "transcript_id": mid,
        "meeting_id": mid,
        "title": title,
        "created_at": created_at,
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


# ---------------------------------------------------------------------------
# HubSpot
# ---------------------------------------------------------------------------


class LiveHubSpotClient:
    """Composio adapter for HubSpot.

    Implements the subset of the HubSpot connector's actions that
    `push_hubspot.py` needs for the KG → CRM projection (read/match-side
    SEARCH and write-side CREATE/UPDATE for contacts + companies). The
    remaining HubSpot connector actions are not wired here yet — extend
    the dispatch when a use case appears.
    """

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        c = _client()

        if action in ("search_contacts", "search_companies"):
            slug = (
                "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA"
                if action == "search_contacts"
                else "HUBSPOT_SEARCH_COMPANIES"
            )
            args: dict[str, Any] = {}
            if "query" in params:
                args["query"] = params["query"]
            if "filter_groups" in params:
                args["filterGroups"] = params["filter_groups"]
            if "sorts" in params:
                args["sorts"] = params["sorts"]
            if "properties" in params:
                args["properties"] = params["properties"]
            if "limit" in params:
                args["limit"] = params["limit"]
            if "after" in params:
                args["after"] = params["after"]
            r = c.tools.execute(slug=slug, user_id=self._user_id, arguments=args)
            return _unwrap(r)

        if action in ("create_contact", "create_company"):
            slug = (
                "HUBSPOT_CREATE_CONTACT"
                if action == "create_contact"
                else "HUBSPOT_CREATE_COMPANY"
            )
            # Composio's create_contact / create_company expect each property
            # at the top level (email, firstname, lastname, name, domain, ...)
            # rather than nested under a "properties" object. Flatten.
            args = dict(params.get("properties") or {})
            if assoc := params.get("associations"):
                args["associations"] = assoc
            r = c.tools.execute(slug=slug, user_id=self._user_id, arguments=args)
            return _unwrap(r)

        if action in ("update_contact", "update_company"):
            slug = (
                "HUBSPOT_UPDATE_CONTACT"
                if action == "update_contact"
                else "HUBSPOT_UPDATE_COMPANY"
            )
            id_field = "contactId" if action == "update_contact" else "companyId"
            args = {
                id_field: str(params["object_id"]),
                "properties": params.get("properties") or {},
            }
            r = c.tools.execute(slug=slug, user_id=self._user_id, arguments=args)
            return _unwrap(r)

        raise ValueError(f"Unknown hubspot action: {action}")


def make_live_hubspot_client(*, user_id: str) -> LiveHubSpotClient:
    return LiveHubSpotClient(user_id=user_id)


# ---------------------------------------------------------------------------
# Monday.com
# ---------------------------------------------------------------------------


class LiveMondayClient:
    """Composio adapter for Monday.com.

    Subset wired for the KG → Monday-CRM projection: workspace/board/column
    discovery, item search by column value (the match operation), create
    item with column_values, and per-column update.

    Monday's API is generic — boards are user-defined, columns are typed
    (text / email / phone / link / status / etc.) and each takes its own
    value shape. This client serializes column values into the JSON form
    Monday expects, then calls the appropriate MONDAY_* tool slug.
    """

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        c = _client()

        # --- discovery ---------------------------------------------------
        if action == "list_workspaces":
            r = c.tools.execute(
                slug="MONDAY_GET_WORKSPACES",
                user_id=self._user_id,
                arguments=params or {},
            )
            return _unwrap(r)

        if action == "list_boards":
            args: dict[str, Any] = {}
            for k in ("workspace_ids", "ids", "limit", "page", "state"):
                if k in params:
                    args[k] = params[k]
            r = c.tools.execute(slug="MONDAY_BOARDS", user_id=self._user_id, arguments=args)
            return _unwrap(r)

        if action == "list_columns":
            args = {"board_ids": params["board_ids"]}
            if "column_types" in params:
                args["column_types"] = params["column_types"]
            r = c.tools.execute(slug="MONDAY_COLUMNS", user_id=self._user_id, arguments=args)
            return _unwrap(r)

        # --- provisioning -----------------------------------------------
        if action == "create_board":
            args = {
                "board_name": params["board_name"],
                "board_kind": params.get("board_kind", "public"),
            }
            for k in ("workspace_id", "folder_id", "description", "template_id"):
                if k in params:
                    args[k] = params[k]
            r = c.tools.execute(slug="MONDAY_CREATE_BOARD", user_id=self._user_id, arguments=args)
            return _unwrap(r)

        if action == "create_column":
            args = {
                "board_id": params["board_id"],
                "title": params["title"],
                "column_type": params["column_type"],
            }
            for k in ("description", "after_column_id", "defaults"):
                if k in params:
                    args[k] = params[k]
            r = c.tools.execute(slug="MONDAY_CREATE_COLUMN", user_id=self._user_id, arguments=args)
            return _unwrap(r)

        # --- search / read ----------------------------------------------
        if action == "search_items_by_column":
            # params: {board_id, column_id, value, limit?}
            args = {
                "board_id": params["board_id"],
                "columns": [
                    {
                        "column_id": params["column_id"],
                        "column_values": [params["value"]],
                    }
                ],
                "limit": params.get("limit", 5),
            }
            r = c.tools.execute(
                slug="MONDAY_LIST_ITEMS_BY_COLUMN_VALUES",
                user_id=self._user_id,
                arguments=args,
            )
            return _unwrap(r)

        # --- write -------------------------------------------------------
        if action == "create_item":
            args = {
                "board_id": params["board_id"],
                "item_name": params["item_name"],
            }
            if "group_id" in params:
                args["group_id"] = params["group_id"]
            if cv := params.get("column_values"):
                # Monday expects column_values as a JSON-stringified map.
                import json as _json
                args["column_values"] = _json.dumps(cv) if isinstance(cv, dict) else cv
            r = c.tools.execute(slug="MONDAY_CREATE_ITEM", user_id=self._user_id, arguments=args)
            return _unwrap(r)

        if action == "set_column_value":
            # Per-column update — covers text/email/phone/link via
            # CHANGE_SIMPLE_COLUMN_VALUE which accepts a stringified value.
            import json as _json
            value = params["value"]
            value_str = (
                _json.dumps(value) if not isinstance(value, str) else value
            )
            args = {
                "board_id": params["board_id"],
                "item_id": str(params["item_id"]),
                "column_id": params["column_id"],
                "value": value_str,
                "create_labels_if_missing": params.get("create_labels_if_missing", False),
            }
            r = c.tools.execute(
                slug="MONDAY_CHANGE_SIMPLE_COLUMN_VALUE",
                user_id=self._user_id,
                arguments=args,
            )
            return _unwrap(r)

        raise ValueError(f"Unknown monday action: {action}")


def make_live_monday_client(*, user_id: str) -> LiveMondayClient:
    return LiveMondayClient(user_id=user_id)


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------


class LiveNotionClient:
    """Composio adapter for Notion.

    Subset wired for the KG → Notion projection: page search/create
    (parent discovery + provisioning), database create/fetch, and per-row
    insert. Notion has a native UPSERT primitive (NOTION_UPSERT_ROW_DATABASE)
    we may switch to once the basic insert path works.
    """

    def __init__(self, *, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        c = _client()

        if action == "search_pages":
            args = {"query": params.get("query", ""), "page_size": params.get("page_size", 25)}
            for k in ("filter_value", "start_cursor"):
                if k in params:
                    args[k] = params[k]
            r = c.tools.execute(
                slug="NOTION_SEARCH_NOTION_PAGE", user_id=self._user_id, arguments=args
            )
            return _unwrap(r)

        if action == "create_page":
            # NOTION_CREATE_NOTION_PAGE — create a page with a parent (page or workspace).
            r = c.tools.execute(
                slug="NOTION_CREATE_NOTION_PAGE",
                user_id=self._user_id,
                arguments=params,
            )
            return _unwrap(r)

        if action == "create_database":
            args = {
                "parent_id": params["parent_id"],
                "title": params["title"],
            }
            if props := params.get("properties"):
                args["properties"] = props
            r = c.tools.execute(
                slug="NOTION_CREATE_DATABASE", user_id=self._user_id, arguments=args
            )
            return _unwrap(r)

        if action == "fetch_database":
            r = c.tools.execute(
                slug="NOTION_FETCH_DATABASE",
                user_id=self._user_id,
                arguments={"database_id": params["database_id"]},
            )
            return _unwrap(r)

        if action == "query_database":
            args = {"database_id": params["database_id"]}
            for k in ("filter", "sorts", "page_size", "start_cursor"):
                if k in params:
                    args[k] = params[k]
            r = c.tools.execute(
                slug="NOTION_QUERY_DATABASE_WITH_FILTER",
                user_id=self._user_id,
                arguments=args,
            )
            return _unwrap(r)

        if action == "insert_row":
            args = {"database_id": params["database_id"]}
            if props := params.get("properties"):
                args["properties"] = props
            r = c.tools.execute(
                slug="NOTION_INSERT_ROW_DATABASE",
                user_id=self._user_id,
                arguments=args,
            )
            return _unwrap(r)

        if action == "update_row":
            args = {"row_id": params["row_id"]}
            if props := params.get("properties"):
                args["properties"] = props
            r = c.tools.execute(
                slug="NOTION_UPDATE_ROW_DATABASE",
                user_id=self._user_id,
                arguments=args,
            )
            return _unwrap(r)

        raise ValueError(f"Unknown notion action: {action}")


def make_live_notion_client(*, user_id: str) -> LiveNotionClient:
    return LiveNotionClient(user_id=user_id)
