"""Quick test on the 3 known multi-session failures."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import LongMemEvalHarness

with open("data/longmemeval_oracle.json") as f:
    oracle = json.load(f)

target_qids = ["bf659f65", "gpt4_2ba83207", "gpt4_15e38248"]
samples = [s for s in oracle if any(t in s["question_id"] for t in target_qids)]
print(f"Testing {len(samples)} samples")

model = os.environ.get("MODEL", "minpeter/sonnet-4.6")
harness = LongMemEvalHarness(model=model, top_k=15, verbose=False)

for s in samples:
    print(f"\n{'='*80}\nQID: {s['question_id']}\nQ: {s['question']}\nGold: {s['answer']}")
    t0 = time.time()
    r = harness.run_sample(s)
    dt = time.time() - t0
    print(f"Pred ({dt:.1f}s):\n{r.get('prediction','')}")
    print(f"ctx_chars: {r.get('context_used_chars')}")
