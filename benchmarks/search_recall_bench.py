#!/usr/bin/env python3
"""Search recall benchmark for MemKraft retrieval experiments.

This benchmark creates a deterministic synthetic corpus and compares a
candidate search path against the baseline full ``MemKraft.search`` result
ordering.  It is intentionally small and dependency-free so future pruning or
scorer experiments can plug in a candidate path and quantify top-k recall before
shipping.
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
from typing import Any, Callable, Iterable

from memkraft import MemKraft


SearchFn = Callable[[MemKraft, str, int], list[dict[str, Any]]]


def _file_ids(results: Iterable[dict[str, Any]], limit: typing.Optional[int] = None) -> list[str]:
    files = [str(r.get("file", "")) for r in results if r.get("file")]
    return files if limit is None else files[:limit]


def recall_at_k(reference: list[str], candidate: list[str], k: int) -> float:
    """Fraction of reference top-k ids recovered by candidate top-k ids."""
    if k <= 0:
        return 0.0
    ref = set(reference[:k])
    if not ref:
        return 1.0
    cand = set(candidate[:k])
    return len(ref & cand) / len(ref)


def summarise_samples(name: str, samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {
            "workload": name,
            "n": 0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
        }
    ordered = sorted(samples)
    p95_idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "workload": name,
        "n": len(samples),
        "mean_ms": round(statistics.mean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[p95_idx], 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def evaluate_query(
    query: str,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    top_k: int,
) -> dict[str, Any]:
    baseline_top = _file_ids(baseline, top_k)
    candidate_top = _file_ids(candidate, top_k)
    return {
        "query": query,
        "baseline_hits": len(baseline_top),
        "candidate_hits": len(candidate_top),
        "recall_at_k": recall_at_k(baseline_top, candidate_top, top_k),
        "baseline_top": baseline_top,
        "candidate_top": candidate_top,
    }


def _seed_corpus(mk: MemKraft, size: int) -> list[str]:
    """Create a deterministic mixed corpus and return benchmark queries."""
    topics = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(size):
            topic = topics[i % len(topics)]
            suffix = " Priority Topic" if i >= max(0, size - 10) else ""
            name = f"Recall Doc {i:05d} {topic}{suffix}"
            # The repeated topic controls broad queries; unique marker controls
            # exact retrieval.  A little common text keeps realistic overlap.
            info = (
                f"topic {topic} common shared memory retrieval benchmark "
                f"unique_marker_{i:05d} rank_signal_{size - i:05d} "
                f"{topic} {topic} priority_topic"
            )
            if i >= max(0, size - 10):
                info += " " + " ".join(["priority_topic"] * 25)
                info += " " + " ".join(["alpha", "zeta"] * 20)
            mk.track(name, source="bench")
            mk.update(name, info=info, source="bench")
    queries = ["alpha", "beta", "gamma", "common shared", "priority_topic", "alpha zeta"]
    if size:
        queries.extend(["unique_marker_00000", f"unique_marker_{size // 2:05d}"])
    return queries


def _baseline_search(mk: MemKraft, query: str, top_k: int) -> list[dict[str, Any]]:
    return mk.search(query, top_k=top_k, cache=False)


def run_one(
    size: int,
    top_k: int = 20,
    candidate_fn: typing.Optional[SearchFn] = None,
) -> dict[str, Any]:
    """Run recall comparison on one corpus size.

    By default the candidate path is the current production search path, so
    recall is expected to be 1.0. Future experiments can pass a pruning/scorer
    candidate function and use this harness to quantify recall loss.
    """
    root = Path(tempfile.mkdtemp(prefix="mk_recall_bench_")) / "memory"
    try:
        mk = MemKraft(base_dir=str(root))
        queries = _seed_corpus(mk, size)
        candidate_fn = candidate_fn or _baseline_search

        query_results: list[dict[str, Any]] = []
        baseline_latencies: list[float] = []
        candidate_latencies: list[float] = []
        with contextlib.redirect_stdout(io.StringIO()):
            for query in queries:
                t0 = time.perf_counter()
                baseline = _baseline_search(mk, query, top_k)
                baseline_latencies.append((time.perf_counter() - t0) * 1000)

                t0 = time.perf_counter()
                candidate = candidate_fn(mk, query, top_k)
                candidate_latencies.append((time.perf_counter() - t0) * 1000)

                query_results.append(evaluate_query(query, baseline, candidate, top_k))

        recalls = [q["recall_at_k"] for q in query_results]
        return {
            "documents": size,
            "top_k": top_k,
            "queries": query_results,
            "mean_recall_at_k": round(statistics.mean(recalls), 4) if recalls else 1.0,
            "min_recall_at_k": round(min(recalls), 4) if recalls else 1.0,
            "baseline_latency_ms": summarise_samples("baseline_search", baseline_latencies),
            "candidate_latency_ms": summarise_samples("candidate_search", candidate_latencies),
        }
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="100,1000,3000")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--out", default="/tmp/v2.12-search-recall-bench.json")
    args = parser.parse_args()

    sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()]
    results = [run_one(size=size, top_k=args.top_k) for size in sizes]
    payload = {"benchmark": "search_recall", "results": results}
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
