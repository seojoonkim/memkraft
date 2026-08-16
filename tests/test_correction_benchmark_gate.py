import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "benchmarks" / "corrections" / "cases_v1.json"
RUNNER = ROOT / "benchmarks" / "corrections" / "run.py"
CASES_SHA256 = "6ba4095b414b44bf90663a11f2eb22ace2e48fd3b86d8a88ed1b26fd385ac498"


def _load_runner():
    spec = importlib.util.spec_from_file_location("correction_benchmark", RUNNER)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_frozen_cases_digest_and_contract():
    assert hashlib.sha256(CASES.read_bytes()).hexdigest() == CASES_SHA256
    fixture = json.loads(CASES.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    assert fixture["frozen"] is True
    assert len(fixture["cases"]) >= 5
    capabilities = {item["capability"] for item in fixture["cases"]}
    assert {"fidelity", "interference", "conflict_handling", "budget_omission",
            "recorded_outcome_plumbing"} <= capabilities


def test_benchmark_is_green_and_repeatable():
    runner = _load_runner()
    first = runner.run_benchmark(warm_iterations=20)
    second = runner.run_benchmark(warm_iterations=20)

    assert first["gate"]["passed"] is True
    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["fidelity"] == 1.0
    assert first["metrics"]["interference"] == 0
    assert first["metrics"]["budget_overflows"] == 0
    assert first["metrics"]["unresolved_conflicts_injected"] == 0
    assert first["metrics"]["recorded_compliance_rate"] == 1.0
    assert first["metrics"]["outcomes_observable"] is True
    outcome_case = next(
        result for result in first["case_results"]
        if result["capability"] == "recorded_outcome_plumbing"
    )
    assert outcome_case["observed"]["outcome_tallies"] == {
        "complied": 2, "violated": 0, "not_applicable": 0,
    }


def test_recorded_outcome_gate_depends_on_persisted_memkraft_outcomes(monkeypatch):
    runner = _load_runner()

    def drop_outcome(self, *args, **kwargs):
        return {"outcome": "applied"}

    monkeypatch.setattr(runner.MemKraft, "correction_record_outcome", drop_outcome)
    report = runner.run_benchmark(warm_iterations=5)

    assert report["gate"]["passed"] is False
    assert report["metrics"]["recorded_compliance_rate"] == 0.0
    assert any("recorded compliance" in error for error in report["gate"]["errors"])


def test_benchmark_reports_honest_host_and_latency_evidence():
    runner = _load_runner()
    first = runner.run_benchmark(warm_iterations=20)
    assert first["gate"]["passed"] is True
    assert first["metrics"]["outcomes_observable"] is True
    assert first["two_host_evidence"] == {
        "status": "not_ready", "evidence": None,
        "reason": "single-host deterministic benchmark cannot establish two-host evidence",
    }
    assert first["latency_ms"]["cold"] >= 0
    assert first["latency_ms"]["warm_p95"] >= 0
    assert first["gate"]["warm_p95_bound_ms"] == 50.0
    assert first["latency_ms"]["warm_p95"] <= first["gate"]["warm_p95_bound_ms"]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda report: report["metrics"].__setitem__("fidelity", 0.99), "fidelity"),
        (lambda report: report["metrics"].__setitem__("interference", 1), "interference"),
        (lambda report: report["metrics"].__setitem__("budget_overflows", 1), "budget"),
        (lambda report: report["metrics"].__setitem__("unresolved_conflicts_injected", 1), "conflict"),
        (lambda report: report["two_host_evidence"].update(status="verified", evidence={"hosts": 2}),
         "two-host"),
    ],
)
def test_validator_fails_closed(mutation, reason):
    runner = _load_runner()
    report = runner.run_benchmark(warm_iterations=5)
    mutation(report)
    errors = runner.validate_report(report)
    assert errors
    assert any(reason in error.lower() for error in errors)


def test_cli_uses_repository_source_and_exits_nonzero_for_bad_report(tmp_path):
    runner = _load_runner()
    report = runner.run_benchmark(warm_iterations=5)
    report["metrics"]["fidelity"] = 0.0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(report), encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--validate", str(bad)],
        cwd=str(ROOT), env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "fidelity" in result.stderr.lower()


def test_cli_green_report_is_json():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--warm-iterations", "5"],
        cwd=str(ROOT), env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["gate"]["passed"] is True
    assert result.stderr == ""
