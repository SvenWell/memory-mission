"""Component 1.3 — Connector Layer.

Public surface:

- ``Connector`` Protocol + ``ConnectorAction`` / ``ConnectorResult``
- ``invoke()`` harness (observability + PII-scrubbed preview)
- ``ComposioConnector`` + ``ComposioClient`` Protocol
- Per-app Composio-backed connector factories
- ``InMemoryConnector`` test double
"""

from memory_mission.ingestion.connectors.affinity import (
    AFFINITY_ACTIONS,
    make_affinity_connector,
)
from memory_mission.ingestion.connectors.attio import (
    ATTIO_ACTIONS,
    make_attio_connector,
)
from memory_mission.ingestion.connectors.base import (
    Connector,
    ConnectorAction,
    ConnectorResult,
    invoke,
)
from memory_mission.ingestion.connectors.calendar import (
    CALENDAR_ACTIONS,
    make_calendar_connector,
)
from memory_mission.ingestion.connectors.composio import (
    ComposioClient,
    ComposioConnector,
)
from memory_mission.ingestion.connectors.drive import (
    DRIVE_ACTIONS,
    make_drive_connector,
)
from memory_mission.ingestion.connectors.gmail import (
    GMAIL_ACTIONS,
    make_gmail_connector,
)
from memory_mission.ingestion.connectors.granola import (
    GRANOLA_ACTIONS,
    make_granola_connector,
)
from memory_mission.ingestion.connectors.hubspot import (
    HUBSPOT_ACTIONS,
    HUBSPOT_STANDARD_OBJECT_TYPES,
    make_hubspot_connector,
)
from memory_mission.ingestion.connectors.notion import (
    NOTION_ACTIONS,
    make_notion_connector,
)
from memory_mission.ingestion.connectors.onedrive import (
    ONEDRIVE_ACTIONS,
    make_onedrive_connector,
)
from memory_mission.ingestion.connectors.outlook import (
    OUTLOOK_ACTIONS,
    make_outlook_connector,
)
from memory_mission.ingestion.connectors.slack import (
    SLACK_ACTIONS,
    make_slack_connector,
)
from memory_mission.ingestion.connectors.testing import InMemoryConnector

__all__ = [
    "AFFINITY_ACTIONS",
    "ATTIO_ACTIONS",
    "CALENDAR_ACTIONS",
    "DRIVE_ACTIONS",
    "GMAIL_ACTIONS",
    "GRANOLA_ACTIONS",
    "HUBSPOT_ACTIONS",
    "HUBSPOT_STANDARD_OBJECT_TYPES",
    "NOTION_ACTIONS",
    "ONEDRIVE_ACTIONS",
    "OUTLOOK_ACTIONS",
    "SLACK_ACTIONS",
    "ComposioClient",
    "ComposioConnector",
    "Connector",
    "ConnectorAction",
    "ConnectorResult",
    "InMemoryConnector",
    "invoke",
    "make_affinity_connector",
    "make_attio_connector",
    "make_calendar_connector",
    "make_drive_connector",
    "make_gmail_connector",
    "make_granola_connector",
    "make_hubspot_connector",
    "make_notion_connector",
    "make_onedrive_connector",
    "make_outlook_connector",
    "make_slack_connector",
]
