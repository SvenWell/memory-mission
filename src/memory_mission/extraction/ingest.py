"""Persist extracted facts to staging + update mention tracking.

Step 9's contract with the host agent:

1. Host agent reads a source item from StagingWriter (Step 7 output).
2. Host agent runs its own LLM with ``EXTRACTION_PROMPT`` and the
   source body.
3. Host agent parses the LLM's JSON response with
   ``ExtractionReport.model_validate_json(...)``.
4. Host agent calls ``ingest_facts(report, ...)`` — THIS function —
   which validates the report, writes it to fact staging, and records
   entity mentions.
5. Promotion pipeline (Step 10) reads fact-staging reports and
   produces ``Proposal`` objects for review.

Nothing in this module imports an LLM client. The LLM call lives with
the host agent; our code ships the prompt template, the schema, the
validator, and the storage layer.

Layout (mirroring source staging from Step 7):

    <wiki_root>/staging/personal/<emp>/.facts/<source>/<source_id>.json
    <wiki_root>/staging/firm/.facts/<source>/<source_id>.json
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from memory_mission.extraction.schema import ExtractionReport
from memory_mission.ingestion.mentions import MentionTracker, Tier
from memory_mission.memory.schema import Plane, plane_root, validate_employee_id

_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")


class TierCrossing(BaseModel):
    """A single entity's mention-tier transition as a result of ingest.

    Callers (typically the review-proposals skill in Step 10) surface
    crossings as signals for which proposals to prioritize. Not every
    crossing is worth human attention — ``previous_tier → new_tier``
    where ``new_tier`` exceeds ``previous_tier`` is the interesting case.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_name: str
    previous_tier: Tier
    new_tier: Tier

    @property
    def is_promotion(self) -> bool:
        """``new_tier > previous_tier`` in the ``none → stub → enrich → full`` order."""
        order = {"none": 0, "stub": 1, "enrich": 2, "full": 3}
        return order[self.new_tier] > order[self.previous_tier]


class IngestResult(BaseModel):
    """What ingest_facts produced — the saved report path + mention crossings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_path: Path
    entity_names: list[str]
    tier_crossings: list[TierCrossing]


class ExtractionWriter:
    """Reads + writes ``ExtractionReport`` JSON files in fact staging.

    Scoped to one ``target_plane`` + source. Personal reports are per-
    employee; firm reports are shared across the firm.
    """

    def __init__(
        self,
        *,
        wiki_root: Path,
        source: str,
        target_plane: Plane,
        employee_id: str | None = None,
    ) -> None:
        _validate_segment(source, name="source")
        if target_plane == "personal":
            if not employee_id:
                raise ValueError("personal target_plane requires employee_id")
            validate_employee_id(employee_id)
        elif target_plane == "firm":
            if employee_id is not None:
                raise ValueError("firm target_plane must not carry an employee_id")
        else:
            raise ValueError(f"unknown target_plane: {target_plane!r}")

        self._source = source
        self._target_plane: Plane = target_plane
        self._employee_id = employee_id
        self._wiki_root = Path(wiki_root)
        # Mirror the staging layout but under a hidden .facts directory
        # so it doesn't pollute pending-source-item listings.
        self._facts_dir = (
            self._wiki_root / "staging" / plane_root(target_plane, employee_id) / ".facts" / source
        )

    @property
    def facts_dir(self) -> Path:
        return self._facts_dir

    def write(self, report: ExtractionReport) -> Path:
        """Persist a report. Overwrites any prior report for the same source_id."""
        _validate_segment(report.source_id, name="source_id")
        if report.source != self._source:
            raise ValueError(
                f"report.source {report.source!r} does not match writer source {self._source!r}"
            )
        if report.target_plane != self._target_plane:
            raise ValueError(
                f"report.target_plane {report.target_plane!r} does not match "
                f"writer target_plane {self._target_plane!r}"
            )
        if report.employee_id != self._employee_id:
            raise ValueError(
                f"report.employee_id {report.employee_id!r} does not match "
                f"writer employee_id {self._employee_id!r}"
            )

        self._facts_dir.mkdir(parents=True, exist_ok=True)
        path = self._facts_dir / f"{report.source_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            report.model_dump_json(indent=2, by_alias=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    def read(self, source_id: str) -> ExtractionReport | None:
        """Return the stored report for ``source_id`` or None if missing."""
        _validate_segment(source_id, name="source_id")
        path = self._facts_dir / f"{source_id}.json"
        if not path.exists():
            return None
        return ExtractionReport.model_validate_json(path.read_text())

    def iter_reports(self) -> Iterator[ExtractionReport]:
        """Iterate every stored report for this source."""
        if not self._facts_dir.exists():
            return
        for path in sorted(self._facts_dir.glob("*.json")):
            yield ExtractionReport.model_validate_json(path.read_text())

    def remove(self, source_id: str) -> bool:
        """Delete the stored report. Returns True if anything was removed."""
        _validate_segment(source_id, name="source_id")
        path = self._facts_dir / f"{source_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


def ingest_facts(
    report: ExtractionReport,
    *,
    wiki_root: Path,
    mention_tracker: MentionTracker | None = None,
) -> IngestResult:
    """Persist a report to fact staging; optionally update mention counts.

    - Writes the report via a plane-scoped ``ExtractionWriter``.
    - If ``mention_tracker`` is supplied, records one mention per
      UNIQUE entity name in the report (one extraction ≠ one mention
      per fact; we count the source-item level) and returns the tier
      crossings caused by this ingest.

    Returns an ``IngestResult`` the caller can use to decide what to
    surface for review. The ``TierCrossing.is_promotion`` check tells
    the review skill "this entity just crossed into a higher enrichment
    tier — worth flagging."
    """
    writer = ExtractionWriter(
        wiki_root=wiki_root,
        source=report.source,
        target_plane=report.target_plane,
        employee_id=report.employee_id,
    )
    report_path = writer.write(report)

    entity_names = report.entity_names()
    crossings: list[TierCrossing] = []
    if mention_tracker is not None:
        for name in entity_names:
            prev, new = mention_tracker.record(name)
            crossings.append(TierCrossing(entity_name=name, previous_tier=prev, new_tier=new))

    return IngestResult(
        report_path=report_path,
        entity_names=entity_names,
        tier_crossings=crossings,
    )


def _validate_segment(value: str, *, name: str) -> None:
    if not value or not _SAFE_PATH_SEGMENT.match(value):
        raise ValueError(
            f"{name} {value!r} must match {_SAFE_PATH_SEGMENT.pattern} "
            "(alphanumerics + ._- only, 1-128 chars, no path separators)"
        )


__all__ = [
    "ExtractionWriter",
    "IngestResult",
    "TierCrossing",
    "ingest_facts",
]
