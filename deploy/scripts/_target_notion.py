"""Notion CRMTarget — implements the protocol from _crm_target.

Reads provisioned database IDs from env (set once via push_to_crm.py
--target=notion --provision). Match by mm_entity_id, fallback to
email/Domain. Composio's INSERT_ROW_DATABASE / UPDATE_ROW_DATABASE
expect properties as a list of {name, type, value} (not raw Notion
JSON), so the projection emits that shape directly.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from _crm_target import ProjectedRecord
from _kg_projection import Company, Person


def _prop(name: str, ptype: str, value: str) -> dict[str, str]:
    return {"name": name, "type": ptype, "value": value}


class NotionTarget:
    name = "notion"

    def __init__(self) -> None:
        self._client = None
        self._contacts_db = ""
        self._companies_db = ""

    # --- env / connect ---------------------------------------------------

    def validate_env(self) -> None:
        for var in (
            "MM_NOTION_USER_ID",
            "MM_NOTION_CONTACTS_DB_ID",
            "MM_NOTION_COMPANIES_DB_ID",
        ):
            if not os.environ.get(var):
                sys.stderr.write(
                    f"missing required env var {var} for notion target — "
                    f"run --provision first\n"
                )
                raise SystemExit(2)

    def connect(self) -> None:
        from composio_live import make_live_notion_client  # noqa: E402

        self._client = make_live_notion_client(
            user_id=os.environ["MM_NOTION_USER_ID"]
        )
        self._contacts_db = os.environ["MM_NOTION_CONTACTS_DB_ID"]
        self._companies_db = os.environ["MM_NOTION_COMPANIES_DB_ID"]

    # --- projection ------------------------------------------------------

    def _db_id(self, object_type: str) -> str:
        return self._contacts_db if object_type == "contact" else self._companies_db

    def project_person(self, p: Person) -> ProjectedRecord:
        display = (p.firstname + (" " + p.lastname if p.lastname else "")).strip() or p.slug
        props: list[dict[str, str]] = [
            _prop("Name", "title", display),
            _prop("mm_entity_id", "rich_text", p.slug),
            _prop("Email", "email", p.email),
        ]
        if p.phone:
            props.append(_prop("Phone", "phone_number", p.phone))
        if p.role:
            props.append(_prop("Job title", "rich_text", p.role))
        if p.organization:
            props.append(_prop("Company", "rich_text", p.organization))
        if p.linkedin_url:
            props.append(_prop("LinkedIn", "url", p.linkedin_url))

        return {
            "target": self.name,
            "object_type": "contact",
            "mm_entity_id": p.slug,
            "match_field": "Email",
            "match_value": p.email,
            "match_field_type": "email",
            "props": props,
            "evidence": p.evidence,
        }

    def project_company(self, c: Company) -> ProjectedRecord:
        props: list[dict[str, str]] = [
            _prop("Name", "title", c.display_name),
            _prop("Domain", "rich_text", c.domain),
            _prop("mm_entity_id", "rich_text", c.slug),
        ]
        if c.website:
            props.append(_prop("Website", "url", c.website))
        if c.industry:
            props.append(_prop("Industry", "rich_text", c.industry))
        if c.address:
            props.append(_prop("Address", "rich_text", c.address))

        return {
            "target": self.name,
            "object_type": "company",
            "mm_entity_id": c.slug,
            "match_field": "Domain",
            "match_value": c.domain,
            "match_field_type": "rich_text",
            "props": props,
            "evidence": c.evidence,
        }

    # --- search / create / update ---------------------------------------

    def _query_row(
        self, db_id: str, prop_name: str, prop_value: str, prop_type: str
    ) -> str | None:
        assert self._client is not None
        if prop_type == "email":
            flt = {"property": prop_name, "email": {"equals": prop_value}}
        else:
            flt = {"property": prop_name, "rich_text": {"equals": prop_value}}
        res = self._client.execute(
            "query_database",
            {"database_id": db_id, "filter": flt, "page_size": 5},
        )
        rows = res.get("results") or []
        return rows[0]["id"] if rows else None

    def search(self, p: ProjectedRecord) -> str | None:
        # Match by mm_entity_id first, then by email/Domain fallback.
        db_id = self._db_id(p["object_type"])
        rid = self._query_row(db_id, "mm_entity_id", p["mm_entity_id"], "rich_text")
        if rid:
            return rid
        if p.get("match_value"):
            return self._query_row(
                db_id, p["match_field"], p["match_value"], p["match_field_type"]
            )
        return None

    def create(self, p: ProjectedRecord) -> str:
        assert self._client is not None
        res = self._client.execute(
            "insert_row",
            {"database_id": self._db_id(p["object_type"]), "properties": p["props"]},
        )
        return str(res.get("id") or (res.get("page") or {}).get("id") or "")

    def update(self, target_id: str, p: ProjectedRecord) -> None:
        assert self._client is not None
        self._client.execute(
            "update_row",
            {"row_id": target_id, "properties": p["props"]},
        )

    # --- provisioning (target-specific; orchestrator calls when --provision)

    def provision(self) -> None:
        """Create parent page + 2 databases. Idempotent: search first."""
        from composio_live import make_live_notion_client  # noqa: E402

        client = make_live_notion_client(user_id=os.environ["MM_NOTION_USER_ID"])

        contacts_schema = [
            {"name": "Name", "type": "title"},
            {"name": "Email", "type": "email"},
            {"name": "Phone", "type": "phone_number"},
            {"name": "Job title", "type": "rich_text"},
            {"name": "Company", "type": "rich_text"},
            {"name": "LinkedIn", "type": "url"},
            {"name": "mm_entity_id", "type": "rich_text"},
        ]
        companies_schema = [
            {"name": "Name", "type": "title"},
            {"name": "Domain", "type": "rich_text"},
            {"name": "Website", "type": "url"},
            {"name": "Industry", "type": "rich_text"},
            {"name": "Address", "type": "rich_text"},
            {"name": "mm_entity_id", "type": "rich_text"},
        ]

        parent_id = os.environ.get("MM_NOTION_PARENT_PAGE_ID")
        if not parent_id:
            s = client.execute(
                "search_pages", {"query": "Memory Mission CRM", "page_size": 5}
            )
            for item in s.get("results") or []:
                if item.get("object") != "page":
                    continue
                title_prop = (item.get("properties") or {}).get("title", {})
                arr = title_prop.get("title") or []
                if arr and arr[0].get("plain_text", "").strip() == "Memory Mission CRM":
                    parent_id = item["id"]
                    print(f"[provision] found parent page — id={parent_id}")
                    break
            if not parent_id:
                s = client.execute("search_pages", {"query": "", "page_size": 5})
                pages = [r for r in (s.get("results") or []) if r.get("object") == "page"]
                if not pages:
                    sys.stderr.write("no accessible Notion pages — share one in OAuth\n")
                    raise SystemExit(2)
                host = pages[0]["id"]
                cp = client.execute(
                    "create_page", {"parent_id": host, "title": "Memory Mission CRM"}
                )
                parent_id = cp.get("id") or (cp.get("page") or {}).get("id")
                print(f"[provision] created parent page — id={parent_id}")

        def _ensure_db(title: str, schema: list[dict]) -> str:
            s = client.execute("search_pages", {"query": title, "page_size": 10})
            for item in (s.get("results") or []):
                if item.get("object") != "database":
                    continue
                t = item.get("title") or []
                if t and t[0].get("plain_text", "").strip() == title:
                    p = (item.get("parent") or {}).get("page_id", "")
                    if p.replace("-", "") == parent_id.replace("-", ""):
                        print(f"[provision] db {title!r} exists — id={item['id']}")
                        return item["id"]
            r = client.execute(
                "create_database",
                {"parent_id": parent_id, "title": title, "properties": schema},
            )
            db_id = r.get("id") or (r.get("database") or {}).get("id")
            print(f"[provision] created db {title!r} — id={db_id}")
            return db_id

        contacts_db = _ensure_db("Contacts (mm)", contacts_schema)
        companies_db = _ensure_db("Companies (mm)", companies_schema)

        print()
        print("Add these to deploy/.env.local:")
        print(f"MM_NOTION_PARENT_PAGE_ID={parent_id}")
        print(f"MM_NOTION_CONTACTS_DB_ID={contacts_db}")
        print(f"MM_NOTION_COMPANIES_DB_ID={companies_db}")
