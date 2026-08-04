#!/usr/bin/env python3
"""Measure MKEP/0 release gates G11 and G12 on the standard corpus.

The protocol is locked by the 3.3.0 plan: 40 goals; 1,000 versus 10,000
records per goal for G11; 20 warmups followed by 200 measurements; and a
same-run ``memkraft --version`` cold-start baseline for G12.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List

NOW = "2026-08-04T11:22:33Z"
TARGET_GOAL = "bench/goal-00"
GOALS = 40


def _record(seq: int, goal_id: str, first: bool) -> Dict[str, object]:
    common: Dict[str, object] = {
        "schema_version": 1,
        "execution_schema": 1,
        "record_type": "goal_declared" if first else "assessment_recorded",
        "goal_id": goal_id,
        "event_seq": seq,
        "id": "%032x" % seq,
        "emitted_at": NOW,
        "operation_id": "%064x" % seq,
        "privacy": "local_private",
        "authority_claim": "agent",
        "authority_verified": False,
    }
    if first:
        common.update({
            "title": "benchmark goal",
            "intent": "measure projection performance",
            "constraints": [],
            "success_criteria": [],
        })
    else:
        common["assessment"] = {"advisory": True}
    return common


def generate_corpus(base_dir: Path, records_per_goal: int) -> Path:
    path = base_dir / ".memkraft" / "execution" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = 0
    with path.open("w", encoding="utf-8") as handle:
        for goal_index in range(GOALS):
            goal_id = "bench/goal-%02d" % goal_index
            for local_index in range(records_per_goal):
                seq += 1
                handle.write(json.dumps(
                    _record(seq, goal_id, local_index == 0),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n")
    return path


def percentile(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[rank]


def summarize(values: List[float]) -> Dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values) * 1000, 3),
        "p95_ms": round(percentile(values, 0.95) * 1000, 3),
        "max_ms": round(max(values) * 1000, 3),
    }


def measure(call: Callable[[], None], warmups: int, repetitions: int) -> List[float]:
    for _ in range(warmups):
        call()
    values = []
    for _ in range(repetitions):
        started = time.perf_counter()
        call()
        values.append(time.perf_counter() - started)
    return values


def projection_call(path: Path) -> Callable[[], None]:
    from memkraft.execution_projection_cache import read_projection

    def call() -> None:
        result = read_projection(path, NOW, TARGET_GOAL).state
        if result["goal_status"] != "open" or not result["consistent"]:
            raise RuntimeError("benchmark projection is invalid")

    return call


def subprocess_call(args: List[str], *, stdin: str = "") -> Callable[[], None]:
    env = dict(os.environ)
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")

    def call() -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "memkraft.cli"] + args,
            input=stdin,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("subprocess failed: %s" % completed.stderr.strip())

    return call


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.warmups < 0 or args.repetitions < 1:
        parser.error("warmups must be non-negative and repetitions must be positive")

    with tempfile.TemporaryDirectory(prefix="memkraft-gates-") as temporary:
        root = Path(temporary)
        small_dir = root / "small"
        standard_dir = root / "standard"
        small_path = generate_corpus(small_dir, 1_000)
        standard_path = generate_corpus(standard_dir, 10_000)

        small_values = measure(projection_call(small_path), args.warmups, args.repetitions)
        standard_values = measure(projection_call(standard_path), args.warmups, args.repetitions)
        small_summary = summarize(small_values)
        standard_summary = summarize(standard_values)
        ratio = standard_summary["p95_ms"] / small_summary["p95_ms"]

        request = json.dumps({
            "mkep": "0",
            "kind": "query",
            "request_id": "0123456789abcdef0123456789abcdef",
            "op": "state.read",
            "now": NOW,
            "target": {"goal_id": TARGET_GOAL},
            "args": {"include": []},
        }, separators=(",", ":"))
        version_values = measure(
            subprocess_call(["--version"]), args.warmups, args.repetitions)
        cli_values = measure(
            subprocess_call(["exec", "call", "--base-dir", str(standard_dir)], stdin=request),
            args.warmups,
            args.repetitions,
        )
        version_summary = summarize(version_values)
        cli_summary = summarize(cli_values)
        cli_margin = cli_summary["p95_ms"] - version_summary["p95_ms"]

        result = {
            "protocol": {
                "goals": GOALS,
                "small_records_per_goal": 1_000,
                "standard_records_per_goal": 10_000,
                "small_total_records": GOALS * 1_000,
                "standard_total_records": GOALS * 10_000,
                "warmups": args.warmups,
                "repetitions": args.repetitions,
                "single_process_projection": True,
                "subprocess_cold_start": True,
            },
            "machine": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "g11": {
                "baseline": small_summary,
                "standard": standard_summary,
                "p95_ratio": round(ratio, 4),
                "threshold_ratio": 12.0,
                "soft_ceiling_ms": 150.0,
                "soft_ceiling_warning": standard_summary["p95_ms"] > 150.0,
                "passed": ratio <= 12.0,
            },
            "g12": {
                "version_baseline": version_summary,
                "state_read": cli_summary,
                "p95_margin_ms": round(cli_margin, 3),
                "threshold_margin_ms": 400.0,
                "passed": cli_margin <= 400.0,
            },
        }
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0 if result["g11"]["passed"] and result["g12"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
