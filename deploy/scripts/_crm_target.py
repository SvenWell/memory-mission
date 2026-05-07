"""CRMTarget protocol — what every per-CRM adapter must implement.

A target adapter takes generic Person/Company records (from
_kg_projection) and handles the CRM-specific work: shape the record for
that CRM's API, search for an existing record, create or update.

Every adapter exposes:
  - name (str)              human-readable target id ("hubspot", "notion")
  - validate_env()           raise SystemExit(2) on missing env
  - project(record)          → projected dict suitable for that target +
                               metadata used for matching/idempotency
  - search(projected)        → target_id | None (existing record id)
  - create(projected)        → target_id of the newly-created record
  - update(target_id, projected)  → None (no return — we don't track delta here)

The orchestrator (push_to_crm.py) calls these in a uniform loop and
keeps stats. Adapters can no-op `update` or implement a delta if their
API exposes one.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from _kg_projection import Company, Person


# A "projected" record is a dict carrying both the target-shaped payload
# and the metadata the orchestrator needs (object_type, mm_entity_id,
# match key, evidence). Keeping it a dict (not a dataclass) lets it
# serialize cleanly to JSONL for previews.
ProjectedRecord = dict[str, Any]


@runtime_checkable
class CRMTarget(Protocol):
    name: str

    def validate_env(self) -> None: ...
    def connect(self) -> None: ...
    def project_person(self, person: Person) -> ProjectedRecord: ...
    def project_company(self, company: Company) -> ProjectedRecord: ...
    def search(self, projected: ProjectedRecord) -> str | None: ...
    def create(self, projected: ProjectedRecord) -> str: ...
    def update(self, target_id: str, projected: ProjectedRecord) -> None: ...
