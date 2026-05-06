"""Backfill Granola transcripts (last 30 days) → staging.

Same shape as backfill.py for gmail/calendar — uses our Composio live adapter,
walks meetings via list_meetings, fetches each meeting's metadata + full
transcript, runs them through `granola_transcript_to_envelope`, and stages.

Granola's `time_range` enum supports 'last_30_days'; we use that.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from composio_live import make_live_granola_client

from memory_mission.ingestion.envelopes import granola_transcript_to_envelope
from memory_mission.ingestion.staging import StagingWriter
from memory_mission.ingestion.systems_manifest import load_systems_manifest
from memory_mission.observability import observability_scope

FIRM_ROOT = Path("/root/memory-mission-data")
WIKI_ROOT = FIRM_ROOT / "wiki"
OBS_ROOT = FIRM_ROOT / ".observability"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"
USER_ID = "keagan"  # Composio connection user_id


def main() -> None:
    manifest = load_systems_manifest(FIRM_ROOT / "firm" / "systems.yaml")
    client = make_live_granola_client(user_id=USER_ID)
    writer = StagingWriter(
        wiki_root=WIKI_ROOT,
        source="granola",
        target_plane="personal",
        employee_id=EMPLOYEE,
    )

    fetched = staged = skipped = errored = 0
    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        list_res = client.execute("list_meetings", {"time_range": "last_30_days"})
        meetings = list_res.get("meetings", [])
        print(f"[granola] {len(meetings)} meetings in last 30 days", flush=True)

        for m in meetings:
            mid = m["id"]
            fetched += 1
            try:
                if writer.get(mid) is not None:
                    skipped += 1
                    continue
            except Exception:
                pass
            # Granola MCP rate-limit window is ~90s between successful calls
            # (probe: 30s/60s gaps both hit the limit; 90s succeeded). Pace
            # accordingly — slow but steady beats hammering retries.
            time.sleep(90.0)
            try:
                meeting = client.execute("get_meeting", {"meeting_id": mid})
                # Adapter handles its own rate-limit retry; if we still got
                # nothing parsed back, treat as a hard error rather than
                # writing a malformed envelope.
                if not meeting.get("title") and not meeting.get("created_at"):
                    raise RuntimeError("empty meeting parse (likely persistent rate-limit)")
                item = granola_transcript_to_envelope(meeting, manifest=manifest)
                writer.write_envelope(item)
                staged += 1
            except Exception as e:
                errored += 1
                print(f"[granola] err {mid}: {type(e).__name__}: {str(e)[:140]}", flush=True)
            if fetched % 10 == 0:
                print(f"[granola] {fetched}/{len(meetings)} staged={staged} skipped={skipped} errored={errored}", flush=True)
    print(f"[granola] DONE  fetched={fetched} staged={staged} skipped={skipped} errored={errored}", flush=True)


if __name__ == "__main__":
    main()
