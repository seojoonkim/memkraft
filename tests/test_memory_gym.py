"""Tests for the MemKraft v3 Memory Gym vertical slice."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.gym import gates, metrics, run, scenarios


@pytest.fixture
def stub_scenario():
    name = "stub_fixed"

    def runner(*, sizes, top_k=20, candidate="baseline", hybrid_alpha=0.025):
        return {
            "scenario": name,
            "candidate": candidate,
            "top_k": int(top_k),
            "results": [{"documents": 1, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}],
        }

    scenarios.register_scenario(name, runner)
    yield name
    scenarios._SCENARIOS.pop(name, None)


def test_metrics_recall_at_k_matches_existing_benchmark_helper():
    assert metrics.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 2) == 0.5
    assert metrics.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 3) == 2 / 3


def test_search_recall_scenario_wraps_existing_benchmark():
    payload = scenarios.run_scenario("search_recall", sizes=[20], top_k=5, candidate="baseline")

    assert payload["scenario"] == "search_recall"
    assert payload["candidate"] == "baseline"
    assert payload["top_k"] == 5
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["documents"] == 20
    assert result["mean_recall_at_k"] == 1.0
    assert result["min_recall_at_k"] == 1.0


def test_search_recall_scenario_supports_legacy_candidate():
    payload = scenarios.run_scenario("search_recall", sizes=[20], top_k=5, candidate="legacy")

    assert payload["scenario"] == "search_recall"
    assert payload["candidate"] == "legacy"
    assert payload["results"][0]["mean_recall_at_k"] == 1.0


def test_search_recall_hybrid_candidate_uses_search_hybrid(monkeypatch):
    calls: list[tuple[int, float, object]] = []

    def fake_run_one(*, size, top_k, candidate_fn=None):
        class FakeMemKraft:
            def search_hybrid(self, query, *, top_k, alpha):
                calls.append((top_k, alpha, self))
                return [{"file": f"hybrid-{query}"}]

        assert size == 20
        assert top_k == 5
        assert candidate_fn is not None
        assert candidate_fn(FakeMemKraft(), "needle", 5) == [{"file": "hybrid-needle"}]
        return {"documents": size, "top_k": top_k, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}

    monkeypatch.setattr(scenarios, "run_search_recall_one", fake_run_one)

    payload = scenarios.run_scenario("search_recall", sizes=[20], top_k=5, candidate="hybrid", hybrid_alpha=0.025)

    assert payload["candidate"] == "hybrid"
    assert payload["hybrid_alpha"] == 0.025
    assert calls and calls[0][:2] == (5, 0.025)


def test_search_recall_scenario_rejects_unknown_candidate():
    with pytest.raises(ValueError, match="unknown Memory Gym candidate"):
        scenarios.run_scenario("search_recall", sizes=[20], top_k=5, candidate="nope")


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
        "--min-mean-recall-at-k",
        "0.0",
        "--min-min-recall-at-k",
        "0.0",
        "--candidate",
        "legacy",
    ]

    completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["candidate"] == "legacy"
    assert payload["gate"]["passed"] is True


def test_memory_gym_cli_accepts_hybrid_alpha(tmp_path: Path):
    out = tmp_path / "gym-hybrid.json"
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
        "0.0",
        "--min-min-recall-at-k",
        "0.0",
        "--candidate",
        "hybrid",
        "--hybrid-alpha",
        "0.025",
    ]

    completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["candidate"] == "hybrid"
    assert payload["hybrid_alpha"] == 0.025
    assert payload["gate"]["passed"] is True


def test_memory_gym_cli_rejects_invalid_inputs_without_traceback(tmp_path: Path):
    cases = [
        ["--sizes", "abc"],
        ["--sizes", "-1"],
        ["--top-k", "0"],
        ["--hybrid-alpha", "nan"],
        ["--hybrid-alpha", "1.5"],
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
        {"sizes": [20], "top_k": 5, "candidate": "hybrid", "hybrid_alpha": float("nan")},
        {"sizes": [20], "top_k": 5, "candidate": "hybrid", "hybrid_alpha": 1.5},
    ]:
        try:
            scenarios.run_scenario("search_recall", **kwargs)
        except ValueError as exc:
            assert "positive" in str(exc) or "hybrid_alpha" in str(exc)
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


def test_register_scenario_enables_run_scenario(stub_scenario):
    payload = scenarios.run_scenario(stub_scenario, sizes=[1], top_k=5)

    assert payload["scenario"] == stub_scenario
    assert payload["top_k"] == 5
    assert payload["results"] == [{"documents": 1, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}]


def test_register_scenario_enables_cli_scenario_name(stub_scenario, tmp_path: Path):
    out = tmp_path / "stub.json"

    exit_code = run.main(["--scenario", stub_scenario, "--out", str(out)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == stub_scenario


def test_unregistered_scenario_raises_structured_error(stub_scenario):
    with pytest.raises(ValueError, match="unknown Memory Gym scenario"):
        scenarios.run_scenario("definitely_not_registered", sizes=[1])


def test_memory_gym_cli_unknown_scenario_writes_structured_error(tmp_path: Path, capsys):
    out = tmp_path / "unknown-scenario.json"

    exit_code = run.main(["--scenario", "definitely_not_registered", "--out", str(out)])

    assert exit_code == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    error = payload["error"]
    assert error["kind"] == "unknown_scenario"
    assert error["param"] == "scenario"
    assert "definitely_not_registered" in error["message"]
    printed = capsys.readouterr().out
    assert json.loads(printed) == payload
    assert "Traceback" not in printed


def test_stub_scenario_end_to_end_gate_payload(stub_scenario, tmp_path: Path):
    out = tmp_path / "stub-gate.json"

    exit_code = run.main(["--scenario", stub_scenario, "--out", str(out), "--gate"])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == stub_scenario
    assert payload["results"] == [{"documents": 1, "mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}]
    assert payload["gate"] == {"passed": True, "failures": []}


def test_search_recall_scenario_is_registered_by_default():
    assert "search_recall" in scenarios.registered_scenarios()
