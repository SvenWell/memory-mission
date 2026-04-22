"""Doctrine tiers — the constitutional hierarchy (Step 15).

Why this exists: Memory Mission had no defense against multi-owner
document drift. Strategy, positioning, and product docs can say
different things about the same entity, and agents consuming all of
them as context end up pulled in conflicting directions. The tier
system borrows Maciek's constitutional frame: higher tiers are harder
to change, lower tiers must not silently contradict them.

V1 tiers (coarsest to finest):

- ``constitution`` — the firm's core immutable-ish truths. Hardest to
  amend. Example: mission, fiduciary duties.
- ``doctrine`` — canonical operating beliefs derived from the
  constitution. Example: investment thesis, brand positioning.
- ``policy`` — operational rules applied day-to-day. Example:
  meeting-prep checklist, CRM hygiene rules.
- ``decision`` — specific facts captured from observation. The default
  for every triple / page that doesn't explicitly opt into a higher
  tier. Example: "Alice mentioned attending the Q3 offsite."

Coherence rule: lower tiers must not silently contradict higher tiers.
Step 15b's ``check_coherence`` enforces this as an advisory warning;
firms that opt into constitutional mode get it as a blocking check.

Ordering: higher tiers override lower. ``tier_level(t)`` returns an
``int`` so tests and filters can use numeric comparison directly.
"""

from __future__ import annotations

from typing import Literal

Tier = Literal["constitution", "doctrine", "policy", "decision"]

# Ordinal ranking — higher number = higher authority. Decision = 0 so
# default-filter-off behavior (``tier_floor="decision"``) matches every
# tier, which is what callers expect.
_TIER_ORDER: dict[Tier, int] = {
    "decision": 0,
    "policy": 1,
    "doctrine": 2,
    "constitution": 3,
}

DEFAULT_TIER: Tier = "decision"

ALL_TIERS: tuple[Tier, ...] = ("constitution", "doctrine", "policy", "decision")


def tier_level(tier: Tier) -> int:
    """Return the ordinal authority level. Higher int = stronger rule."""
    return _TIER_ORDER[tier]


def is_above(a: Tier, b: Tier) -> bool:
    """True if ``a`` is strictly higher authority than ``b``."""
    return tier_level(a) > tier_level(b)


def is_at_least(a: Tier, floor: Tier) -> bool:
    """True if ``a`` is at or above ``floor``."""
    return tier_level(a) >= tier_level(floor)


__all__ = [
    "ALL_TIERS",
    "DEFAULT_TIER",
    "Tier",
    "is_above",
    "is_at_least",
    "tier_level",
]
