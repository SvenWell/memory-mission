"""Ingest staged source items into MemPalace.

Walks every raw-sidecar JSON in `staging/personal/<employee>/{gmail,gcal}/.raw/`,
re-runs the envelope helper to rebuild a `NormalizedSourceItem`, and pushes
each item to the per-employee MemPalace palace via `MemPalaceAdapter.ingest`.

Idempotent at the MemPalace layer — re-ingesting the same item updates the
chromadb document rather than duplicating.

Identity (EMPLOYEE, FIRM_ID, FIRM_ROOT) comes from env — see
deploy/.env.example.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Avoid the protobuf C++ binding mismatch chromadb sometimes hits.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import EMPLOYEE, FIRM_ID, FIRM_ROOT, OBS_ROOT, STAGING

from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.ingestion.envelopes import (
    calendar_event_to_envelope,
    gmail_message_to_envelope,
    granola_transcript_to_envelope,
)
from memory_mission.ingestion.systems_manifest import load_systems_manifest
from memory_mission.observability import observability_scope
from memory_mission.personal_brain import MemPalaceAdapter


def main() -> None:
    manifest = load_systems_manifest(FIRM_ROOT / "firm" / "systems.yaml")
    resolver = LocalIdentityResolver(FIRM_ROOT / "identity.db")
    adapter = MemPalaceAdapter(firm_root=FIRM_ROOT, identity_resolver=resolver)

    counts: dict[str, int] = {"gmail": 0, "gcal": 0, "granola": 0, "errors": 0}
    started = time.time()

    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        for source, helper in (
            ("gmail", gmail_message_to_envelope),
            ("gcal", calendar_event_to_envelope),
            ("granola", granola_transcript_to_envelope),
        ):
            raw_dir = STAGING / source / ".raw"
            if not raw_dir.exists():
                continue
            files = sorted(raw_dir.glob("*.json"))
            print(f"=== {source}: {len(files)} items ===", flush=True)
            for i, jf in enumerate(files, 1):
                try:
                    payload = json.loads(jf.read_text())
                    item = helper(payload, manifest=manifest)
                    adapter.ingest(item, employee_id=EMPLOYEE)
                    counts[source] += 1
                except Exception as e:
                    counts["errors"] += 1
                    if counts["errors"] <= 5:
                        print(f"  err {source}/{jf.stem}: {type(e).__name__}: {str(e)[:120]}", flush=True)
                if i % 200 == 0:
                    elapsed = time.time() - started
                    rate = (counts["gmail"] + counts["gcal"]) / max(elapsed, 1)
                    print(f"  [{i}/{len(files)}] {source} ok={counts[source]} err={counts['errors']} rate={rate:.1f}/s", flush=True)

    elapsed = time.time() - started
    print(f"\n=== DONE in {elapsed:.0f}s  gmail={counts['gmail']} gcal={counts['gcal']} granola={counts['granola']} errors={counts['errors']} ===")


if __name__ == "__main__":
    main()
