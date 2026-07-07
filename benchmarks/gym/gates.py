"""Quality gates for MemKraft Memory Gym payloads."""
from __future__ import annotations

import math
from typing import Any

DEFAULT_GATE: dict[str, float] = {
    "min_mean_recall_at_k": 1.0,
    "min_min_recall_at_k": 1.0,
}


def evaluate_gate(payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate a Memory Gym payload against simple scalar thresholds."""
    thresholds = dict(DEFAULT_GATE)
    failures: list[str] = []
    if config:
        for key, raw_value in config.items():
            try:
                thresholds[key] = float(raw_value)
            except (TypeError, ValueError):
                failures.append(f"invalid threshold {key}: {raw_value!r}")

    for key, value in thresholds.items():
        if not math.isfinite(value):
            failures.append(f"invalid threshold {key}: {value!r}")
    results = payload.get("results", [])
    if not results:
        failures.append("payload has no results")

    for result in results:
        documents = result.get("documents", "unknown")
        mean_recall = _coerce_metric(result, "mean_recall_at_k", documents, failures)
        min_recall = _coerce_metric(result, "min_recall_at_k", documents, failures)
        if mean_recall is None or min_recall is None:
            continue

        required_mean = thresholds["min_mean_recall_at_k"]
        if mean_recall < required_mean:
            failures.append(
                f"documents={documents} mean_recall_at_k {mean_recall} < required {required_mean}"
            )

        required_min = thresholds["min_min_recall_at_k"]
        if min_recall < required_min:
            failures.append(
                f"documents={documents} min_recall_at_k {min_recall} < required {required_min}"
            )
    return {"passed": not failures, "failures": failures}


def _coerce_metric(
    result: dict[str, Any],
    key: str,
    documents: Any,
    failures: list[str],
) -> float | None:
    if key not in result:
        failures.append(f"documents={documents} missing {key}")
        return None
    try:
        value = float(result[key])
    except (TypeError, ValueError):
        failures.append(f"documents={documents} invalid {key}: {result[key]!r}")
        return None
    if not math.isfinite(value):
        failures.append(f"documents={documents} invalid {key}: {result[key]!r}")
        return None
    return value
