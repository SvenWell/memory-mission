"""HubSpot CRMTarget — implements the protocol from _crm_target.

Match by email/domain via HUBSPOT_SEARCH_*_BY_CRITERIA.
Create via HUBSPOT_CREATE_CONTACT / CREATE_COMPANY.
Update with delta via HUBSPOT_UPDATE_CONTACT / UPDATE_COMPANY.

Idempotency v1: search + delta. The mm_entity_id custom property is
NOT bootstrapped yet — match falls back to email/domain. Bootstrapping
the property is a one-time follow-up (HUBSPOT_CREATE_PROPERTY_FOR_*).
"""
from __future__ import annotations

import os
import sys
from typing import Any

from _crm_target import ProjectedRecord
from _kg_projection import Company, Person


class HubSpotTarget:
    name = "hubspot"

    def __init__(self) -> None:
        self._client = None
        self._connector = None
        self._harness_invoke = None

    # --- env / connect ----------------------------------------------------

    def validate_env(self) -> None:
        if not os.environ.get("MM_HUBSPOT_USER_ID"):
            sys.stderr.write(
                "missing required env var MM_HUBSPOT_USER_ID for hubspot target\n"
            )
            raise SystemExit(2)

    def connect(self) -> None:
        from composio_live import make_live_hubspot_client  # noqa: E402
        from memory_mission.ingestion.connectors.base import invoke as harness_invoke
        from memory_mission.ingestion.connectors.hubspot import make_hubspot_connector

        user_id = os.environ["MM_HUBSPOT_USER_ID"]
        self._client = make_live_hubspot_client(user_id=user_id)
        self._connector = make_hubspot_connector(client=self._client)
        self._harness_invoke = harness_invoke

    # --- projection -------------------------------------------------------

    def project_person(self, p: Person) -> ProjectedRecord:
        props: dict[str, str] = {
            "email": p.email,
            "firstname": p.firstname,
            "lastname": p.lastname,
        }
        if p.role:
            props["jobtitle"] = p.role
        if p.organization:
            props["company"] = p.organization
        if p.phone:
            props["phone"] = p.phone
        if p.linkedin_url:
            props["hs_linkedin_url"] = p.linkedin_url

        return {
            "target": self.name,
            "object_type": "contact",
            "mm_entity_id": p.slug,
            "match_field": "email",
            "match_value": p.email,
            "props": props,
            "evidence": p.evidence,
        }

    def project_company(self, c: Company) -> ProjectedRecord:
        props: dict[str, str] = {
            "domain": c.domain,
            "name": c.display_name,
        }
        if c.address:
            props["address"] = c.address
        if c.industry:
            props["industry"] = c.industry

        return {
            "target": self.name,
            "object_type": "company",
            "mm_entity_id": c.slug,
            "match_field": "domain",
            "match_value": c.domain,
            "props": props,
            "evidence": c.evidence,
        }

    # --- search / create / update ----------------------------------------

    def _search_action(self, object_type: str) -> str:
        return "search_contacts" if object_type == "contact" else "search_companies"

    def search(self, p: ProjectedRecord) -> str | None:
        assert self._connector is not None and self._harness_invoke is not None
        action = self._search_action(p["object_type"])
        filter_groups = [
            {
                "filters": [
                    {
                        "propertyName": p["match_field"],
                        "operator": "EQ",
                        "value": p["match_value"],
                    }
                ]
            }
        ]
        res = self._harness_invoke(
            self._connector,
            action,
            {
                "filter_groups": filter_groups,
                "limit": 5,
                "properties": [p["match_field"]],
            },
        )
        results = res.data.get("results") or []
        return str(results[0].get("id") or "") if results else None

    def create(self, p: ProjectedRecord) -> str:
        assert self._connector is not None and self._harness_invoke is not None
        action = "create_contact" if p["object_type"] == "contact" else "create_company"
        res = self._harness_invoke(self._connector, action, {"properties": p["props"]})
        return str(res.data.get("id") or "")

    def update(self, target_id: str, p: ProjectedRecord) -> None:
        assert self._connector is not None and self._harness_invoke is not None
        # Compute the actual delta vs what's in HubSpot today: re-fetch the
        # props we'd write, compare, only send what differs.
        search_action = self._search_action(p["object_type"])
        snap = self._harness_invoke(
            self._connector,
            search_action,
            {
                "filter_groups": [
                    {
                        "filters": [
                            {
                                "propertyName": p["match_field"],
                                "operator": "EQ",
                                "value": p["match_value"],
                            }
                        ]
                    }
                ],
                "limit": 1,
                "properties": list(p["props"]),
            },
        )
        existing = (snap.data.get("results") or [{}])[0].get("properties") or {}
        delta: dict[str, Any] = {
            k: v for k, v in p["props"].items() if existing.get(k) != v
        }
        if not delta:
            return  # unchanged
        update_action = "update_contact" if p["object_type"] == "contact" else "update_company"
        self._harness_invoke(
            self._connector,
            update_action,
            {"object_id": target_id, "properties": delta},
        )
