"""Scenario adapters for MemKraft Memory Gym."""
from __future__ import annotations

from typing import Any, Iterable

from benchmarks.search_recall_bench import run_one as run_search_recall_one


def run_scenario(scenario: str, sizes: Iterable[int], top_k: int = 20) -> dict[str, Any]:
    """Run a named Memory Gym scenario and return a JSON-serialisable payload."""
    if scenario != "search_recall":
        raise ValueError(f"unknown Memory Gym scenario: {scenario}")

    parsed_sizes = [int(size) for size in sizes]
    parsed_top_k = int(top_k)
    if any(size <= 0 for size in parsed_sizes):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")
    return {
        "scenario": scenario,
        "top_k": parsed_top_k,
        "results": [run_search_recall_one(size=size, top_k=parsed_top_k) for size in parsed_sizes],
    }
