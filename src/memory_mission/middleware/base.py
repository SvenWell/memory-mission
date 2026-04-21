"""Middleware base class with the four canonical hooks.

TODO (Step 4): Implement:
- Abstract base class with before_model / wrap_model_call / wrap_tool_call / after_model hooks
- MiddlewareChain composer
- Wire into LLM call sites from Hermes runtime adapter

Concrete middleware (Step 4 onward):
- PIIRedactionMiddleware (critical for wealth management compliance — ships Phase 1)
- ToolCallLimitMiddleware
- ModelFallbackMiddleware
- SummarizationMiddleware
"""
