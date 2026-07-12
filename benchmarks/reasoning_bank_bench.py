"""v2.12 — ReasoningBank scale benchmark.

Measures trajectory completion, reasoning_recall, and reasoning_stats latency as
ReasoningBank grows. Outputs JSON to stdout and /tmp/v2.12-reasoning-bank-bench.json.

Run:
    PYTHONPATH=src python3 benchmarks/reasoning_bank_bench.py
"""
from __future__ import annotations

import typing
import argparse
import contextlib
import io
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Iterable, List

from memkraft import MemKraft


DEFAULT_SIZES = [100, 1000, 3000, 8000]


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def summarise_samples(workload: str, samples: List[float]) -> dict:
    if not samples:
        return {
            "workload": workload,
            "n": 0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "workload": workload,
        "n": len(samples),
        "mean_ms": round(statistics.mean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p50_ms": round(percentile(samples, 0.50), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def _seed_reasoning_bank(mk: MemKraft, n: int) -> List[float]:
    complete_times: List[float] = []
    trajectory_start = getattr(mk, "trajectory_start")
    trajectory_log = getattr(mk, "trajectory_log")
    trajectory_complete = getattr(mk, "trajectory_complete")
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(n):
            task_id = f"task-{i:05d}"
            trajectory_start(
                task_id,
                title=f"Debug search cache issue {i}",
                tags=["perf", "search", "cache"] if i % 2 == 0 else ["ops", "reasoning"],
            )
            trajectory_log(task_id, 1, action="inspect code")
            trajectory_log(task_id, 2, action="run benchmark")
            t0 = time.perf_counter()
            trajectory_complete(
                task_id,
                status="success" if i % 3 else "failure",
                lesson="cache invalidation and search latency root cause",
                tags=["perf", "cache"],
            )
            complete_times.append((time.perf_counter() - t0) * 1000.0)
    return complete_times


def run_one(size: int, iterations: int) -> dict:
    root = Path(tempfile.mkdtemp(prefix="memkraft_reasoning_bench_")) / "memory"
    try:
        mk = MemKraft(base_dir=str(root))
        reasoning_recall = getattr(mk, "reasoning_recall")
        reasoning_stats = getattr(mk, "reasoning_stats")
        complete_times = _seed_reasoning_bank(mk, size)
        recall_times: List[float] = []
        stats_times: List[float] = []
        hit_count = 0
        total = 0
        for _ in range(iterations):
            t0 = time.perf_counter()
            hits = reasoning_recall("search cache latency root cause", top_k=5)
            recall_times.append((time.perf_counter() - t0) * 1000.0)
            hit_count = len(hits)

            t1 = time.perf_counter()
            stats = reasoning_stats()
            stats_times.append((time.perf_counter() - t1) * 1000.0)
            total = int(stats.get("total", 0))

        return {
            "trajectories": size,
            "complete_last_ms": round(complete_times[-1], 3) if complete_times else 0.0,
            "complete": summarise_samples("trajectory_complete", complete_times),
            "recall": summarise_samples("reasoning_recall", recall_times),
            "stats": summarise_samples("reasoning_stats", stats_times),
            "hits": hit_count,
            "total": total,
        }
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def parse_sizes(raw: str) -> List[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def main(argv: typing.Optional[Iterable[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default=",".join(str(x) for x in DEFAULT_SIZES))
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--out", default="/tmp/v2.12-reasoning-bank-bench.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results = {
        "benchmark": "reasoning_bank",
        "sizes": [run_one(size, args.iterations) for size in parse_sizes(args.sizes)],
    }
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


if __name__ == "__main__":
    main()
