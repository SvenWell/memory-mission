"""``InMemoryConnector`` — test double for the connector harness.

Tests construct this with a mapping of ``action -> responder``. A responder
is either a static ``ConnectorResult`` or a callable taking params and
returning a result. Every invocation is recorded on ``self.invocations`` so
tests can assert call shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memory_mission.ingestion.connectors.base import (
    ConnectorAction,
    ConnectorResult,
)

Responder = ConnectorResult | Callable[[dict[str, Any]], ConnectorResult]


class InMemoryConnector:
    """Records invocations and returns seeded responses."""

    def __init__(
        self,
        *,
        name: str = "in-memory",
        responders: dict[str, Responder] | None = None,
    ) -> None:
        self._name = name
        self._responders: dict[str, Responder] = dict(responders or {})
        self.invocations: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._name

    def list_actions(self) -> list[ConnectorAction]:
        return [ConnectorAction(name=a, description=f"stub action {a!r}") for a in self._responders]

    def register(self, action: str, responder: Responder) -> None:
        """Add or replace a responder for a single action."""
        self._responders[action] = responder

    def invoke(self, action: str, params: dict[str, Any]) -> ConnectorResult:
        self.invocations.append((action, dict(params)))
        if action not in self._responders:
            known = sorted(self._responders)
            raise ValueError(f"Unknown action {action!r}. Known: {known}")
        responder = self._responders[action]
        if callable(responder):
            return responder(params)
        return responder
