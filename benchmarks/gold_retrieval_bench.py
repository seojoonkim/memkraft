"""Gold-set retrieval evaluator for transport-neutral MemKraft benchmarks.

The benchmark deliberately accepts ranked document IDs rather than an agent or
provider object. Hermes, OpenClaw, and other hosts can therefore produce the
same result envelope and be evaluated without coupling the evaluator to them.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Optional

ADAPTER_OPERATIONS = ("remember", "recall", "feedback", "health")


def _ids(ranked: Iterable[Any]) -> list[str]:
    values = []
    for item in ranked:
        values.append(str(item.get("file", "")) if isinstance(item, dict) else str(item))
    return values


def recall_at_k(ranked: Iterable[Any], gold: set[str], k: int) -> float:
    if not gold or k <= 0:
        return 0.0
    return len(set(_ids(ranked)[:k]) & set(gold)) / len(gold)


def mrr(ranked: Iterable[Any], gold: set[str]) -> float:
    if not gold:
        return 0.0
    for rank, item in enumerate(_ids(ranked), 1):
        if item in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Iterable[Any], gold: set[str], k: int) -> float:
    if not gold or k <= 0:
        return 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(_ids(ranked)[:k], 1) if item in gold)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(gold)) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate_query(
    query: str,
    ranked: list[dict[str, Any]],
    gold: set[str],
    *,
    latency_ms: float,
    top_k: int,
    stale_ids: Optional[set[str]] = None,
) -> dict[str, Any]:
    retrieved = _ids(ranked)[:top_k]
    return {
        "query": query,
        "retrieved": retrieved,
        "gold": sorted(gold),
        "recall_at_k": recall_at_k(retrieved, gold, top_k),
        "mrr": mrr(retrieved, gold),
        "ndcg_at_k": ndcg_at_k(retrieved, gold, top_k),
        "latency_ms": float(latency_ms),
        "stale_hits": len(set(retrieved) & (stale_ids or set())),
    }


def validate_adapter_response(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is True and payload.get("operation") in ADAPTER_OPERATIONS


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"queries": 0, "passed": False}
    n = len(rows)
    mean = lambda key: sum(float(row[key]) for row in rows) / n
    retrieved = sum(len(row.get("retrieved", [])) for row in rows)
    stale = sum(int(row.get("stale_hits", 0)) for row in rows)
    return {
        "queries": n,
        "mean_recall_at_k": mean("recall_at_k"),
        "mean_mrr": mean("mrr"),
        "mean_ndcg_at_k": mean("ndcg_at_k"),
        "mean_latency_ms": mean("latency_ms"),
        "stale_hit_rate": stale / retrieved if retrieved else 0.0,
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="JSON list of evaluated query rows")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("input must be a JSON list")
    payload = {"rows": rows, "summary": summarise(rows)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
