# MemKraft v3 Baseline: Memory Gym Milestone 0

This baseline freezes the minimal Memory Gym vertical slice for the existing `search_recall` benchmark.

## Scenario

- Name: `search_recall`
- Adapter: `benchmarks.gym.scenarios.run_scenario("search_recall", sizes=[20], top_k=5)`
- Underlying benchmark: `benchmarks/search_recall_bench.py`

## Current gate

The initial gate is intentionally strict because the candidate path is the current baseline search path:

- `min_mean_recall_at_k`: `1.0`
- `min_min_recall_at_k`: `1.0`

## Reproduction

```bash
python benchmarks/gym/run.py --scenario search_recall --sizes 20 --top-k 5 --out /tmp/gym.json --gate
```

Expected result: exit code `0`, JSON written to `/tmp/gym.json`, and `gate.passed == true`.
