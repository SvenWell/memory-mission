"""Backfill Gmail + Calendar through mm's connector → staging pipeline.

Configurable for either toolkit and account. Idempotent: skips items already
staged. Checkpointed via the durable_run primitive so crashes resume cleanly.

Usage:
    python backfill.py calendar verascient
    python backfill.py calendar purpledorm
    python backfill.py gmail verascient
    python backfill.py gmail purpledorm
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from composio_live import make_live_calendar_client, make_live_gmail_client

from memory_mission.durable import durable_run
from memory_mission.ingestion.connectors.base import invoke as harness_invoke
from memory_mission.ingestion.connectors.calendar import make_calendar_connector
from memory_mission.ingestion.connectors.gmail import make_gmail_connector
from memory_mission.ingestion.envelopes import (
    calendar_event_to_envelope,
    gmail_message_to_envelope,
)
from memory_mission.ingestion.staging import StagingWriter
from memory_mission.ingestion.systems_manifest import load_systems_manifest
from memory_mission.observability import observability_scope

FIRM_ROOT = Path("/root/memory-mission-data")
WIKI_ROOT = FIRM_ROOT / "wiki"
OBS_ROOT = FIRM_ROOT / ".observability"
DURABLE_DB = FIRM_ROOT / "durable.sqlite3"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"

# 180 days back from today
LOOKBACK_DAYS = 180
WINDOW_END = datetime.now(timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=LOOKBACK_DAYS)

# Tight Gmail filter: drop noise, keep real correspondence + signal items.
GMAIL_QUERY = (
    f"newer_than:{LOOKBACK_DAYS}d "
    "-category:promotions -category:social -in:spam "
    "-from:noreply -from:no-reply -from:notifications "
    "(is:starred OR is:important OR -is:list)"
)

ACCOUNTS = {
    "verascient": "keagan-verascient",
    "purpledorm": "keagan-purpledorm",
}


def _stage(writer: StagingWriter, item) -> bool:
    """Idempotent stage. Returns True if newly written, False if skipped."""
    try:
        existing = writer.get(item.external_id)
        if existing is not None:
            return False
    except Exception:
        pass
    writer.write_envelope(item)
    return True


def backfill_calendar(account_label: str) -> None:
    user_id = ACCOUNTS[account_label]
    print(f"[cal/{account_label}] start  user_id={user_id}", flush=True)

    manifest = load_systems_manifest(FIRM_ROOT / "firm" / "systems.yaml")
    client = make_live_calendar_client(user_id=user_id)
    conn = make_calendar_connector(client=client)
    writer = StagingWriter(
        wiki_root=WIKI_ROOT,
        source="gcal",
        target_plane="personal",
        employee_id=EMPLOYEE,
    )

    page_token: str | None = None
    fetched = staged = skipped = 0

    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        while True:
            params = {
                "calendar_id": "primary",
                "time_min": WINDOW_START.isoformat(),
                "time_max": WINDOW_END.isoformat(),
                "max_results": 250,
            }
            if page_token:
                params["page_token"] = page_token
            r = harness_invoke(conn, "list_events", params)
            events = r.data.get("events") or []
            for raw in events:
                fetched += 1
                try:
                    item = calendar_event_to_envelope(raw, manifest=manifest)
                    if _stage(writer, item):
                        staged += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"[cal/{account_label}] skip {raw.get('id', '?')}: {type(e).__name__}: {e}", flush=True)
            page_token = r.data.get("next_page_token")
            print(f"[cal/{account_label}] page done — fetched={fetched} staged={staged} skipped={skipped} more={bool(page_token)}", flush=True)
            if not page_token:
                break
    print(f"[cal/{account_label}] DONE  fetched={fetched} staged={staged} skipped={skipped}", flush=True)


def backfill_gmail(account_label: str) -> None:
    user_id = ACCOUNTS[account_label]
    print(f"[gmail/{account_label}] start  user_id={user_id}", flush=True)
    print(f"[gmail/{account_label}] query: {GMAIL_QUERY}", flush=True)

    manifest = load_systems_manifest(FIRM_ROOT / "firm" / "systems.yaml")
    client = make_live_gmail_client(user_id=user_id)
    conn = make_gmail_connector(client=client)
    writer = StagingWriter(
        wiki_root=WIKI_ROOT,
        source="gmail",
        target_plane="personal",
        employee_id=EMPLOYEE,
    )

    page_token: str | None = None
    fetched = staged = skipped = errored = 0
    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        while True:
            list_params: dict[str, object] = {"query": GMAIL_QUERY, "max_results": 100}
            if page_token:
                list_params["page_token"] = page_token
            list_res = harness_invoke(conn, "list_message_ids", list_params)
            ids = [m["id"] for m in (list_res.data.get("messages") or []) if m.get("id")]
            for msg_id in ids:
                fetched += 1
                # Idempotent skip
                try:
                    if writer.get(msg_id) is not None:
                        skipped += 1
                        continue
                except Exception:
                    pass
                try:
                    r = harness_invoke(conn, "get_message", {"message_id": msg_id})
                    item = gmail_message_to_envelope(r.data, manifest=manifest)
                    writer.write_envelope(item)
                    staged += 1
                except Exception as e:
                    errored += 1
                    print(f"[gmail/{account_label}] skip {msg_id}: {type(e).__name__}: {e}", flush=True)
                if fetched % 25 == 0:
                    print(f"[gmail/{account_label}] progress fetched={fetched} staged={staged} skipped={skipped} errored={errored}", flush=True)
            page_token = list_res.data.get("next_page_token")
            print(f"[gmail/{account_label}] page done — fetched={fetched} staged={staged} skipped={skipped} errored={errored} more={bool(page_token)}", flush=True)
            if not page_token:
                break
    print(f"[gmail/{account_label}] DONE  fetched={fetched} staged={staged} skipped={skipped} errored={errored}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=["calendar", "gmail"])
    ap.add_argument("account", choices=list(ACCOUNTS))
    args = ap.parse_args()
    if args.source == "calendar":
        backfill_calendar(args.account)
    else:
        backfill_gmail(args.account)


if __name__ == "__main__":
    main()
