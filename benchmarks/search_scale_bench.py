"""v2.12 — search scale benchmark.

Measures MemKraft.search latency as markdown corpus size grows. Outputs JSON to
stdout and /tmp/v2.12-search-scale-bench.json.

Run:
    PYTHONPATH=src python3 benchmarks/search_scale_bench.py
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Iterable, List

from memkraft import MemKraft


DEFAULT_SIZES = [100, 1000, 3000, 8000]
VOCAB = [
    "memkraft", "search", "cache", "latency", "reasoning", "agent", "context",
    "retrieval", "index", "profile", "entity", "memory", "semantic", "episodic",
    "procedural", "python", "benchmark", "performance", "token", "query",
]


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


def _seed_corpus(base: Path, n: int, words_per_doc: int) -> None:
    random.seed(7)
    base.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        words = [random.choice(VOCAB) for _ in range(words_per_doc)]
        if i % 10 == 0:
            words.extend(["search", "cache", "latency", "root", "cause"])
        (base / f"entity-{i:05d}.md").write_text(
            f"# Entity {i}\n\n" + " ".join(words), encoding="utf-8"
        )


def _search(mk: MemKraft, query: str) -> list:
    fn = getattr(mk, "search")
    return fn(query)


def run_one(size: int, iterations: int, words_per_doc: int) -> dict:
    root = Path(tempfile.mkdtemp(prefix="memkraft_search_scale_")) / "memory"
    try:
        _seed_corpus(root, size, words_per_doc)
        mk = MemKraft(base_dir=str(root))
        query = "search cache latency root cause"
        with contextlib.redirect_stdout(io.StringIO()):
            t0 = time.perf_counter()
            cold_hits = _search(mk, query)
            cold_ms = (time.perf_counter() - t0) * 1000.0
        warm_times: List[float] = []
        hit_count = len(cold_hits)
        for _ in range(iterations):
            with contextlib.redirect_stdout(io.StringIO()):
                t1 = time.perf_counter()
                hits = _search(mk, query)
                warm_times.append((time.perf_counter() - t1) * 1000.0)
            hit_count = len(hits)
        return {
            "documents": size,
            "words_per_doc": words_per_doc,
            "cold_ms": round(cold_ms, 3),
            "warm": summarise_samples("search_warm", warm_times),
            "hits": hit_count,
        }
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def parse_sizes(raw: str) -> List[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def main(argv: Iterable[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default=",".join(str(x) for x in DEFAULT_SIZES))
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--words-per-doc", type=int, default=120)
    parser.add_argument("--out", default="/tmp/v2.12-search-scale-bench.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results = {
        "benchmark": "search_scale",
        "sizes": [run_one(size, args.iterations, args.words_per_doc) for size in parse_sizes(args.sizes)],
    }
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


if __name__ == "__main__":
    main()
