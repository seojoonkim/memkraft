"""
LongMemEval v5 — temperature=0 + majority vote.

3회 실행 × 50 oracle 샘플 → 각 question별 prediction 다수결 → LLM-judge 채점.

환경변수:
  MODEL                 (e.g. minpeter/sonnet-4.6)
  ANTHROPIC_API_KEY
  ANTHROPIC_BASE_URL    (e.g. https://llm.vhh.sh)
  JUDGE_MODEL           (optional; default = MODEL)
  RUNS                  (default 3)
  N                     (default 50)

사용:
  python3 run_majority_vote.py
"""
from __future__ import annotations

import os
import sys
import json
import glob
import time
import datetime
import collections
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")


def run_single(run_idx: int, tag_prefix: str, n: int, dataset: str) -> str:
    """Run run.py once with TAG=f'{tag_prefix}_run{run_idx}'. Returns result file path."""
    tag = f"{tag_prefix}_run{run_idx}"
    env = os.environ.copy()
    env["TAG"] = tag
    env["TEMPERATURE"] = "0"
    # Ensure the child run.py sees the API config we set.
    print(f"\n▶️  Run {run_idx}/{os.environ.get('RUNS', 3)}  tag={tag}")
    t0 = time.time()
    proc = subprocess.run(
        ["python3", "run.py", str(n), dataset],
        cwd=HERE,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"run.py failed for {tag} (rc={proc.returncode})")
    elapsed = time.time() - t0

    # find latest file matching this tag
    pattern = os.path.join(RESULTS_DIR, f"{tag}_{dataset}_n{n}_*.json")
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise RuntimeError(f"No result file for tag={tag} matching {pattern}")
    # exclude _judged suffix if any
    matches = [m for m in matches if not m.endswith("_judged.json")]
    result_path = matches[-1]
    print(f"   ✓ {elapsed:.0f}s → {os.path.basename(result_path)}")
    return result_path


def aggregate_majority(result_paths: list[str], tag_prefix: str, dataset: str, n: int) -> str:
    """Load all runs, compute majority-vote prediction per question_id, write merged file."""
    runs: list[list[dict]] = []
    for p in result_paths:
        with open(p) as f:
            d = json.load(f)
        runs.append(d["results"])

    # sanity: collect qids
    qids_per_run = [[r["question_id"] for r in run] for run in runs]
    # all runs should have same qids (deterministic sampling via seed=42)
    base_qids = qids_per_run[0]
    for i, qs in enumerate(qids_per_run[1:], 2):
        if set(qs) != set(base_qids):
            print(f"⚠️  run {i} qid set differs from run 1 "
                  f"(extra={set(qs)-set(base_qids)}, missing={set(base_qids)-set(qs)})")

    # map qid -> list of result dicts (one per run)
    by_qid: dict[str, list[dict]] = collections.defaultdict(list)
    for run in runs:
        for r in run:
            by_qid[r["question_id"]].append(r)

    merged: list[dict] = []
    vote_stats = {"unanimous": 0, "majority": 0, "tie": 0}

    for qid in base_qids:
        entries = by_qid.get(qid, [])
        if not entries:
            continue
        preds = [e.get("prediction", "") for e in entries]

        # Bucket identical predictions. For open-ended text, exact string
        # match is too strict → we fall back to "first" if no ties,
        # but since temperature=0 we expect most to be identical.
        counter = collections.Counter(preds)
        top_pred, top_count = counter.most_common(1)[0]

        if top_count == len(preds):
            vote_stats["unanimous"] += 1
            votes_meta = "unanimous"
        elif top_count > len(preds) / 2:
            vote_stats["majority"] += 1
            votes_meta = f"majority({top_count}/{len(preds)})"
        else:
            vote_stats["tie"] += 1
            votes_meta = f"tie({top_count}/{len(preds)}) - picked first most_common"

        base = dict(entries[0])  # keep metadata from run 1
        base["prediction"] = top_pred
        base["_majority_vote"] = {
            "n_runs": len(entries),
            "top_count": top_count,
            "decision": votes_meta,
            "all_predictions_first120": [p[:120] for p in preds],
        }
        merged.append(base)

    print("\n📊 Majority-vote stats:")
    print(f"   unanimous : {vote_stats['unanimous']}")
    print(f"   majority  : {vote_stats['majority']}")
    print(f"   tie       : {vote_stats['tie']}")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(
        RESULTS_DIR,
        f"{tag_prefix}_majority_{dataset}_n{n}_{stamp}.json",
    )
    out = {
        "meta": {
            "dataset": dataset,
            "n": n,
            "model": os.environ.get("MODEL", "unknown"),
            "runs": len(runs),
            "source_files": [os.path.basename(p) for p in result_paths],
            "vote_stats": vote_stats,
            "temperature": 0,
            "timestamp": stamp,
        },
        "results": merged,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"💾 Saved: {out_path}")
    return out_path


def main():
    n = int(os.environ.get("N", "50"))
    runs = int(os.environ.get("RUNS", "3"))
    dataset = os.environ.get("DATASET", "oracle")
    tag_prefix = os.environ.get("TAG_PREFIX", "v5")

    model = os.environ.get("MODEL", "claude-haiku-4-5")
    print("=" * 60)
    print("LongMemEval v5 — temperature=0 + majority vote")
    print("=" * 60)
    print(f"Model    : {model}")
    print(f"Dataset  : {dataset}  N={n}")
    print(f"Runs     : {runs}")
    print(f"Tag      : {tag_prefix}")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        print(f"Base URL : {base_url}")
    print("=" * 60)

    # 1) Run N times
    result_paths: list[str] = []
    for i in range(1, runs + 1):
        p = run_single(i, tag_prefix, n, dataset)
        result_paths.append(p)

    # 2) Aggregate majority vote
    merged_path = aggregate_majority(result_paths, tag_prefix, dataset, n)

    # 3) LLM-judge on merged file
    print("\n" + "=" * 60)
    print("⚖️  Running LLM-judge on majority-vote results")
    print("=" * 60)
    env = os.environ.copy()
    proc = subprocess.run(
        ["python3", "llm_judge.py", merged_path],
        cwd=HERE,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if proc.returncode != 0:
        print(f"⚠️  llm_judge failed (rc={proc.returncode})")
        sys.exit(proc.returncode)

    judged_path = merged_path.replace(".json", "_judged.json")
    print(f"\n✅ Final judged file: {judged_path}")

    # 4) quick accuracy summary
    with open(judged_path) as f:
        jd = json.load(f)
    correct = sum(1 for r in jd["results"] if r.get("llm_judge") is True)
    wrong = sum(1 for r in jd["results"] if r.get("llm_judge") is False)
    errors = sum(1 for r in jd["results"] if r.get("llm_judge") is None)
    total = len(jd["results"])
    acc = correct / total * 100 if total else 0.0

    print("\n" + "=" * 60)
    print(f"🏁 v5 final: {correct}/{total} = {acc:.1f}%  (wrong={wrong}, errors={errors})")
    print("=" * 60)
    # show wrong qids for easy inspection
    print("Wrong qids:")
    for r in jd["results"]:
        if r.get("llm_judge") is False:
            print(f"  - {r['question_id']} | {r.get('question_type','?')} | A={str(r.get('answer',''))[:80]}")


if __name__ == "__main__":
    main()
