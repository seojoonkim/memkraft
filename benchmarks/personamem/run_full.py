#!/usr/bin/env python3
"""PersonaMem v3 — Full production runner.

Runs the full 32k split (589 questions) across all 3 variants
(baseline / hybrid / memkraft) with automatic checkpointing, retry,
and a final JSON report.

Usage:
    python3 run_full.py                            # full 589 × 3
    python3 run_full.py --split 128k               # 128k split
    python3 run_full.py --max-questions 50         # smoke test
    python3 run_full.py --resume /tmp/ckpt.json    # resume checkpoint
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from harness_v3 import run_benchmark, print_report  # noqa: E402


def _default_checkpoint(split: str) -> Path:
    ts = int(time.time())
    return HERE / "checkpoints" / f"{split}_{ts}.json"


def write_markdown_report(run: Dict[str, Any], path: Path) -> None:
    """Render a human-friendly Markdown report."""
    variants = run["variants"]
    results = run["results"]
    rs = run.get("run_stats", {})

    lines: List[str] = []
    lines.append(f"# PersonaMem v3 Results — split={run['split']}, model={run['model']}")
    lines.append("")
    lines.append(f"- Questions completed: **{run['n_completed']}/{run['n_questions']}**")
    lines.append(f"- Elapsed: **{run['elapsed_seconds']:.1f}s**")
    lines.append(f"- Ingestions: **{rs.get('ingestions', 0)}** "
                 f"(statements: {rs.get('total_statements', 0)}, "
                 f"preferences: {rs.get('total_preferences', 0)}, "
                 f"facts: {rs.get('total_facts', 0)})")
    lines.append("")
    lines.append("## Overall Accuracy")
    lines.append("")
    lines.append("| Variant | Accuracy | Correct | Total | Errors |")
    lines.append("|---------|---------:|--------:|------:|-------:|")
    for v in variants:
        r = results[v]
        lines.append(
            f"| {v} | {r['accuracy']:.2f}% | {r['correct']} | {r['total']} | {len(r['errors'])} |"
        )
    lines.append("")

    # Per-type
    all_types = set()
    for v in variants:
        all_types.update(results[v]["by_type"].keys())
    if all_types:
        lines.append("## Accuracy by Query Type")
        lines.append("")
        header = "| Query Type |" + "|".join(f" {v} |" for v in variants)
        lines.append(header)
        sep = "|---|" + "|".join(":---:|" for _ in variants)
        lines.append(sep)
        for qt in sorted(all_types):
            row = f"| {qt} |"
            for v in variants:
                bt = results[v]["by_type"].get(qt, {})
                acc = bt.get("accuracy")
                c = bt.get("correct", 0)
                t = bt.get("total", 0)
                if acc is None:
                    row += " — |"
                else:
                    row += f" {acc:.1f}% ({c}/{t}) |"
            lines.append(row)
        lines.append("")

    # Delta vs baseline
    if "baseline" in variants and len(variants) > 1:
        lines.append("## Delta vs Baseline")
        lines.append("")
        b_acc = results["baseline"]["accuracy"]
        for v in variants:
            if v == "baseline":
                continue
            d = results[v]["accuracy"] - b_acc
            sign = "+" if d >= 0 else ""
            lines.append(f"- **{v}**: {sign}{d:.2f}% vs baseline")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="PersonaMem v3 Full Benchmark Runner")
    parser.add_argument("--split", default="32k", choices=["32k", "128k", "1M"])
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--variants",
                        default="baseline,hybrid,memkraft",
                        help="Comma-separated list")
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--resume", default=None,
                        help="Path to existing checkpoint (alias for --checkpoint)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--md", default=None,
                        help="Path for the Markdown report")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    ckpt_arg = args.resume or args.checkpoint
    ckpt_path = Path(ckpt_arg) if ckpt_arg else _default_checkpoint(args.split)
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"▶ Starting PersonaMem v3 run")
    print(f"   split={args.split} | model={args.model} | variants={variants}")
    print(f"   checkpoint={ckpt_path}")

    run = run_benchmark(
        split=args.split,
        max_questions=args.max_questions,
        variants=variants,
        model=args.model,
        checkpoint_path=ckpt_path,
        checkpoint_every=args.checkpoint_every,
        verbose=not args.quiet,
    )

    print_report(run)

    ts = int(time.time())
    json_path = Path(args.out or HERE / f"results_full_{args.split}_{ts}.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON saved: {json_path}")

    md_path = Path(args.md or HERE / f"results_full_{args.split}_{ts}.md")
    write_markdown_report(run, md_path)
    print(f"Markdown saved: {md_path}")


if __name__ == "__main__":
    main()
