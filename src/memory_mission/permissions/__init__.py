"""Component 4.1 — Access-control policy.

Per-firm scopes + employee assignments. Native to the architecture
(Emile: "if a person cannot see it, their agent cannot"). Pure library
— host-agent skills call ``can_read`` / ``can_propose`` as utility
functions; no engine integration.
"""

from memory_mission.permissions.policy import (
    PUBLIC_SCOPE,
    EmployeeEntry,
    Policy,
    Scope,
    can_propose,
    can_read,
    load_policy,
    page_scope,
    parse_policy_markdown,
)

__all__ = [
    "PUBLIC_SCOPE",
    "EmployeeEntry",
    "Policy",
    "Scope",
    "can_propose",
    "can_read",
    "load_policy",
    "page_scope",
    "parse_policy_markdown",
]
