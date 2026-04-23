"""Composio SDK adapter.

The Composio Python SDK handles OAuth, credential storage, and action
dispatch across 500+ integrations. In production we pass a real
``composio.Client`` as the ``client`` parameter; in tests we inject a fake
satisfying the ``ComposioClient`` Protocol.

**Status:** adapter shape is in place; concrete SDK calls are TODO — the
adapter raises ``NotImplementedError`` on ``invoke()`` until a client is
attached. The Composio SDK itself is already running in adjacent production
systems; we just haven't wired credentials in this repo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from memory_mission.ingestion.connectors.base import (
    ConnectorAction,
    ConnectorResult,
)


@runtime_checkable
class ComposioClient(Protocol):
    """Minimal interface we need from the real Composio SDK client.

    Narrow on purpose so tests can pass a fake without importing the real
    SDK. Production wires ``composio.Client().actions.execute`` (or the
    equivalent call) behind this method.
    """

    def execute(
        self, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:  # pragma: no cover - protocol shape
        ...


PreviewFn = Callable[[dict[str, Any]], str]


class ComposioConnector:
    """Generic Composio-backed connector.

    Factories in ``granola.py`` / ``gmail.py`` preconfigure this with the
    appropriate action list + preview formatter. Callers inject a
    ``ComposioClient`` when they're ready to hit live APIs.
    """

    def __init__(
        self,
        *,
        name: str,
        actions: tuple[ConnectorAction, ...],
        client: ComposioClient | None = None,
        preview_fn: PreviewFn | None = None,
    ) -> None:
        self._name = name
        self._actions = actions
        self._client = client
        self._preview_fn = preview_fn or _default_preview

    @property
    def name(self) -> str:
        return self._name

    def list_actions(self) -> list[ConnectorAction]:
        return list(self._actions)

    def invoke(self, action: str, params: dict[str, Any]) -> ConnectorResult:
        if not any(a.name == action for a in self._actions):
            known = [a.name for a in self._actions]
            raise ValueError(
                f"Unknown action {action!r} for connector {self._name!r}. Known: {known}"
            )
        if self._client is None:
            raise NotImplementedError(
                f"ComposioConnector({self._name!r}) has no client attached. "
                "Inject a ComposioClient (production) or a fake (tests) "
                "before calling invoke()."
            )
        raw = self._client.execute(action, params)
        return ConnectorResult(
            data=raw,
            preview=self._preview_fn(raw),
            metadata={"composio_action": action},
        )


def _default_preview(raw: dict[str, Any]) -> str:
    """Fallback preview: stringify a few top-level keys. Override per connector."""
    return " ".join(f"{k}={raw[k]!r}" for k in list(raw)[:5])[:500]
