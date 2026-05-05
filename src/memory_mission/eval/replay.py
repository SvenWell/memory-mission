"""Replay captured ``mm_*`` tool calls at HEAD and diff against stored signatures.

Phase-1 scope: only ``mm_query_entity`` captures replay faithfully —
its captured args (``name`` / ``direction`` / ``as_of``) are all
pass-through under PII scrubbing, so the replayed call matches the
original exactly.

``mm_boot_context`` captures are skipped for now because the
``task_hint`` arg is redacted on capture (free-text). Replaying with
``task_hint=None`` would not isolate substrate changes from input
changes — surface that as a follow-up when we decide whether to store
unscrubbed task hints in contributor mode.

Usage from code:

    from memory_mission.eval.replay import replay_captures
    result = replay_captures(store, kg, tool_name="mm_query_entity", limit=50)
    print(result.summary())
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from memory_mission.eval.captures import EvalCapture, EvalCapturesStore, _result_signature
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

# Tools we know how to replay faithfully (args fully preserved through scrubbing).
REPLAYABLE_TOOLS = frozenset({"mm_query_entity"})


@dataclass(frozen=True)
class ReplayCase:
    """One capture's replay outcome."""

    capture_id: int
    tool_name: str
    matched: bool
    skipped: bool
    skip_reason: str | None
    old_latency_ms: int | None
    new_latency_ms: int | None
    old_signature: str
    new_signature: str | None


@dataclass(frozen=True)
class ReplayResult:
    """Aggregate replay outcome across many captures."""

    total: int
    matches: int
    differs: int
    skipped: int
    cases: list[ReplayCase] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def match_rate(self) -> float:
        replayed = self.matches + self.differs
        return self.matches / replayed if replayed > 0 else 0.0

    @property
    def mean_latency_delta_ms(self) -> float | None:
        deltas = [
            (c.new_latency_ms - c.old_latency_ms)
            for c in self.cases
            if c.old_latency_ms is not None and c.new_latency_ms is not None
        ]
        return sum(deltas) / len(deltas) if deltas else None

    def summary(self) -> str:
        lines = [
            f"Total captures replayed: {self.total}",
            f"  matches:  {self.matches}",
            f"  differs:  {self.differs}",
            f"  skipped:  {self.skipped}",
        ]
        if self.matches + self.differs > 0:
            lines.append(f"Match rate: {self.match_rate * 100:.1f}%")
        delta = self.mean_latency_delta_ms
        if delta is not None:
            sign = "+" if delta >= 0 else ""
            lines.append(f"Mean latency delta: {sign}{delta:.1f} ms")
        if self.skip_reasons:
            lines.append("Skip reasons:")
            for reason, count in sorted(self.skip_reasons.items()):
                lines.append(f"  {reason}: {count}")
        return "\n".join(lines)


def replay_captures(
    *,
    store: EvalCapturesStore,
    kg: PersonalKnowledgeGraph,
    tool_name: str | None = None,
    limit: int = 50,
) -> ReplayResult:
    """Replay the most recent ``limit`` captures and aggregate the diff."""
    captures = store.list_captures(tool_name=tool_name, limit=limit)
    cases: list[ReplayCase] = []
    skip_reasons: dict[str, int] = defaultdict(int)
    matches = 0
    differs = 0
    skipped = 0

    for capture in captures:
        case = _replay_one(capture, kg=kg)
        cases.append(case)
        if case.skipped:
            skipped += 1
            if case.skip_reason:
                skip_reasons[case.skip_reason] += 1
        elif case.matched:
            matches += 1
        else:
            differs += 1

    return ReplayResult(
        total=len(captures),
        matches=matches,
        differs=differs,
        skipped=skipped,
        cases=cases,
        skip_reasons=dict(skip_reasons),
    )


def _replay_one(capture: EvalCapture, *, kg: PersonalKnowledgeGraph) -> ReplayCase:
    """Replay one capture or mark it skipped with a reason."""
    if capture.tool_name not in REPLAYABLE_TOOLS:
        return ReplayCase(
            capture_id=capture.capture_id,
            tool_name=capture.tool_name,
            matched=False,
            skipped=True,
            skip_reason=f"tool_not_replayable:{capture.tool_name}",
            old_latency_ms=capture.latency_ms,
            new_latency_ms=None,
            old_signature=capture.result_signature,
            new_signature=None,
        )

    try:
        args = json.loads(capture.args_json)
    except json.JSONDecodeError:
        return _skip(capture, "args_json_invalid")

    if capture.tool_name == "mm_query_entity":
        return _replay_query_entity(capture, args, kg)

    return _skip(capture, f"unhandled_tool:{capture.tool_name}")


def _replay_query_entity(
    capture: EvalCapture, args: dict[str, Any], kg: PersonalKnowledgeGraph
) -> ReplayCase:
    """Faithfully re-run an ``mm_query_entity`` capture and diff signatures."""
    name = args.get("name")
    direction = args.get("direction", "outgoing")
    as_of_raw = args.get("as_of")
    as_of: date | None = None
    if isinstance(as_of_raw, str):
        try:
            as_of = date.fromisoformat(as_of_raw)
        except ValueError:
            return _skip(capture, "as_of_invalid")
    if not isinstance(name, str) or not name:
        return _skip(capture, "name_missing")
    if direction not in {"outgoing", "incoming", "both"}:
        return _skip(capture, "direction_invalid")

    started = time.perf_counter()
    triples = kg.query_entity(
        name,
        as_of=as_of,
        direction=direction,
    )
    if as_of is None:
        triples = [t for t in triples if t.valid_to is None]

    # Mirror the conflicts_with annotation from the live tool so the
    # replayed result is shape-equivalent to the captured payload.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for t in triples:
        groups[(t.subject, t.predicate)].append(t)
    out: list[dict[str, Any]] = []
    for t in triples:
        payload = t.model_dump(mode="json")
        peers = [other for other in groups[(t.subject, t.predicate)] if other.object != t.object]
        if peers:
            payload["conflicts_with"] = [
                {
                    "object": other.object,
                    "confidence": other.confidence,
                    "source_closet": other.source_closet,
                    "source_file": other.source_file,
                }
                for other in sorted(peers, key=lambda x: -x.confidence)
            ]
        out.append(payload)

    new_latency_ms = int((time.perf_counter() - started) * 1000)
    new_sig = _result_signature(out)
    return ReplayCase(
        capture_id=capture.capture_id,
        tool_name=capture.tool_name,
        matched=(new_sig == capture.result_signature),
        skipped=False,
        skip_reason=None,
        old_latency_ms=capture.latency_ms,
        new_latency_ms=new_latency_ms,
        old_signature=capture.result_signature,
        new_signature=new_sig,
    )


def _skip(capture: EvalCapture, reason: str) -> ReplayCase:
    return ReplayCase(
        capture_id=capture.capture_id,
        tool_name=capture.tool_name,
        matched=False,
        skipped=True,
        skip_reason=reason,
        old_latency_ms=capture.latency_ms,
        new_latency_ms=None,
        old_signature=capture.result_signature,
        new_signature=None,
    )
