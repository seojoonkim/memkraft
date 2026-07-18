"""Combine ReasoningBank A/B artifacts using unique tasks as inferential units."""
import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, NoReturn, Optional


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _validate_artifact(artifact: Any, path: Path) -> list[dict[str, Any]]:
    if not isinstance(artifact, dict):
        raise ValueError(f"{path}: artifact must be an object")
    if "schema_version" in artifact and artifact["schema_version"] not in {1, 2}:
        raise ValueError(f"{path}: unsupported schema_version")
    rows = artifact.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: rows must be a nonempty array")
    for index, row in enumerate(rows):
        label = f"{path}: row {index}"
        if not isinstance(row, dict):
            raise ValueError(f"{label} must be an object")
        for key in ("pair_id", "case_id", "condition", "latency_ms", "correct", "usage"):
            if key not in row:
                raise ValueError(f"{label} missing {key}")
        if not isinstance(row["pair_id"], str) or not row["pair_id"]:
            raise ValueError(f"{label} pair_id must be a nonempty string")
        if not isinstance(row["case_id"], str) or not row["case_id"]:
            raise ValueError(f"{label} case_id must be a nonempty string")
        if row["condition"] not in {"control", "injected"}:
            raise ValueError(f"{label} has invalid condition")
        _finite_number(row["latency_ms"], f"{label} latency_ms")
        if not isinstance(row["correct"], bool) or not isinstance(row["usage"], dict):
            raise ValueError(f"{label} correct/usage schema is invalid")
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
            value = row["usage"].get(field)
            if value is not None:
                _finite_number(value, f"{label} usage.{field}")
    return rows


def load_pairs(paths: list[Path]) -> list[dict[str, Any]]:
    pairs = []
    for run_index, path in enumerate(paths):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"{path}: invalid JSON artifact: {error}") from error
        by_pair: dict[str, dict[str, Any]] = {}
        for row in _validate_artifact(artifact, path):
            key = f"run-{run_index}:{row['pair_id']}"
            pair = by_pair.setdefault(key, {})
            condition = row["condition"]
            if condition in pair:
                raise ValueError(f"duplicate {condition!r} row for {key!r}")
            pair[condition] = row
        for key, pair in by_pair.items():
            if set(pair) != {"control", "injected"}:
                raise ValueError(f"incomplete pair {key!r}")
            if pair["control"]["case_id"] != pair["injected"]["case_id"]:
                raise ValueError(f"mismatched case ids for pair {key!r}")
            pairs.append(pair)
    return pairs


def optional_deltas(pairs: list[dict[str, Any]], field: str) -> list[float]:
    values = []
    for pair in pairs:
        control = pair["control"].get("usage", {}).get(field)
        injected = pair["injected"].get("usage", {}).get(field)
        if control is not None and injected is not None:
            values.append(float(injected) - float(control))
    return values


def optional_median(values: list[float]) -> Optional[float]:
    return round(statistics.median(values), 3) if values else None


def optional_mean(values: list[float]) -> Optional[float]:
    return round(statistics.mean(values), 3) if values else None


def task_cluster_bootstrap_ci(
    effects: dict[str, list[float]], *, samples: int = 20_000, seed: int = 42
) -> dict[str, Any]:
    """Bootstrap tasks (not calls), after collapsing each task to its median effect."""
    if samples <= 0:
        raise ValueError("bootstrap samples must be > 0")
    task_values = [statistics.median(values) for _, values in sorted(effects.items()) if values]
    if not task_values:
        return {"estimate": None, "95_ci": None, "tasks": 0, "samples": samples,
                "seed": seed, "unit": "task"}
    estimate = statistics.mean(task_values)
    rng = random.Random(seed)
    draws = sorted(statistics.mean(rng.choices(task_values, k=len(task_values)))
                   for _ in range(samples))
    return {
        "estimate": round(estimate, 3),
        "95_ci": [round(draws[int(.025 * samples)], 3),
                   round(draws[min(samples - 1, int(.975 * samples))], 3)],
        "tasks": len(task_values), "samples": samples, "seed": seed, "unit": "task",
    }


def _aggregate(task_effects: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for effect in task_effects:
        value = effect.get(key)
        if value is not None:
            grouped.setdefault(str(value), []).append(effect)
    return {
        value: {
            "tasks": len(items),
            "median_latency_delta_ms": round(statistics.median(
                item["median_latency_delta_ms"] for item in items), 3),
            "accuracy_regressions": sum(item["accuracy_regression"] for item in items),
        }
        for value, items in sorted(grouped.items())
    }


def summarize(paths: list[Path], *, bootstrap_samples: int = 20_000,
              bootstrap_seed: int = 42) -> dict[str, Any]:
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap samples must be > 0")
    if not paths:
        raise ValueError("at least one artifact is required")
    pairs = load_pairs(paths)
    if not pairs:
        raise ValueError("no complete pairs")
    by_task: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        by_task.setdefault(pair["control"]["case_id"], []).append(pair)

    task_effects = []
    metric_clusters: dict[str, dict[str, list[float]]] = {
        metric: {} for metric in ("latency_ms", "prompt_tokens", "total_tokens", "reasoning_tokens")
    }
    for case_id, task_pairs in sorted(by_task.items()):
        row = task_pairs[0]["control"]
        latencies = [p["injected"]["latency_ms"] - p["control"]["latency_ms"] for p in task_pairs]
        metric_clusters["latency_ms"][case_id] = latencies
        for metric in ("prompt_tokens", "total_tokens", "reasoning_tokens"):
            values = optional_deltas(task_pairs, metric)
            if values:
                metric_clusters[metric][case_id] = values
        task_effects.append({
            "case_id": case_id, "pairs": len(task_pairs),
            "median_latency_delta_ms": round(statistics.median(latencies), 3),
            "median_prompt_token_delta": optional_median(optional_deltas(task_pairs, "prompt_tokens")),
            "median_total_token_delta": optional_median(optional_deltas(task_pairs, "total_tokens")),
            "median_reasoning_token_delta": optional_median(optional_deltas(task_pairs, "reasoning_tokens")),
            "pairs_faster": sum(value < 0 for value in latencies),
            "accuracy_regression": sum(p["control"]["correct"] and not p["injected"]["correct"] for p in task_pairs),
            **{key: row.get(key) for key in ("family", "difficulty", "split", "expects_injection")},
            "abstained": task_pairs[0]["injected"].get("abstained"),
        })

    latency_deltas = [p["injected"]["latency_ms"] - p["control"]["latency_ms"] for p in pairs]
    control_latencies = [p["control"]["latency_ms"] for p in pairs]
    median_control = statistics.median(control_latencies)
    median_delta = statistics.median(latency_deltas)
    result = {
        "schema_version": 2 if any(e.get("family") is not None for e in task_effects) else 1,
        "analysis_unit": "unique task; call-level results are descriptive only",
        "source_artifacts": [path.name for path in paths],
        "unique_tasks": len(by_task), "paired_calls": len(pairs),
        "accuracy_control": sum(p["control"]["correct"] for p in pairs) / len(pairs),
        "accuracy_injected": sum(p["injected"]["correct"] for p in pairs) / len(pairs),
        "paired_losses": sum(p["control"]["correct"] and not p["injected"]["correct"] for p in pairs),
        "descriptive_call_level": {
            "control_median_latency_ms": round(median_control, 3),
            "paired_median_latency_delta_ms": round(median_delta, 3),
            "paired_median_latency_change_pct": (
                round(100 * median_delta / median_control, 3) if median_control else None
            ),
            "mean_latency_delta_ms": round(statistics.mean(latency_deltas), 3),
            "pairs_faster": sum(v < 0 for v in latency_deltas),
            "pairs_slower": sum(v > 0 for v in latency_deltas),
            "median_reasoning_token_delta": optional_median(optional_deltas(pairs, "reasoning_tokens")),
            "mean_reasoning_token_delta": optional_mean(optional_deltas(pairs, "reasoning_tokens")),
            "median_completion_token_delta": optional_median(optional_deltas(pairs, "completion_tokens")),
            "median_total_token_delta": optional_median(optional_deltas(pairs, "total_tokens")),
        },
        "task_level": {
            "tasks_with_lower_median_latency": sum(e["median_latency_delta_ms"] < 0 for e in task_effects),
            "tasks_with_higher_median_latency": sum(e["median_latency_delta_ms"] > 0 for e in task_effects),
            "median_of_task_median_latency_deltas_ms": round(statistics.median(e["median_latency_delta_ms"] for e in task_effects), 3),
            "effects": task_effects,
        },
        "errors": sum(bool(row.get("error")) for pair in pairs for row in pair.values()),
    }
    if result["schema_version"] == 2:
        result["aggregates"] = {key: _aggregate(task_effects, key) for key in ("family", "difficulty", "split")}
        result["task_cluster_bootstrap_95_ci"] = {
            metric: task_cluster_bootstrap_ci(clusters, samples=bootstrap_samples, seed=bootstrap_seed)
            for metric, clusters in metric_clusters.items()
        }
        result["inference_note"] = "Repeated calls are descriptive only; no call-level p-values are inferential evidence."
    return result


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print(json.dumps({"status": "error", "error": message}), file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--bootstrap-seed", type=int, default=42)
    args = parser.parse_args()
    try:
        result = summarize(args.inputs, bootstrap_samples=args.bootstrap_samples,
                           bootstrap_seed=args.bootstrap_seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
