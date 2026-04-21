"""Component 1.3 — Connector Layer.

Public surface:

- ``Connector`` Protocol + ``ConnectorAction`` / ``ConnectorResult``
- ``invoke()`` harness (observability + PII-scrubbed preview)
- ``ComposioConnector`` + ``ComposioClient`` Protocol
- Factories: ``make_granola_connector``, ``make_gmail_connector``
- ``InMemoryConnector`` test double
"""

from memory_mission.ingestion.connectors.base import (
    Connector,
    ConnectorAction,
    ConnectorResult,
    invoke,
)
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)
from memory_mission.ingestion.connectors.gmail import (
    GMAIL_ACTIONS,
    make_gmail_connector,
)
from memory_mission.ingestion.connectors.granola import (
    GRANOLA_ACTIONS,
    make_granola_connector,
)
from memory_mission.ingestion.connectors.testing import InMemoryConnector

__all__ = [
    "GMAIL_ACTIONS",
    "GRANOLA_ACTIONS",
    "ComposioClient",
    "ComposioConnector",
    "Connector",
    "ConnectorAction",
    "ConnectorResult",
    "InMemoryConnector",
    "invoke",
    "make_gmail_connector",
    "make_granola_connector",
]
