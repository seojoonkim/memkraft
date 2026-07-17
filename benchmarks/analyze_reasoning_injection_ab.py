"""Combine ReasoningBank A/B artifacts without treating repeats as new tasks."""
import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Optional


def load_pairs(paths: list[Path]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for run_index, path in enumerate(paths):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        by_pair: dict[str, dict[str, Any]] = {}
        for row in artifact["rows"]:
            key = f"run-{run_index}:{row['pair_id']}"
            pair = by_pair.setdefault(key, {})
            condition = row["condition"]
            if condition in pair:
                raise ValueError(f"duplicate {condition!r} row for {key!r}")
            pair[condition] = row
        for key, pair in by_pair.items():
            if set(pair) != {"control", "injected"}:
                raise ValueError(f"incomplete pair {key!r}")
            pairs.append(pair)
    return pairs


def optional_deltas(pairs: list[dict[str, Any]], field: str) -> list[float]:
    deltas = []
    for pair in pairs:
        control = pair["control"]["usage"].get(field)
        injected = pair["injected"]["usage"].get(field)
        if control is not None and injected is not None:
            deltas.append(float(injected) - float(control))
    return deltas


def optional_median(values: list[float]) -> Optional[float]:
    return round(statistics.median(values), 3) if values else None


def optional_mean(values: list[float]) -> Optional[float]:
    return round(statistics.mean(values), 3) if values else None


def summarize(paths: list[Path]) -> dict[str, Any]:
    pairs = load_pairs(paths)
    by_task: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        by_task.setdefault(pair["control"]["case_id"], []).append(pair)

    task_effects = []
    for case_id, task_pairs in sorted(by_task.items()):
        latencies = [
            pair["injected"]["latency_ms"] - pair["control"]["latency_ms"]
            for pair in task_pairs
        ]
        reasoning = optional_deltas(task_pairs, "reasoning_tokens")
        task_effects.append(
            {
                "case_id": case_id,
                "pairs": len(task_pairs),
                "median_latency_delta_ms": round(statistics.median(latencies), 3),
                "median_reasoning_token_delta": optional_median(reasoning),
                "pairs_faster": sum(value < 0 for value in latencies),
            }
        )

    latency_deltas = [
        pair["injected"]["latency_ms"] - pair["control"]["latency_ms"]
        for pair in pairs
    ]
    control_latencies = [pair["control"]["latency_ms"] for pair in pairs]
    reasoning_deltas = optional_deltas(pairs, "reasoning_tokens")
    completion_deltas = optional_deltas(pairs, "completion_tokens")
    total_deltas = optional_deltas(pairs, "total_tokens")
    median_control = statistics.median(control_latencies)
    median_latency_delta = statistics.median(latency_deltas)
    return {
        "schema_version": 1,
        "analysis_unit": "unique task; call-level results are descriptive only",
        "source_artifacts": [path.name for path in paths],
        "unique_tasks": len(by_task),
        "paired_calls": len(pairs),
        "accuracy_control": sum(pair["control"]["correct"] for pair in pairs) / len(pairs),
        "accuracy_injected": sum(pair["injected"]["correct"] for pair in pairs) / len(pairs),
        "paired_losses": sum(
            pair["control"]["correct"] and not pair["injected"]["correct"]
            for pair in pairs
        ),
        "descriptive_call_level": {
            "control_median_latency_ms": round(median_control, 3),
            "paired_median_latency_delta_ms": round(median_latency_delta, 3),
            "paired_median_latency_change_pct": round(
                100 * median_latency_delta / median_control, 3
            ),
            "mean_latency_delta_ms": round(statistics.mean(latency_deltas), 3),
            "pairs_faster": sum(value < 0 for value in latency_deltas),
            "pairs_slower": sum(value > 0 for value in latency_deltas),
            "median_reasoning_token_delta": optional_median(reasoning_deltas),
            "mean_reasoning_token_delta": optional_mean(reasoning_deltas),
            "median_completion_token_delta": optional_median(completion_deltas),
            "median_total_token_delta": optional_median(total_deltas),
        },
        "task_level": {
            "tasks_with_lower_median_latency": sum(
                effect["median_latency_delta_ms"] < 0 for effect in task_effects
            ),
            "tasks_with_higher_median_latency": sum(
                effect["median_latency_delta_ms"] > 0 for effect in task_effects
            ),
            "median_of_task_median_latency_deltas_ms": round(
                statistics.median(effect["median_latency_delta_ms"] for effect in task_effects), 3
            ),
            "effects": task_effects,
        },
        "errors": sum(bool(row["error"]) for path in paths for row in json.loads(path.read_text())["rows"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = summarize(args.inputs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
