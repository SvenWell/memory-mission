"""Project the personal KG into a CRM target. One entry point, swappable target.

Usage:
  python push_to_crm.py --target=hubspot                # dry-run JSONL
  python push_to_crm.py --target=hubspot --apply        # match → create or update
  python push_to_crm.py --target=notion --provision     # one-time setup (Notion)
  python push_to_crm.py --target=notion --apply

Idempotent: re-runs match existing records and update only the delta
(or no-op when nothing changed). The dry-run JSONL shows exactly what
would be written, target-shaped — useful for review before --apply.

Adding a new target: implement CRMTarget in _target_<name>.py, register
it in TARGETS below.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import EMPLOYEE, FIRM_ID, FIRM_ROOT, OBS_ROOT  # noqa: E402
from _crm_target import CRMTarget, ProjectedRecord
from _kg_projection import (
    Company,
    Person,
    dedupe_companies,
    dedupe_persons,
    select_companies,
    select_persons,
)


KG_PATH = FIRM_ROOT / "personal" / EMPLOYEE / "personal_kg.db"
PREVIEW_DIR = FIRM_ROOT / ".crm-preview"


def _load_target(name: str) -> CRMTarget:
    if name == "hubspot":
        from _target_hubspot import HubSpotTarget
        return HubSpotTarget()
    if name == "notion":
        from _target_notion import NotionTarget
        return NotionTarget()
    raise SystemExit(f"unknown target {name!r}; supported: hubspot, notion")


def _write_preview(records: list[ProjectedRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _project_all(
    target: CRMTarget, persons: list[Person], companies: list[Company]
) -> list[ProjectedRecord]:
    out: list[ProjectedRecord] = []
    for p in persons:
        out.append(target.project_person(p))
    for c in companies:
        out.append(target.project_company(c))
    return out


def _apply(target: CRMTarget, records: list[ProjectedRecord]) -> None:
    from memory_mission.observability import observability_scope

    target.connect()

    created = updated = errored = 0
    print(
        f"[push-to-crm] APPLY: {len(records)} records → {target.name}",
        flush=True,
    )

    with observability_scope(
        observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE
    ):
        for i, rec in enumerate(records, 1):
            obj_type = rec["object_type"]
            mm_id = rec["mm_entity_id"]
            try:
                target_id = target.search(rec)
            except Exception as e:
                errored += 1
                print(
                    f"  [{i}/{len(records)}] {obj_type} {mm_id}: SEARCH FAILED — "
                    f"{type(e).__name__}: {str(e)[:160]}",
                    flush=True,
                )
                continue

            try:
                if target_id is None:
                    new_id = target.create(rec)
                    created += 1
                    print(
                        f"  [{i}/{len(records)}] {obj_type} {mm_id}: "
                        f"CREATED id={new_id}",
                        flush=True,
                    )
                else:
                    target.update(target_id, rec)
                    updated += 1
                    print(
                        f"  [{i}/{len(records)}] {obj_type} {mm_id}: "
                        f"UPDATED id={target_id}",
                        flush=True,
                    )
            except Exception as e:
                errored += 1
                print(
                    f"  [{i}/{len(records)}] {obj_type} {mm_id}: WRITE FAILED — "
                    f"{type(e).__name__}: {str(e)[:200]}",
                    flush=True,
                )
            time.sleep(0.1)

    print(
        f"[push-to-crm] APPLY DONE  target={target.name}  "
        f"created={created} updated={updated} errored={errored}",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, choices=["hubspot", "notion"])
    ap.add_argument(
        "--provision",
        action="store_true",
        help="target-specific one-time setup (Notion: create parent page + databases)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write to the target. Default: dry-run JSONL only.",
    )
    ap.add_argument("--limit-persons", type=int, default=-1)
    ap.add_argument("--limit-companies", type=int, default=-1)
    args = ap.parse_args()

    target = _load_target(args.target)
    target.validate_env()

    if args.provision:
        prov = getattr(target, "provision", None)
        if not callable(prov):
            sys.stderr.write(f"target {target.name!r} has no --provision step\n")
            raise SystemExit(2)
        prov()
        return

    if not KG_PATH.exists():
        sys.stderr.write(f"missing personal_kg.db at {KG_PATH}\n")
        raise SystemExit(2)

    con = sqlite3.connect(f"file:{KG_PATH}?mode=ro", uri=True)
    try:
        persons = select_persons(con)
        if args.limit_persons >= 0:
            persons = persons[: args.limit_persons]
        companies = select_companies(con)
        if args.limit_companies >= 0:
            companies = companies[: args.limit_companies]
    finally:
        con.close()

    pre_p, pre_c = len(persons), len(companies)
    persons = dedupe_persons(persons)
    companies = dedupe_companies(companies)
    if dropped := (pre_p - len(persons)) + (pre_c - len(companies)):
        print(f"[push-to-crm] deduped: dropped {dropped} duplicate(s)", flush=True)

    print(
        f"[push-to-crm] target={target.name} kg={KG_PATH} "
        f"persons={len(persons)} companies={len(companies)}",
        flush=True,
    )

    target.connect()
    records = _project_all(target, persons, companies)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    preview_path = PREVIEW_DIR / f"{target.name}-{ts}.jsonl"
    _write_preview(records, preview_path)
    n_contacts = sum(1 for r in records if r["object_type"] == "contact")
    n_companies = sum(1 for r in records if r["object_type"] == "company")
    print(
        f"[push-to-crm] preview written: {preview_path}  "
        f"contacts={n_contacts} companies={n_companies}",
        flush=True,
    )

    if not args.apply:
        print(
            "[push-to-crm] dry-run only. Re-run with --apply to write.",
            flush=True,
        )
        return

    _apply(target, records)


if __name__ == "__main__":
    main()
