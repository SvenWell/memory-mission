"""MiddlewareChain — composes middleware around an LLM invocation.

Hook ordering (explicit, so middleware authors can reason about it):

1. ``before_model`` hooks run in chain order (first registered runs first).
2. ``wrap_model_call`` hooks wrap the call onion-style: the first middleware
   is the OUTERMOST wrapper, the last is closest to the actual model call.
3. ``after_model`` hooks run in REVERSE order (last registered runs first).

This matches the mental model of request middleware in web frameworks: a request
enters through layer 1, exits through layer N, and the response unwinds back
out the same layers it came through.
"""

from __future__ import annotations

from collections.abc import Sequence

from memory_mission.middleware.types import Middleware, ModelCall, ModelFn, ModelResponse


class MiddlewareChain:
    """An ordered composition of middleware applied around a model call."""

    def __init__(self, middlewares: Sequence[Middleware] | None = None) -> None:
        self._middlewares: list[Middleware] = list(middlewares or [])

    def __len__(self) -> int:
        return len(self._middlewares)

    def append(self, middleware: Middleware) -> None:
        """Add a middleware to the end of the chain (innermost)."""
        self._middlewares.append(middleware)

    def execute(self, call: ModelCall, model_fn: ModelFn) -> ModelResponse:
        """Invoke model_fn wrapped by the full middleware chain."""
        # Step 1: before_model hooks (in chain order).
        for m in self._middlewares:
            if _has_hook(m, "before_model"):
                call = m.before_model(call)

        # Step 2: wrap_model_call — build the onion from innermost to outermost.
        wrapped: ModelFn = model_fn
        for m in reversed(self._middlewares):
            if _has_hook(m, "wrap_model_call"):
                wrapped = _bind_wrap(m, wrapped)

        response = wrapped(call)

        # Step 3: after_model hooks (in REVERSE order).
        for m in reversed(self._middlewares):
            if _has_hook(m, "after_model"):
                response = m.after_model(call, response)

        return response


def _has_hook(middleware: object, name: str) -> bool:
    """True if the middleware defines ``name`` as a non-default method.

    We can't just ``hasattr`` because ``Protocol`` classes technically satisfy
    the hook by declaration. Check that the method is defined on the instance's
    class (or a subclass), not inherited only from ``Protocol``.
    """
    method = getattr(type(middleware), name, None)
    if method is None:
        return False
    # Pure Protocol declarations are unbound functions with ... body. Subclasses
    # override them with concrete implementations. We accept any callable that
    # isn't the Protocol's own stub.
    return callable(method) and not _is_protocol_stub(method)


def _is_protocol_stub(method: object) -> bool:
    """Detect the Protocol's own ``...`` stub method so we skip it.

    The Protocol class in ``types.py`` defines stubs with ``...`` bodies. These
    get wrapped by the runtime_checkable decorator. We check for the absence of
    a real implementation by looking at the owning class.
    """
    qualname: str = getattr(method, "__qualname__", "")
    return qualname.startswith("Middleware.")


def _bind_wrap(middleware: Middleware, inner: ModelFn) -> ModelFn:
    """Bind a middleware's wrap_model_call around an inner function."""

    def wrapped(call: ModelCall) -> ModelResponse:
        return middleware.wrap_model_call(call, inner)

    return wrapped
