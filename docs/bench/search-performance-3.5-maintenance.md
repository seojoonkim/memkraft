# Search performance maintenance audit

## Scope

This maintenance change measures the MemKraft 3.5 search core without result-cache hits, separates cold corpus-index construction from warm search, and preserves the existing ranking and result schema.

The implementation defers token-fallback snippet construction until after stable ranking and `top_k` selection. Exact-match and fuzzy snippets, scoring, candidate selection, deduplication, and ordering are unchanged. Last-access timestamp updates also avoid redundant file-stat calls while conservatively invalidating the read cache whenever a write may have reached disk.

## Accuracy

- Deterministic recall benchmark at 100, 1,000, and 3,000 documents: mean recall@20 `1.0`, minimum recall@20 `1.0` at every size.
- Memory Gym `search_recall` baseline, legacy, and hybrid gates: all passed with observed mean/min recall `1.0/1.0`.
- Contract tests require limited results to equal the unlimited result prefix, including snippets and scores.

## Performance evidence

The corrected scale harness calls `search(..., cache=False)`, records one cold index-build sample, and records warm unlimited and `top_k=20` samples separately.

Representative 50-iteration post-change warm medians:

- 100 documents: unlimited `2.067 ms`, top-k `1.925 ms`.
- 1,000 documents: unlimited `63.644 ms`, top-k `47.845 ms`.
- 3,000 documents: unlimited `171.305 ms`, top-k `145.190 ms`.

An earlier same-host 30-iteration paired run measured the 3,000-document top-k median at `145.612 ms` before deferred snippets and `137.293 ms` after, a `5.71%` descriptive improvement. The later 50-iteration run showed high host variance and only `0.29%` against that earlier baseline. Therefore this change is accepted for reduced unnecessary work and contract correctness, not as a claimed universal latency percentage.

Search latency remains dominated by full candidate scoring and corpus file reads for broad queries. Candidate pruning was not introduced because prior adversarial recall tests showed unacceptable ranking loss.

## Verification

- Focused search/cache/touch suite: `121 passed`.
- Full suite: `3,359 passed, 2 skipped`; two pre-existing environment/release checks failed before manifest registration and are audited separately.
- Independent frozen-diff review after the partial-write cache fix: blocker `0`, important `0`, suggestion `0`.
- Diff security scan: no hardcoded secret, shell injection, eval/exec, pickle, or formatted SQL matches.

## Project Memory Compiler status

The 3.5.0 Project Memory Compiler v0 Preview remains implemented and its focused contract/API/normalization/store plus Hermes integration suite passed `73/73`. This maintenance change does not alter Project Memory Compiler behavior.
