"""Migrate the firm-mode KG (knowledge.db) into PersonalKnowledgeGraph.

The pilot populated `/root/memory-mission-data/knowledge.db` with 179
entities + 1,312 triples through the firm-mode pipeline. We've since
swapped Hermes back to individual-mode + MemPalace, which reads its KG
from `personal/keagan/personal_kg.db` — so those facts are unreachable
through the active agent surface.

Schemas are identical (same triples + entities columns). This script
walks firm KG and writes into personal KG via the corroborate-or-insert
pattern: same logic the framework's promotion pipeline uses, idempotent
across re-runs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.observability import observability_scope
from memory_mission.personal_brain.personal_kg import PersonalKnowledgeGraph

FIRM_ROOT = Path("/root/memory-mission-data")
FIRM_KG = FIRM_ROOT / "knowledge.db"
OBS_ROOT = FIRM_ROOT / ".observability"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"


def parse_date_or_none(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.split("T", 1)[0])
    except Exception:
        return None


def main() -> None:
    resolver = LocalIdentityResolver(FIRM_ROOT / "identity.db")
    pkg = PersonalKnowledgeGraph.for_employee(
        firm_root=FIRM_ROOT,
        employee_id=EMPLOYEE,
        identity_resolver=resolver,
    )

    src = sqlite3.connect(FIRM_KG)
    src.row_factory = sqlite3.Row

    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        # Entities — idempotent on name
        n_entities = 0
        for row in src.execute("SELECT name, entity_type, properties FROM entities"):
            try:
                props = json.loads(row["properties"]) if row["properties"] else {}
            except Exception:
                props = {}
            pkg.add_entity(row["name"], entity_type=row["entity_type"] or "unknown", properties=props)
            n_entities += 1

        # Triples — corroborate-or-insert; preserves the corroboration the firm KG already accumulated
        n_corroborated = n_inserted = n_invalidated = 0
        cur = src.execute(
            "SELECT subject, predicate, object, valid_from, valid_to, confidence, "
            "source_closet, source_file, tier FROM triples"
        )
        for row in cur:
            s, p, o = row["subject"], row["predicate"], row["object"]
            confidence = float(row["confidence"] or 0.7)
            existing = pkg.corroborate(
                s, p, o,
                confidence=confidence,
                source_closet=row["source_closet"],
                source_file=row["source_file"],
            )
            if existing is None:
                pkg.add_triple(
                    s, p, o,
                    valid_from=parse_date_or_none(row["valid_from"]),
                    valid_to=parse_date_or_none(row["valid_to"]),
                    confidence=confidence,
                    source_closet=row["source_closet"],
                    source_file=row["source_file"],
                    tier=row["tier"] or "decision",
                )
                n_inserted += 1
            else:
                n_corroborated += 1
            # If the source row was invalidated, mirror it
            if row["valid_to"]:
                pkg.invalidate(s, p, o, ended=parse_date_or_none(row["valid_to"]))
                n_invalidated += 1

    src.close()
    pkg.close()

    # Verify
    dst = sqlite3.connect(FIRM_ROOT / "personal" / EMPLOYEE / "personal_kg.db")
    dst_e = dst.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    dst_t = dst.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    dst.close()

    print(f"=== migration done ===")
    print(f"entities written: {n_entities}")
    print(f"triples corroborated: {n_corroborated}")
    print(f"triples inserted:     {n_inserted}")
    print(f"triples invalidated:  {n_invalidated}")
    print(f"personal_kg.db now: {dst_e} entities, {dst_t} triples")


if __name__ == "__main__":
    main()
