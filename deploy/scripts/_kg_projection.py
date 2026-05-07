"""KG → generic CRM-record projection — target-agnostic.

Reads from <FIRM_ROOT>/personal/<USER>/personal_kg.db and yields generic
`Person` and `Company` records that any CRM target adapter can consume.

The filter (must have email for persons, must have domain/website for
companies, single-token slugs excluded) and the dedup (collapse same-
email contacts and same-domain companies onto the entity-id with more
evidence triples) live here too — they're target-agnostic.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


# --- generic record types --------------------------------------------------

@dataclass
class Person:
    slug: str                          # KG entity name (e.g. "alice-smith")
    email: str
    firstname: str
    lastname: str
    role: str | None = None
    organization: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Company:
    slug: str
    display_name: str                   # legal_name preferred, else titleized slug
    domain: str
    website: str | None = None
    industry: str | None = None
    address: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


# --- helpers (used by both selection and per-target projection) -----------

def has_full_name(slug: str) -> bool:
    """Slug has at least firstname AND lastname tokens."""
    parts = [p for p in slug.replace("_", "-").split("-") if p]
    return len(parts) >= 2


def split_name(slug: str) -> tuple[str, str]:
    parts = [p for p in slug.replace("_", "-").split("-") if p]
    if not parts:
        return ("", "")
    cap = [p.capitalize() for p in parts]
    return (cap[0], " ".join(cap[1:]))


def titleize_slug(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.replace("_", "-").split("-") if p)


def domain_from_website(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def normalize_website(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    return s if "://" in s else "https://" + s


# --- selection from KG ----------------------------------------------------

def select_triples_about(
    con: sqlite3.Connection, subject: str, top_n: int = 8
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT predicate, object, confidence, corroboration_count, source_file
        FROM triples
        WHERE subject = ? AND valid_to IS NULL
        ORDER BY corroboration_count DESC, confidence DESC
        LIMIT ?
        """,
        (subject, top_n),
    ).fetchall()
    return [
        {
            "predicate": p,
            "object": o,
            "confidence": round(c, 3),
            "corroboration_count": cc,
            "source": sf,
        }
        for p, o, c, cc, sf in rows
    ]


def select_persons(con: sqlite3.Connection, top_n_evidence: int = 8) -> list[Person]:
    rows = con.execute(
        """
        SELECT name, properties FROM entities
        WHERE entity_type = 'person'
          AND json_extract(properties, '$.email') IS NOT NULL
          AND json_extract(properties, '$.email') != ''
        ORDER BY name
        """
    ).fetchall()
    out: list[Person] = []
    for name, props_json in rows:
        if not has_full_name(name):
            continue
        props = json.loads(props_json)
        first, last = split_name(name)
        out.append(
            Person(
                slug=name,
                email=str(props["email"]).strip().lower(),
                firstname=first,
                lastname=last,
                role=str(props["role"]) if props.get("role") else None,
                organization=str(props["organization"]) if props.get("organization") else None,
                phone=str(props["phone"]) if props.get("phone") else None,
                linkedin_url=str(props["linkedin_url"]) if props.get("linkedin_url") else None,
                evidence=select_triples_about(con, name, top_n_evidence),
            )
        )
    return out


def select_companies(con: sqlite3.Connection, top_n_evidence: int = 8) -> list[Company]:
    rows = con.execute(
        """
        SELECT name, properties FROM entities
        WHERE entity_type IN ('company', 'organization')
          AND (json_extract(properties, '$.domain') IS NOT NULL
               OR json_extract(properties, '$.website') IS NOT NULL)
        ORDER BY name
        """
    ).fetchall()
    out: list[Company] = []
    for name, props_json in rows:
        props = json.loads(props_json)
        domain = (
            props.get("domain") or domain_from_website(str(props.get("website", "")))
        ).lower()
        if not domain:
            continue
        display = props.get("legal_name") or titleize_slug(name)
        out.append(
            Company(
                slug=name,
                display_name=str(display),
                domain=domain,
                website=normalize_website(props.get("website")),
                industry=str(props["industry"]) if props.get("industry") else None,
                address=str(props["address"]) if props.get("address") else None,
                evidence=select_triples_about(con, name, top_n_evidence),
            )
        )
    return out


# --- dedup -----------------------------------------------------------------

def dedupe_persons(persons: list[Person]) -> list[Person]:
    """Same email → keep the one with more evidence. Stderr-warn on drops."""
    by_email: dict[str, Person] = {}
    for p in persons:
        if not p.email:
            continue
        existing = by_email.get(p.email)
        if existing is None:
            by_email[p.email] = p
            continue
        keep, drop = (p, existing) if len(p.evidence) > len(existing.evidence) else (existing, p)
        sys.stderr.write(
            f"[kg-projection] dedup-by-email: keep={keep.slug!r} "
            f"(evidence={len(keep.evidence)}) drop={drop.slug!r} "
            f"(evidence={len(drop.evidence)}) email={p.email}\n"
        )
        by_email[p.email] = keep
    return list(by_email.values())


def dedupe_companies(companies: list[Company]) -> list[Company]:
    by_domain: dict[str, Company] = {}
    for c in companies:
        if not c.domain:
            continue
        existing = by_domain.get(c.domain)
        if existing is None:
            by_domain[c.domain] = c
            continue
        keep, drop = (c, existing) if len(c.evidence) > len(existing.evidence) else (existing, c)
        sys.stderr.write(
            f"[kg-projection] dedup-by-domain: keep={keep.slug!r} "
            f"(evidence={len(keep.evidence)}) drop={drop.slug!r} "
            f"(evidence={len(drop.evidence)}) domain={c.domain}\n"
        )
        by_domain[c.domain] = keep
    return list(by_domain.values())
