"""Smoke tests for v2.12 performance benchmark helpers."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module(name: str, rel_path: str):
    path = Path(rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reasoning_bank_bench_summary_shape():
    mod = _load_module("reasoning_bank_bench", "benchmarks/reasoning_bank_bench.py")
    summary = mod.summarise_samples("recall", [1.0, 2.0, 3.0])
    assert summary == {
        "workload": "recall",
        "n": 3,
        "mean_ms": 2.0,
        "median_ms": 2.0,
        "p50_ms": 2.0,
        "p95_ms": 2.9,
        "min_ms": 1.0,
        "max_ms": 3.0,
    }


def test_search_scale_bench_summary_shape():
    mod = _load_module("search_scale_bench", "benchmarks/search_scale_bench.py")
    summary = mod.summarise_samples("search", [10.0, 20.0, 30.0])
    assert summary["workload"] == "search"
    assert summary["n"] == 3
    assert summary["median_ms"] == 20.0
    assert summary["p95_ms"] == 29.0


def test_search_scale_bench_reports_limited_and_unlimited_paths():
    mod = _load_module("search_scale_bench", "benchmarks/search_scale_bench.py")
    result = mod.run_one(size=5, iterations=1, words_per_doc=10, top_k=2)
    assert result["documents"] == 5
    assert "unlimited" in result
    assert "limited" in result
    assert result["limited"]["top_k"] == 2
    assert result["limited"]["hits"] <= 2
    assert result["unlimited"]["hits"] >= result["limited"]["hits"]
