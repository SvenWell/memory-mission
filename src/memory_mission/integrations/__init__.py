"""External integrations — adapters that make Memory Mission show up
as a first-class backend in other agent runtimes.

Modules:

- ``hermes_provider`` — ``MemoryMissionProvider`` mirroring Hermes'
  ``MemoryProvider`` ABC so Memory Mission Individual is a drop-in
  alongside Honcho / Mem0 / Supermemory in Hermes config dispatch
  (ADR-0015 §4 + reference memory ``reference_memory_provider_apis.md``).
"""

from memory_mission.integrations.hermes_provider import (
    MemoryMissionProvider,
    register,
)

__all__ = ["MemoryMissionProvider", "register"]
