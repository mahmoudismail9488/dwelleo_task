"""Measure LLM latency and token usage per message, for the README's cost section.

Usage (needs a real key in .env or the shell; NOT part of the test suite):
    uv run python scripts/measure_llm.py tests/fixtures/messages.jsonl \
        --input-price <USD per 1M input tokens> --output-price <USD per 1M output tokens>

Prices are USD per 1M tokens for the configured model; look them up on the provider's
pricing page. Without them, only tokens and latency are reported.
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_env  # noqa: E402
from app.extractors.llm import LlmExtractor  # noqa: E402
from app.models import RawMessage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--input-price", type=float, help="USD per 1M input tokens")
    ap.add_argument("--output-price", type=float, help="USD per 1M output tokens")
    args = ap.parse_args()

    load_env()
    if not os.environ.get("LLM_API_KEY"):
        print("error: LLM_API_KEY is not set. This script calls the real LLM.\n"
              "  cp .env.example .env and fill in LLM_API_KEY (or export it in the shell)",
              file=sys.stderr)
        return 2
    ex = LlmExtractor()
    rows = []
    for line in Path(args.input).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        msg = RawMessage.model_validate(json.loads(line))
        try:
            cand = ex.extract(msg)
            status = cand.classification
        except Exception as exc:  # measure failures too
            status = f"ERROR {type(exc).__name__}"
        u = ex.last_usage
        rows.append({
            "id": msg.id, "status": status, "latency_s": ex.last_latency_s or 0.0,
            "in": getattr(u, "prompt_tokens", 0) or 0, "out": getattr(u, "completion_tokens", 0) or 0,
        })

    print(f"model: {ex.model}")
    print(f"{'id':8} {'result':16} {'latency':>8} {'in':>6} {'out':>6} {'cost $':>10}")
    for r in rows:
        cost = ""
        if args.input_price is not None and args.output_price is not None:
            r["cost"] = (r["in"] * args.input_price + r["out"] * args.output_price) / 1_000_000
            cost = f"{r['cost']:.6f}"
        print(f"{r['id']:8} {r['status']:16} {r['latency_s']:7.2f}s {r['in']:6} {r['out']:6} {cost:>10}")

    lat = [r["latency_s"] for r in rows]
    print(f"\nmessages: {len(rows)}  latency mean {statistics.mean(lat):.2f}s  "
          f"median {statistics.median(lat):.2f}s  max {max(lat):.2f}s")
    print(f"tokens/message: in {statistics.mean(r['in'] for r in rows):.0f}  "
          f"out {statistics.mean(r['out'] for r in rows):.0f}")
    if "cost" in rows[0]:
        print(f"cost/message: mean ${statistics.mean(r['cost'] for r in rows):.6f}  "
              f"(per 1,000 messages ≈ ${1000 * statistics.mean(r['cost'] for r in rows):.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
