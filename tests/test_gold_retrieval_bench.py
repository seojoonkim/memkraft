from __future__ import annotations

import importlib.util
import math
from pathlib import Path


def _load():
    path = Path("benchmarks/gold_retrieval_bench.py")
    spec = importlib.util.spec_from_file_location("gold_retrieval_bench", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rank_metrics_use_declared_gold_ids():
    mod = _load()
    ranked = ["a", "b", "c"]
    assert mod.recall_at_k(ranked, {"b", "c"}, 2) == 0.5
    assert mod.mrr(ranked, {"b", "c"}) == 0.5
    assert round(mod.ndcg_at_k(ranked, {"b", "c"}, 3), 6) == round((1 / math.log2(3) + 1 / 2) / (1 + 1 / math.log2(3)), 6)


def test_evaluate_queries_reports_quality_and_latency_shape():
    mod = _load()
    result = mod.evaluate_query(
        "deployment rollback",
        [{"file": "memory/rollback.md", "score": 1.0}],
        {"memory/rollback.md", "memory/deploy.md"},
        latency_ms=12.5,
        top_k=5,
    )
    assert result == {
        "query": "deployment rollback",
        "retrieved": ["memory/rollback.md"],
        "gold": ["memory/deploy.md", "memory/rollback.md"],
        "recall_at_k": 0.5,
        "mrr": 1.0,
        "ndcg_at_k": 1 / (1 + 1 / math.log2(3)),
        "latency_ms": 12.5,
        "stale_hits": 0,
    }


def test_evaluate_query_counts_stale_hits_separately():
    mod = _load()
    result = mod.evaluate_query(
        "old plan",
        [{"file": "memory/old.md"}, {"file": "memory/current.md"}],
        {"memory/current.md"},
        latency_ms=1.0,
        top_k=2,
        stale_ids={"memory/old.md"},
    )
    assert result["recall_at_k"] == 1.0
    assert result["stale_hits"] == 1
    assert result["retrieved"] == ["memory/old.md", "memory/current.md"]


def test_agent_adapter_contract_is_transport_neutral():
    mod = _load()
    assert mod.ADAPTER_OPERATIONS == ("remember", "recall", "feedback", "health")
    assert mod.validate_adapter_response({"ok": True, "operation": "recall"}) is True
    assert mod.validate_adapter_response({"ok": True}) is False
    assert mod.validate_adapter_response({"ok": True, "operation": "search"}) is False


def test_summarise_includes_aggregate_metrics():
    mod = _load()
    summary = mod.summarise([{"recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0, "latency_ms": 2.0, "stale_hits": 0}])
    assert summary["queries"] == 1
    assert summary["mean_recall_at_k"] == 1.0
    assert summary["mean_latency_ms"] == 2.0
    assert summary["stale_hit_rate"] == 0.0
    assert summary["passed"] is True
