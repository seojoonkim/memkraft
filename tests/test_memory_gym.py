"""Tests for the MemKraft v3 Memory Gym vertical slice."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmarks.gym import gates, metrics, scenarios


def test_metrics_recall_at_k_matches_existing_benchmark_helper():
    assert metrics.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 2) == 0.5
    assert metrics.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 3) == 2 / 3


def test_search_recall_scenario_wraps_existing_benchmark():
    payload = scenarios.run_scenario("search_recall", sizes=[20], top_k=5)

    assert payload["scenario"] == "search_recall"
    assert payload["top_k"] == 5
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["documents"] == 20
    assert result["mean_recall_at_k"] == 1.0
    assert result["min_recall_at_k"] == 1.0


def test_gate_passes_current_search_recall_baseline_and_reports_failures():
    payload = {
        "scenario": "search_recall",
        "results": [{"documents": 20, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}],
    }

    passing = gates.evaluate_gate(payload, {"min_mean_recall_at_k": 1.0, "min_min_recall_at_k": 1.0})
    assert passing == {"passed": True, "failures": []}

    failing = gates.evaluate_gate(payload, {"min_mean_recall_at_k": 1.1})
    assert failing["passed"] is False
    assert failing["failures"]
    assert "mean_recall_at_k" in failing["failures"][0]


def test_memory_gym_cli_writes_json_and_gate_status(tmp_path: Path):
    out = tmp_path / "gym.json"
    cmd = [
        sys.executable,
        "benchmarks/gym/run.py",
        "--scenario",
        "search_recall",
        "--sizes",
        "20",
        "--top-k",
        "5",
        "--out",
        str(out),
        "--gate",
    ]

    completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == "search_recall"
    assert payload["gate"]["passed"] is True
    assert payload["gate"]["failures"] == []


def test_memory_gym_cli_rejects_invalid_inputs_without_traceback(tmp_path: Path):
    cases = [
        ["--scenario", "does_not_exist"],
        ["--sizes", "abc"],
        ["--sizes", "-1"],
        ["--top-k", "0"],
    ]
    for extra in cases:
        out = tmp_path / ("invalid-" + "-".join(part.replace("-", "neg") for part in extra) + ".json")
        cmd = [
            sys.executable,
            "benchmarks/gym/run.py",
            "--out",
            str(out),
            *extra,
        ]

        completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

        assert completed.returncode == 2
        assert "Traceback" not in completed.stderr
        assert not out.exists()


def test_memory_gym_cli_creates_output_parent_directory(tmp_path: Path):
    out = tmp_path / "nested" / "bench" / "gym.json"
    cmd = [
        sys.executable,
        "benchmarks/gym/run.py",
        "--scenario",
        "search_recall",
        "--sizes",
        "20",
        "--top-k",
        "5",
        "--out",
        str(out),
        "--gate",
    ]

    completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert out.exists()


def test_gate_reports_malformed_metric_instead_of_raising():
    payload = {"results": [{"documents": 20, "mean_recall_at_k": "not-a-number"}]}

    result = gates.evaluate_gate(payload)

    assert result["passed"] is False
    assert any("invalid mean_recall_at_k" in failure for failure in result["failures"])


def test_gate_reports_non_finite_metrics_and_thresholds():
    payload = {"results": [{"documents": 20, "mean_recall_at_k": float("nan"), "min_recall_at_k": 1.0}]}

    metric_result = gates.evaluate_gate(payload)
    assert metric_result["passed"] is False
    assert any("invalid mean_recall_at_k" in failure for failure in metric_result["failures"])

    threshold_result = gates.evaluate_gate(
        {"results": [{"documents": 20, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}]},
        {"min_mean_recall_at_k": float("inf")},
    )
    assert threshold_result["passed"] is False
    assert any("invalid threshold" in failure for failure in threshold_result["failures"])

    string_threshold_result = gates.evaluate_gate(
        {"results": [{"documents": 20, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}]},
        {"min_mean_recall_at_k": "abc"},
    )
    assert string_threshold_result["passed"] is False
    assert any("invalid threshold" in failure for failure in string_threshold_result["failures"])


def test_scenario_rejects_non_positive_sizes_and_top_k():
    for kwargs in [
        {"sizes": [-1], "top_k": 5},
        {"sizes": [20], "top_k": 0},
    ]:
        try:
            scenarios.run_scenario("search_recall", **kwargs)
        except ValueError as exc:
            assert "positive" in str(exc)
        else:  # pragma: no cover - assertion path
            raise AssertionError("expected non-positive scenario inputs to fail")


def test_memory_gym_cli_gate_failure_exits_nonzero(tmp_path: Path):
    out = tmp_path / "gym-fail.json"
    cmd = [
        sys.executable,
        "benchmarks/gym/run.py",
        "--scenario",
        "search_recall",
        "--sizes",
        "20",
        "--top-k",
        "5",
        "--out",
        str(out),
        "--gate",
        "--min-mean-recall-at-k",
        "1.1",
    ]

    completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

    assert completed.returncode == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["gate"]["passed"] is False
    assert payload["gate"]["failures"]
