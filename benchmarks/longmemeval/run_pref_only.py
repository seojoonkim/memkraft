"""
Run only single-session-preference samples from oracle dataset.

Usage:
  python3 run_pref_only.py [N]           # N=30 by default (all)
  MK_PREF_PROMPT=0 MK_PREF_BOOST=0 python3 run_pref_only.py 30  # disable improvements (baseline)
  python3 run_pref_only.py 30            # with improvements (default)
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
DATA_FILE = os.path.join(HERE, "data/longmemeval_oracle.json")
RESULTS_DIR = os.path.join(HERE, "results")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    tag = os.environ.get("TAG", "pref_only")
    model = os.environ.get("MODEL", "claude-haiku-4-5")

    pref_on = os.environ.get("MK_PREF_PROMPT", "1") != "0"
    print(f"LongMemEval × MemKraft — preference-only")
    print(f"N={n}  Model={model}  Tag={tag}")
    print(f"MK_PREF_PROMPT={'ON' if pref_on else 'OFF'}  MK_PREF_BOOST={os.environ.get('MK_PREF_BOOST', '1')}")
    print("-" * 60)

    with open(DATA_FILE) as f:
        data = json.load(f)
    pref = [s for s in data if s.get("question_type") == "single-session-preference"]
    samples = pref[:n]
    print(f"Using {len(samples)}/{len(pref)} preference samples")

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
            elapsed = time.time() - t_run0
            eta = (elapsed / (i + 1)) * (len(samples) - i - 1)
            print(f"[{i+1:>3}/{len(samples)}] {dt:5.1f}s | elapsed={elapsed:.0f}s eta={eta:.0f}s", flush=True)

    scores = score_results(results)
    print()
    print("=" * 60)
    print(f"📊 최종 결과 (preference only, N={len(results)})")
    print("=" * 60)
    print(f"Contains Match: {scores['contains_match']:.1%}")
    print(f"Errors: {scores['errors']}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outfile = os.path.join(RESULTS_DIR, f"{tag}_pref_n{len(results)}_{stamp}.json")
    with open(outfile, "w") as f:
        json.dump({"meta": {"n": len(results), "model": harness.model, "timestamp": stamp, "pref_prompt": pref_on}, "scores": scores, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"💾 {outfile}")


if __name__ == "__main__":
    main()
