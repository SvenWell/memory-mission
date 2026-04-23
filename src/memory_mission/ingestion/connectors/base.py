"""Connector layer (component 1.3) — Protocol + invocation harness.

Connectors pull data from external sources: Granola transcripts, Gmail
mailboxes, Google / Microsoft calendars, CRM systems. Every connector
implements the same ``Connector`` Protocol so the harness can dispatch
invocations uniformly.

The harness (``invoke``) threads every call through our three foundational
layers:

1. **Observability (0.4)** — writes a ``ConnectorInvocationEvent`` for every
   call so the audit trail shows when external data was pulled, from where,
   with what parameters, and how long it took.
2. **PII redaction (0.7)** — scrubs the preview of the result before it lands
   in the audit log. Raw data flows back to the caller unredacted; only what
   gets persisted is scrubbed.
3. **Durable execution (0.6)** — the caller's responsibility. Backfill loops
   wrap each ``invoke()`` in a checkpointed step so crashes resume cleanly.

Status: Protocol + harness are production shape. Concrete SDK calls on
``ComposioConnector`` are stubbed — inject a ``ComposioClient`` to wire live.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.middleware.pii import PIIRedactionMiddleware
from memory_mission.observability.api import log_connector_invocation

_DEFAULT_PREVIEW_CHARS = 500


class ConnectorAction(BaseModel):
    """One callable action a connector exposes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ConnectorResult(BaseModel):
    """Result of a connector invocation.

    ``data`` is the raw, unredacted response — the caller decides where it
    goes next. ``preview`` is a short-form string the harness uses to produce
    a PII-scrubbed audit entry; keep it small (< 1KB) and informative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: Any
    preview: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """Minimal shape every connector implements."""

    @property
    def name(self) -> str:  # pragma: no cover - protocol shape
        ...

    def list_actions(self) -> list[ConnectorAction]:  # pragma: no cover
        ...

    def invoke(self, action: str, params: dict[str, Any]) -> ConnectorResult:  # pragma: no cover
        ...


def invoke(
    connector: Connector,
    action: str,
    params: dict[str, Any] | None = None,
    *,
    redactor: PIIRedactionMiddleware | None = None,
    preview_chars: int = _DEFAULT_PREVIEW_CHARS,
) -> ConnectorResult:
    """Invoke a connector action with audit logging + PII-scrubbed preview.

    The connector's raw ``ConnectorResult`` flows back to the caller unchanged.
    What lands in the observability log is a redacted, truncated preview so
    the audit trail never contains raw email bodies, transcripts, or account
    numbers.

    Errors are logged with ``success=False`` and then re-raised — the
    harness doesn't swallow failures. Latency is measured across the whole
    call including the failure path.

    The caller is expected to run inside an ``observability_scope`` so
    ``firm_id`` / ``employee_id`` / ``trace_id`` flow into the event
    automatically. If no scope is active the underlying log call raises.
    """
    params = params or {}
    effective_redactor = redactor if redactor is not None else PIIRedactionMiddleware()

    started = time.perf_counter()
    try:
        result = connector.invoke(action, params)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_connector_invocation(
            connector_name=connector.name,
            action=action,
            preview="",
            preview_redactions={},
            latency_ms=latency_ms,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    preview_raw = result.preview[:preview_chars]
    preview_redacted, counts = effective_redactor.scrub(preview_raw)
    log_connector_invocation(
        connector_name=connector.name,
        action=action,
        preview=preview_redacted,
        preview_redactions=counts,
        latency_ms=latency_ms,
        success=True,
        error=None,
    )
    return result
