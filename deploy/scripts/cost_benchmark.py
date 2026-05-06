"""Token-counting benchmark for extraction cost projection.

No LLM calls — uses tiktoken (GPT family) and Anthropic's tokenizer-equivalent
(via chars/3.5 heuristic when SDK tokenizer unavailable) to count real tokens
on actually-staged content. Projects cost across major model rates so a customer
quote can be assembled without burning subscription quota.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import mean, median, quantiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import FIRM_ROOT as FIRM, STAGING

from memory_mission.extraction import EXTRACTION_PROMPT

# Token rates per 1M tokens (input, output), current public list prices.
# Adjust if a model is added or rates change.
RATES = {
    "claude-haiku-4.5":   (1.00,  5.00),
    "claude-sonnet-4.6":  (3.00, 15.00),
    "gpt-5.5 (chatgpt)":  (1.25, 10.00),   # GPT-5 family list price; Plus/Pro absorbs
    "gpt-5.5-mini":       (0.25,  2.00),
}


def load_body(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        return text[end + 5 :] if end != -1 else text
    return text


def count_tokens_tiktoken(s: str) -> int:
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")  # GPT-4o / 5 family
    return len(enc.encode(s))


def count_tokens_anthropic_approx(s: str) -> int:
    """Anthropic doesn't ship a public Python tokenizer; cl100k is close enough.
    Empirically Claude tokens ≈ 0.95 × cl100k tokens for English."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    return int(len(enc.encode(s)) * 0.97)


def collect_bodies(source: str, limit: int | None = None) -> list[str]:
    d = STAGING / source
    files = sorted(d.glob("*.md"))
    if limit:
        files = files[:limit]
    return [load_body(f) for f in files]


def main() -> None:
    samples = {
        "gmail":  collect_bodies("gmail"),
        "gcal":   collect_bodies("gcal"),
    }

    prompt_tk_gpt = count_tokens_tiktoken(EXTRACTION_PROMPT)
    prompt_tk_claude = count_tokens_anthropic_approx(EXTRACTION_PROMPT)
    print(f"EXTRACTION_PROMPT: {len(EXTRACTION_PROMPT):,} chars  ≈  {prompt_tk_gpt:,} GPT tok / {prompt_tk_claude:,} Claude tok\n")

    # Per-source tokenizations
    rows = {}
    for src, bodies in samples.items():
        if not bodies:
            continue
        # Token counts for each item (body + prompt)
        gpt_in = [count_tokens_tiktoken(b) + prompt_tk_gpt for b in bodies]
        claude_in = [count_tokens_anthropic_approx(b) + prompt_tk_claude for b in bodies]
        rows[src] = {
            "n": len(bodies),
            "body_chars_avg": mean(len(b) for b in bodies),
            "body_chars_med": median(len(b) for b in bodies),
            "body_chars_p95": quantiles((len(b) for b in bodies), n=20)[18] if len(bodies) >= 20 else max(len(b) for b in bodies),
            "gpt_in_total":    sum(gpt_in),
            "gpt_in_avg":      mean(gpt_in),
            "claude_in_total": sum(claude_in),
            "claude_in_avg":   mean(claude_in),
        }

    print("=== INPUT TOKENS PER ITEM (body + extraction prompt) ===")
    print(f"{'source':10s}  {'n':>5s}  {'avg body ch':>12s}  {'med body':>10s}  {'p95':>6s}  {'avg GPT in':>11s}  {'avg Cl in':>11s}")
    for src, r in rows.items():
        print(f"{src:10s}  {r['n']:>5d}  {r['body_chars_avg']:>12,.0f}  {r['body_chars_med']:>10,.0f}  {r['body_chars_p95']:>6,.0f}  {r['gpt_in_avg']:>11,.0f}  {r['claude_in_avg']:>11,.0f}")
    print()

    # Output token estimate. ExtractionReport is a small JSON of facts.
    # Empirically: ~150-450 output tokens depending on item richness.
    OUTPUT_EST = 350
    print(f"Output tokens estimated at {OUTPUT_EST}/item (typical ExtractionReport JSON).\n")

    print("=== TOTAL COST PROJECTIONS (full corpus) ===")
    # Sum across sources
    n_total = sum(r["n"] for r in rows.values())
    gpt_in_total = sum(r["gpt_in_total"] for r in rows.values())
    claude_in_total = sum(r["claude_in_total"] for r in rows.values())
    out_total = n_total * OUTPUT_EST

    print(f"{'model':25s}  {'in M tok':>10s}  {'out M tok':>10s}  {'cost USD':>10s}  {'$/item':>9s}")
    for model, (in_rate, out_rate) in RATES.items():
        is_claude = "claude" in model
        in_tk = claude_in_total if is_claude else gpt_in_total
        cost = (in_tk * in_rate + out_total * out_rate) / 1_000_000
        per_item = cost / n_total
        print(f"{model:25s}  {in_tk/1e6:>10.2f}  {out_total/1e6:>10.2f}  {cost:>10,.2f}  {per_item:>9.4f}")
    print()

    # Slice projections
    print("=== SLICE PROJECTIONS (sonnet 4.6 / gpt-5.5) ===")
    in_rate_s, out_rate_s = RATES["claude-sonnet-4.6"]
    in_rate_g, out_rate_g = RATES["gpt-5.5 (chatgpt)"]

    # Sample sizes
    slices = [
        ("Calendar 90d (≈300 items)", 300, "gcal"),
        ("Gmail 30d (≈500 items)", 500, "gmail"),
        ("Gmail 90d (≈1500 items)", 1500, "gmail"),
        ("Full corpus (3543 items)", n_total, "gmail"),  # mostly gmail-shaped
    ]
    for label, n, src in slices:
        avg_in_g = rows[src]["gpt_in_avg"]
        avg_in_c = rows[src]["claude_in_avg"]
        cost_son = (n * avg_in_c * in_rate_s + n * OUTPUT_EST * out_rate_s) / 1_000_000
        cost_gpt = (n * avg_in_g * in_rate_g + n * OUTPUT_EST * out_rate_g) / 1_000_000
        # If user has Claude Pro / Codex Plus subscription, real $ is $0; this is the
        # paid-API equivalent — what a customer would pay if they ship at API rates.
        print(f"  {label:35s}  Sonnet 4.6: ${cost_son:6.2f}    GPT-5.5: ${cost_gpt:6.2f}")
    print()
    print("NOTE: numbers assume API rates. Your subscription absorbs these for personal use.")
    print("They are the right number to quote a customer who'll be billed per-token.")


if __name__ == "__main__":
    main()
