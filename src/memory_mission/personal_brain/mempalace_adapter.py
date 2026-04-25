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
        if employee_id not in self._instances:
            palace_path = self._firm_root / "personal" / employee_id / "mempalace"
            self._instances[employee_id] = _PerEmployeeInstance(palace_path)
        return self._instances[employee_id]

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
            documents=[document],
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
            metadata = self._metadata_from_row(row, employee_id, inst)
            if metadata is None:
                continue
            hit = PersonalHit(
                hit_id=metadata["external_id"],
                title=metadata.get("title", row.get("text", "")[:80]),
                snippet=row.get("text", "")[:500],
                score=float(row.get("similarity", row.get("bm25_score", 0.0))),
                cited_at=_parse_iso(metadata.get("modified_at")) or datetime.now(UTC),
                citations=[self._citation_from_metadata(metadata)],
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
            yield CandidateFact(
                employee_id=employee_id,
                fact_kind="event",
                payload={
                    "kind": "event",
                    "confidence": 0.5,
                    "support_quote": (doc or "")[:150] or title or ext_id,
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

    def _metadata_from_row(
        self,
        row: dict[str, Any],
        employee_id: str,
        inst: _PerEmployeeInstance,
    ) -> dict[str, Any] | None:
        """Pull the source metadata for a search-result row.

        ``search_memories`` returns wing/room/source_file/created_at on
        the row but not our stored metadata blob — fetch it back from
        the drawer collection by content match.
        """
        text = row.get("text") or ""
        if not text:
            return None
        try:
            res = inst.drawers.query(
                query_texts=[text[:500]],
                n_results=1,
            )
        except Exception:
            return None
        metadatas = (res.get("metadatas") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        if not metadatas or not ids:
            return None
        meta = dict(metadatas[0]) if metadatas[0] else {}
        meta.setdefault("external_id", ids[0])
        return meta

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


__all__ = ["MemPalaceAdapter"]
