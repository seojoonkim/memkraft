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
