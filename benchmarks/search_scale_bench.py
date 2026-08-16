"""v2.12 — search scale benchmark.

Measures MemKraft.search latency as markdown corpus size grows. Outputs JSON to
stdout and /tmp/v2.12-search-scale-bench.json.

Run:
    PYTHONPATH=src python3 benchmarks/search_scale_bench.py
"""
from __future__ import annotations

import typing
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


def _search(mk: MemKraft, query: str, top_k: typing.Optional[int] = None) -> list:
    fn = getattr(mk, "search")
    if top_k is None:
        return fn(query, cache=False)
    return fn(query, top_k=top_k, cache=False)


def _time_searches(mk: MemKraft, query: str, iterations: int, top_k: typing.Optional[int] = None) -> tuple[List[float], int]:
    times: List[float] = []
    hit_count = 0
    for _ in range(iterations):
        with contextlib.redirect_stdout(io.StringIO()):
            t0 = time.perf_counter()
            hits = _search(mk, query, top_k=top_k)
            times.append((time.perf_counter() - t0) * 1000.0)
        hit_count = len(hits)
    return times, hit_count


def run_one(size: int, iterations: int, words_per_doc: int, top_k: int = 20) -> dict:
    root = Path(tempfile.mkdtemp(prefix="memkraft_search_scale_")) / "memory"
    try:
        _seed_corpus(root, size, words_per_doc)
        mk = MemKraft(base_dir=str(root))
        query = "search cache latency root cause"
        cold_index_build, _ = _time_searches(mk, query, 1, top_k=None)
        unlimited_warm, unlimited_hits = _time_searches(mk, query, iterations, top_k=None)
        limited_warm, limited_hits = _time_searches(mk, query, iterations, top_k=top_k)
        return {
            "documents": size,
            "words_per_doc": words_per_doc,
            "cold_index_build": summarise_samples(
                "search_cold_index_build", cold_index_build
            ),
            "unlimited": {
                "warm": summarise_samples("search_unlimited_warm", unlimited_warm),
                "hits": unlimited_hits,
            },
            "limited": {
                "top_k": top_k,
                "warm": summarise_samples("search_limited_warm", limited_warm),
                "hits": limited_hits,
            },
        }
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def parse_sizes(raw: str) -> List[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def main(argv: typing.Optional[Iterable[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default=",".join(str(x) for x in DEFAULT_SIZES))
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--words-per-doc", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--out", default="/tmp/v2.12-search-scale-bench.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results = {
        "benchmark": "search_scale",
        "sizes": [
            run_one(size, args.iterations, args.words_per_doc, top_k=args.top_k)
            for size in parse_sizes(args.sizes)
        ],
    }
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


if __name__ == "__main__":
    main()
