"""Backfill Gmail + Calendar through mm's connector → staging pipeline.

Configurable for either toolkit and account. Idempotent: skips items already
staged. Checkpointed via the durable_run primitive so crashes resume cleanly.

Identity (EMPLOYEE, FIRM_ID, FIRM_ROOT) and the per-toolkit account mapping
come from environment variables — see deploy/scripts/_config.py and
deploy/.env.example for the contract.

Usage (account label must exist in MM_GMAIL_ACCOUNTS / MM_CALENDAR_ACCOUNTS):
    python backfill.py calendar primary
    python backfill.py gmail work
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import EMPLOYEE, FIRM_ID, FIRM_ROOT, OBS_ROOT, WIKI_ROOT, parse_accounts
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

DURABLE_DB = FIRM_ROOT / "durable.sqlite3"

# 365 days back from today (was 180)
LOOKBACK_DAYS = 365
WINDOW_END = datetime.now(timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=LOOKBACK_DAYS)

# Tight Gmail filter: drop noise, keep real correspondence + signal items.
GMAIL_QUERY = (
    f"newer_than:{LOOKBACK_DAYS}d "
    "-category:promotions -category:social -in:spam "
    "-from:noreply -from:no-reply -from:notifications "
    "(is:starred OR is:important OR -is:list)"
)


def _resolve_account(env_var: str, label: str) -> str:
    accounts = parse_accounts(env_var)
    if label not in accounts:
        sys.stderr.write(
            f"account label {label!r} not in {env_var}={accounts or 'unset'}\n"
        )
        raise SystemExit(2)
    return accounts[label]


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
    user_id = _resolve_account("MM_CALENDAR_ACCOUNTS", account_label)
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
    user_id = _resolve_account("MM_GMAIL_ACCOUNTS", account_label)
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
    ap.add_argument("account", help="label from MM_CALENDAR_ACCOUNTS / MM_GMAIL_ACCOUNTS")
    args = ap.parse_args()
    if args.source == "calendar":
        backfill_calendar(args.account)
    else:
        backfill_gmail(args.account)


if __name__ == "__main__":
    main()
