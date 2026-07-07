# MemKraft v3 Baseline: Memory Gym

This baseline freezes the Memory Gym vertical slice for the existing `search_recall` benchmark and records the first v3 candidate policy: conservative hybrid retrieval.

## Scenario

- Name: `search_recall`
- Adapter: `benchmarks.gym.scenarios.run_scenario("search_recall", sizes=[20], top_k=5)`
- Underlying benchmark: `benchmarks/search_recall_bench.py`

## Baseline candidate

The baseline candidate is the current production search path.

```bash
PYTHONPATH=src python3 benchmarks/gym/run.py \
  --scenario search_recall \
  --sizes 20,100 \
  --top-k 5 \
  --candidate baseline \
  --out /tmp/memkraft-gym-baseline.json \
  --gate
```

Gate:

- `min_mean_recall_at_k`: `1.0`
- `min_min_recall_at_k`: `1.0`

Expected result: exit code `0`, JSON written to the output path, and `gate.passed == true`.

## Hybrid candidate scaffold

Memory Gym can also evaluate the opt-in dense hybrid retriever:

```bash
PYTHONPATH=src python3 benchmarks/gym/run.py \
  --scenario search_recall \
  --sizes 20,100,300 \
  --top-k 5 \
  --candidate hybrid \
  --hybrid-alpha 0.025 \
  --out /tmp/memkraft-gym-hybrid-conservative.json \
  --gate \
  --min-mean-recall-at-k 1.0 \
  --min-min-recall-at-k 1.0
```

`--hybrid-alpha` is intentionally conservative. A sweep on the current synthetic/adversarial `search_recall` scenario showed that even-mix hybrid (`alpha=0.5`) can reduce lexical recall sharply, while `alpha=0.025` preserves baseline recall through 20/100/300 document runs.

Observed with `sentence-transformers` installed in the Hermes venv:

- 20 docs: mean/min recall@5 `1.0 / 1.0`; candidate p50 `13.814ms`; baseline p50 `0.818ms`
- 100 docs: mean/min recall@5 `1.0 / 1.0`; candidate p50 `17.843ms`; baseline p50 `3.874ms`
- 300 docs: mean/min recall@5 `1.0 / 1.0`; candidate p50 `18.938ms`; baseline p50 `7.4ms`

Important implementation note: dense semantic results are absolute paths from the embedding index, while BM25 results may be MemKraft-relative paths. `search_hybrid` must fuse by canonical path identity and emit base-dir-relative paths where possible; otherwise Memory Gym sees duplicate document identities and recall is artificially depressed.

## Interpretation

Milestone 1 does **not** make hybrid the default retriever. It adds the measurement path and fixes identity fusion so future retrieval policies can be compared safely under Gym gates.
