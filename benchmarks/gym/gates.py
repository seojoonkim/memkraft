"""Quality gates for MemKraft Memory Gym payloads."""
from __future__ import annotations

import math
from typing import Any, Optional

# Named threshold constants — the only source of default gate thresholds.
# Scenario code must reference these instead of embedding magic numbers.
MIN_MEAN_RECALL_AT_K: float = 1.0
MIN_MIN_RECALL_AT_K: float = 1.0
MIN_SESSION_OVERLAY_RECALL: float = 1.0
MAX_SESSION_OVERLAY_LEAKS: float = 0.0
MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES: float = 0.0
MAX_SESSION_OVERLAY_GOVERNED_EXPOSURES: float = 0.0
MIN_SESSION_OVERLAY_FREE_TEXT_DISCOVERIES: float = 1.0
MIN_RESOLVER_VERDICT_ACCURACY: float = 0.95
MAX_LAST_INTERACTION_P95_MS: float = 5.0
# 3.0.1 correctness scenarios are binary: no measured numeric baseline exists,
# so anything short of exact correctness is a regression.
MIN_SEARCH_PRECISION_AT_K: float = 1.0
MIN_KO_SEARCH_RECALL_AT_K: float = 1.0
MIN_CLAIM_EXTRACTION_ACCURACY: float = 1.0
MIN_TRUTH_FRESHNESS_CONTRACT: float = 1.0

# Provenance of the default thresholds, reported as `baseline_ref` in gate payloads.
BASELINE_REF: str = "benchmarks/gym/gates.py::DEFAULT_GATE"

DEFAULT_GATE: dict[str, float] = {
    "min_mean_recall_at_k": MIN_MEAN_RECALL_AT_K,
    "min_min_recall_at_k": MIN_MIN_RECALL_AT_K,
}

OBSERVED_METRIC_KEYS: tuple[str, ...] = ("mean_recall_at_k", "min_recall_at_k")

# Explicit contracts avoid presenting scenario-specific correctness or latency as recall.
SCENARIO_GATES: dict[str, tuple[tuple[str, str, float], ...]] = {
    "session_overlay_recall": (
        ("same_session_recall", "min", MIN_SESSION_OVERLAY_RECALL),
        ("cross_session_leaks", "max", MAX_SESSION_OVERLAY_LEAKS),
        ("expired_exposures", "max", MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES),
        ("governed_exposures", "max", MAX_SESSION_OVERLAY_GOVERNED_EXPOSURES),
        ("governed_free_text_discoveries", "min", MIN_SESSION_OVERLAY_FREE_TEXT_DISCOVERIES),
    ),
    "resolver_verdicts": (
        ("accuracy", "min", MIN_RESOLVER_VERDICT_ACCURACY),
        ("determinism", "min", 1.0),
        ("missing_source_promotions", "max", 0.0),
    ),
    "last_interaction": (("accuracy", "min", 1.0), ("p95_ms", "max", MAX_LAST_INTERACTION_P95_MS)),
    "search_precision": (
        ("precision_at_k", "min", MIN_SEARCH_PRECISION_AT_K),
        ("recall_at_k", "min", MIN_MIN_RECALL_AT_K),
    ),
    "search_recall_ko": (
        ("recall_at_k", "min", MIN_KO_SEARCH_RECALL_AT_K),
        ("broad_query_hit", "min", MIN_KO_SEARCH_RECALL_AT_K),
    ),
    "claim_extraction": (
        ("positive_accuracy", "min", MIN_CLAIM_EXTRACTION_ACCURACY),
        ("negative_accuracy", "min", MIN_CLAIM_EXTRACTION_ACCURACY),
    ),
    "truth_freshness": (
        ("compiled_read_stable", "min", MIN_TRUTH_FRESHNESS_CONTRACT),
        ("sleep_refreshes_truth", "min", MIN_TRUTH_FRESHNESS_CONTRACT),
    ),
}


def observed_metrics(payload: dict[str, Any]) -> dict[str, Optional[float]]:
    """Return the worst observed value per gated metric across all results.

    Thresholds are minimums, so the minimum observed value is what a gate
    compares against. Missing or non-finite metrics stay ``None``.
    """
    contract = SCENARIO_GATES.get(str(payload.get("scenario")))
    keys = tuple(item[0] for item in contract) if contract else OBSERVED_METRIC_KEYS
    observed: dict[str, Optional[float]] = {key: None for key in keys}
    for result in payload.get("results", []):
        for key in keys:
            try:
                value = float(result[key])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            current = observed[key]
            if current is None or value < current:
                observed[key] = value
    return observed


def evaluate_gate(payload: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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

    contract = SCENARIO_GATES.get(str(payload.get("scenario")))
    if contract:
        for result in results:
            documents = result.get("documents", "unknown")
            for key, direction, required in contract:
                value = _coerce_metric(result, key, documents, failures)
                if value is None:
                    continue
                failed = value < required if direction == "min" else value > required
                if failed:
                    operator = "<" if direction == "min" else ">"
                    failures.append(f"documents={documents} {key} {value} {operator} required {required}")
        return {"passed": not failures, "failures": failures}

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
) -> Optional[float]:
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
