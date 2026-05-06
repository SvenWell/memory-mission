"""Personal-plane simple-write: walk fact-staging → land triples in the KG.

This is the path the framework intends for personal-plane facts:
extraction lands in fact-staging, then facts go straight into the KG
with provenance — no proposal gate. (ADR-0015 simple-write policy.)

We were running the firm-plane proposal pipeline by mistake. This script
implements the correct personal-plane shape: open the KG, open the identity
resolver, walk every fact-staging file, write each fact's triples directly.

Idempotent: KG.add_triple is upsert-shaped via (subject, predicate, object)
+ source provenance. Re-running on the same fact-staging yields the same
end state.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from memory_mission.extraction import ExtractionReport
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.memory.knowledge_graph import KnowledgeGraph
from memory_mission.observability import observability_scope

FIRM_ROOT = Path("/root/memory-mission-data")
KG_PATH = FIRM_ROOT / "knowledge.db"
IDENTITY_PATH = FIRM_ROOT / "identity.db"
FACT_STAGING = FIRM_ROOT / "wiki" / "staging" / "personal" / "keagan" / ".facts"
OBS_ROOT = FIRM_ROOT / ".observability"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"


def canonicalize(resolver: LocalIdentityResolver, name: str, identifiers: list[str], kind: str) -> str:
    """Resolve to a stable id when identifiers are present; else pass-through name."""
    if not identifiers:
        return name
    entity_kind = "person" if kind == "person" else ("organization" if kind in {"company", "firm", "org"} else "person")
    try:
        return resolver.resolve(set(identifiers), entity_type=entity_kind, canonical_name=name)
    except Exception:
        return name


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def is_mention_only_identity(fact: dict) -> bool:
    """Mirrors the new EXTRACTION_PROMPT contract: identity facts must
    carry a NEW property or identifier. Empty-on-both = mention-only.
    """
    if fact.get("kind") != "identity":
        return False
    props = fact.get("properties") or {}
    idents = fact.get("identifiers") or []
    return not props and not idents


def upsert_triple(
    kg: KnowledgeGraph,
    s: str,
    p: str,
    o: str,
    *,
    confidence: float,
    source_closet: str,
    source_file: str,
    valid_from=None,
) -> str:
    """Corroborate-or-insert. Returns 'corroborated' or 'inserted'.

    The framework's `corroborate` returns the existing triple (Noisy-OR
    confidence bump) if a currently-true match exists, else None. We
    fall through to `add_triple` when no match — same shape as
    `_apply_facts` in promotion/pipeline.
    """
    existing = kg.corroborate(
        s, p, o,
        confidence=confidence,
        source_closet=source_closet,
        source_file=source_file,
    )
    if existing is not None:
        return "corroborated"
    kg.add_triple(
        s, p, o,
        valid_from=valid_from,
        confidence=confidence,
        source_closet=source_closet,
        source_file=source_file,
    )
    return "inserted"


def write_fact(
    kg: KnowledgeGraph,
    resolver: LocalIdentityResolver,
    fact: dict,
    *,
    source_closet: str,
    source_file: str,
    counters: dict,
) -> int:
    """Returns number of KG operations performed for this fact."""
    kind = fact.get("kind")
    confidence = float(fact.get("confidence") or 0.7)

    if kind == "identity":
        if is_mention_only_identity(fact):
            counters["filtered_identity"] = counters.get("filtered_identity", 0) + 1
            return 0
        name = fact.get("entity_name")
        if not name:
            return 0
        etype = fact.get("entity_type") or "unknown"
        canonical = canonicalize(resolver, name, fact.get("identifiers") or [], etype)
        kg.add_entity(canonical, entity_type=etype, properties=fact.get("properties") or {})
        return 1

    if kind == "relationship":
        s = fact.get("subject")
        p = fact.get("predicate")
        o = fact.get("object")
        if not (s and p and o):
            return 0
        result = upsert_triple(kg, s, p, o, confidence=confidence,
                               source_closet=source_closet, source_file=source_file)
        counters[result] = counters.get(result, 0) + 1
        return 1

    if kind == "preference":
        s = fact.get("subject")
        pred = fact.get("predicate") or fact.get("preference_predicate") or "prefers"
        if not pred.startswith("prefers"):
            pred = f"prefers_{pred}"
        value = fact.get("preference") or fact.get("value") or fact.get("object") or ""
        if not (s and value):
            return 0
        result = upsert_triple(kg, s, pred, str(value), confidence=confidence,
                               source_closet=source_closet, source_file=source_file)
        counters[result] = counters.get(result, 0) + 1
        return 1

    if kind == "event":
        e = fact.get("entity_name") or fact.get("subject")
        if not e:
            return 0
        evt_date = parse_date(fact.get("event_date"))
        evt_type = fact.get("event_type") or "event"
        # Don't double-prefix when the model emitted "event" as the type
        predicate = "event" if evt_type == "event" else f"event_{evt_type}"
        desc = fact.get("description") or fact.get("event_description") or ""
        obj = (desc[:500] if desc else evt_type)
        result = upsert_triple(kg, e, predicate, obj, valid_from=evt_date,
                               confidence=confidence,
                               source_closet=source_closet, source_file=source_file)
        counters[result] = counters.get(result, 0) + 1
        return 1

    if kind == "update":
        s = fact.get("subject")
        p = fact.get("predicate")
        new_o = fact.get("new_object")
        old_o = fact.get("supersedes_object")
        if not (s and p and new_o):
            return 0
        ops = 0
        if old_o:
            kg.invalidate(s, p, old_o, ended=parse_date(fact.get("event_date")))
            ops += 1
        result = upsert_triple(kg, s, p, new_o, confidence=confidence,
                               source_closet=source_closet, source_file=source_file)
        counters[result] = counters.get(result, 0) + 1
        ops += 1
        return ops

    if kind == "open_question":
        s = fact.get("subject") or "open_questions"
        q = fact.get("question") or ""
        if not q:
            return 0
        result = upsert_triple(kg, s, "open_question", q[:500],
                               confidence=min(confidence, 0.5),
                               source_closet=source_closet, source_file=source_file)
        counters[result] = counters.get(result, 0) + 1
        return 1

    return 0


def main() -> None:
    kg = KnowledgeGraph(KG_PATH)
    resolver = LocalIdentityResolver(IDENTITY_PATH)

    n_reports = n_ops = n_facts = 0
    skipped_empty = 0
    counters: dict = {}

    with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
        for source_dir in sorted(FACT_STAGING.iterdir()):
            if not source_dir.is_dir():
                continue
            source = source_dir.name
            source_closet = source  # 'gmail' or 'gcal'
            for jf in sorted(source_dir.glob("*.json")):
                try:
                    report = ExtractionReport.model_validate_json(jf.read_text())
                except Exception as e:
                    print(f"  bad report {jf.name}: {e}", flush=True)
                    continue
                n_reports += 1
                source_file = report.source_id
                raw = json.loads(jf.read_text())
                for fact_dict in raw.get("facts", []):
                    n_facts += 1
                    try:
                        ops = write_fact(
                            kg, resolver, fact_dict,
                            source_closet=source_closet, source_file=source_file,
                            counters=counters,
                        )
                        n_ops += ops
                        if ops == 0:
                            skipped_empty += 1
                    except Exception as e:
                        print(f"  write fail {source}/{source_file} {fact_dict.get('kind')}: {type(e).__name__}: {str(e)[:120]}", flush=True)
                if n_reports % 50 == 0:
                    print(f"  reports={n_reports} facts={n_facts} kg_ops={n_ops} {counters}", flush=True)

    print()
    print(f"=== DONE  reports={n_reports} facts={n_facts} kg_ops={n_ops} skipped_empty={skipped_empty} ===")
    print(f"counters: {counters}")
    # Quick summary of KG state
    import sqlite3
    con = sqlite3.connect(KG_PATH)
    cur = con.cursor()
    print(f"KG entities: {cur.execute('SELECT COUNT(*) FROM entities').fetchone()[0]}")
    print(f"KG triples:  {cur.execute('SELECT COUNT(*) FROM triples').fetchone()[0]}")


if __name__ == "__main__":
    main()
