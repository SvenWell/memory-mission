"""Capability-based connector roles + the normalized source-item envelope.

Lands in the P0 contract pass so the ``PersonalMemoryBackend`` Protocol
(``personal_brain/backend.py``) has a typed input shape from day one.
P2 fills in the connector implementations; this file fixes the contract
they have to emit.

Logical roles let firms swap concrete apps without rewrites: Notion can
fulfil ``document_system``, ``workspace_system``, or both; Salesforce
fulfils ``workspace_system``; Affinity / Attio / Monday do the same.
The same envelope flows from any concrete app into staging → extraction
→ promotion → personal substrate → optional sync-back.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.schema import Plane


class ConnectorRole(StrEnum):
    """Logical capability a connector fulfils for a firm.

    A firm's ``systems.yaml`` binds each role to a concrete app. The
    same app may satisfy multiple roles (e.g., Notion as both
    ``DOCUMENT`` and ``WORKSPACE``).
    """

    EMAIL = "email"
    CALENDAR = "calendar"
    TRANSCRIPT = "transcript"
    DOCUMENT = "document"
    WORKSPACE = "workspace"
    CHAT = "chat"


class NormalizedSourceItem(BaseModel):
    """The single envelope every connector emits before staging.

    Notion pages, Monday boards, Salesforce records, Gmail threads,
    Calendar events, Granola transcripts all enter the same downstream
    pipeline through this shape — no per-connector special cases in
    extraction / promotion / personal substrate code paths.

    Visibility mapping is fail-closed by P2 design: a connector that
    cannot map an external visibility annotation to a firm scope must
    refuse the item (or map it to the most-restrictive scope per the
    operator's explicit ``default_visibility`` config). No item ever
    enters staging with an unresolved scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_role: ConnectorRole
    concrete_app: str
    external_object_type: str
    external_id: str
    container_id: str | None = None
    url: str | None = None
    modified_at: datetime
    visibility_metadata: dict[str, Any] = Field(default_factory=dict)
    target_scope: str
    target_plane: Plane
    title: str
    body: str
    raw: dict[str, Any] = Field(default_factory=dict)


__all__ = ["ConnectorRole", "NormalizedSourceItem"]
