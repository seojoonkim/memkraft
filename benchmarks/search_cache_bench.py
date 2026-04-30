"""v2.7.0 — Search cache benchmark.

Synthetic workload that compares search latency with cache ON vs OFF.

Run::

    python3 benchmarks/search_cache_bench.py

Outputs JSON summary to stdout AND ``/tmp/v2.7.0-bench-result.json``.
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import time
from pathlib import Path
from typing import Callable, List

from memkraft import MemKraft


# ---------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------

QUERIES_REPEAT = [
    "Alice",
    "Bob",
    "engineer",
    "loves coffee",
    "what does Alice do",
    "tell me about Bob",
    "matcha",
    "deploy",
    "incident",
    "decision",
]

QUERIES_VARIED = [f"unique-token-{i}" for i in range(50)]


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _seed_corpus(mk: MemKraft, n_entities: int = 50) -> None:
    names = [
        "Alice",
        "Bob",
        "Charlie",
        "Dana",
        "Erin",
        "Frank",
        "Grace",
        "Henry",
        "Ivy",
        "Jack",
    ]
    roles = ["engineer", "designer", "manager", "founder", "investor"]
    foods = ["coffee", "tea", "matcha", "ramen", "pizza", "salad"]
    for i in range(n_entities):
        nm = f"{random.choice(names)}{i}"
        mk.track(nm, source="bench")
        mk.update(
            nm,
            f"{nm} is a {random.choice(roles)} who loves {random.choice(foods)}",
            source="bench",
        )
    # Add some events for additional searchable text.
    for i in range(20):
        mk.log_event(f"deploy {i} succeeded", tags="deploy")


def _time_calls(fn: Callable[[str], list], queries: List[str]) -> List[float]:
    out: List[float] = []
    for q in queries:
        t0 = time.perf_counter()
        fn(q)
        out.append((time.perf_counter() - t0) * 1000.0)  # ms
    return out


# ---------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------

def workload_repeat(mk: MemKraft, iterations: int = 100, cache: bool = True) -> List[float]:
    """Same 10 queries repeated -> heavy cache reuse."""
    qs = (QUERIES_REPEAT * (iterations // len(QUERIES_REPEAT) + 1))[:iterations]
    if cache:
        return _time_calls(lambda q: mk.search_v2(q, top_k=20), qs)
    else:
        return _time_calls(lambda q: mk.search_v2(q, top_k=20, cache=False), qs)


def workload_mixed(mk: MemKraft, iterations: int = 100, cache: bool = True) -> List[float]:
    """50% repeated + 50% varied -> realistic mix."""
    half = iterations // 2
    qs = list(QUERIES_REPEAT) * (half // len(QUERIES_REPEAT) + 1)
    qs = qs[:half] + QUERIES_VARIED[:iterations - half]
    random.shuffle(qs)
    if cache:
        return _time_calls(lambda q: mk.search_v2(q, top_k=20), qs)
    else:
        return _time_calls(lambda q: mk.search_v2(q, top_k=20, cache=False), qs)


def workload_smart_repeat(mk: MemKraft, iterations: int = 100, cache: bool = True) -> List[float]:
    qs = (QUERIES_REPEAT * (iterations // len(QUERIES_REPEAT) + 1))[:iterations]
    if cache:
        return _time_calls(lambda q: mk.search_smart(q, top_k=20), qs)
    else:
        return _time_calls(lambda q: mk.search_smart(q, top_k=20, cache=False), qs)


def workload_invalidation(mk: MemKraft, iterations: int = 100) -> List[float]:
    """Every 10 queries, mutate -> invalidation correctness sanity."""
    out: List[float] = []
    for i in range(iterations):
        if i > 0 and i % 10 == 0:
            mk.log_event(f"bench tick {i}", tags="bench")
        q = QUERIES_REPEAT[i % len(QUERIES_REPEAT)]
        t0 = time.perf_counter()
        mk.search_v2(q, top_k=20)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------

def _summarise(name: str, samples: List[float]) -> dict:
    return {
        "workload": name,
        "n": len(samples),
        "mean_ms": round(statistics.mean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p50_ms": round(_percentile(samples, 0.50), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "p99_ms": round(_percentile(samples, 0.99), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "throughput_qps": round(1000.0 / statistics.mean(samples), 1)
        if statistics.mean(samples) > 0
        else 0.0,
    }


def main(out_path: str = "/tmp/v2.7.0-bench-result.json") -> dict:
    random.seed(42)

    base = Path("/tmp/memkraft_v270_bench")
    shutil.rmtree(base, ignore_errors=True)
    mk = MemKraft(str(base))
    if hasattr(mk, "init"):
        mk.init()
    print("[bench] seeding corpus (50 entities + 20 events)...")
    _seed_corpus(mk)
    mk.cache_clear()
    mk._get_search_cache().reset_stats()

    results = {}

    # 1. Repeat workload — cache OFF
    print("[bench] repeat workload, cache=OFF (100 calls)...")
    s_off = workload_repeat(mk, iterations=100, cache=False)
    results["repeat_cache_off"] = _summarise("repeat_cache_off", s_off)

    # 2. Repeat workload — cache ON
    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] repeat workload, cache=ON (100 calls)...")
    s_on = workload_repeat(mk, iterations=100, cache=True)
    results["repeat_cache_on"] = _summarise("repeat_cache_on", s_on)
    results["repeat_cache_on"]["cache_stats"] = mk.cache_stats()

    # 3. Mixed workload — OFF then ON
    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] mixed workload, cache=OFF (100 calls)...")
    m_off = workload_mixed(mk, iterations=100, cache=False)
    results["mixed_cache_off"] = _summarise("mixed_cache_off", m_off)

    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] mixed workload, cache=ON (100 calls)...")
    m_on = workload_mixed(mk, iterations=100, cache=True)
    results["mixed_cache_on"] = _summarise("mixed_cache_on", m_on)
    results["mixed_cache_on"]["cache_stats"] = mk.cache_stats()

    # 4. search_smart workload
    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] smart workload, cache=OFF (100 calls)...")
    sm_off = workload_smart_repeat(mk, iterations=100, cache=False)
    results["smart_repeat_cache_off"] = _summarise("smart_repeat_cache_off", sm_off)

    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] smart workload, cache=ON (100 calls)...")
    sm_on = workload_smart_repeat(mk, iterations=100, cache=True)
    results["smart_repeat_cache_on"] = _summarise("smart_repeat_cache_on", sm_on)
    results["smart_repeat_cache_on"]["cache_stats"] = mk.cache_stats()

    # 5. Invalidation correctness — runs without crashing.
    mk.cache_clear()
    mk._get_search_cache().reset_stats()
    print("[bench] invalidation workload (100 calls + mutations)...")
    inv = workload_invalidation(mk, iterations=100)
    results["invalidation"] = _summarise("invalidation", inv)
    results["invalidation"]["cache_stats"] = mk.cache_stats()

    # Speedup deltas.
    results["speedup"] = {
        "repeat_mean_speedup_x": round(
            results["repeat_cache_off"]["mean_ms"]
            / max(results["repeat_cache_on"]["mean_ms"], 0.0001),
            2,
        ),
        "repeat_throughput_gain_pct": round(
            (
                results["repeat_cache_on"]["throughput_qps"]
                / max(results["repeat_cache_off"]["throughput_qps"], 0.0001)
                - 1.0
            )
            * 100.0,
            1,
        ),
        "mixed_mean_speedup_x": round(
            results["mixed_cache_off"]["mean_ms"]
            / max(results["mixed_cache_on"]["mean_ms"], 0.0001),
            2,
        ),
        "smart_mean_speedup_x": round(
            results["smart_repeat_cache_off"]["mean_ms"]
            / max(results["smart_repeat_cache_on"]["mean_ms"], 0.0001),
            2,
        ),
    }

    Path(out_path).write_text(json.dumps(results, indent=2))
    print(f"\n[bench] saved -> {out_path}\n")
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    main()
