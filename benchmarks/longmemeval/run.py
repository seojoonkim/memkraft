"""
LongMemEval × MemKraft 베이스라인 실행.

사용법:
  python3 run.py [N] [dataset]
    N        : 샘플 수 (기본 50)
    dataset  : oracle | s   (기본 oracle - 가벼움)

예:
  python3 run.py 50 oracle
  python3 run.py 20 s
"""
from __future__ import annotations

import os
import sys
import json
import time
import datetime
import traceback

from harness import LongMemEvalHarness
from evaluator import score_results


HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILES = {
    "oracle": os.path.join(HERE, "data/longmemeval_oracle.json"),
    "s": os.path.join(HERE, "data/longmemeval_s.json"),
    "m": os.path.join(HERE, "data/longmemeval_m.json"),
}
RESULTS_DIR = os.path.join(HERE, "results")


def load_samples(dataset: str, n: int, stratified: bool = True, seed: int = 42) -> list[dict]:
    path = DATA_FILES[dataset]
    if dataset == "oracle":
        with open(path) as f:
            data = json.load(f)
    else:
        import ijson
        data = []
        with open(path, "rb") as f:
            parser = ijson.items(f, "item", use_float=True)
            for item in parser:
                data.append(item)

    if not stratified:
        return data[:n]

    # Stratified sampling by question_type (카테고리 비율 유지)
    import random
    from collections import defaultdict
    rng = random.Random(seed)
    by_type: dict = defaultdict(list)
    for s in data:
        by_type[s.get("question_type", "unknown")].append(s)
    total = len(data)
    out: list = []
    for qtype, items in by_type.items():
        quota = max(1, round(n * len(items) / total))
        rng.shuffle(items)
        out.extend(items[:quota])
    rng.shuffle(out)
    return out[:n]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    dataset = sys.argv[2] if len(sys.argv) > 2 else "oracle"

    if dataset not in DATA_FILES:
        print(f"Unknown dataset: {dataset}. Use one of {list(DATA_FILES)}")
        sys.exit(1)

    model = os.environ.get("MODEL", "claude-haiku-4-5")
    tag = os.environ.get("TAG", "baseline")

    print(f"LongMemEval × MemKraft")
    print(f"Dataset: {dataset}  N={n}")
    print(f"Model: {model}")
    print(f"Tag:   {tag}")
    # Show which LLM backend the harness will use — selected via
    # MK_LME_LLM_BACKEND (anthropic default | openai | openrouter | litellm-vhh).
    backend_env = os.environ.get("MK_LME_LLM_BACKEND", "anthropic")
    backend_model = os.environ.get("MK_LME_LLM_MODEL", "<backend default>")
    print(f"LLM:   backend={backend_env} model={backend_model}")
    print("-" * 60)

    print("Loading samples...", flush=True)
    t0 = time.time()
    samples = load_samples(dataset, n)
    print(f"  Loaded {len(samples)} samples in {time.time() - t0:.1f}s")

    harness = LongMemEvalHarness(model=model, top_k=15, verbose=False)
    results: list[dict] = []

    t_run0 = time.time()
    for i, sample in enumerate(samples):
        t_s = time.time()
        try:
            r = harness.run_sample(sample)
        except Exception as e:
            r = {
                "question_id": sample.get("question_id", "?"),
                "question": sample.get("question", ""),
                "answer": sample.get("answer", ""),
                "prediction": "",
                "question_type": sample.get("question_type", "unknown"),
                "error": f"{type(e).__name__}: {e}",
            }
            traceback.print_exc()
        results.append(r)
        dt = time.time() - t_s

        if (i + 1) % 5 == 0 or i + 1 == len(samples):
            partial = score_results(results)
            elapsed = time.time() - t_run0
            eta = (elapsed / (i + 1)) * (len(samples) - i - 1)
            print(
                f"[{i+1:>3}/{len(samples)}] {dt:5.1f}s | "
                f"EM={partial['exact_match']:.1%} "
                f"Contains={partial['contains_match']:.1%} "
                f"Abst={partial['abstention_rate']:.1%} | "
                f"elapsed={elapsed:.0f}s eta={eta:.0f}s",
                flush=True,
            )

    scores = score_results(results)
    print()
    print("=" * 60)
    print(f"📊 최종 결과 ({dataset}, N={len(results)})")
    print("=" * 60)
    print(f"Exact Match       : {scores['exact_match']:.1%}")
    print(f"Contains Match    : {scores['contains_match']:.1%}  ← 주 메트릭")
    print(f"Abstention Rate   : {scores['abstention_rate']:.1%}")
    print(f"Errors            : {scores['errors']}")
    print()
    print("카테고리별 (Contains Match):")
    for cat, v in sorted(scores["by_category"].items(), key=lambda x: -x[1]["total"]):
        print(f"  {cat:30s} {v['contains']:6.1%}  (EM={v['em']:.1%}, abst={v['abst']:.1%}, n={v['total']})")

    print()
    print("타이밍:")
    print(f"  ingest total : {harness.ingest_time_total:.1f}s")
    print(f"  search total : {harness.search_time_total:.1f}s")
    print(f"  llm total    : {harness.llm_time_total:.1f}s")
    print(f"  total run    : {time.time() - t_run0:.1f}s")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outfile = os.path.join(RESULTS_DIR, f"{tag}_{dataset}_n{len(results)}_{stamp}.json")
    with open(outfile, "w") as f:
        json.dump(
            {
                "meta": {
                    "dataset": dataset,
                    "n": len(results),
                    "model": harness.model,
                    "top_k": harness.top_k,
                    "timestamp": stamp,
                    "ingest_time_total": harness.ingest_time_total,
                    "search_time_total": harness.search_time_total,
                    "llm_time_total": harness.llm_time_total,
                },
                "scores": scores,
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n💾 저장: {outfile}")


if __name__ == "__main__":
    main()
