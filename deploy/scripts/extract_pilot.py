"""Pilot extraction: drives Codex CLI per staged item, validates → ingests.

Pilot scope: Calendar 90d + Gmail 30d.

For each staged item:
  1. Build full prompt = EXTRACTION_PROMPT + body + JSON-only instruction
  2. Pipe to `codex exec` (subscription, no API spend)
  3. Parse JSON → validate ExtractionReport
  4. Call ingest_facts() to land into proposal store
  5. Track success / fail counts and timing

Parallelism: 4 workers via concurrent.futures.
Idempotency: skip items that already have an extraction report on disk.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/root/memory-mission")
from memory_mission.extraction import EXTRACTION_PROMPT, ExtractionReport, ingest_facts
from memory_mission.identity.local import LocalIdentityResolver
from memory_mission.observability import observability_scope

FIRM_ROOT = Path("/root/memory-mission-data")
WIKI_ROOT = FIRM_ROOT / "wiki"
STAGING = WIKI_ROOT / "staging" / "personal" / "keagan"
OBS_ROOT = FIRM_ROOT / ".observability"
EMPLOYEE = "keagan"
FIRM_ID = "keagan"

WORKERS = 4
BODY_TRUNC_CHARS = 8000  # cap input cost on long emails
JSON_INSTRUCTION = (
    "\n\nReturn ONLY a single JSON object matching the ExtractionReport schema. "
    "No prose, no markdown fences, no commentary. Start with { and end with }. "
    f"Set source_id to the value provided. employee_id must be '{EMPLOYEE}'."
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def select_pilot_items() -> list[Path]:
    """Pilot slice: gcal events whose START is within 90 days, gmail within 30.

    Reads the .raw/<id>.json sidecar (has the original payload with proper dates).
    """
    cal_cutoff = now_utc() - timedelta(days=90)
    mail_cutoff = now_utc() - timedelta(days=30)
    items: list[Path] = []

    # Calendar: filter on event start date from raw sidecar
    for md in sorted((STAGING / "gcal").glob("*.md")):
        raw = STAGING / "gcal" / ".raw" / f"{md.stem}.json"
        try:
            payload = json.loads(raw.read_text())
            start = payload.get("start") or {}
            s = start.get("dateTime") or start.get("date")
            if not s:
                continue
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cal_cutoff:
                items.append(md)
        except Exception:
            pass

    # Gmail: filter on internal_date from raw sidecar
    for md in sorted((STAGING / "gmail").glob("*.md")):
        raw = STAGING / "gmail" / ".raw" / f"{md.stem}.json"
        try:
            payload = json.loads(raw.read_text())
            s = payload.get("internal_date") or payload.get("messageTimestamp")
            if not s:
                continue
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= mail_cutoff:
                items.append(md)
        except Exception:
            pass
    return items


def fact_staging_path(source: str, source_id: str) -> Path:
    """Where ingest_facts writes the report."""
    return WIKI_ROOT / "staging" / "personal" / EMPLOYEE / f".facts/{source}/{source_id}.json"


def load_body(md_path: Path) -> tuple[str, str]:
    text = md_path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[4:end], text[end + 5 :]
    return "", text


def parse_frontmatter(fm: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in fm.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def detect_source(md_path: Path) -> str:
    return md_path.parent.name  # 'gmail' or 'gcal'


def extract_json(blob: str) -> dict:
    """Find the largest top-level JSON object in blob."""
    # Strip markdown fences if present
    blob = re.sub(r"^```(?:json)?\s*", "", blob.strip())
    blob = re.sub(r"\s*```$", "", blob.strip())
    # Quick pass: try whole thing first
    try:
        return json.loads(blob)
    except Exception:
        pass
    # Fallback: scan for { ... } balanced
    depth = 0
    start = -1
    for i, ch in enumerate(blob):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidate = blob[start : i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    continue
    raise ValueError("no parseable JSON object found")


def call_codex(prompt: str, timeout_s: int = 120) -> tuple[str, float]:
    """Pipe prompt to codex exec, return (stdout, elapsed_s).

    Uses --output-last-message so we get the final answer cleanly without
    parsing event noise.
    """
    out_file = f"/tmp/codex-out-{os.getpid()}-{time.time_ns()}.txt"
    try:
        t0 = time.time()
        cp = subprocess.run(
            [
                "codex", "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox", "read-only",
                "--output-last-message", out_file,
                "--color", "never",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        elapsed = time.time() - t0
        if cp.returncode != 0:
            raise RuntimeError(f"codex exit {cp.returncode}: {cp.stderr[:300]}")
        out = Path(out_file).read_text() if Path(out_file).exists() else cp.stdout
        return out, elapsed
    finally:
        try:
            os.unlink(out_file)
        except FileNotFoundError:
            pass


def process_item(md_path: Path, identity_resolver) -> dict:
    """Returns dict with status, source_id, source, elapsed, fact_count, error."""
    source = detect_source(md_path)
    fm_text, body = load_body(md_path)
    fm = parse_frontmatter(fm_text)
    source_id = fm.get("source_id") or fm.get("external_id") or md_path.stem

    # Idempotent skip
    if fact_staging_path(source, source_id).exists():
        return {"status": "skip", "source": source, "source_id": source_id}

    body_clipped = body[:BODY_TRUNC_CHARS]
    full_prompt = (
        EXTRACTION_PROMPT
        + JSON_INSTRUCTION
        + f"\n\nsource: {source}\nsource_id: {source_id}\n\n## Source body\n\n"
        + body_clipped
    )

    try:
        text, elapsed = call_codex(full_prompt, timeout_s=180)
        data = extract_json(text)
        # Force expected ids in case the model invents them
        data["source"] = source
        data["source_id"] = source_id
        data.setdefault("target_plane", "personal")
        data["employee_id"] = EMPLOYEE
        report = ExtractionReport.model_validate(data)
    except Exception as e:
        return {
            "status": "fail",
            "source": source,
            "source_id": source_id,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }

    # Ingest into fact staging + proposal store (creates proposals automatically)
    try:
        with observability_scope(observability_root=OBS_ROOT, firm_id=FIRM_ID, employee_id=EMPLOYEE):
            ingest_facts(
                report,
                wiki_root=WIKI_ROOT,
                identity_resolver=identity_resolver,
            )
    except Exception as e:
        return {
            "status": "fail_ingest",
            "source": source,
            "source_id": source_id,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "fact_count": len(report.facts),
        }

    return {
        "status": "ok",
        "source": source,
        "source_id": source_id,
        "elapsed": elapsed,
        "fact_count": len(report.facts),
    }


def main() -> None:
    identity_resolver = LocalIdentityResolver(FIRM_ROOT / "identity.sqlite3")

    items = select_pilot_items()
    print(f"=== pilot slice: {len(items)} items ===", flush=True)
    by_src: dict[str, int] = {}
    for f in items:
        s = detect_source(f)
        by_src[s] = by_src.get(s, 0) + 1
    print(f"  by source: {by_src}", flush=True)

    n = len(items)
    ok = fail = skip = 0
    fact_total = 0
    elapsed_total = 0.0
    started = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_item, it, identity_resolver): it for it in items}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            if r["status"] == "ok":
                ok += 1
                fact_total += r.get("fact_count", 0)
                elapsed_total += r.get("elapsed", 0.0)
            elif r["status"] == "skip":
                skip += 1
            else:
                fail += 1
                print(f"  FAIL {r['source']}/{r['source_id']}: {r.get('error', '?')}", flush=True)
            if i % 10 == 0 or i == n:
                wall = time.time() - started
                avg_per = (elapsed_total / max(ok, 1))
                eta = avg_per * (n - i) / WORKERS
                print(f"  [{i}/{n}] ok={ok} skip={skip} fail={fail} facts={fact_total} wall={wall:.0f}s avg_call={avg_per:.1f}s ETA~{eta:.0f}s", flush=True)
    print(f"=== DONE  ok={ok} skip={skip} fail={fail} facts={fact_total} wall={time.time()-started:.0f}s ===", flush=True)


if __name__ == "__main__":
    main()
