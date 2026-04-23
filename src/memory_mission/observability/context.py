"""Ambient observability context — so deep call stacks don't need to thread
firm_id / employee_id / trace_id through every function signature.

Usage:

    with observability_scope(firm_id="firm-123", employee_id="emp-456"):
        log_extraction(...)  # reads context automatically
        some_deep_call()     # any log_*() inside here gets the same scope
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID, uuid4

from memory_mission.observability.logger import ObservabilityLogger

_firm_id: ContextVar[str | None] = ContextVar("firm_id", default=None)
_employee_id: ContextVar[str | None] = ContextVar("employee_id", default=None)
_trace_id: ContextVar[UUID | None] = ContextVar("trace_id", default=None)
_logger: ContextVar[ObservabilityLogger | None] = ContextVar("logger", default=None)


@contextmanager
def observability_scope(
    *,
    observability_root: Path | None = None,
    firm_id: str,
    employee_id: str | None = None,
    trace_id: UUID | None = None,
    logger: ObservabilityLogger | None = None,
) -> Iterator[None]:
    """Bind observability context for the duration of the with-block.

    Either pass a pre-built ``logger`` OR pass ``observability_root`` and a
    logger is constructed for ``firm_id``.
    """
    if logger is None:
        if observability_root is None:
            raise ValueError("observability_root is required if logger is not provided")
        logger = ObservabilityLogger(observability_root=observability_root, firm_id=firm_id)
    if logger.firm_id != firm_id:
        raise ValueError(
            f"logger.firm_id={logger.firm_id!r} does not match scope firm_id={firm_id!r}"
        )

    firm_token = _firm_id.set(firm_id)
    employee_token = _employee_id.set(employee_id)
    trace_token = _trace_id.set(trace_id or uuid4())
    logger_token = _logger.set(logger)
    try:
        yield
    finally:
        # Reset in reverse order (LIFO) per contextvars best practice.
        _logger.reset(logger_token)
        _trace_id.reset(trace_token)
        _employee_id.reset(employee_token)
        _firm_id.reset(firm_token)


def current_firm_id() -> str:
    value = _firm_id.get()
    if value is None:
        raise RuntimeError(
            "No firm_id in observability context. Wrap the call in observability_scope()."
        )
    return value


def current_employee_id() -> str | None:
    return _employee_id.get()


def current_trace_id() -> UUID:
    value = _trace_id.get()
    if value is None:
        raise RuntimeError(
            "No trace_id in observability context. Wrap the call in observability_scope()."
        )
    return value


def current_logger() -> ObservabilityLogger:
    value = _logger.get()
    if value is None:
        raise RuntimeError(
            "No logger in observability context. Wrap the call in observability_scope()."
        )
    return value
