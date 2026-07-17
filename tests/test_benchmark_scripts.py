"""Smoke tests for v2.12 performance benchmark helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module(name: str, rel_path: str):
    path = Path(rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reasoning_bank_bench_summary_shape():
    mod = _load_module("reasoning_bank_bench", "benchmarks/reasoning_bank_bench.py")
    summary = mod.summarise_samples("recall", [1.0, 2.0, 3.0])
    assert summary == {
        "workload": "recall",
        "n": 3,
        "mean_ms": 2.0,
        "median_ms": 2.0,
        "p50_ms": 2.0,
        "p95_ms": 2.9,
        "min_ms": 1.0,
        "max_ms": 3.0,
    }


def test_search_scale_bench_summary_shape():
    mod = _load_module("search_scale_bench", "benchmarks/search_scale_bench.py")
    summary = mod.summarise_samples("search", [10.0, 20.0, 30.0])
    assert summary["workload"] == "search"
    assert summary["n"] == 3
    assert summary["median_ms"] == 20.0
    assert summary["p95_ms"] == 29.0


def _load_reasoning_injection_ab():
    return _load_module(
        "reasoning_injection_ab",
        "benchmarks/reasoning_injection_ab.py",
    )


def test_reasoning_injection_ab_exact_scoring_is_strict():
    mod = _load_reasoning_injection_ab()

    assert mod.score_exact("233168", "233168")
    assert mod.score_exact(" 233168\n", "233168")
    assert not mod.score_exact("The answer is 233168", "233168")
    assert not mod.score_exact("233,168", "233168")
    assert not mod.score_exact("The answer is 233168 because 3+5", "233168")
    assert not mod.score_exact("2331680", "233168")


def test_reasoning_injection_ab_paired_summary_preserves_quality_contract():
    mod = _load_reasoning_injection_ab()
    rows = [
        {
            "pair_id": "a:0", "condition": "control", "latency_ms": 100.0,
            "correct": True,
            "usage": {"total_tokens": 30, "reasoning_tokens": 10},
        },
        {
            "pair_id": "a:0", "condition": "injected", "latency_ms": 70.0,
            "correct": True,
            "usage": {"total_tokens": 20, "reasoning_tokens": 4},
        },
        {
            "pair_id": "b:0", "condition": "control", "latency_ms": 120.0,
            "correct": True,
            "usage": {"total_tokens": 35, "reasoning_tokens": 12},
        },
        {
            "pair_id": "b:0", "condition": "injected", "latency_ms": 90.0,
            "correct": False,
            "usage": {"total_tokens": 25, "reasoning_tokens": 5},
        },
    ]

    summary = mod.paired_summary(rows)

    assert summary["complete_pairs"] == 2
    assert summary["accuracy"]["control"] == 1.0
    assert summary["accuracy"]["injected"] == 0.5
    assert summary["accuracy"]["paired_losses"] == 1
    assert summary["latency_ms"]["paired_delta_injected_minus_control"]["median"] == -30.0
    assert summary["total_tokens_paired_delta_injected_minus_control"]["median"] == -10.0
    assert summary["reasoning_tokens_observable"] is True
    assert summary["latency_ms"]["paired_median_bootstrap_95_ci"] == [-30.0, -30.0]


def test_reasoning_injection_ab_exact_sign_test_is_deterministic():
    mod = _load_reasoning_injection_ab()

    assert mod.exact_sign_test_two_sided([-1, -2, -3, -4, -5]) == 0.0625
    assert mod.exact_sign_test_two_sided([0, 0]) == 1.0


def test_reasoning_injection_ab_rejects_duplicate_pair_conditions():
    mod = _load_reasoning_injection_ab()
    row = {
        "pair_id": "a:0", "condition": "control", "latency_ms": 1.0,
        "correct": True, "usage": {"total_tokens": 1, "reasoning_tokens": 0},
    }

    import pytest
    with pytest.raises(ValueError, match="duplicate condition"):
        mod.paired_summary([row, dict(row)])


def test_reasoning_injection_ab_aggregator_namespaces_runs(tmp_path):
    mod = _load_module(
        "analyze_reasoning_injection_ab",
        "benchmarks/analyze_reasoning_injection_ab.py",
    )
    import json

    def artifact(delta):
        rows = []
        for condition, latency in (("control", 100.0), ("injected", 100.0 + delta)):
            rows.append({
                "pair_id": "same:0", "case_id": "same", "condition": condition,
                "latency_ms": latency, "correct": True, "error": "",
                "usage": {"reasoning_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            })
        return {"rows": rows}

    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    paths[0].write_text(json.dumps(artifact(-10)))
    paths[1].write_text(json.dumps(artifact(20)))
    summary = mod.summarize(paths)

    assert summary["paired_calls"] == 2
    assert summary["unique_tasks"] == 1
    assert summary["descriptive_call_level"]["paired_median_latency_delta_ms"] == 5.0


def test_reasoning_injection_ab_aggregator_allows_missing_token_telemetry(tmp_path):
    mod = _load_module(
        "analyze_reasoning_injection_ab_nullable",
        "benchmarks/analyze_reasoning_injection_ab.py",
    )
    import json

    rows = []
    for condition, latency in (("control", 100.0), ("injected", 90.0)):
        rows.append({
            "pair_id": "a:0", "case_id": "a", "condition": condition,
            "latency_ms": latency, "correct": True, "error": "",
            "usage": {
                "reasoning_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
        })
    path = tmp_path / "nullable.json"
    path.write_text(json.dumps({"rows": rows}))

    summary = mod.summarize([path])

    assert summary["descriptive_call_level"]["median_reasoning_token_delta"] is None
    assert summary["descriptive_call_level"]["median_total_token_delta"] is None


def test_reasoning_injection_ab_builds_nonempty_memkraft_hint(tmp_path):
    mod = _load_reasoning_injection_ab()
    from typing import Any
    from memkraft import MemKraft

    mk: Any = MemKraft(base_dir=str(tmp_path))
    cases = mod.benchmark_cases()
    mod.seed_reasoning(mk, cases)
    hint = mk.reasoning_inject_for_task(cases[0].task)

    assert hint.startswith("## ReasoningBank task context")
    assert "inclusion-exclusion" in hint


def _load_longmemeval_run():
    benchmark_dir = Path("benchmarks/longmemeval").resolve()
    sys.path.insert(0, str(benchmark_dir))
    try:
        return _load_module("longmemeval_run", str(benchmark_dir / "run.py"))
    finally:
        sys.path.remove(str(benchmark_dir))


def test_longmemeval_sample_latency_summary_is_deterministic():
    mod = _load_longmemeval_run()

    assert mod.summarise_elapsed_ms([30.0, 10.0, 20.0]) == {
        "latency_count": 3,
        "p50_ms": 20.0,
        "p95_ms": 29.0,
        "min_ms": 10.0,
        "max_ms": 30.0,
    }


def _load_longmemeval_harness():
    benchmark_dir = Path("benchmarks/longmemeval").resolve()
    sys.path.insert(0, str(benchmark_dir))
    try:
        return _load_module("longmemeval_harness", str(benchmark_dir / "harness.py"))
    finally:
        sys.path.remove(str(benchmark_dir))


def _load_longmemeval_evaluator():
    benchmark_dir = Path("benchmarks/longmemeval").resolve()
    return _load_module("longmemeval_evaluator", str(benchmark_dir / "evaluator.py"))


def test_longmemeval_canonical_match_recovers_known_surface_variants():
    mod = _load_longmemeval_evaluator()

    assert mod.canonical_match("June 3, 2023.", "June 3rd")
    assert mod.canonical_match("You get home around 6:30 p.m.", "6:30 pm")
    assert mod.canonical_match("The latest total is 7.", "seven")
    assert mod.canonical_match("You upgraded to 16 GB of RAM.", "16GB")
    assert mod.canonical_match(
        "It took 18 days.",
        "18 days. 19 days (including the last day) is also acceptable.",
    )


def test_longmemeval_canonical_match_stays_conservative():
    mod = _load_longmemeval_evaluator()

    assert not mod.canonical_match("June 4, 2023.", "June 3rd")
    assert not mod.canonical_match("It took 17 days.", "18 days. 19 days is also acceptable.")
    assert not mod.canonical_match("There were 0 footballs.", "The information is not enough.")


def test_longmemeval_canonical_match_rejects_numeric_and_date_prefixes():
    mod = _load_longmemeval_evaluator()

    assert not mod.canonical_match("17", "seven")
    assert not mod.canonical_match("10", "one")
    assert not mod.canonical_match("June 30", "June 3rd")


def test_longmemeval_scores_add_canonical_without_changing_legacy_metrics():
    mod = _load_longmemeval_evaluator()
    results = [
        {
            "prediction": "You get home around 6:30 p.m.",
            "answer": "6:30 pm",
            "question_type": "single-session-user",
        }
    ]

    scores = mod.score_results(results)

    assert scores["exact_match"] == 0.0
    assert scores["contains_match"] == 0.0
    assert scores["canonical_match"] == 1.0
    assert scores["by_category"]["single-session-user"]["canonical"] == 1.0




def test_longmemeval_split_recommendation_route_is_opt_in(monkeypatch):
    mod = _load_longmemeval_harness()
    recall_questions = [
        "You recommended five bottles. Can you remind me what the fifth bottle was?",
        "Can you remind me of the languages you recommended I learn?",
        "What type of beer did you specifically recommend?",
    ]
    recall_questions.extend(
        [
            "Which hotel was your recommendation?",
            "What was your suggested hotel?",
            "Which language did you advise me to learn?",
            "Did you say I should learn Rust?",
            "Could you remind me what type of beer you specifically recommended?",
            "What did you recommend for my upcoming trip?",
            "What was your recommendation for tomorrow?",
        ]
    )
    current_questions = [
        "Can you suggest a hotel for my upcoming trip?",
        "Any suggestions for staying connected with colleagues?",
        "What do you recommend I learn next?",
        "In our previous conversation, what do you recommend now?",
        "Can you suggest another hotel based on what we discussed?",
        "Remind me to recommend a hotel tomorrow.",
    ]

    monkeypatch.delenv("MK_SPLIT_RECOMMENDATION_ROUTE", raising=False)
    assert all(
        mod.LongMemEvalHarness._is_preference_question(q)
        for q in recall_questions[:3]
    )

    monkeypatch.setenv("MK_SPLIT_RECOMMENDATION_ROUTE", "1")
    assert not any(mod.LongMemEvalHarness._is_preference_question(q) for q in recall_questions)
    assert all(mod.LongMemEvalHarness._is_preference_question(q) for q in current_questions)


def test_longmemeval_run_sample_records_route_and_stage_latency(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()

    class FakeMemKraft:
        def __init__(self, base_dir):
            self.base_dir = Path(base_dir)

    monkeypatch.setattr(mod, "MemKraft", FakeMemKraft)
    harness = object.__new__(mod.LongMemEvalHarness)
    harness.ingest_time_total = 1.0
    harness.search_time_total = 2.0
    harness.llm_time_total = 3.0

    def ingest(mk, sample):
        harness.ingest_time_total += 0.010
        return 4

    harness.ingest_sessions = ingest

    def answer(mk, question, question_date):
        harness.search_time_total += 0.020
        harness.llm_time_total += 0.030
        return "7", "proof"

    harness.retrieve_and_answer = answer
    harness._is_preference_question = lambda question: False
    harness._is_aggregation_question = lambda question: True
    harness._needs_full_assistant_content = lambda question: False

    result = harness.run_sample(
        {
            "question_id": "qid",
            "question": "How many?",
            "answer": "seven",
            "question_type": "multi-session",
            "question_date": "2026-01-01",
        }
    )

    assert result["route"] == "aggregation"
    assert result["used_evidence_context"] is False
    assert result["used_full_assistant_content"] is False
    assert result["context_used_chars"] == 5
    assert result["prediction_chars"] == 1
    assert result["prediction_words"] == 1
    assert result["ingest_ms"] == 10.0
    assert result["search_ms"] == 20.0
    assert result["llm_ms"] == 30.0
    assert result["e2e_ms"] >= 0.0


def test_longmemeval_selective_evidence_bypasses_risky_routes(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()
    inbox = tmp_path / "inbox" / "a.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("legacy full context\n", encoding="utf-8")
    calls = []

    class FakeMemKraft:
        base_dir = tmp_path

        def compile_evidence_context(self, question, *, results, budget):
            calls.append(question)
            return {"text": "compiled"}

    harness = object.__new__(mod.LongMemEvalHarness)
    monkeypatch.setenv("MK_EVIDENCE_CONTEXT", "1")
    results = [{"file": "inbox/a.md", "score": 0.9}]

    aggregation = harness._format_context(results, FakeMemKraft(), question="How many albums did I buy?")
    preference = harness._format_context(results, FakeMemKraft(), question="Can you suggest a hotel?")

    assert "legacy full context" in aggregation
    assert "legacy full context" in preference
    assert calls == []


def test_longmemeval_selective_evidence_preserves_ordinal_full_sidecar(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()
    inbox = tmp_path / "inbox" / "a.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("truncated inbox\n", encoding="utf-8")
    sidecar = tmp_path / "_full_sessions" / "a.md"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("FULL ITEM 27\n", encoding="utf-8")

    class FakeMemKraft:
        base_dir = tmp_path

        def compile_evidence_context(self, *args, **kwargs):
            raise AssertionError("ordinal route must not compile selective evidence")

    harness = object.__new__(mod.LongMemEvalHarness)
    harness._current_question = "What was the 27th item you listed?"
    monkeypatch.setenv("MK_EVIDENCE_CONTEXT", "1")
    rendered = harness._format_context(
        [{"file": "inbox/a.md", "score": 0.9}],
        FakeMemKraft(),
        question=harness._current_question,
    )

    assert "FULL ITEM 27" in rendered


def test_longmemeval_selective_evidence_falls_back_when_compiler_is_empty(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()
    inbox = tmp_path / "inbox" / "a.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("legacy fallback proof\n", encoding="utf-8")

    class FakeMemKraft:
        base_dir = tmp_path

        def compile_evidence_context(self, question, *, results, budget):
            return {"text": "", "evidence": []}

    harness = object.__new__(mod.LongMemEvalHarness)
    monkeypatch.setenv("MK_EVIDENCE_CONTEXT", "1")
    rendered = harness._format_context(
        [{"file": "inbox/a.md", "score": 0.9}], FakeMemKraft(), question="Where is the proof?"
    )

    assert "legacy fallback proof" in rendered
    assert harness._last_context_used_evidence is False


def test_longmemeval_context_instrumentation_reports_actual_full_sidecar_use(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()
    inbox = tmp_path / "inbox" / "a.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("truncated inbox\n", encoding="utf-8")
    sidecar = tmp_path / "_full_sessions" / "a.md"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("FULL ITEM 27\n", encoding="utf-8")

    class FakeMemKraft:
        base_dir = tmp_path

    harness = object.__new__(mod.LongMemEvalHarness)
    question = "What was the 27th item you listed?"
    harness._current_question = question
    rendered = harness._format_context(
        [{"file": "inbox/a.md", "score": 0.9}], FakeMemKraft(), question=question
    )

    assert "FULL ITEM 27" in rendered
    assert harness._last_context_used_full_sidecar is True

    sidecar.unlink()
    rendered = harness._format_context(
        [{"file": "inbox/a.md", "score": 0.9}], FakeMemKraft(), question=question
    )
    assert "truncated inbox" in rendered
    assert harness._last_context_used_full_sidecar is False


def test_longmemeval_evidence_context_is_opt_in_and_uses_core_api(monkeypatch, tmp_path):
    mod = _load_longmemeval_harness()
    calls = []

    class FakeMemKraft:
        base_dir = tmp_path

        def compile_evidence_context(self, question, *, results, budget):
            calls.append((question, results, budget))
            return {"text": "{\"file\":\"inbox/a.md\",\"span\":[0,6],\"text\":\"proof\"}"}

    harness = object.__new__(mod.LongMemEvalHarness)
    monkeypatch.setenv("MK_EVIDENCE_CONTEXT", "1")
    rendered = harness._format_context(
        [{"file": "inbox/a.md", "score": 0.9}], FakeMemKraft(), question="where is proof?"
    )

    assert rendered == "{\"file\":\"inbox/a.md\",\"span\":[0,6],\"text\":\"proof\"}"
    assert calls == [("where is proof?", [{"file": "inbox/a.md", "score": 0.9}], 7500)]


def test_longmemeval_llm_metadata_uses_env_and_stable_fallbacks(monkeypatch):
    mod = _load_longmemeval_run()

    monkeypatch.setenv("MK_LME_LLM_BACKEND", "openrouter")
    monkeypatch.setenv("MK_LME_LLM_MODEL", "vendor/model-v1")
    assert mod.llm_backend_metadata() == {
        "llm_backend": "openrouter",
        "llm_backend_model": "vendor/model-v1",
    }

    monkeypatch.delenv("MK_LME_LLM_BACKEND")
    monkeypatch.delenv("MK_LME_LLM_MODEL")
    assert mod.llm_backend_metadata() == {
        "llm_backend": "anthropic",
        "llm_backend_model": "<backend default>",
    }


def test_search_scale_bench_reports_limited_and_unlimited_paths():
    mod = _load_module("search_scale_bench", "benchmarks/search_scale_bench.py")
    result = mod.run_one(size=5, iterations=1, words_per_doc=10, top_k=2)
    assert result["documents"] == 5
    assert "unlimited" in result
    assert "limited" in result
    assert result["limited"]["top_k"] == 2
    assert result["limited"]["hits"] <= 2
    assert result["unlimited"]["hits"] >= result["limited"]["hits"]


def test_search_recall_bench_metric_helpers():
    mod = _load_module("search_recall_bench", "benchmarks/search_recall_bench.py")
    assert mod.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 2) == 0.5
    assert mod.recall_at_k(["a", "b", "c"], ["b", "d", "a"], 3) == 2 / 3
    summary = mod.evaluate_query(
        "needle",
        baseline=[{"file": "a.md"}, {"file": "b.md"}],
        candidate=[{"file": "b.md"}, {"file": "c.md"}],
        top_k=2,
    )
    assert summary == {
        "query": "needle",
        "baseline_hits": 2,
        "candidate_hits": 2,
        "recall_at_k": 0.5,
        "baseline_top": ["a.md", "b.md"],
        "candidate_top": ["b.md", "c.md"],
    }


def test_search_recall_bench_run_one_shape():
    mod = _load_module("search_recall_bench", "benchmarks/search_recall_bench.py")
    result = mod.run_one(size=12, top_k=3)
    assert result["documents"] == 12
    assert result["top_k"] == 3
    assert result["queries"]
    assert result["mean_recall_at_k"] == 1.0
    assert result["min_recall_at_k"] == 1.0
    assert result["baseline_latency_ms"]["n"] == len(result["queries"])
    assert result["candidate_latency_ms"]["n"] == len(result["queries"])
    assert any(q["query"] == "priority_topic" for q in result["queries"])
