"""Standalone retrieval/provenance/executor/fallback routing spike.

The router treats recalled trajectory text only as retrieval/provenance data. It
never executes that text and gives the fallback callable only the original task.
"""
from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Sequence

_SPIKE_002 = Path(__file__).resolve().parents[1] / "002-reasoning-provenance-bridge"
if str(_SPIKE_002) not in sys.path:
    sys.path.insert(0, str(_SPIKE_002))

from bridge import (  # noqa: E402
    TrustedManifest,
    build_trusted_manifest,
    execute_recalled_path,
    procedure_ref,
)

Route = Literal["executor", "model_fallback"]


@dataclass(frozen=True)
class RoutingResult:
    answer: str
    route: Route
    procedure_id: Optional[str]
    retrieval_score: Optional[float]
    latency_ms: float
    model_calls: int
    reason: str


# Frozen copies of the benchmark's six procedural titles and lessons. There are
# deliberately no tasks, expected answers, or answer functions in this module.
_TRUSTED_PROCEDURES: tuple[tuple[str, str, str], ...] = (
    (
        "A.inclusion_exclusion_sum",
        "Sum integers below a limit divisible by listed divisors",
        "Procedure: use inclusion-exclusion for overlapping divisibility sets and arithmetic-series sums.",
    ),
    (
        "B.legendre_factorial_exponent",
        "Find zeroes or prime exponent in factorial",
        "Procedure: use Legendre's formula, repeatedly dividing the factorial input by the relevant prime and summing the quotients.",
    ),
    (
        "C.shortest_grid_paths",
        "Count shortest paths across grid",
        "Procedure: encode shortest monotone grid paths as choices of move positions and evaluate a binomial coefficient.",
    ),
    (
        "D.divisor_count_prime_powers",
        "Count positive divisors",
        "Procedure: for a prime factorization, multiply each exponent increased by one to count positive divisors.",
    ),
    (
        "E.sum_squares_or_cubes",
        "Sum integer squares or cubes",
        "Procedure: apply the appropriate closed form for a sum of consecutive integer powers with exact arithmetic.",
    ),
    (
        "F.modular_exponentiation",
        "Compute powers modulo an integer",
        "Procedure: use repeated squaring and reduce after every multiplication to evaluate a modular power.",
    ),
)


def seed_trusted_procedures(mk: Any) -> TrustedManifest:
    """Store six A-F trajectories and return their external authorization."""
    entries = []
    for procedure_id, title, lesson in _TRUSTED_PROCEDURES:
        family = procedure_id.split(".", 1)[0].lower()
        task_id = f"spike-003-trusted-{family}"
        mk.trajectory_start(task_id, title=title, tags=[])
        mk.trajectory_log(
            task_id,
            1,
            action="record fixed allowlisted procedure identity",
            metadata={"procedure_ref": procedure_ref(procedure_id)},
        )
        mk.trajectory_complete(
            task_id,
            status="success",
            lesson=lesson,
            pattern_signature=title,
            tags=[],
        )
        hits = mk.reasoning_recall(title, top_k=10, status="success")
        hit = next(item for item in hits if item.get("task_id") == task_id)
        entries.append((hit["path"], task_id, procedure_id))
    return build_trusted_manifest(entries)


def _score(hit: dict[str, Any]) -> float:
    value = hit.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("recall hit has invalid score")
    score = float(value)
    if not math.isfinite(score) or score <= 0.0:
        raise ValueError("recall hit has invalid score")
    return score


def _fallback_result(
    task: str,
    fallback: Callable[[str], str],
    *,
    started: float,
    reason: str,
    retrieval_score: Optional[float] = None,
    procedure_id: Optional[str] = None,
) -> RoutingResult:
    # Only the original task crosses the model boundary. Exceptions intentionally
    # propagate, defining a clear contract while preserving exactly-once calling.
    answer = fallback(task)
    if not isinstance(answer, str):
        raise TypeError("fallback answer must be a string")
    return RoutingResult(
        answer=answer,
        route="model_fallback",
        procedure_id=procedure_id,
        retrieval_score=retrieval_score,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        model_calls=1,
        reason=reason,
    )


def route_task(
    task: str,
    *,
    mk: Any,
    base_dir: os.PathLike[str] | str,
    manifest: TrustedManifest | None,
    fallback: Callable[[str], str],
) -> RoutingResult:
    """Recall top-1 success, execute only if provenance validates, else model."""
    started = time.perf_counter()
    try:
        hits = mk.reasoning_recall(task, top_k=1, status="success")
    except Exception as exc:
        return _fallback_result(
            task,
            fallback,
            started=started,
            reason=f"retrieval failed closed: {type(exc).__name__}: {exc}",
        )

    if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)) or not hits:
        return _fallback_result(task, fallback, started=started, reason="no valid success recall hit")
    hit = hits[0]
    if not isinstance(hit, dict) or hit.get("status") != "success":
        return _fallback_result(task, fallback, started=started, reason="malformed success recall hit")
    try:
        retrieval_score = _score(hit)
    except ValueError as exc:
        return _fallback_result(task, fallback, started=started, reason=f"malformed success recall hit: {exc}")

    execution = execute_recalled_path(
        task, hit, base_dir=base_dir, manifest=manifest
    )
    if execution.status != "executed" or execution.answer is None:
        return _fallback_result(
            task,
            fallback,
            started=started,
            reason=execution.reason,
            retrieval_score=retrieval_score,
            procedure_id=execution.procedure_id,
        )
    return RoutingResult(
        answer=execution.answer,
        route="executor",
        procedure_id=execution.procedure_id,
        retrieval_score=retrieval_score,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        model_calls=0,
        reason=execution.reason,
    )


def summarize_results(results: Sequence[RoutingResult], *, correct: int) -> dict[str, int | float]:
    """Return deterministic route, accuracy, and model-call accounting."""
    total = len(results)
    if not 0 <= correct <= total:
        raise ValueError("correct count is outside result bounds")
    executor = sum(result.route == "executor" for result in results)
    fallback = sum(result.route == "model_fallback" for result in results)
    model_calls = sum(result.model_calls for result in results)
    if executor + fallback != total or any(
        (result.route == "executor" and result.model_calls != 0)
        or (result.route == "model_fallback" and result.model_calls != 1)
        for result in results
    ):
        raise ValueError("inconsistent route/model-call accounting")
    baseline = total
    reduction = ((baseline - model_calls) / baseline * 100.0) if baseline else 0.0
    return {
        "total": total,
        "executor": executor,
        "fallback": fallback,
        "model_calls": model_calls,
        "baseline_model_calls": baseline,
        "call_reduction_pct": reduction,
        "accuracy": (correct / total) if total else 0.0,
    }
