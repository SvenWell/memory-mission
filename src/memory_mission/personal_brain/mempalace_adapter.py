"""``MemPalaceAdapter`` — ``PersonalMemoryBackend`` over per-employee MemPalace palaces.

Each employee gets their own MemPalace palace at
``firm/personal/<employee_id>/mempalace/``. That directory is the
per-employee storage substrate; queries / citations / candidate-facts
are scoped to that path. **Employee-private isolation is structural**:
data ingested under one ``employee_id`` lives in a different palace
directory and is never reachable from another employee's queries.

The adapter avoids MemPalace's CLI ``init`` flow by creating the two
ChromaDB collections (``mempalace_drawers`` + ``mempalace_closets``)
directly via the lower-level ``palace.get_collection`` /
``get_closets_collection`` helpers. This skips the entity-detection +
language-config init step but lets us push items via the API instead of
running ``mempalace mine`` against a directory tree.

ADR-0004 documents the adoption rationale; this file is the
implementation that decision points to.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mempalace import palace as _mp_palace
from mempalace import searcher as _mp_searcher

from memory_mission.identity.base import IdentityResolver, parse_identifier
from memory_mission.ingestion.roles import NormalizedSourceItem
from memory_mission.memory.schema import validate_employee_id
from memory_mission.personal_brain.backend import (
    CandidateFact,
    Citation,
    EntityRef,
    IngestResult,
    PersonalHit,
    WorkingContext,
)


class _PerEmployeeInstance:
    """One employee's MemPalace palace + cached collection handles."""

    def __init__(self, palace_path: Path) -> None:
        self.palace_path = palace_path
        palace_path.mkdir(parents=True, exist_ok=True)
        # Eagerly create both ChromaDB collections so search_memories
        # (which assumes both exist) doesn't error on empty palaces.
        self.closets = _mp_palace.get_closets_collection(str(palace_path))
        self.drawers = _mp_palace.get_collection(str(palace_path))


class MemPalaceAdapter:
    """Personal-substrate backend backed by per-employee MemPalace palaces.

    Constructor takes an ``IdentityResolver`` so ``resolve_entity`` can
    bridge to firm-wide stable IDs (``p_<token>`` / ``o_<token>``). The
    resolver itself is shared across all employees of one firm — only
    the MemPalace palaces are per-employee.
    """

    def __init__(self, firm_root: Path, identity_resolver: IdentityResolver) -> None:
        self._firm_root = firm_root
        self._identity = identity_resolver
        self._instances: dict[str, _PerEmployeeInstance] = {}

    def _instance(self, employee_id: str) -> _PerEmployeeInstance:
        safe_employee_id = validate_employee_id(employee_id)
        if safe_employee_id not in self._instances:
            palace_path = self._firm_root / "personal" / safe_employee_id / "mempalace"
            self._instances[safe_employee_id] = _PerEmployeeInstance(palace_path)
        return self._instances[safe_employee_id]

    # ---------- Protocol surface ----------

    def ingest(
        self,
        item: NormalizedSourceItem,
        *,
        employee_id: str,
    ) -> IngestResult:
        inst = self._instance(employee_id)
        wing = item.source_role.value
        room = item.container_id or "default"
        document = f"{item.title}\n\n{item.body}"
        indexed_document = _encode_indexed_document(item.external_id, document)
        metadata = self._metadata_for(item)

        # Write to closets (MemPalace's primary line-level store).
        # MemPalace's helpers are untyped — `# type: ignore` accepted at
        # the boundary; we never re-export their types onto our surface.
        lines = _mp_palace.build_closet_lines(  # type: ignore[no-untyped-call]
            source_file=item.url or item.external_id,
            drawer_ids=[item.external_id],
            content=document,
            wing=wing,
            room=room,
        )
        _mp_palace.upsert_closet_lines(  # type: ignore[no-untyped-call]
            inst.closets,
            item.external_id,
            lines,
            metadata=metadata,
        )

        # Write to drawers (the document-level collection that
        # ``search_memories`` reads). Without this the search returns
        # zero hits even after closet upsert.
        inst.drawers.upsert(
            ids=[item.external_id],
            documents=[indexed_document],
            metadatas=[metadata],
        )
        return IngestResult(items_ingested=1)

    def query(
        self,
        question: str,
        *,
        employee_id: str,
        limit: int = 10,
    ) -> list[PersonalHit]:
        inst = self._instance(employee_id)
        result = _mp_searcher.search_memories(
            query=question,
            palace_path=str(inst.palace_path),
            n_results=limit,
        )
        if isinstance(result, dict) and "error" in result:
            return []
        if not isinstance(result, dict):
            return []
        rows: list[dict[str, Any]] = result.get("results", [])
        hits: list[PersonalHit] = []
        for row in rows:
            metadata, clean_text = self._metadata_and_text_from_row(row, inst)
            if metadata is None:
                continue
            hit_id = metadata["external_id"]
            citation = self._citation_from_metadata(metadata)
            if citation.external_id != hit_id:
                continue
            hit = PersonalHit(
                hit_id=hit_id,
                title=metadata.get("title", clean_text[:80]),
                snippet=clean_text[:500],
                score=float(row.get("similarity", row.get("bm25_score", 0.0))),
                cited_at=_parse_iso(metadata.get("modified_at")) or datetime.now(UTC),
                citations=[citation],
            )
            hits.append(hit)
        return hits

    def citations(
        self,
        hit_id: str,
        *,
        employee_id: str,
    ) -> list[Citation]:
        inst = self._instance(employee_id)
        try:
            res = inst.drawers.get(ids=[hit_id])
        except Exception:
            return []
        ids = res.get("ids") or []
        metadatas = res.get("metadatas") or []
        if not ids or not metadatas:
            return []
        first_metadata = metadatas[0]
        if not first_metadata:
            return []
        return [self._citation_from_metadata(first_metadata)]

    def resolve_entity(
        self,
        identifiers: list[str],
        *,
        employee_id: str,
    ) -> EntityRef:
        validated: set[str] = set()
        for raw in identifiers:
            parse_identifier(raw)
            validated.add(raw)
        entity_id = self._identity.resolve(validated)
        identity = self._identity.get_identity(entity_id)
        canonical = identity.canonical_name if identity is not None else None
        return EntityRef(
            entity_id=entity_id,
            canonical_name=canonical,
            identifiers=sorted(validated),
        )

    def working_context(
        self,
        *,
        employee_id: str,
        task: str,
    ) -> WorkingContext:
        relevant = self.query(task, employee_id=employee_id, limit=5)
        return WorkingContext(
            employee_id=employee_id,
            task=task,
            relevant_hits=relevant,
        )

    def candidate_facts(
        self,
        *,
        employee_id: str,
        since: datetime | None = None,
    ) -> Iterable[CandidateFact]:
        inst = self._instance(employee_id)
        try:
            payload = inst.drawers.get()
        except Exception:
            return
        ids = payload.get("ids") or []
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        for ext_id, doc, metadata in zip(ids, documents, metadatas, strict=False):
            if metadata is None:
                continue
            source_role = metadata.get("source_role")
            if source_role not in {"transcript", "email"}:
                continue
            modified_at = _parse_iso(metadata.get("modified_at"))
            if since is not None and modified_at is not None and modified_at < since:
                continue
            citation = self._citation_from_metadata(metadata)
            title = metadata.get("title", "")
            clean_doc = _strip_index_marker(doc or "")
            yield CandidateFact(
                employee_id=employee_id,
                fact_kind="event",
                payload={
                    "kind": "event",
                    "confidence": 0.5,
                    "support_quote": clean_doc[:150] or title or ext_id,
                    "entity_name": title or ext_id,
                    "description": title or ext_id,
                },
                citations=[citation],
                confidence=0.5,
                surfaced_at=datetime.now(UTC),
            )

    # ---------- Metadata helpers ----------

    def _metadata_for(self, item: NormalizedSourceItem) -> dict[str, str]:
        """Flatten a NormalizedSourceItem into Chroma-friendly string metadata.

        ChromaDB metadata values must be primitives; we serialize datetimes
        to ISO strings and drop ``raw`` (kept on the item but not in the
        index — too large to store every time).
        """
        return {
            "source_role": item.source_role.value,
            "concrete_app": item.concrete_app,
            "external_object_type": item.external_object_type,
            "external_id": item.external_id,
            "container_id": item.container_id or "",
            "url": item.url or "",
            "modified_at": item.modified_at.isoformat(),
            "target_scope": item.target_scope,
            "target_plane": item.target_plane,
            "title": item.title,
        }

    def _metadata_and_text_from_row(
        self,
        row: dict[str, Any],
        inst: _PerEmployeeInstance,
    ) -> tuple[dict[str, Any] | None, str]:
        """Return exact source metadata + marker-stripped text for a search row."""
        text = row.get("text") or ""
        if not text:
            return None, ""
        marker_id, clean_text = _decode_indexed_document(text)
        row_metadata = _metadata_from_search_row(row)
        if row_metadata is not None:
            row_external_id = row_metadata.get("external_id")
            if row_external_id and (marker_id is None or row_external_id == marker_id):
                return row_metadata, clean_text
        if marker_id:
            metadata = self._metadata_for_external_id(marker_id, inst)
            return metadata, clean_text
        metadata = self._metadata_by_exact_document(text, inst)
        return metadata, _strip_index_marker(text)

    def _metadata_for_external_id(
        self,
        external_id: str,
        inst: _PerEmployeeInstance,
    ) -> dict[str, Any] | None:
        try:
            res = inst.drawers.get(ids=[external_id])
        except Exception:
            return None
        ids = res.get("ids") or []
        metadatas = res.get("metadatas") or []
        if not metadatas or not ids:
            return None
        metadata = dict(metadatas[0]) if metadatas[0] else {}
        metadata.setdefault("external_id", ids[0])
        return metadata

    def _metadata_by_exact_document(
        self,
        document: str,
        inst: _PerEmployeeInstance,
    ) -> dict[str, Any] | None:
        """Fallback for pre-marker rows: match the exact stored document text."""
        try:
            res = inst.drawers.get()
        except Exception:
            return None
        ids = res.get("ids") or []
        documents = res.get("documents") or []
        metadatas = res.get("metadatas") or []
        for external_id, stored_doc, metadata in zip(ids, documents, metadatas, strict=False):
            if stored_doc != document:
                continue
            out = dict(metadata) if metadata else {}
            out.setdefault("external_id", external_id)
            return out
        return None

    def _citation_from_metadata(self, metadata: dict[str, Any]) -> Citation:
        modified_at = _parse_iso(metadata.get("modified_at")) or datetime.now(UTC)
        url_raw = metadata.get("url") or ""
        container_raw = metadata.get("container_id") or ""
        return Citation(
            source_role=metadata.get("source_role", "unknown"),
            concrete_app=metadata.get("concrete_app", "unknown"),
            external_id=metadata.get("external_id", ""),
            container_id=container_raw or None,
            url=url_raw or None,
            modified_at=modified_at,
            excerpt=metadata.get("title"),
        )


def _parse_iso(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _metadata_from_search_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Extract first-class row metadata if MemPalace exposes it."""
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        return dict(metadata)
    metadatas = row.get("metadatas")
    if isinstance(metadatas, list) and metadatas and isinstance(metadatas[0], dict):
        return dict(metadatas[0])
    return None


_INDEX_MARKER_PREFIX = "MM_SOURCE_ID:"


def _encode_indexed_document(external_id: str, document: str) -> str:
    return f"{_INDEX_MARKER_PREFIX}{external_id}\n{document}"


def _decode_indexed_document(document: str) -> tuple[str | None, str]:
    if not document.startswith(_INDEX_MARKER_PREFIX):
        return None, document
    first_line, _, rest = document.partition("\n")
    marker_id = first_line.removeprefix(_INDEX_MARKER_PREFIX)
    return marker_id or None, rest


def _strip_index_marker(document: str) -> str:
    _, clean = _decode_indexed_document(document)
    return clean


__all__ = ["MemPalaceAdapter"]
