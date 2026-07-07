"""Scenario adapters for MemKraft Memory Gym."""
from __future__ import annotations

import math
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from benchmarks.search_recall_bench import run_one as run_search_recall_one
from benchmarks.gym.gates import (
    MAX_LAST_INTERACTION_P95_MS,
    MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES,
    MAX_SESSION_OVERLAY_LEAKS,
    MIN_RESOLVER_VERDICT_ACCURACY,
    MIN_SESSION_OVERLAY_RECALL,
)

ScenarioRunner = Callable[..., dict[str, Any]]

# Registration point: new Gym scenarios (session_overlay_recall, resolver_verdicts, ...) register here.
_SCENARIOS: dict[str, ScenarioRunner] = {}


def register_scenario(name: str, runner: ScenarioRunner) -> None:
    """Register a Memory Gym scenario runner under a unique name."""
    label = str(name).strip()
    if not label:
        raise ValueError("Memory Gym scenario name must be a non-empty string")
    if not callable(runner):
        raise ValueError(f"Memory Gym scenario runner for {label!r} must be callable")
    _SCENARIOS[label] = runner


def registered_scenarios() -> list[str]:
    """Return the sorted names of all registered Memory Gym scenarios."""
    return sorted(_SCENARIOS)


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
    runner = _SCENARIOS.get(scenario)
    if runner is None:
        raise ValueError(f"unknown Memory Gym scenario: {scenario}")
    return runner(sizes=sizes, top_k=top_k, candidate=candidate, hybrid_alpha=hybrid_alpha)


def _run_search_recall(
    *,
    sizes: Iterable[int],
    top_k: int = 20,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
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
        "scenario": "search_recall",
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


def _run_session_overlay_recall(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    from memkraft import MemKraft

    parsed_sizes = [int(size) for size in sizes]
    parsed_top_k = int(top_k)
    if any(size <= 0 for size in parsed_sizes):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")

    results = []
    for size in parsed_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            for i in range(size):
                mk.remember_candidate(f"session alpha topic {i}", session_id="alpha")
                mk.remember_candidate(f"session beta topic {i}", session_id="beta")
            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
            mk.remember_candidate("session alpha expired sentinel", session_id="alpha", expires_at=expired)

            found = 0
            leaks = 0
            expired_hits = 0
            for i in range(size):
                hits = mk.session_overlay("alpha", f"topic {i}", top_k=parsed_top_k)
                if any(hit["text"] == f"session alpha topic {i}" for hit in hits):
                    found += 1
                leaks += sum(1 for hit in hits if hit.get("session_id") != "alpha")
                expired_hits += sum(1 for hit in hits if "expired sentinel" in hit.get("text", ""))

            same_session_recall = found / size
            results.append(
                {
                    "documents": size,
                    "same_session_recall": same_session_recall,
                    "cross_session_leaks": leaks,
                    "expired_exposures": expired_hits,
                    "thresholds": {
                        "min_same_session_recall": MIN_SESSION_OVERLAY_RECALL,
                        "max_cross_session_leaks": MAX_SESSION_OVERLAY_LEAKS,
                        "max_expired_exposures": MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES,
                    },
                }
            )
    return {"scenario": "session_overlay_recall", "top_k": parsed_top_k, "results": results}


def _run_resolver_verdicts(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    import json

    from memkraft.resolver import resolver_dry_run

    fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "resolver_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    parsed_sizes = [int(size) for size in sizes]
    results = []
    for size in parsed_sizes:
        subset = cases[:size]
        outputs = resolver_dry_run([case["claim"] for case in subset])
        correct = sum(out["verdict"] == case["expected_verdict"] for out, case in zip(outputs, subset))
        repeated = resolver_dry_run([case["claim"] for case in subset])
        missing_promotions = sum(
            1
            for out, case in zip(outputs, subset)
            if case["expected_verdict"] == "MISSING_SOURCE_REJECT" and out["can_promote"]
        )
        results.append(
            {
                "documents": len(subset),
                "accuracy": correct / len(subset) if subset else 0.0,
                "determinism": 1.0 if repeated == outputs else 0.0,
                "missing_source_promotions": missing_promotions,
                "thresholds": {"min_accuracy": MIN_RESOLVER_VERDICT_ACCURACY},
            }
        )
    return {"scenario": "resolver_verdicts", "results": results}


def _run_last_interaction(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    from memkraft import MemKraft

    parsed_sizes = [int(size) for size in sizes]
    results = []
    for size in parsed_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            base = datetime(2026, 7, 8, tzinfo=timezone.utc)
            for i in range(size):
                mk.record_interaction(
                    f"subject-{i:05d}",
                    (base + timedelta(seconds=i)).isoformat(),
                    f"interaction-{i:05d}",
                )
            ok = 0
            durations = []
            probes = min(200, size)
            for i in range(probes):
                subject = f"subject-{i:05d}"
                start = time.perf_counter()
                latest = mk.last_interaction(subject)
                durations.append((time.perf_counter() - start) * 1000)
                if latest and latest.get("interaction_id") == f"interaction-{i:05d}":
                    ok += 1
            durations.sort()
            p95 = durations[int(len(durations) * 0.95) - 1] if durations else 0.0
            results.append(
                {
                    "documents": size,
                    "accuracy": ok / probes if probes else 0.0,
                    "p95_ms": p95,
                    "thresholds": {"max_p95_ms": MAX_LAST_INTERACTION_P95_MS},
                }
            )
    return {"scenario": "last_interaction", "results": results}


register_scenario("search_recall", _run_search_recall)
register_scenario("session_overlay_recall", _run_session_overlay_recall)
register_scenario("resolver_verdicts", _run_resolver_verdicts)
register_scenario("last_interaction", _run_last_interaction)
