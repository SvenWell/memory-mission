"""Middleware types: ModelCall, ModelResponse, Middleware protocol.

Every LLM invocation in Memory Mission flows through a MiddlewareChain. The
chain wraps the call with four hook points:

- ``before_model(call) -> call``       Input transformation (redaction, enrichment)
- ``wrap_model_call(call, next_)``     Wrap the call itself (retry, fallback, timing)
- ``after_model(call, response) -> r`` Output transformation (filtering, scrubbing)
- ``wrap_tool_call(tool_call, next_)`` (stub for Step 5+) Rate limits, permissions

All middleware methods are OPTIONAL — implement only the hooks you need.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]


class ModelCall(BaseModel):
    """An outgoing LLM request. Immutable; middleware returns a new instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: list[dict[str, Any]]
    model: str
    provider: str
    max_tokens: int | None = None
    temperature: float | None = None
    tools: list[dict[str, Any]] | None = None
    # Free-form context passed through the middleware chain. Middleware can
    # stamp flags here (e.g., ``{"pii_redacted": True}``) for downstream
    # consumers and observability.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """An LLM response. Immutable; middleware returns a new instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    provider: str
    model: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# The actual model-invocation function signature — what MiddlewareChain calls
# at the innermost layer after all middleware have wrapped it.
ModelFn = Callable[[ModelCall], ModelResponse]


@runtime_checkable
class Middleware(Protocol):
    """Protocol for middleware. Implement any subset of hooks.

    All methods are optional; MiddlewareChain skips hooks that aren't defined.
    Use duck typing / hasattr checks rather than inheritance so tests and
    user-defined middleware don't need a base class.
    """

    def before_model(self, call: ModelCall) -> ModelCall:  # pragma: no cover
        """Transform the call before it reaches the model. Return a new ModelCall."""
        ...

    def wrap_model_call(self, call: ModelCall, next_: ModelFn) -> ModelResponse:  # pragma: no cover
        """Wrap the actual model invocation. Must eventually call ``next_(call)``."""
        ...

    def after_model(
        self, call: ModelCall, response: ModelResponse
    ) -> ModelResponse:  # pragma: no cover
        """Transform the response after the model returns. Return a new ModelResponse."""
        ...
