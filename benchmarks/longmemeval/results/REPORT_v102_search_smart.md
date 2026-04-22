# v1.0.2 search_smart — LongMemEval Oracle Report

**Date:** 2026-04-22
**Harness:** LongMemEval oracle, N=50, stratified sample (seed=42)
**Retriever candidates:** `search_v2 (no-expand)` · `search_expand` · `search_smart`
**Answer model:** `claude-haiku-4-5`, top_k=15
**Judge model:** `claude-haiku-4-5`

## Scores

| Retriever            | contains_match | LLM-judge |
|----------------------|---------------:|----------:|
| baseline (no-expand) |          68.0% |     84.0% |
| search_expand        |          66.0% |     (n/a) |
| **search_smart**     |      **66.0%** | **84.0%** |

## Per-category LLM-judge (baseline → smart)

| Category                   | Baseline | Smart  | Δ       |
|----------------------------|---------:|-------:|--------:|
| knowledge-update           |    87.5% |  87.5% |  +0.0%  |
| multi-session              |    69.2% |  69.2% |  +0.0%  |
| single-session-assistant   |    83.3% |  66.7% | **-16.7%** |
| single-session-preference  |    33.3% |  66.7% | **+33.3%** |
| single-session-user        |   100.0% | 100.0% |  +0.0%  |
| temporal-reasoning         |   100.0% | 100.0% |  +0.0%  |

**Flipped samples:** 4/50 (2 baseline-only right, 2 smart-only right).
All flips occurred in single-session categories where both retrievers
return the identical (and only) file — confirming the flips are
**LLM answer-generation variance**, not retrieval effects.

## Why search_smart does not move the oracle score

1. **Oracle sessions are pre-filtered** — `n_results ≈ n_sessions` for every
   sample (measured on all 50: average 2.26 files in, 2.26 out). Every
   retrieved file IS a relevant file, so there is nothing to cut with a
   score floor and nothing to add with query expansion.
2. **Average score gap between top-1 and top-2 is 0.11** — ranking is
   already well-separated; re-ranking keeps the same file order for 29/50
   samples and only shuffles low-score tails for the rest.
3. **Ceiling is the answer generator, not the retriever** — contains_match
   gap (68 → 84 under judge) is driven by format mismatches ("The Sugar
   Factory at Icon Park" vs "The Sugar Factory at Icon Park."), not by
   missing context. `search_smart` cannot fix prompt/answer shape.

## What `search_smart` still gives us

Even with no oracle delta, the redesign is strictly additive and shipped
in the v1.0.2 public API:

- `search_ranked(query, top_k, min_score)` — precision-first, no variant
  expansion, floors that never starve the caller on small corpora.
- `search_smart(query, top_k, date_hint)` — query-type dispatcher:
  - `count`/`how many` → `search_expand` + wider top_k (multi-session recall)
  - `temporal`/`when`/`how long` → `search_temporal` with optional
    `date_hint` (exact-match +0.15, window-decay boost)
  - `preference` → `search_expand` (recall-first)
  - `fact` → `search_ranked` (precision-first)
- `_v102_classify(query)` helper exposed for tests & debugging.

All 32 tests in `tests/test_v102_search.py` pass (20 prior + 12 new for
`search_ranked` / `search_smart` / `_v102_classify`).

## Honest verdict

**Per the task's release rule — "개선 없으면 배포 ❌" — v1.0.2 is NOT released.**

The code is kept in-tree (non-breaking, guarded by env var
`MK_SEARCH_MODE=smart` on the benchmark harness) and ready for the next
evaluation on a harder dataset (LongMemEval-s/m) where retrieval
actually has noise to filter.

### Expected lift conditions for `search_smart`

`search_smart` should help when **all** of these hold:

- `n_sessions >> n_relevant` (i.e. the retriever must actually filter)
- question type is identifiable (count/temporal dominate the mix)
- corpus contains dates parseable as YYYY-MM-DD (for temporal boost)

The oracle dataset satisfies none of these by construction. Re-run on
the `s` (500-session haystack) and `m` (5k-session haystack) splits to
validate.

## Result files

- `results/v102_smart_oracle_n50_20260422_0239.json` — raw run
- `results/v102_smart_oracle_n50_20260422_0239_judged.json` — LLM-judged
- Baseline for comparison: `results/baseline_oracle_n50_20260422_0152_judged.json`
