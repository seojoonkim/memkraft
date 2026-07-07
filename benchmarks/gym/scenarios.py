"""Scenario adapters for MemKraft Memory Gym."""
from __future__ import annotations

import math
from typing import Any, Iterable, cast

from benchmarks.search_recall_bench import run_one as run_search_recall_one


def _hybrid_search(alpha: float):
    def search(mk: Any, query: str, top_k: int) -> list[dict[str, Any]]:
        search_hybrid = getattr(mk, "search_hybrid", None)
        if not callable(search_hybrid):
            raise ValueError("hybrid candidate requires MemKraft.search_hybrid")
        return cast(list[dict[str, Any]], search_hybrid(query, top_k=top_k, alpha=alpha))

    return search


def run_scenario(
    scenario: str,
    sizes: Iterable[int],
    top_k: int = 20,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    """Run a named Memory Gym scenario and return a JSON-serialisable payload."""
    if scenario != "search_recall":
        raise ValueError(f"unknown Memory Gym scenario: {scenario}")

    candidate_label = str(candidate).strip().lower()
    if candidate_label not in {"baseline", "legacy", "hybrid"}:
        raise ValueError(f"unknown Memory Gym candidate: {candidate}")

    parsed_sizes = [int(size) for size in sizes]
    parsed_top_k = int(top_k)
    if any(size <= 0 for size in parsed_sizes):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")
    parsed_hybrid_alpha = float(hybrid_alpha)
    if not math.isfinite(parsed_hybrid_alpha) or not 0.0 <= parsed_hybrid_alpha <= 1.0:
        raise ValueError("Memory Gym scenario hybrid_alpha must be finite and between 0.0 and 1.0")

    candidate_fn = _hybrid_search(parsed_hybrid_alpha) if candidate_label == "hybrid" else None
    payload = {
        "scenario": scenario,
        "candidate": candidate_label,
        "top_k": parsed_top_k,
        "results": [
            run_search_recall_one(size=size, top_k=parsed_top_k, candidate_fn=candidate_fn)
            for size in parsed_sizes
        ],
    }
    if candidate_label == "hybrid":
        payload["hybrid_alpha"] = parsed_hybrid_alpha
    return payload
