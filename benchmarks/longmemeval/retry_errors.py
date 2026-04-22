"""Re-run errored samples from a results file and patch them in-place."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import LongMemEvalHarness

results_file = sys.argv[1] if len(sys.argv) > 1 else None
if not results_file:
    import glob
    files = sorted(glob.glob("results/sonnet46_ms_v2*.json"))
    results_file = files[-1]

print(f"Patching: {results_file}")
with open(results_file) as f:
    data = json.load(f)

results = data["results"]
error_idx = [i for i, r in enumerate(results) if r.get("error") or r.get("prediction","") == ""]
print(f"Found {len(error_idx)} errors to retry")

# Load oracle for full sample data
with open("data/longmemeval_oracle.json") as f:
    oracle = json.load(f)
oracle_by_id = {s["question_id"]: s for s in oracle}

model = os.environ.get("MODEL", "minpeter/sonnet-4.6")
harness = LongMemEvalHarness(model=model, top_k=15, verbose=False)

for i in error_idx:
    r = results[i]
    qid = r["question_id"]
    sample = oracle_by_id.get(qid)
    if not sample:
        print(f"  [{qid}] NOT FOUND in oracle, skip")
        continue
    print(f"  Retrying [{qid}]...", flush=True)
    time.sleep(1)  # small backoff
    try:
        new_r = harness.run_sample(sample)
        results[i] = new_r
        pred = new_r.get("prediction","")[:80]
        print(f"    OK: {pred!r}")
    except Exception as e:
        print(f"    FAIL: {e}")

data["results"] = results
with open(results_file, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\nSaved: {results_file}")
