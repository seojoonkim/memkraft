"""Tests for the MemKraft v3 Memory Gym vertical slice."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.gym import gates, metrics, run, scenarios

CLAIM_EXTRACTION_FIXTURE = Path(__file__).parent / "fixtures" / "claim_extraction_cases.json"
_HANGUL_RE = re.compile(r"[가-힣]+")
NEW_CORRECTNESS_SCENARIOS = ("search_precision", "search_recall_ko", "claim_extraction", "truth_freshness")
MANDATORY_CLAIM_CASE_IDS = {
    "ko-prefers-positive",
    "ko-uses-polite-positive",
    "en-changed-date-trailer-positive",
    "en-maybe-negative",
    "en-think-negative",
    "en-hearsay-negative",
}


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
        (["--sizes", "abc"], "sizes"),
        (["--sizes", "-1"], "sizes"),
        (["--top-k", "0"], "top_k"),
        (["--hybrid-alpha", "nan"], "hybrid_alpha"),
        (["--hybrid-alpha", "1.5"], "hybrid_alpha"),
    ]
    for extra, param in cases:
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
        payload = json.loads(out.read_text(encoding="utf-8"))
        error = payload["error"]
        assert error["kind"] == "invalid_parameter"
        assert error["param"] == param
        assert error["message"]
        assert json.loads(completed.stdout) == payload


def test_memory_gym_cli_bad_params_write_structured_error(tmp_path: Path):
    cases = [
        (["--top-k", "-3"], "top_k"),
        (["--gate", "--min-mean-recall-at-k", "nan"], "min_mean_recall_at_k"),
        (["--gate", "--min-min-recall-at-k", "inf"], "min_min_recall_at_k"),
    ]
    for extra, param in cases:
        out = tmp_path / f"usage-error-{param}.json"
        cmd = [
            sys.executable,
            "benchmarks/gym/run.py",
            "--scenario",
            "search_recall",
            "--sizes",
            "20",
            "--out",
            str(out),
            *extra,
        ]

        completed = subprocess.run(cmd, cwd=Path.cwd(), text=True, capture_output=True, check=False)

        assert completed.returncode == 2
        assert "Traceback" not in completed.stderr
        payload = json.loads(out.read_text(encoding="utf-8"))
        error = payload["error"]
        assert error["kind"] == "invalid_parameter"
        assert error["param"] == param
        assert error["message"]
        assert json.loads(completed.stdout) == payload


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
    assert payload["scenario"] == "search_recall"
    assert payload["pass"] is False
    assert payload["thresholds"]["min_mean_recall_at_k"] == 1.1
    assert payload["observed"]["mean_recall_at_k"] == 1.0
    assert payload["baseline_ref"] == gates.BASELINE_REF
    assert json.loads(completed.stdout) == payload


def test_memory_gym_cli_gate_failure_emits_structured_summary(stub_scenario, tmp_path: Path, capsys):
    out = tmp_path / "stub-gate-fail.json"

    exit_code = run.main(
        ["--scenario", stub_scenario, "--out", str(out), "--gate", "--min-mean-recall-at-k", "1.1"]
    )

    assert exit_code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == stub_scenario
    assert payload["pass"] is False
    assert payload["thresholds"] == {"min_mean_recall_at_k": 1.1, "min_min_recall_at_k": 1.0}
    assert payload["observed"] == {"mean_recall_at_k": 1.0, "min_recall_at_k": 1.0}
    assert payload["baseline_ref"] == gates.BASELINE_REF
    assert payload["gate"]["passed"] is False
    printed = capsys.readouterr().out
    assert json.loads(printed) == payload
    assert "Traceback" not in printed


def test_gates_exposes_named_threshold_constants():
    assert gates.DEFAULT_GATE == {
        "min_mean_recall_at_k": gates.MIN_MEAN_RECALL_AT_K,
        "min_min_recall_at_k": gates.MIN_MIN_RECALL_AT_K,
    }
    assert isinstance(gates.BASELINE_REF, str)
    assert gates.BASELINE_REF


def test_run_gate_defaults_come_from_gates_constants(stub_scenario, tmp_path: Path, monkeypatch):
    monkeypatch.setitem(gates.DEFAULT_GATE, "min_mean_recall_at_k", 1.1)
    out = tmp_path / "constants.json"

    exit_code = run.main(["--scenario", stub_scenario, "--out", str(out), "--gate"])

    assert exit_code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["pass"] is False
    assert payload["thresholds"]["min_mean_recall_at_k"] == 1.1


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
    assert payload["pass"] is True
    assert payload["thresholds"] == dict(gates.DEFAULT_GATE)
    assert payload["baseline_ref"] == gates.BASELINE_REF


def test_search_recall_scenario_is_registered_by_default():
    assert "search_recall" in scenarios.registered_scenarios()


# ── MemKraft 3.0.1 deterministic correctness scenarios ──────────────────────


def test_new_correctness_scenarios_are_registered():
    registered = scenarios.registered_scenarios()
    for name in NEW_CORRECTNESS_SCENARIOS:
        assert name in registered


def test_new_scenarios_have_binary_gate_contracts():
    assert gates.SCENARIO_GATES["search_precision"] == (
        ("precision_at_k", "min", 1.0),
        ("recall_at_k", "min", 1.0),
    )
    assert gates.SCENARIO_GATES["search_recall_ko"] == (
        ("recall_at_k", "min", 1.0),
        ("broad_query_hit", "min", 1.0),
    )
    assert gates.SCENARIO_GATES["claim_extraction"] == (
        ("positive_accuracy", "min", 1.0),
        ("negative_accuracy", "min", 1.0),
    )
    assert gates.SCENARIO_GATES["truth_freshness"] == tuple(
        (metric, "min", 1.0)
        for metric in (
            "initial_status_fresh",
            "initial_old_truth_visible",
            "append_status_stale",
            "append_pending_exactly_one",
            "stale_old_truth_preserved",
            "sleep_status_fresh",
            "sleep_new_truth_visible",
            "policy_value_cached_before_governance",
            "policy_cached_value_hidden",
            "tombstone_value_cached_before_forget",
            "tombstoned_cached_value_hidden",
            "tombstoned_value_hidden_after_compaction",
        )
    )


def test_new_scenario_gates_report_missing_metrics_structurally():
    for scenario, key in [
        ("search_precision", "precision_at_k"),
        ("search_recall_ko", "recall_at_k"),
        ("claim_extraction", "positive_accuracy"),
    ]:
        result = gates.evaluate_gate({"scenario": scenario, "results": [{"documents": 1}]})
        assert result["passed"] is False
        assert any(f"missing {key}" in failure for failure in result["failures"])


def test_search_precision_scenario_passes_with_trap_documents():
    payload = scenarios.run_scenario("search_precision", sizes=[5], top_k=20)

    assert payload["scenario"] == "search_precision"
    result = payload["results"][0]
    assert result["documents"] == 5
    assert result["trap_documents"] >= 2
    assert result["precision_at_k"] == 1.0
    assert result["recall_at_k"] == 1.0
    assert gates.evaluate_gate(payload)["passed"] is True


def test_search_precision_gate_fails_on_recall_only_inflation(monkeypatch):
    from memkraft import MemKraft

    original = MemKraft.search

    def inflated(self, query, **kwargs):
        hits = original(self, query, **kwargs)
        seen = {hit.get("file") for hit in hits}
        extras = [hit for hit in original(self, "decoy", **kwargs) if hit.get("file") not in seen]
        return hits + extras

    monkeypatch.setattr(MemKraft, "search", inflated)
    payload = scenarios.run_scenario("search_precision", sizes=[5], top_k=20)

    result = payload["results"][0]
    assert result["recall_at_k"] == 1.0, "recall alone must not catch trap-document inflation"
    assert result["precision_at_k"] < 1.0
    gate = gates.evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("precision_at_k" in failure for failure in gate["failures"])


def test_search_precision_rejects_top_k_smaller_than_relevant_set():
    with pytest.raises(ValueError, match="top_k"):
        scenarios.run_scenario("search_precision", sizes=[30], top_k=5)


def test_search_recall_ko_scenario_finds_hangul_documents():
    payload = scenarios.run_scenario("search_recall_ko", sizes=[5], top_k=5)

    assert payload["scenario"] == "search_recall_ko"
    result = payload["results"][0]
    assert result["documents"] == 5
    assert result["recall_at_k"] == 1.0
    assert result["broad_query_hit"] == 1.0
    assert gates.evaluate_gate(payload)["passed"] is True


def test_search_recall_ko_gate_fails_when_hangul_tokens_are_dropped(monkeypatch):
    from memkraft import MemKraft

    original = MemKraft.search

    def hangul_stripping_search(self, query, **kwargs):
        stripped = _HANGUL_RE.sub(" ", str(query)).strip()
        if not stripped:
            return []
        return original(self, stripped, **kwargs)

    monkeypatch.setattr(MemKraft, "search", hangul_stripping_search)
    payload = scenarios.run_scenario("search_recall_ko", sizes=[5], top_k=5)

    result = payload["results"][0]
    assert result["recall_at_k"] < 1.0
    gate = gates.evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("recall_at_k" in failure for failure in gate["failures"])


def test_claim_extraction_fixture_covers_en_and_ko_positive_and_negative_cases():
    data = json.loads(CLAIM_EXTRACTION_FIXTURE.read_text(encoding="utf-8"))
    cases_by_id = {case["id"]: case for case in data["cases"]}
    assert MANDATORY_CLAIM_CASE_IDS <= cases_by_id.keys()
    coverage = {(case["lang"], case["expect_claims"]) for case in data["cases"]}
    assert {("en", True), ("en", False), ("ko", True), ("ko", False)} <= coverage
    for case in data["cases"]:
        assert case["id"] and case["text"]
        if case["expect_claims"]:
            assert case["expected_claim"]

    assert cases_by_id["ko-prefers-positive"]["text"] == "철수가 파이썬을 선호한다"
    assert cases_by_id["ko-uses-polite-positive"]["text"] == "시몬이 OKR를 사용합니다"
    assert cases_by_id["en-maybe-negative"]["text"] == "Alice prefers dark mode (maybe)."
    assert cases_by_id["en-think-negative"]["text"] == "I think Alice uses Obsidian."
    assert cases_by_id["en-hearsay-negative"]["text"] == "It is said that Bob uses Obsidian."
    changed = cases_by_id["en-changed-date-trailer-positive"]
    assert changed["text"] == "Bob changed editor to vim on 2026-07-08 during migration."
    assert changed["expected_claim"] == {
        "subject": "Bob",
        "predicate": "changed",
        "object_value": {"field": "editor", "to": "vim"},
        "valid_from": "2026-07-08",
    }


def test_claim_extraction_scenario_verifies_remember_candidate_end_to_end():
    payload = scenarios.run_scenario("claim_extraction", sizes=[1])

    assert payload["scenario"] == "claim_extraction"
    result = payload["results"][0]
    assert result["positive_cases"] >= 2
    assert result["negative_cases"] >= 2
    assert result["positive_accuracy"] == 1.0
    assert result["negative_accuracy"] == 1.0
    assert result["failed_case_ids"] == []
    assert gates.evaluate_gate(payload)["passed"] is True


def test_claim_extraction_gate_fails_when_extraction_is_suppressed(monkeypatch):
    import memkraft.candidates as candidates_module

    monkeypatch.setattr(candidates_module, "extract_claims", lambda text, entity_hint=None: [])
    payload = scenarios.run_scenario("claim_extraction", sizes=[1])

    result = payload["results"][0]
    assert result["positive_accuracy"] == 0.0
    assert result["negative_accuracy"] == 1.0
    assert result["failed_case_ids"]
    gate = gates.evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("positive_accuracy" in failure for failure in gate["failures"])


def test_claim_extraction_gate_fails_when_extraction_returns_an_extra_claim(monkeypatch):
    import memkraft.candidates as candidates_module

    original = candidates_module.extract_claims

    def extract_with_extra_claim(text, entity_hint=None):
        claims = original(text, entity_hint=entity_hint)
        if claims:
            return claims + [{"subject": "synthetic", "predicate": "uses", "object_value": "extra"}]
        return claims

    monkeypatch.setattr(candidates_module, "extract_claims", extract_with_extra_claim)
    payload = scenarios.run_scenario("claim_extraction", sizes=[1])

    result = payload["results"][0]
    assert result["positive_accuracy"] == 0.0
    assert result["failed_case_ids"]
    gate = gates.evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("positive_accuracy" in failure for failure in gate["failures"])


def test_truth_freshness_scenario_exercises_public_freshness_and_governance_contract():
    payload = scenarios.run_scenario("truth_freshness", sizes=[1])

    assert payload["scenario"] == "truth_freshness"
    assert "provisional" not in payload
    assert "pending_contract" not in payload
    result = payload["results"][0]
    assert result["documents"] == 4
    contract = gates.SCENARIO_GATES["truth_freshness"]
    assert set(result) >= {metric for metric, _direction, _threshold in contract}
    assert all(result[metric] == 1.0 for metric, _direction, _threshold in contract)
    assert gates.evaluate_gate(payload) == {"passed": True, "failures": []}


def test_truth_freshness_gate_fails_when_any_invariant_is_broken():
    payload = scenarios.run_scenario("truth_freshness", sizes=[1])
    for metric, _direction, _threshold in gates.SCENARIO_GATES["truth_freshness"]:
        broken = json.loads(json.dumps(payload))
        broken["results"][0][metric] = 0.0
        gate = gates.evaluate_gate(broken)
        assert gate["passed"] is False
        assert any(metric in failure for failure in gate["failures"])


def test_truth_freshness_gate_catches_post_compaction_resurrection(monkeypatch):
    from memkraft import MemKraft
    from memkraft.store_core import compact

    def buggy_compaction(self, dry_run=True):
        if dry_run:
            return {"schema_version": 1, "dry_run": True, "stores": {}, "status": "planned"}
        compact(self._canonical_events_path())
        return {"schema_version": 1, "dry_run": False, "stores": {}, "status": "applied"}

    monkeypatch.setattr(MemKraft, "compact_memory", buggy_compaction)
    payload = scenarios.run_scenario("truth_freshness", sizes=[1])

    assert payload["results"][0]["tombstoned_value_hidden_after_compaction"] == 0.0
    gate = gates.evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("tombstoned_value_hidden_after_compaction" in failure for failure in gate["failures"])


@pytest.mark.parametrize(
    "scenario,extra",
    [
        ("search_precision", ["--sizes", "5", "--top-k", "20"]),
        ("search_recall_ko", ["--sizes", "5", "--top-k", "5"]),
        ("claim_extraction", ["--sizes", "1"]),
        ("truth_freshness", ["--sizes", "1"]),
    ],
)
def test_memory_gym_cli_gates_new_scenarios(scenario: str, extra: list, tmp_path: Path):
    out = tmp_path / f"{scenario}.json"

    exit_code = run.main(["--scenario", scenario, *extra, "--gate", "--out", str(out)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == scenario
    assert payload["gate"] == {"passed": True, "failures": []}
    assert payload["pass"] is True
    assert all(value is not None for value in payload["observed"].values())


def test_derived_views_benchmark_cli_writes_versioned_results(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    out = tmp_path / "nested" / "derived.json"
    completed = subprocess.run(
        [sys.executable, str(repo_root / "benchmarks/derived_views_bench.py"), "--sizes", "2,3", "--out", str(out)],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "memkraft.derived_views_benchmark"
    assert payload["version"] == 1
    assert payload["options"] == {"sizes": [2, 3]}
    assert [row["events"] for row in payload["results"]] == [2, 3]
    for row in payload["results"]:
        assert row["cold_current_truth_ms"] >= 0.0
        assert row["warm_current_truth_ms"] >= 0.0
        assert row["truth_items"] == row["events"]


def test_derived_views_benchmark_out_error_is_clean_argparse_failure(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(repo_root / "benchmarks/derived_views_bench.py"),
         "--sizes", "1", "--out", "/dev/null/result.json"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )

    assert completed.returncode == 2
    assert "error:" in completed.stderr
    assert "Traceback" not in completed.stderr
