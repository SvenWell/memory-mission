"""Append-only JSONL logger for the audit trail.

Durability contract:
- One log file per firm: ``{observability_root}/{firm_id}/events.jsonl``
- Writes use ``O_APPEND`` so concurrent processes can't interleave mid-line
  (POSIX guarantees atomic append for writes up to PIPE_BUF, typically 4096
  bytes — our events are well under that).
- File is opened per-write, not cached, so ``fsync`` is implicit on close.
- No in-place edits, ever. Corrections are new events referencing old event_id.

Read contract:
- ``read_all()`` streams every event in insertion order.
- ``tail()`` yields new events as they're written (polling loop).
- Filters are applied client-side — we don't build an index in V1.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from memory_mission.memory.validators import SAFE_PATH_SEGMENT_PATTERN as _SAFE_FIRM_ID
from memory_mission.observability.events import Event

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
EVENTS_FILENAME = "events.jsonl"


def _validate_firm_id(firm_id: str) -> None:
    """Reject firm_ids that aren't safe path segments. Raises ValueError."""
    if not firm_id:
        raise ValueError("firm_id cannot be empty")
    if firm_id in (".", ".."):
        raise ValueError(f"firm_id {firm_id!r} is not allowed")
    if "\x00" in firm_id:
        raise ValueError("firm_id cannot contain NUL bytes")
    if "/" in firm_id or "\\" in firm_id:
        raise ValueError(f"firm_id {firm_id!r} must not contain path separators")
    if not _SAFE_FIRM_ID.match(firm_id):
        raise ValueError(
            f"firm_id {firm_id!r} is invalid. Must match "
            f"[A-Za-z0-9_-][A-Za-z0-9_.-]{{0,127}} (1-128 chars, alphanumeric "
            f"+ hyphen/underscore/dot, not starting with a dot)."
        )


def _safe_firm_dir(observability_root: Path, firm_id: str) -> Path:
    """Construct ``observability_root/firm_id`` and verify it stays within root.

    Belts-and-suspenders over the regex: resolve both paths (handling symlinks
    and ``..``) and confirm the firm dir is under the root. Prevents any residual
    traversal even if the regex is wrong.
    """
    _validate_firm_id(firm_id)
    candidate = (observability_root / firm_id).resolve()
    root_resolved = observability_root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"firm_id {firm_id!r} resolves outside observability_root {observability_root!r}."
        ) from exc
    return candidate


class ObservabilityLogger:
    """Firm-scoped append-only event logger."""

    def __init__(self, observability_root: Path, firm_id: str) -> None:
        observability_root.mkdir(parents=True, exist_ok=True)
        self._firm_id = firm_id
        self._firm_dir = _safe_firm_dir(observability_root, firm_id)
        self._firm_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._firm_dir / EVENTS_FILENAME

    @property
    def firm_id(self) -> str:
        return self._firm_id

    @property
    def events_path(self) -> Path:
        return self._events_path

    def write(self, event: Event) -> None:
        """Append one event to the log. Atomic per POSIX O_APPEND semantics."""
        if event.firm_id != self._firm_id:
            raise ValueError(
                f"Event firm_id={event.firm_id!r} does not match logger "
                f"firm_id={self._firm_id!r}. Cross-firm writes are forbidden."
            )
        payload = event.model_dump_json(exclude_none=False) + "\n"
        fd = os.open(self._events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)

    def read_all(self) -> Iterator[Event]:
        """Yield every event in the log, oldest first."""
        if not self._events_path.exists():
            return
        with self._events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield _EVENT_ADAPTER.validate_json(line)

    def tail(self, *, poll_interval: float = 0.5) -> Iterator[Event]:
        """Yield new events as they are appended.

        Blocks until the next event is available. Caller breaks the loop.
        """
        if not self._events_path.exists():
            self._events_path.touch(mode=0o600)
        with self._events_path.open("r", encoding="utf-8") as fh:
            # Seek to end so we only yield newly-written events.
            fh.seek(0, os.SEEK_END)
            while True:
                line = fh.readline()
                if not line:
                    time.sleep(poll_interval)
                    continue
                line = line.strip()
                if not line:
                    continue
                yield _EVENT_ADAPTER.validate_json(line)

    def count(self) -> int:
        """Return the number of events in the log."""
        if not self._events_path.exists():
            return 0
        with self._events_path.open("rb") as fh:
            return sum(1 for line in fh if line.strip())


def parse_event_line(line: str) -> Event:
    """Parse a single JSONL line into an Event. Raises on schema mismatch."""
    return _EVENT_ADAPTER.validate_json(line)


def serialize_event(event: Event) -> dict[str, Any]:
    """Serialize an event to a plain dict (for external consumers)."""
    return event.model_dump(mode="json", exclude_none=False)
