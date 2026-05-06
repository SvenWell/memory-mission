"""Run extraction on a sample of staged items, measure real token costs."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from anthropic import Anthropic

sys.path.insert(0, "/root/memory-mission")
from memory_mission.extraction import EXTRACTION_PROMPT

FIRM = Path("/root/memory-mission-data")
STAGING = FIRM / "wiki" / "staging" / "personal" / "keagan"

# Model pricing per 1M tokens (input/output) — current Anthropic published rates
PRICES = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5":  (1.00, 5.00),
}

MODEL = "claude-sonnet-4-5"


def load_body(md_path: Path) -> tuple[str, str]:
    text = md_path.read_text(encoding="utf-8")
    # frontmatter is between --- ... ---; body is everything after
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        body = text[end + 5 :] if end != -1 else text
        fm = text[4:end] if end != -1 else ""
    else:
        body, fm = text, ""
    return fm, body


def sample_files(source: str, n: int) -> list[Path]:
    d = STAGING / source
    return sorted(d.glob("*.md"))[:n]


def extract_one(client: Anthropic, body: str) -> dict:
    prompt = EXTRACTION_PROMPT + "\n\n# Source item\n\n" + body
    t0 = time.time()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.time() - t0
    return {
        "elapsed_s": elapsed,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "text": "".join(b.text for b in resp.content if hasattr(b, "text")),
    }


def main() -> None:
    sample_n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    sources = sys.argv[2:] if len(sys.argv) > 2 else ["gmail", "gcal"]

    client = Anthropic()
    results: list[dict] = []
    for source in sources:
        files = sample_files(source, sample_n)
        print(f"=== {source}: {len(files)} sample files ===")
        for f in files:
            _fm, body = load_body(f)
            r = extract_one(client, body)
            r["source"] = source
            r["body_chars"] = len(body)
            r["file"] = f.name
            results.append(r)
            print(f"  {source}/{f.stem[:32]:32s}  body={len(body):5d}ch  in={r['input_tokens']:5d}  out={r['output_tokens']:4d}  {r['elapsed_s']:.1f}s")

    # Aggregate
    avg_in = sum(r["input_tokens"] for r in results) / len(results)
    avg_out = sum(r["output_tokens"] for r in results) / len(results)
    avg_body = sum(r["body_chars"] for r in results) / len(results)
    avg_t = sum(r["elapsed_s"] for r in results) / len(results)

    print()
    print("=== AVERAGES (per item) ===")
    print(f"  body chars:     {avg_body:.0f}")
    print(f"  input tokens:   {avg_in:.0f}")
    print(f"  output tokens:  {avg_out:.0f}")
    print(f"  latency:        {avg_t:.1f}s")

    # Cost projections
    print()
    print("=== PROJECTIONS at current Anthropic rates ===")
    for model, (in_rate, out_rate) in PRICES.items():
        per_item = (avg_in * in_rate + avg_out * out_rate) / 1_000_000
        print(f"  {model:25s} per-item: ${per_item:.4f}")
        for n_label, n in [("596 cal", 596), ("2947 gmail", 2947), ("3543 total", 3543)]:
            print(f"    {n_label:15s} → ${per_item * n:.2f}  ({per_item*n*avg_t/60:.0f} min wall (sequential))")
        print()


if __name__ == "__main__":
    main()
