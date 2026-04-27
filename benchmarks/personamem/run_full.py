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
    python3 run_full.py --majority-vote 3          # 3-run majority vote (v3, 2026-04-27)
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

from harness_v3 import run_benchmark, print_report, _empty_results, _accumulate, _finalize, QTYPE_MAP, load_persona_mem  # noqa: E402


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
    if run.get("majority_vote_runs"):
        lines.append(f"- **Majority-vote runs**: {run['majority_vote_runs']}")
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

    # Per-run accuracy when majority vote is enabled
    runs_meta = run.get("majority_vote_per_run") or []
    if runs_meta:
        lines.append("## Per-Run Accuracy (Majority Vote)")
        lines.append("")
        lines.append("| Run | " + " | ".join(variants) + " |")
        lines.append("|---|" + "|".join(":---:" for _ in variants) + "|")
        for i, r in enumerate(runs_meta):
            row = f"| {i+1} |"
            for v in variants:
                acc = r.get(v, {}).get("accuracy")
                row += f" {acc:.2f}% |" if acc is not None else " — |"
            lines.append(row)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _aggregate_majority_vote(
    runs: List[Dict[str, Any]],
    variants: List[str],
    questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fuse N benchmark runs via per-question majority vote.

    For every question_id, pick the outcome that won the most votes
    across runs. On a tie (e.g. 2 vs 2 with N=4), fall back to the
    first run's answer for that question — matches the task spec.

    Returns an aggregated `run` dict with the same shape as a single
    run of `harness_v3.run_benchmark`, plus:
      - majority_vote_runs: int (N)
      - majority_vote_per_run: list of {variant: {accuracy, correct, total}}
    """
    n = len(runs)
    if n == 0:
        raise ValueError("aggregate_majority_vote called with zero runs")

    # Build aggregated results from scratch using _empty_results / _accumulate
    agg_results: Dict[str, Dict[str, Any]] = {v: _empty_results(v) for v in variants}

    # Question metadata for per-type accumulation
    qmeta = {q.get("question_id", str(i)): q for i, q in enumerate(questions)}

    # Per-run summary (accuracy etc.) for the report
    per_run_summary: List[Dict[str, Any]] = []
    for r in runs:
        per_run_summary.append({
            v: {
                "accuracy": r["results"][v].get("accuracy"),
                "correct": r["results"][v].get("correct"),
                "total": r["results"][v].get("total"),
            }
            for v in variants if v in r["results"]
        })

    # Majority-vote loop
    all_qids: set = set()
    for r in runs:
        for v in variants:
            all_qids.update((r.get("per_question") or {}).get(v, {}).keys())

    for qid in all_qids:
        q = qmeta.get(qid)
        if q is None:
            continue
        readable = QTYPE_MAP.get(q["question_type"], q["question_type"])
        for v in variants:
            votes = []
            for r in runs:
                pq = (r.get("per_question") or {}).get(v, {})
                if qid in pq:
                    votes.append(bool(pq[qid]))
            if not votes:
                continue
            true_votes = sum(1 for x in votes if x)
            false_votes = len(votes) - true_votes
            if true_votes > false_votes:
                final = True
            elif false_votes > true_votes:
                final = False
            else:
                # Tie → first run wins (task spec)
                final = votes[0]
            _accumulate(agg_results[v], readable, final)

    for v in variants:
        _finalize(agg_results[v])

    # Aggregate run_stats / errors from first run (representative)
    base = runs[0]
    fused = {
        "split": base["split"],
        "model": base["model"],
        "variants": variants,
        "n_questions": base["n_questions"],
        "n_completed": base["n_completed"],
        "run_stats": base.get("run_stats", {}),
        "results": agg_results,
        "elapsed_seconds": sum(r.get("elapsed_seconds", 0.0) for r in runs),
        "memkraft_dir": base.get("memkraft_dir"),
        "majority_vote_runs": n,
        "majority_vote_per_run": per_run_summary,
    }
    return fused


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
    parser.add_argument(
        "--majority-vote", type=int, default=1,
        help=(
            "Run the benchmark N times and take the per-question majority "
            "vote (default 1 = no voting). Tie-breaking: first run wins."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    n_runs = max(1, int(args.majority_vote))

    if n_runs == 1:
        # Single-run path (legacy behaviour preserved)
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
        return

    # ── Majority-vote path (N >= 2) ───────────────────────────────────
    print(f"▶ Starting PersonaMem v3 run (majority-vote N={n_runs})")
    print(f"   split={args.split} | model={args.model} | variants={variants}")
    if args.resume or args.checkpoint:
        print(
            "   ⚠️  --resume/--checkpoint ignored under --majority-vote "
            "(each sub-run uses its own fresh checkpoint)"
        )

    runs: List[Dict[str, Any]] = []
    for i in range(n_runs):
        print(f"\n── Sub-run {i+1}/{n_runs} ──────────────────────────────")
        sub_ckpt = HERE / "checkpoints" / f"{args.split}_mv{i+1}_{int(time.time())}.json"
        sub_ckpt.parent.mkdir(parents=True, exist_ok=True)
        run_i = run_benchmark(
            split=args.split,
            max_questions=args.max_questions,
            variants=variants,
            model=args.model,
            checkpoint_path=sub_ckpt,
            checkpoint_every=args.checkpoint_every,
            verbose=not args.quiet,
        )
        per_v = " | ".join(
            f"{v}: {run_i['results'][v]['accuracy']:.2f}%"
            for v in variants if v in run_i["results"]
        )
        print(f"  sub-run {i+1} complete — {per_v}")
        runs.append(run_i)

    # Re-load questions to map question_id → question_type for aggregation
    questions, _ = load_persona_mem(args.split)
    if args.max_questions and args.max_questions > 0:
        questions = questions[: args.max_questions]

    fused = _aggregate_majority_vote(runs, variants, questions)
    print("\n══ Majority-Vote Aggregate ══════════════════════════════")
    print_report(fused)

    # Print per-run line for debugging
    print("\nPer-run accuracy:")
    for i, r in enumerate(runs):
        line = " | ".join(
            f"{v}: {r['results'][v]['accuracy']:.2f}%" for v in variants
        )
        print(f"  run {i+1}: {line}")

    ts = int(time.time())
    json_path = Path(args.out or HERE / f"results_full_{args.split}_mv{n_runs}_{ts}.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fused": fused,
        "runs": runs,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON saved: {json_path}")

    md_path = Path(args.md or HERE / f"results_full_{args.split}_mv{n_runs}_{ts}.md")
    write_markdown_report(fused, md_path)
    print(f"Markdown saved: {md_path}")


if __name__ == "__main__":
    main()
