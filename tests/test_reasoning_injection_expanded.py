"""Pure contract tests for the expanded ReasoningBank benchmark (no network)."""
from __future__ import annotations

import json
import math
import multiprocessing
import fcntl
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from memkraft import MemKraft
from benchmarks import analyze_reasoning_injection_ab as analysis
from benchmarks import gate_reasoning_injection as gate
from benchmarks import project_reasoning_injection_gate as projection
from benchmarks import reasoning_injection_ab as runner
from benchmarks.reasoning_tasks import expanded_cases, seed_lessons, validate_catalog


def test_catalog_is_balanced_deterministic_and_leak_free():
    cases = expanded_cases()
    again = expanded_cases()
    assert len(cases) == 28
    assert cases == again
    assert len({case.case_id for case in cases}) == 28
    procedural = [case for case in cases if case.expects_injection]
    assert Counter((c.family, c.split, c.difficulty) for c in procedural) == Counter(
        {(family, split, difficulty): 1
         for family in "ABCDEF" for split in ("dev", "holdout")
         for difficulty in ("easy", "hard")}
    )
    unrelated = [case for case in cases if not case.expects_injection]
    assert len(unrelated) == 4 and {c.family for c in unrelated} == {"G"}
    assert Counter(c.split for c in unrelated) == {"dev": 2, "holdout": 2}
    assert all(c.expected == c.answer_fn() for c in cases)
    lessons = seed_lessons(cases)
    assert set(lessons) == set("ABCDEF")
    for family, lesson in lessons.items():
        assert lesson and "procedure" in lesson.lower()
        for case in cases:
            assert case.case_id not in lesson
            assert case.expected not in lesson
    validate_catalog(cases)


def test_catalog_validation_rejects_bad_metadata_duplicates_and_leaks():
    cases = expanded_cases()
    with pytest.raises(ValueError, match="duplicate"):
        validate_catalog(cases + [cases[0]])
    with pytest.raises(ValueError, match="split"):
        validate_catalog([replace(cases[0], split="test")])
    with pytest.raises(ValueError, match="difficulty"):
        validate_catalog([replace(cases[0], difficulty="medium")])
    with pytest.raises(ValueError, match="leak"):
        validate_catalog([replace(cases[0], lesson=f"procedure {cases[0].expected}")])


def test_catalog_matches_frozen_plan():
    cases = {(c.family, c.split, c.difficulty): c for c in expanded_cases()}
    phrases = {
        ("A", "dev", "easy"): "below 1000", ("A", "dev", "hard"): "3, 5, or 7",
        ("A", "holdout", "easy"): "below 5000", ("A", "holdout", "hard"): "2, 3, or 11",
        ("B", "dev", "easy"): "1000 factorial", ("B", "dev", "hard"): "250000 factorial",
        ("B", "holdout", "easy"): "5000 factorial", ("B", "holdout", "hard"): "1000000 factorial",
        ("C", "dev", "easy"): "10 by 10", ("C", "dev", "hard"): "40 by 40",
        ("C", "holdout", "easy"): "12 by 12", ("C", "holdout", "hard"): "25 by 35",
        ("D", "dev", "easy"): "2^5 * 3^2", ("D", "dev", "hard"): "2^10 * 3^6 * 5^4 * 7^3 * 11^2",
        ("D", "holdout", "easy"): "2^4 * 3^3 * 5^2", ("D", "holdout", "hard"): "2^15 * 3^9 * 5^6 * 7^2",
        ("E", "dev", "easy"): "squares from 1 through 1000", ("E", "dev", "hard"): "cubes from 1 through 200000",
        ("E", "holdout", "easy"): "squares from 1 through 2000", ("E", "holdout", "hard"): "cubes from 1 through 300000",
        ("F", "dev", "easy"): "3^1000 modulo 101", ("F", "dev", "hard"): "11^54321 modulo 1000033",
        ("F", "holdout", "easy"): "5^2024 modulo 10007", ("F", "holdout", "hard"): "13^87654 modulo 999983",
    }
    assert all(phrase in cases[key].task.lower() for key, phrase in phrases.items())
    assert cases[("C", "dev", "hard")].expected == str(math.comb(80, 40))
    assert cases[("C", "holdout", "hard")].expected == str(math.comb(60, 25))
    g_tasks = " ".join(c.task.lower() for c in cases.values() if c.family == "G")
    assert all(term in g_tasks for term in ("mmxxvi", "specified character", "100 days", "base-7"))


def test_production_injection_transfers_each_family_and_g_abstains(tmp_path):
    cases = expanded_cases()
    lessons = seed_lessons(cases)
    mk = MemKraft(base_dir=str(tmp_path))
    runner.seed_expanded_reasoning(mk, cases)
    for case in cases:
        for style in ("full", "compact"):
            hint, metadata = mk.reasoning_inject_for_task(
                case.task, style=style, return_metadata=True, **runner.EXPANDED_RETRIEVAL
            )
            if case.expects_injection:
                assert hint and lessons[case.family] in hint
                assert metadata["emitted"]["total"] == 1
                if style == "compact":
                    assert "untrusted quoted data" in hint
                    assert f'lesson="{lessons[case.family]}"' in hint
            else:
                assert hint == ""
            # The benchmark checks this post-render too; avoid substring false
            # positives from retrieval scores for one-digit expected values.
            if len(case.expected) > 1:
                assert case.expected not in hint
            assert all(other.case_id not in hint for other in cases)


def test_holdout_ledger_first_duplicate_and_reasoned_rerun(tmp_path):
    ledger, artifact = tmp_path / "nested" / "ledger.json", tmp_path / "holdout.json"
    first = runner.reserve_holdout_run(ledger, artifact)
    assert first["current"]["artifact_path"] == str(artifact.resolve()) and first["prior"] is None
    with pytest.raises(RuntimeError, match="already"):
        runner.reserve_holdout_run(ledger, artifact)
    rerun = runner.reserve_holdout_run(ledger, artifact, rerun_reason="provider outage")
    assert rerun["reason"] == "provider outage"
    assert rerun["prior"]["timestamp"] == first["current"]["timestamp"]
    assert len(json.loads(ledger.read_text())["runs"]) == 2


def _reserve_in_process(ledger, artifact, start, results):
    start.wait()
    try:
        runner.reserve_holdout_run(Path(ledger), Path(artifact))
        results.put("ok")
    except Exception as error:
        results.put(type(error).__name__)


def test_holdout_ledger_concurrent_first_reservation_is_one_shot(tmp_path):
    ledger, artifact = tmp_path / "ledger.json", tmp_path / "artifact.json"
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [context.Process(target=_reserve_in_process,
                 args=(str(ledger), str(artifact), start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in processes) == ["RuntimeError", "ok"]
    assert len(json.loads(ledger.read_text())["runs"]) == 1
    assert len(list(tmp_path.glob("*.lock"))) == 1 and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("ledger_data", [{}, {"schema_version": 9, "runs": []},
                                           {"schema_version": 1, "runs": "bad"}])
def test_holdout_ledger_rejects_invalid_prior_schema(tmp_path, ledger_data):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(ledger_data))
    with pytest.raises(ValueError, match="ledger schema"):
        runner.reserve_holdout_run(ledger, tmp_path / "out.json", rerun_reason="retry")
    assert len(list(tmp_path.glob("*.lock"))) == 1 and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("repeats,url", [(0, "https://example.invalid/v1"),
                                           (1, "not-a-url"),
                                           (1, "https://user:***@example.invalid/v1")])
def test_holdout_preflight_failure_does_not_consume_ledger(monkeypatch, tmp_path, repeats, url):
    ledger = tmp_path / "ledger.json"
    monkeypatch.setenv("MK_RB_BENCH_BASE_URL", url)
    monkeypatch.setenv("MK_RB_BENCH_API_KEY", "secret")
    monkeypatch.setenv("MK_RB_BENCH_MODEL", "model")
    monkeypatch.setattr(sys, "argv", ["bench", "--expanded", "--repeats", str(repeats),
        "--out", str(tmp_path / "out.json"), "--holdout-ledger", str(ledger)])
    with pytest.raises((ValueError, RuntimeError)):
        runner.main()
    assert not ledger.exists()


def test_holdout_cli_requires_ledger_but_dev_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["bench", "--out", str(tmp_path / "x"), "--expanded"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    monkeypatch.setattr(runner, "run_benchmark", lambda **kwargs: {"summary": {}, "errors": 0})
    monkeypatch.setattr(sys, "argv", ["bench", "--out", str(tmp_path / "x"), "--expanded", "--split", "dev"])
    assert runner.main() == 0


def test_expanded_runner_rows_distinguish_no_retrieval_from_abstention(monkeypatch, tmp_path):
    class FakeMemKraft:
        def __init__(self, base_dir):
            pass

        def trajectory_start(self, *args, **kwargs):
            pass

        def trajectory_complete(self, *args, **kwargs):
            pass

        def reasoning_inject_for_task(self, task, **kwargs):
            case = next(case for case in expanded_cases() if case.task == task)
            hint = "SAFE PROCEDURAL HINT" if case.expects_injection else ""
            return hint, {"style": kwargs["style"]}

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("Choice", (), {
                "message": type("Message", (), {"content": "wrong"})()})()],
                "model": "fake", "usage": None})()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(runner, "MemKraft", FakeMemKraft)
    monkeypatch.setitem(sys.modules, "openai", type("OpenAIStub", (), {"OpenAI": FakeOpenAI}))
    monkeypatch.setenv("MK_RB_BENCH_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("MK_RB_BENCH_API_KEY", "secret")
    monkeypatch.setenv("MK_RB_BENCH_MODEL", "model")
    artifact = runner.run_benchmark(repeats=1, seed=1, timeout=1,
                                    out=tmp_path / "artifact.json", expanded=True,
                                    split="dev")
    controls = [row for row in artifact["rows"] if row["condition"] == "control"]
    assert controls
    assert all(row["retrieval_attempted"] is False and row["hint_emitted"] is False
               and row["abstained"] is False for row in controls)
    injected = [row for row in artifact["rows"] if row["condition"] == "injected"]
    assert all(row["retrieval_attempted"] is True for row in injected)
    assert all(row["hint_emitted"] == row["expects_injection"] for row in injected)
    assert all(row["abstained"] != row["expects_injection"] for row in injected)
    assert "secret" not in (tmp_path / "artifact.json").read_text()


@pytest.mark.parametrize("failure", ["temp-write", "replace"])
def test_initial_benchmark_write_is_atomic_and_preserves_completed_artifact(
        monkeypatch, tmp_path, failure):
    out = tmp_path / "artifact.json"
    completed = b'{"completed": true}\n'
    out.write_bytes(completed)
    monkeypatch.setattr(runner, "expanded_cases", lambda: [])
    monkeypatch.setattr(runner, "seed_expanded_reasoning", lambda *args: {})

    class FakeMemKraft:
        def __init__(self, base_dir):
            pass

    monkeypatch.setattr(runner, "MemKraft", FakeMemKraft)
    preflight = {
        "model": "model",
        "client": object(),
        "parsed_endpoint": runner.urlsplit("https://example.invalid/v1"),
    }
    real_write_text = Path.write_text
    real_replace = runner.os.replace

    if failure == "temp-write":
        def fail_temp_write(path, *args, **kwargs):
            if path.parent == out.parent and path.name.startswith(f".{out.name}.") \
                    and path.suffix == ".tmp":
                path.write_bytes(b"partial")
                raise OSError("injected temp write failure")
            return real_write_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_temp_write)
    else:
        def fail_replace(source, destination):
            if Path(destination) == out:
                raise OSError("injected replace failure")
            return real_replace(source, destination)

        monkeypatch.setattr(runner.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected"):
        runner.run_benchmark(
            repeats=1, seed=42, timeout=1, out=out, expanded=True, split="dev",
            preflight_context=preflight,
        )
    assert out.read_bytes() == completed
    assert not list(tmp_path.glob(".*.tmp"))


def test_old_runner_helpers_remain_compatible():
    assert len(runner.benchmark_cases()) == 6
    assert runner.score_exact(" 2\n", "2")
    assert not runner.score_exact("answer: 2", "2")
    assert runner.run_benchmark.__defaults__ is None
    assert runner.run_benchmark.__kwdefaults__["comparison"] == "no-hint-vs-full"


def _expanded_artifact(repeats=2):
    rows = []
    for index, case in enumerate(expanded_cases()):
        for repeat in range(repeats):
            base = 100.0 + index
            for condition, latency, prompt, total, reasoning in (
                ("control", base, 100, 130, 20),
                ("injected", base - (2 if case.expects_injection else 0), 130, 150, 18),
            ):
                rows.append({
                    "pair_id": f"{case.case_id}:{repeat}", "case_id": case.case_id,
                    "condition": condition, "latency_ms": latency, "correct": True,
                    "error": "", "family": case.family, "difficulty": case.difficulty,
                    "split": case.split, "expects_injection": case.expects_injection,
                    "abstained": not case.expects_injection,
                    "usage": {"prompt_tokens": prompt, "total_tokens": total,
                              "reasoning_tokens": reasoning, "completion_tokens": 30},
                })
    return {"schema_version": 2, "settings": {"expanded": True}, "rows": rows}


def test_task_cluster_bootstrap_resamples_tasks_not_calls_and_is_deterministic():
    effects = {"a": [1.0] * 100, "b": [9.0]}
    first = analysis.task_cluster_bootstrap_ci(effects, samples=500, seed=42)
    second = analysis.task_cluster_bootstrap_ci(effects, samples=500, seed=42)
    assert first == second
    # Task medians (1 and 9) are equally weighted despite 100 repeated calls for a.
    assert first["estimate"] == 5.0
    assert first["unit"] == "task"


def test_expanded_analysis_has_metadata_aggregates_and_task_cis(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_expanded_artifact()))
    result = analysis.summarize([path], bootstrap_samples=200, bootstrap_seed=42)
    assert result["analysis_unit"].startswith("unique task")
    assert set(result["aggregates"]) == {"family", "difficulty", "split"}
    assert len(result["task_level"]["effects"]) == 28
    for metric in ("latency_ms", "prompt_tokens", "total_tokens", "reasoning_tokens"):
        assert result["task_cluster_bootstrap_95_ci"][metric]["unit"] == "task"
    assert "p_value" not in json.dumps(result).lower()


@pytest.mark.parametrize("samples", [0, -1])
def test_analysis_rejects_nonpositive_bootstrap_samples(tmp_path, samples):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_expanded_artifact()))
    with pytest.raises(ValueError, match="bootstrap samples"):
        analysis.summarize([path], bootstrap_samples=samples)


def test_analysis_rejects_malformed_schema_and_nonfinite_telemetry(tmp_path):
    path = tmp_path / "artifact.json"
    for artifact in ({}, {"rows": "bad"}, _expanded_artifact()):
        if isinstance(artifact.get("rows"), list):
            artifact["rows"][0]["latency_ms"] = float("nan")
        path.write_text(json.dumps(artifact))
        with pytest.raises(ValueError):
            analysis.summarize([path], bootstrap_samples=10)


def test_analysis_zero_baseline_percentage_is_explicit(tmp_path):
    artifact = _expanded_artifact(repeats=1)
    for row in artifact["rows"]:
        row["latency_ms"] = 0 if row["condition"] == "control" else 1
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact))
    result = analysis.summarize([path], bootstrap_samples=10)
    assert result["descriptive_call_level"]["paired_median_latency_change_pct"] is None


@pytest.mark.parametrize("content,extra", [("not json", []), ("{}", []),
                                             (json.dumps(_expanded_artifact()), ["--bootstrap-samples", "0"])])
def test_analysis_cli_fail_closed_json_exit_two_no_traceback(tmp_path, content, extra):
    source, out = tmp_path / "input.json", tmp_path / "nested" / "out.json"
    source.write_text(content)
    result = subprocess.run([sys.executable, "benchmarks/analyze_reasoning_injection_ab.py",
        str(source), "--out", str(out), *extra], cwd=Path.cwd(), capture_output=True, text=True)
    assert result.returncode == 2 and "Traceback" not in result.stderr
    error = json.loads(result.stderr)
    assert error["status"] == "error"


def _happy_gate_fixture():
    tasks = []
    for case in expanded_cases():
        tasks.append({
            "case_id": case.case_id, "family": case.family, "difficulty": case.difficulty,
            "split": case.split, "expects_injection": case.expects_injection,
            "accuracy_control": 2, "accuracy_compact": 2, "accuracy_full": 2,
            "accuracy_compact_delta": 0, "paired_compact_losses": 0,
            "injection_covered": case.expects_injection,
            "abstained": not case.expects_injection,
            "compact_vs_full_prompt_reduction_pct": 40,
            "compact_vs_no_hint_prompt_overhead_tokens": 120,
            "reasoning_change_pct": -1 if case.difficulty == "hard" else 0,
            "latency_slowdown_pct": 2,
        })
    return {
        "schema_version": 1, "tasks": tasks,
        "family_latency_change_pct": {
            split: {family: -2 for family in "ABCDEF"} for split in ("dev", "holdout")
        },
        "latency_task_ci_95": [-3, 0],
        "prompt_overhead_task_ci_95": [100, 130],
        "total_token_delta_task_ci_95": [-5, 5],
        "reasoning_change_task_ci_95": [-3, 0],
        "hard_holdout_latency_task_ci_95": [-3, 0],
    }


def test_gate_happy_fixture_and_neutral_ci():
    result = gate.evaluate_gate(_happy_gate_fixture())
    assert result["status"] == "PASS"
    assert result["neutral"]
    assert result["universal_speed_claim"] is False
    assert not result["rejected"]


@pytest.mark.parametrize("bad", [None, "x", float("nan"), float("inf"), -float("inf")])
def test_gate_missing_non_numeric_and_nonfinite_fail_closed(bad):
    fixture = _happy_gate_fixture()
    fixture["tasks"][0]["latency_slowdown_pct"] = bad
    result = gate.evaluate_gate(fixture)
    assert result["status"] == "FAIL"
    assert result["rejected"]


def test_gate_impossible_thresholds_fail():
    fixture = _happy_gate_fixture()
    for task in fixture["tasks"]:
        if task["split"] == "holdout" and task["difficulty"] == "hard":
            task["reasoning_change_pct"] = 20
        task["latency_slowdown_pct"] = 16
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"


def test_gate_cli_nested_out_and_exit_semantics(tmp_path):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_happy_gate_fixture()))
    out = tmp_path / "nested" / "result.json"
    command = [sys.executable, "benchmarks/gate_reasoning_injection.py", str(source), "--out", str(out)]
    passed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True)
    assert passed.returncode == 0 and out.exists()

    source.write_text("not json")
    malformed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True)
    assert malformed.returncode == 2
    source.write_text(json.dumps({}))
    failed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True)
    assert failed.returncode == 2
    usage = subprocess.run([sys.executable, "benchmarks/gate_reasoning_injection.py"], capture_output=True)
    assert usage.returncode == 2


def test_gate_rejects_boolean_as_numeric():
    fixture = _happy_gate_fixture()
    fixture["tasks"][0]["paired_compact_losses"] = True
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"


def test_catalog_answer_values_are_finite_integer_strings():
    assert all(c.expected.lstrip("-").isdigit() for c in expanded_cases())
    assert all(math.isfinite(float(c.expected)) for c in expanded_cases())


def _nine_artifacts(tmp_path, repeats=5):
    paths = []
    styles = projection.COMPARISON_STYLES
    holdout_paths = {comparison: str((tmp_path / f"holdout-42-{comparison}.json").resolve())
                     for comparison in styles}
    campaign_members = {comparison: {"status": "completed", "artifact_identity": f"id-{comparison}"}
                        for comparison in styles}
    for split, seed, comparison in sorted(projection.PROTOCOL_KEYS):
        style_pair = styles[comparison]
        rows = []
        for case in (c for c in expanded_cases() if c.split == split):
            for repeat in range(repeats):
                for condition, style in zip(("control", "injected"), style_pair):
                    prompt = {"none": 100, "compact": 220, "full": 340}[style]
                    rows.append({"pair_id": f"{case.case_id}:{repeat}", "case_id": case.case_id,
                        "condition": condition, "expected": case.expected, "prediction": case.expected,
                        "task": case.task, "correct": True, "response_model": "served-model",
                        "latency_ms": 100 + {"none": 0, "compact": -2, "full": 1}[style],
                        "family": case.family, "difficulty": case.difficulty, "split": split,
                        "expects_injection": case.expects_injection,
                        "abstained": style != "none" and not case.expects_injection,
                        "retrieval_attempted": style != "none",
                        "hint_emitted": style != "none" and case.expects_injection,
                        "hint_metadata": {"style": style}, "error": "",
                        "usage": {"prompt_tokens": prompt, "completion_tokens": 30,
                                  "total_tokens": prompt + 30,
                                  "reasoning_tokens": {"none": 20, "compact": 18, "full": 22}[style]}})
        path = tmp_path / f"{split}-{seed}-{comparison}.json"
        frozen = {"seed": 42, "repeats": 5, "timeout_seconds": 120.0,
                  "temperature": 0, "reasoning_effort": "medium", "max_tokens_requested": 512,
                  "sdk_max_retries": 0, "retrieval": dict(projection.FROZEN_RETRIEVAL),
                  "selective_policy": "declared-family-transfer"}
        holdout_run = {"campaign_id": "campaign", "generation": 1, "comparison": comparison,
                       "artifact_path": str(path.resolve()), "artifact_identity": f"id-{comparison}",
                       "status": "completed", "expected_artifacts": holdout_paths, "frozen": frozen,
                       "campaign_members": campaign_members}
        artifact = {"schema_version": 2, "requested_model": "frozen-model",
            "response_models": ["served-model"], "endpoint_host": "example.invalid", "errors": 0,
            "settings": {"expanded": True, "split": split, "comparison": comparison,
                         "control_style": style_pair[0], "injected_style": style_pair[1],
                         "repeats": repeats, "seed": seed, "temperature": 0,
                         "reasoning_effort": "medium", "max_tokens_requested": 512,
                         "timeout_seconds": 120.0, "sdk_max_retries": 0,
                         "selective_policy": "declared-family-transfer",
                         "retrieval": dict(projection.FROZEN_RETRIEVAL),
                         **({"holdout_run": holdout_run} if split == "holdout" else {})}, "rows": rows}
        path.write_text(json.dumps(artifact))
        paths.append(path)
    return paths


def test_reasoning_change_is_bounded_and_zero_baseline_safe():
    assert projection._reasoning_change_pct(0, 0) == 0
    assert projection._reasoning_change_pct(33, 0) == 100
    assert projection._reasoning_change_pct(0, 33) == -100
    assert projection._reasoning_change_pct(18, 20) == -10
    assert projection._reasoning_change_pct(22, 20) == pytest.approx(100 * 2 / 22)
    assert projection._reasoning_change_pct(1e308, 0) == 100
    assert projection._reasoning_change_pct(0, 1e308) == -100
    with pytest.raises(ValueError):
        projection._reasoning_change_pct(-1, 0)


def test_projection_large_finite_reasoning_stays_finite_and_bounded(tmp_path):
    paths = _nine_artifacts(tmp_path)
    for path in paths:
        data = json.loads(path.read_text())
        if data["settings"]["comparison"] == "no-hint-vs-compact":
            for row in data["rows"]:
                row["usage"]["reasoning_tokens"] = (
                    0.0 if row["condition"] == "control" else 1e308
                )
            path.write_text(json.dumps(data))
    evidence = projection.project_evidence(paths, bootstrap_samples=20)
    changes = [task["reasoning_change_pct"] for task in evidence["tasks"]]
    assert all(math.isfinite(value) and -100 <= value <= 100 for value in changes)
    assert all(value == 100 for value in changes)
    assert all(math.isfinite(value) and -100 <= value <= 100
               for value in evidence["reasoning_change_task_ci_95"])


def test_projection_cli_rejects_huge_integer_telemetry_without_traceback(tmp_path):
    paths = _nine_artifacts(tmp_path)
    data = json.loads(paths[0].read_text())
    data["rows"][0]["usage"]["reasoning_tokens"] = 10**400
    paths[0].write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing or invalid telemetry"):
        projection.project_evidence(paths, bootstrap_samples=20)
    out = tmp_path / "must-not-exist-huge.json"
    result = subprocess.run(
        [sys.executable, "benchmarks/project_reasoning_injection_gate.py",
         *map(str, paths), "--out", str(out), "--bootstrap-samples", "20"],
        cwd=Path.cwd(), capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "MALFORMED"
    assert "Traceback" not in result.stderr
    assert not out.exists()


def test_projection_cli_rejects_non_object_json_without_traceback(tmp_path):
    paths = _nine_artifacts(tmp_path)
    paths[0].write_text("[]")
    out = tmp_path / "must-not-exist.json"
    result = subprocess.run(
        [sys.executable, "benchmarks/project_reasoning_injection_gate.py",
         *map(str, paths), "--out", str(out), "--bootstrap-samples", "20"],
        cwd=Path.cwd(), capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "MALFORMED"
    assert "Traceback" not in result.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("location", "field"),
    [("row", "latency_ms"), ("usage", "prompt_tokens"),
     ("usage", "completion_tokens"), ("usage", "total_tokens"),
     ("usage", "reasoning_tokens")],
)
def test_projection_rejects_negative_raw_telemetry(tmp_path, location, field):
    paths = _nine_artifacts(tmp_path)
    data = json.loads(paths[0].read_text())
    target = data["rows"][0] if location == "row" else data["rows"][0]["usage"]
    target[field] = -1
    paths[0].write_text(json.dumps(data))
    with pytest.raises(ValueError, match="must be nonnegative"):
        projection.project_evidence(paths, bootstrap_samples=20)


def test_projection_rejects_nonfinite_derived_latency_change(tmp_path):
    paths = _nine_artifacts(tmp_path)
    for path in paths:
        data = json.loads(path.read_text())
        if data["settings"]["comparison"] == "no-hint-vs-compact":
            for row in data["rows"]:
                row["latency_ms"] = 1e-308 if row["condition"] == "control" else 1e308
            path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="derived latency"):
        projection.project_evidence(paths, bootstrap_samples=20)


def test_projection_accepts_zero_reasoning_baseline_as_bounded_regression(tmp_path):
    paths = _nine_artifacts(tmp_path)
    for path in paths:
        data = json.loads(path.read_text())
        if data["settings"]["comparison"] == "no-hint-vs-compact":
            target = next(c for c in expanded_cases()
                          if c.split == data["settings"]["split"] and c.expects_injection)
            for row in data["rows"]:
                if row["case_id"] == target.case_id:
                    row["usage"]["reasoning_tokens"] = 0 if row["condition"] == "control" else 33
            path.write_text(json.dumps(data))
    evidence = projection.project_evidence(paths, bootstrap_samples=20)
    affected = [task for task in evidence["tasks"] if task["reasoning_change_pct"] == 100]
    assert len(affected) == 2


def test_nine_artifact_projection_happy_and_cli_nested_out(tmp_path):
    paths = _nine_artifacts(tmp_path)
    evidence = projection.project_evidence(paths, bootstrap_samples=200)
    task = next(t for t in evidence["tasks"] if t["expects_injection"])
    assert len(evidence["tasks"]) == 28
    assert (task["compact_vs_full_prompt_reduction_pct"], task["compact_vs_no_hint_prompt_overhead_tokens"],
            task["reasoning_change_pct"], task["latency_slowdown_pct"]) == (50, 120, -10, -2)
    assert gate.evaluate_gate(evidence)["status"] == "PASS"
    out = tmp_path / "nested" / "evidence.json"
    result = subprocess.run([sys.executable, "benchmarks/project_reasoning_injection_gate.py", *map(str, paths),
                             "--out", str(out), "--bootstrap-samples", "50"],
                            cwd=Path.cwd(), capture_output=True, text=True)
    assert result.returncode == 0 and out.exists()


@pytest.mark.parametrize("damage", ["missing", "settings", "duplicate", "telemetry"])
def test_projection_malformed_exit_two(tmp_path, damage):
    paths = _nine_artifacts(tmp_path)
    data = json.loads(paths[0].read_text())
    if damage == "missing":
        data["rows"].pop()
    elif damage == "settings":
        data["settings"]["seed"] = 7
    elif damage == "duplicate":
        data["rows"].append(dict(data["rows"][0]))
    else:
        del data["rows"][0]["usage"]["total_tokens"]
    paths[0].write_text(json.dumps(data))
    with pytest.raises(ValueError):
        projection.project_evidence(paths, bootstrap_samples=20)
    result = subprocess.run([sys.executable, "benchmarks/project_reasoning_injection_gate.py", *map(str, paths),
                             "--out", str(tmp_path / "x.json")], cwd=Path.cwd(), capture_output=True, text=True)
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_gate_exact_thresholds_accuracy_and_dev_coverage():
    fixture = _happy_gate_fixture()
    next(t for t in fixture["tasks"] if t["split"] == "holdout" and t["difficulty"] == "easy")["latency_slowdown_pct"] = 9
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    holdout = [t for t in fixture["tasks"] if t["split"] == "holdout"]
    holdout[0]["accuracy_compact"], holdout[1]["accuracy_compact"] = 1, 3
    holdout[0]["accuracy_compact_delta"], holdout[1]["accuracy_compact_delta"] = -1, 1
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    next(t for t in fixture["tasks"] if t["split"] == "dev" and t["expects_injection"])["injection_covered"] = False
    assert gate.evaluate_gate(fixture)["status"] == "PASS"


@pytest.mark.parametrize("field", ["latency_task_ci_95", "prompt_overhead_task_ci_95",
    "total_token_delta_task_ci_95", "reasoning_change_task_ci_95", "hard_holdout_latency_task_ci_95"])
def test_gate_requires_every_ci(field):
    fixture = _happy_gate_fixture()
    del fixture[field]
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"


def test_gate_exact_shape_reasoning_name_and_hard_speed():
    fixture = _happy_gate_fixture()
    fixture["tasks"].pop()
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    del fixture["family_latency_change_pct"]["dev"]["F"]
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    fixture["tasks"][0]["reasoning_delta"] = fixture["tasks"][0].pop("reasoning_change_pct")
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    hard = [t for t in fixture["tasks"] if t["split"] == "holdout" and t["expects_injection"] and t["difficulty"] == "hard"]
    for task in hard[:5]:
        task["latency_slowdown_pct"] = -2
    fixture["hard_holdout_latency_task_ci_95"] = [-4, -1]
    result = gate.evaluate_gate(fixture)
    assert result["status"] == "PASS" and any(x["metric"] == "hard_speed_claim" for x in result["accepted"])
    hard[0]["latency_slowdown_pct"] = 1
    assert any(x["metric"] == "hard_speed_claim" for x in gate.evaluate_gate(fixture)["neutral"])


def test_accuracy_improvement_passes_but_task_regression_cannot_cancel_it():
    fixture = _happy_gate_fixture()
    holdout = [task for task in fixture["tasks"] if task["split"] == "holdout"]
    holdout[0]["accuracy_compact"] = 3
    holdout[0]["accuracy_compact_delta"] = 1
    assert gate.evaluate_gate(fixture)["status"] == "PASS"
    holdout[1]["accuracy_compact"] = 1
    holdout[1]["accuracy_compact_delta"] = -1
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"
    fixture = _happy_gate_fixture()
    fixture["tasks"][0]["accuracy_regression"] = 0
    assert gate.evaluate_gate(fixture)["malformed"] is True


@pytest.mark.parametrize(
    ("split", "field", "value"),
    [("dev", "accuracy_control", 11), ("dev", "accuracy_compact", 11),
     ("dev", "accuracy_full", 11), ("holdout", "accuracy_control", 6),
     ("holdout", "accuracy_compact", 6), ("holdout", "accuracy_full", 6)],
)
def test_gate_rejects_accuracy_counts_above_protocol_repeats(split, field, value):
    fixture = _happy_gate_fixture()
    task = next(item for item in fixture["tasks"] if item["split"] == split)
    task[field] = value
    if field == "accuracy_compact":
        task["accuracy_compact_delta"] = value - task["accuracy_control"]
    result = gate.evaluate_gate(fixture)
    assert result["malformed"] is True


@pytest.mark.parametrize(
    ("control", "compact", "losses"),
    [(2, 2, 3), (4, 2, 1)],
)
def test_gate_rejects_impossible_paired_loss_counts(control, compact, losses):
    fixture = _happy_gate_fixture()
    task = next(item for item in fixture["tasks"] if item["split"] == "holdout")
    task["accuracy_control"] = control
    task["accuracy_compact"] = compact
    task["accuracy_compact_delta"] = compact - control
    task["paired_compact_losses"] = losses
    result = gate.evaluate_gate(fixture)
    assert result["malformed"] is True


def test_gate_paired_losses_are_holdout_only():
    fixture = _happy_gate_fixture()
    dev = next(task for task in fixture["tasks"] if task["split"] == "dev")
    dev["paired_compact_losses"] = 1
    dev["injection_covered"] = False
    assert gate.evaluate_gate(fixture)["status"] == "PASS"
    holdout = next(task for task in fixture["tasks"] if task["split"] == "holdout")
    holdout["paired_compact_losses"] = 1
    assert gate.evaluate_gate(fixture)["status"] == "FAIL"


def test_projection_emits_unambiguous_accuracy_delta_and_loss(tmp_path):
    paths = _nine_artifacts(tmp_path)
    compact = next(path for path in paths if path.name == "holdout-42-no-hint-vs-compact.json")
    artifact = json.loads(compact.read_text())
    case_id = artifact["rows"][0]["case_id"]
    control = next(row for row in artifact["rows"]
                   if row["case_id"] == case_id and row["condition"] == "control")
    control["prediction"] = "wrong"
    control["correct"] = False
    compact.write_text(json.dumps(artifact))
    evidence = projection.project_evidence(paths, bootstrap_samples=20)
    task = next(item for item in evidence["tasks"] if item["case_id"] == case_id)
    assert task["accuracy_compact_delta"] == 1
    assert task["paired_compact_losses"] == 0
    assert "accuracy_regression" not in task


@pytest.mark.parametrize("damage", ["retrieval", "styles", "row_style", "policy", "timeout",
                                           "endpoint", "response_model", "error", "telemetry"])
def test_projection_rejects_incomparable_or_inauthentic_artifacts(tmp_path, damage):
    paths = _nine_artifacts(tmp_path)
    artifact = json.loads(paths[0].read_text())
    if damage == "retrieval":
        artifact["settings"]["retrieval"]["min_score"] = 0.2
    elif damage == "styles":
        artifact["settings"]["control_style"] = "compact"
    elif damage == "row_style":
        artifact["rows"][0]["hint_metadata"]["style"] = "invalid"
    elif damage == "policy":
        artifact["settings"]["selective_policy"] = "other"
    elif damage == "timeout":
        artifact["settings"]["timeout_seconds"] = 30
    elif damage == "endpoint":
        artifact["endpoint_host"] = "other.invalid"
    elif damage == "response_model":
        artifact["response_models"] = ["other-model"]
    elif damage == "error":
        artifact["errors"] = 1
    else:
        artifact["rows"][0]["retrieval_attempted"] = not artifact["rows"][0]["retrieval_attempted"]
    paths[0].write_text(json.dumps(artifact))
    with pytest.raises(ValueError):
        projection.project_evidence(paths, bootstrap_samples=20)


def test_projection_requires_valid_holdout_reservation_metadata(tmp_path):
    paths = _nine_artifacts(tmp_path)
    holdout = next(path for path in paths if path.name.startswith("holdout-"))
    artifact = json.loads(holdout.read_text())
    del artifact["settings"]["holdout_run"]["artifact_identity"]
    holdout.write_text(json.dumps(artifact))
    with pytest.raises(ValueError, match="holdout_run"):
        projection.project_evidence(paths, bootstrap_samples=20)


@pytest.mark.parametrize("campaign_id,generation", [
    (None, 1), ("", 1), ("campaign", "1"),
])
def test_projection_rejects_invalid_campaign_identity_types(tmp_path, campaign_id, generation):
    paths = _nine_artifacts(tmp_path)
    holdout = next(path for path in paths if path.name.startswith("holdout-"))
    artifact = json.loads(holdout.read_text())
    run = artifact["settings"]["holdout_run"]
    run["campaign_id"] = campaign_id
    run["generation"] = generation
    holdout.write_text(json.dumps(artifact))
    with pytest.raises(ValueError, match="campaign"):
        projection.project_evidence(paths, bootstrap_samples=20)


@pytest.mark.parametrize("field", ["expected_artifacts", "campaign_members"])
def test_projection_rejects_divergent_noncurrent_campaign_metadata(tmp_path, field):
    paths = _nine_artifacts(tmp_path)
    holdout = next(path for path in paths if path.name == "holdout-42-no-hint-vs-full.json")
    artifact = json.loads(holdout.read_text())
    run = artifact["settings"]["holdout_run"]
    if field == "expected_artifacts":
        run[field]["no-hint-vs-compact"] = str((tmp_path / "impostor.json").resolve())
    else:
        run[field]["no-hint-vs-compact"]["artifact_identity"] = "impostor"
    holdout.write_text(json.dumps(artifact))
    with pytest.raises(ValueError, match="campaign"):
        projection.project_evidence(paths, bootstrap_samples=20)


def _campaign_reservation(tmp_path, ledger, comparison):
    expected = {name: tmp_path / f"{name}.json" for name in projection.COMPARISONS}
    return runner.reserve_holdout_run(
        ledger, expected[comparison], comparison=comparison, expected_artifacts=expected,
        seed=42, repeats=5, timeout_seconds=120,
    )


def _write_reserved_artifact(path, reservation):
    path.write_text(json.dumps({"settings": {"holdout_run": dict(reservation)}}))


def test_completion_stages_every_artifact_before_ledger_commit_and_is_retryable(tmp_path):
    ledger = tmp_path / "ledger.json"
    first = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    first_path = Path(first["artifact_path"])
    _write_reserved_artifact(first_path, first)
    runner.complete_holdout_run(ledger, first)

    second = _campaign_reservation(tmp_path, ledger, "no-hint-vs-compact")
    second_path = Path(second["artifact_path"])
    second_path.write_text("not-json")
    first_before = first_path.read_bytes()
    with pytest.raises((ValueError, RuntimeError, json.JSONDecodeError)):
        runner.complete_holdout_run(ledger, second)
    generation = json.loads(ledger.read_text())["generations"][-1]
    assert generation["members"]["no-hint-vs-compact"]["status"] == "reserved"
    assert first_path.read_bytes() == first_before
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))

    _write_reserved_artifact(second_path, second)
    completed = runner.complete_holdout_run(ledger, second)
    assert completed["status"] == "completed"
    assert json.loads(ledger.read_text())["generations"][-1]["members"][
        "no-hint-vs-compact"]["status"] == "completed"


def test_completion_requires_current_reserved_artifact(tmp_path):
    ledger = tmp_path / "ledger.json"
    reservation = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    with pytest.raises(ValueError, match="current campaign artifact"):
        runner.complete_holdout_run(ledger, reservation)
    assert json.loads(ledger.read_text())["generations"][-1]["members"][
        "no-hint-vs-full"]["status"] == "reserved"


def test_new_generation_rejects_every_prior_generation_artifact_path(tmp_path):
    ledger = tmp_path / "ledger.json"
    first = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    first_path = Path(first["artifact_path"])
    _write_reserved_artifact(first_path, first)
    runner.complete_holdout_run(ledger, first)

    expected = {name: tmp_path / f"generation-2-{name}.json"
                for name in projection.COMPARISONS}
    expected["full-vs-compact"] = Path(first["expected_artifacts"]["full-vs-compact"])
    ledger_before = ledger.read_bytes()
    with pytest.raises(ValueError, match="generation-unique paths"):
        runner.reserve_holdout_run(
            ledger, expected["no-hint-vs-full"], comparison="no-hint-vs-full",
            expected_artifacts=expected, seed=42, repeats=5, timeout_seconds=120,
            rerun_reason="justified provider rerun",
        )
    assert ledger.read_bytes() == ledger_before


def test_new_generation_collects_sequentially_without_touching_prior_artifacts(tmp_path):
    ledger = tmp_path / "ledger.json"
    generation_1 = {}
    for comparison in projection.COMPARISONS:
        reservation = _campaign_reservation(tmp_path, ledger, comparison)
        path = Path(reservation["artifact_path"])
        _write_reserved_artifact(path, reservation)
        runner.complete_holdout_run(ledger, reservation)
        generation_1[path] = b""
    generation_1 = {path: path.read_bytes() for path in generation_1}

    expected = {name: tmp_path / f"generation-2-{name}.json"
                for name in projection.COMPARISONS}
    for index, comparison in enumerate(projection.COMPARISONS):
        reservation = runner.reserve_holdout_run(
            ledger, expected[comparison], comparison=comparison,
            expected_artifacts=expected, seed=42, repeats=5, timeout_seconds=120,
            rerun_reason="justified provider rerun" if index == 0 else None,
        )
        assert reservation["generation"] == 2
        _write_reserved_artifact(expected[comparison], reservation)
        runner.complete_holdout_run(ledger, reservation)
        assert {path: path.read_bytes() for path in generation_1} == generation_1

    generations = json.loads(ledger.read_text())["generations"]
    assert len(generations) == 2
    assert all(member["status"] == "completed"
               for member in generations[1]["members"].values())


def test_completion_rolls_back_all_artifacts_when_second_replace_fails(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    first = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    first_path = Path(first["artifact_path"])
    _write_reserved_artifact(first_path, first)
    runner.complete_holdout_run(ledger, first)
    second = _campaign_reservation(tmp_path, ledger, "no-hint-vs-compact")
    second_path = Path(second["artifact_path"])
    _write_reserved_artifact(second_path, second)
    before = {path: path.read_bytes() for path in (first_path, second_path)}
    real_replace = runner.os.replace
    replacements = 0

    def fail_second_artifact(source, destination):
        nonlocal replacements
        if Path(destination) in before:
            replacements += 1
            if replacements == 2:
                raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_second_artifact)
    with pytest.raises(OSError, match="injected"):
        runner.complete_holdout_run(ledger, second)
    assert {path: path.read_bytes() for path in before} == before
    assert json.loads(ledger.read_text())["generations"][-1]["members"][
        "no-hint-vs-compact"]["status"] == "reserved"
    assert not list(tmp_path.glob(".*.tmp")) and not list(tmp_path.glob(".*.bak"))
    monkeypatch.setattr(runner.os, "replace", real_replace)
    assert runner.complete_holdout_run(ledger, second)["status"] == "completed"


def test_failed_holdout_main_keeps_same_generation_retryable_and_audited(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    expected = {name: tmp_path / f"{name}.json" for name in projection.COMPARISONS}
    monkeypatch.setattr(runner, "preflight_benchmark", lambda **kwargs: {})
    outcomes = iter((1, 0))

    def fake_benchmark(**kwargs):
        artifact = {"settings": {"holdout_run": kwargs["holdout_run"]},
                    "summary": {}, "errors": next(outcomes)}
        kwargs["out"].write_text(json.dumps(artifact))
        return artifact

    monkeypatch.setattr(runner, "run_benchmark", fake_benchmark)
    argv = ["bench", "--expanded", "--split", "holdout", "--repeats", "5",
            "--comparison", "no-hint-vs-full", "--out", str(expected["no-hint-vs-full"]),
            "--holdout-ledger", str(ledger)]
    for name, path in expected.items():
        argv.extend(("--holdout-artifact", f"{name}={path}"))
    monkeypatch.setattr(sys, "argv", argv)
    assert runner.main() == 1
    failed = json.loads(ledger.read_text())["generations"][-1]
    assert failed["generation"] == 1
    member = failed["members"]["no-hint-vs-full"]
    assert member["status"] == "pending" and len(member["failures"]) == 1
    assert json.loads(expected["no-hint-vs-full"].read_text())["settings"][
        "holdout_run"]["status"] == "failed"

    assert runner.main() == 0
    retried = json.loads(ledger.read_text())["generations"][-1]
    assert retried["generation"] == 1 and len(retried["members"]["no-hint-vs-full"]["failures"]) == 1
    assert retried["members"]["no-hint-vs-full"]["status"] == "completed"


def test_successful_holdout_main_does_not_rewrite_transactional_artifact(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    expected = {name: tmp_path / f"{name}.json" for name in projection.COMPARISONS}
    out = expected["no-hint-vs-full"]
    monkeypatch.setattr(runner, "preflight_benchmark", lambda **kwargs: {})
    writes_to_out = []
    real_write_text = Path.write_text

    def spy_write_text(path, *args, **kwargs):
        if path == out:
            writes_to_out.append(args[0])
        return real_write_text(path, *args, **kwargs)

    def fake_benchmark(**kwargs):
        artifact = {"settings": {"holdout_run": kwargs["holdout_run"]},
                    "summary": {}, "errors": 0}
        kwargs["out"].write_text(json.dumps(artifact))
        return artifact

    monkeypatch.setattr(Path, "write_text", spy_write_text)
    monkeypatch.setattr(runner, "run_benchmark", fake_benchmark)
    argv = ["bench", "--expanded", "--split", "holdout", "--repeats", "5",
            "--comparison", "no-hint-vs-full", "--out", str(out),
            "--holdout-ledger", str(ledger)]
    for name, path in expected.items():
        argv.extend(("--holdout-artifact", f"{name}={path}"))
    monkeypatch.setattr(sys, "argv", argv)

    assert runner.main() == 0
    assert len(writes_to_out) == 1
    assert json.loads(out.read_text())["settings"]["holdout_run"]["status"] == "completed"


def test_completion_replace_failure_is_audited_and_same_generation_retryable(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    expected = {name: tmp_path / f"{name}.json" for name in projection.COMPARISONS}
    out = expected["no-hint-vs-full"]
    monkeypatch.setattr(runner, "preflight_benchmark", lambda **kwargs: {})

    def fake_benchmark(**kwargs):
        artifact = {"settings": {"holdout_run": kwargs["holdout_run"]},
                    "summary": {}, "errors": 0}
        kwargs["out"].write_text(json.dumps(artifact))
        return artifact

    monkeypatch.setattr(runner, "run_benchmark", fake_benchmark)
    real_replace = runner.os.replace
    failed_once = False

    def fail_completion_artifact_replace(source, destination):
        nonlocal failed_once
        if Path(destination) == out and not failed_once:
            failed_once = True
            raise OSError("secret completion replacement detail")
        return real_replace(source, destination)

    monkeypatch.setattr(runner.os, "replace", fail_completion_artifact_replace)
    argv = ["bench", "--expanded", "--split", "holdout", "--repeats", "5",
            "--comparison", "no-hint-vs-full", "--out", str(out),
            "--holdout-ledger", str(ledger)]
    for name, path in expected.items():
        argv.extend(("--holdout-artifact", f"{name}={path}"))
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(OSError, match="secret completion replacement detail"):
        runner.main()
    generation = json.loads(ledger.read_text())["generations"][-1]
    member = generation["members"]["no-hint-vs-full"]
    assert generation["generation"] == 1 and member["status"] == "pending"
    assert member["failures"][-1]["failure_kind"] == "completion_exception"
    assert member["failures"][-1]["message_class"] == "OSError"
    assert "secret completion replacement detail" not in ledger.read_text()
    assert json.loads(out.read_text())["settings"]["holdout_run"]["status"] == "reserved"
    retried = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    assert retried["generation"] == 1


def test_holdout_main_runtime_error_is_audited_and_same_generation_retryable(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    expected = {name: tmp_path / f"{name}.json" for name in projection.COMPARISONS}
    monkeypatch.setattr(runner, "preflight_benchmark", lambda **kwargs: {})

    def raise_runtime_error(**kwargs):
        raise RuntimeError("secret provider body")

    monkeypatch.setattr(runner, "run_benchmark", raise_runtime_error)
    argv = ["bench", "--expanded", "--split", "holdout", "--repeats", "5",
            "--comparison", "no-hint-vs-full", "--out", str(expected["no-hint-vs-full"]),
            "--holdout-ledger", str(ledger)]
    for name, path in expected.items():
        argv.extend(("--holdout-artifact", f"{name}={path}"))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(RuntimeError, match="secret provider body"):
        runner.main()
    generation = json.loads(ledger.read_text())["generations"][-1]
    member = generation["members"]["no-hint-vs-full"]
    assert generation["generation"] == 1 and member["status"] == "pending"
    assert member["failures"][-1]["failure_kind"] == "benchmark_exception"
    assert member["failures"][-1]["message_class"] == "RuntimeError"
    assert "secret provider body" not in ledger.read_text()
    retried = _campaign_reservation(tmp_path, ledger, "no-hint-vs-full")
    assert retried["generation"] == 1


def test_expanded_docs_have_nine_runnable_collection_commands_and_projection():
    text = Path("docs/bench/reasoning-injection-3.0.2.md").read_text()
    block = text[text.index("## Expanded evidence collection"):]
    assert block.count("python benchmarks/reasoning_injection_ab.py") == 9
    for comparison in ("no-hint-vs-full", "no-hint-vs-compact", "full-vs-compact"):
        assert block.count(f"--comparison {comparison}") == 3
    assert block.count("--holdout-ledger /tmp/rb-holdout-campaign.json") == 3
    assert block.count("--seed 43") == 3
    assert "python benchmarks/project_reasoning_injection_gate.py" in block
    assert "python benchmarks/gate_reasoning_injection.py" in block
    assert "--holdout-rerun-reason" in block


def test_frozen_expanded_protocol_is_nine_artifacts():
    assert projection.PROTOCOL_KEYS == {
        ("dev", seed, comparison)
        for seed in (42, 43) for comparison in projection.COMPARISONS
    } | {("holdout", 42, comparison) for comparison in projection.COMPARISONS}


def test_lock_has_bounded_timeout(tmp_path):
    lock = tmp_path / ".ledger.json.lock"
    lock.touch()
    with lock.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="lock timeout"):
            runner.reserve_holdout_run(
                tmp_path / "ledger.json", tmp_path / "a.json",
                comparison="no-hint-vs-full", expected_artifacts={
                    name: tmp_path / f"{name}.json" for name in projection.COMPARISONS
                }, seed=42, repeats=5, timeout_seconds=120, lock_timeout=0.05,
            )


def test_gate_uses_unambiguous_prompt_overhead_tokens_name():
    fixture = _happy_gate_fixture()
    assert gate.evaluate_gate(fixture)["status"] == "PASS"
    assert all("compact_vs_no_hint_prompt_overhead_tokens" in task for task in fixture["tasks"])
