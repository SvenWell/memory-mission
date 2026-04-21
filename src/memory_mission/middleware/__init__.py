"""Component 0.7 — Middleware Layer (Guardrails).

Policies that wrap every LLM call and tool call, deterministically. Runs every
time, not whenever the model happens to remember. Hooks:

- ``before_model`` — transform input (redaction, enrichment)
- ``wrap_model_call`` — wrap the call itself (retry, fallback, timing)
- ``after_model`` — transform output (filtering, scrubbing)
- ``wrap_tool_call`` — (stub for Step 5+) tool call limits, permissions

Usage:

    from memory_mission.middleware import MiddlewareChain, PIIRedactionMiddleware

    chain = MiddlewareChain([
        PIIRedactionMiddleware(redact_input=True, redact_output=True),
        # ToolCallLimitMiddleware(...)        — Step 5
        # ModelRetryMiddleware(...)           — later
        # ModelFallbackMiddleware(...)        — later
        # SummarizationMiddleware(...)        — later
    ])

    response = chain.execute(call, model_fn)

Shipping in Step 4:
- Core types (ModelCall, ModelResponse, Middleware protocol)
- MiddlewareChain composition
- PIIRedactionMiddleware (compliance-critical for wealth management)
"""

from memory_mission.middleware.chain import MiddlewareChain
from memory_mission.middleware.pii import (
    ACCOUNT_PATTERN,
    APIKEY_PATTERN,
    CARD_PATTERN,
    EMAIL_PATTERN,
    PHONE_PATTERN,
    REDACTION_TOKENS,
    SSN_PATTERN,
    PIIRedactionMiddleware,
)
from memory_mission.middleware.types import (
    Middleware,
    ModelCall,
    ModelFn,
    ModelResponse,
    Role,
)

__all__ = [
    "ACCOUNT_PATTERN",
    "APIKEY_PATTERN",
    "CARD_PATTERN",
    "EMAIL_PATTERN",
    "PHONE_PATTERN",
    "REDACTION_TOKENS",
    "SSN_PATTERN",
    "Middleware",
    "MiddlewareChain",
    "ModelCall",
    "ModelFn",
    "ModelResponse",
    "PIIRedactionMiddleware",
    "Role",
]
