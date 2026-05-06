"""Extract every staged item that doesn't yet have a fact-staging report.

Drop-in successor to extract_pilot.py — same process_item / call_codex logic,
but the selection is "all staged source items" rather than the 90d+30d pilot
slice. Idempotent: items with an existing `.facts/<source>/<id>.json` are
skipped on the in-process check; re-running is safe.

Run after Codex is on ChatGPT subscription mode (`codex login status`
must say "Logged in using ChatGPT" — otherwise this hits the metered API).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the heavy logic from the pilot script — same prompt, same parser,
# same ingest_facts call. We only override the selector.
import extract_pilot  # type: ignore[import-not-found]

STAGING = extract_pilot.STAGING


def select_all_items() -> list[Path]:
    """All staged markdown files across every source dir, sorted."""
    items: list[Path] = []
    for source in ("gcal", "gmail", "granola"):
        items.extend(sorted((STAGING / source).glob("*.md")))
    return items


# Monkey-patch the pilot module's selector so its `main()` picks up everything.
extract_pilot.select_pilot_items = select_all_items  # type: ignore[assignment]


if __name__ == "__main__":
    extract_pilot.main()
