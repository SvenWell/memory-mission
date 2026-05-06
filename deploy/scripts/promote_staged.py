"""Walk fact-staging → create_proposal per (source × target entity).

After extract_pilot.py completes, every staged source item has a corresponding
ExtractionReport in <wiki_root>/staging/personal/<emp>/.facts/<source>/<id>.json.

This script groups each report's facts by target entity and creates one
Proposal per (report, entity). Proposals land in proposals.db pending review.

Idempotent: create_proposal() uses a deterministic proposal_id, so re-runs
return the existing proposal instead of duplicating.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from memory_mission.extraction import ExtractionReport
from memory_mission.observability import observability_scope
from memory_mission.promotion.pipeline import create_proposal
from memory_mission.promotion.proposals import ProposalStore

FIRM_ROOT = Path("/root/memory-mission-data")
WIKI_ROOT = FIRM_ROOT / "wiki"
STAGING_FACTS = WIKI_ROOT / "staging" / "personal" / "keagan" / ".facts"
OBS_ROOT = FIRM_ROOT / ".observability"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"


def fact_target_entity(fact: dict) -> str | None:
    """Pick the canonical target entity for a fact."""
    if fact.get("kind") == "identity":
        return fact.get("entity_name")
    if fact.get("kind") in ("event", "preference"):
        return fact.get("entity_name") or fact.get("subject")
    if fact.get("kind") == "relationship":
        return fact.get("subject")
    if fact.get("kind") == "update":
        return fact.get("entity_name") or fact.get("subject")
    if fact.get("kind") == "open_question":
        return fact.get("subject") or "open_questions"
    return None


def main() -> None:
    store = ProposalStore(FIRM_ROOT / "proposals.db")
    n_reports = 0
    n_proposals = 0
    n_skipped_empty = 0

    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        for source in ("gmail", "gcal"):
            d = STAGING_FACTS / source
            if not d.exists():
                continue
            for jf in sorted(d.glob("*.json")):
                n_reports += 1
                try:
                    report = ExtractionReport.model_validate_json(jf.read_text())
                except Exception as e:
                    print(f"  bad report {jf}: {e}", flush=True)
                    continue

                # Group facts by target entity
                groups: dict[str, list] = defaultdict(list)
                raw = json.loads(jf.read_text())
                for fact_dict, fact_obj in zip(raw["facts"], report.facts):
                    target = fact_target_entity(fact_dict)
                    if not target:
                        continue
                    groups[target].append(fact_obj)

                for entity, facts in groups.items():
                    if not facts:
                        n_skipped_empty += 1
                        continue
                    try:
                        prop = create_proposal(
                            store,
                            target_plane="personal",
                            target_entity=entity,
                            facts=facts,
                            source_report_path=str(jf.relative_to(WIKI_ROOT)),
                            proposer_agent_id="extract-pilot",
                            proposer_employee_id=EMPLOYEE,
                            target_employee_id=EMPLOYEE,
                            target_scope="personal",
                        )
                        n_proposals += 1
                    except Exception as e:
                        print(f"  create_proposal fail {jf.stem}/{entity}: {type(e).__name__}: {e}", flush=True)

                if n_reports % 50 == 0:
                    print(f"  reports={n_reports} proposals={n_proposals} empty_groups={n_skipped_empty}", flush=True)

    print(f"=== DONE  reports={n_reports} proposals={n_proposals} empty_groups={n_skipped_empty} ===")


if __name__ == "__main__":
    main()
