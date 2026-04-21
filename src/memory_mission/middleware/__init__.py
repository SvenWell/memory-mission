"""Component 0.7 — Middleware Layer (Guardrails).

FOUNDATIONAL. Ships in Phase 1 Step 4 (at minimum: PII redaction).

Policies that wrap every LLM call and tool call, deterministically:
- PIIRedactionMiddleware — redact client names/accounts/amounts before model sees them
- ToolCallLimitMiddleware — hard cap on paid API calls per run
- ModelFallbackMiddleware — Anthropic -> OpenAI -> Gemini on failure
- SummarizationMiddleware — auto-compress long threads
- ModelRetryMiddleware — exponential backoff on transient failures

Hooks: before_model, wrap_model_call, wrap_tool_call, after_model.

Reference: LangChain's MIT-licensed middleware (portable to Python/Hermes).
"""
