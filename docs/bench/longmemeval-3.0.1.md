# LongMemEval historical evidence — MemKraft 3.0.1 pre-final candidate

> **Status:** historical pre-final candidate evidence, generated 2026-07-14 at
> `HEAD` `1bef5b0` with a dirty worktree. This is a seeded `oracle` subset run,
> not a full LongMemEval score or clean final-release validation.

The run predates the final `compile_evidence_context(...)` public API/wiring,
validated-source-timestamp temporal latest/past/compare selection, numeric
evidence preservation, and strict provenance-bearing
`aggregate_numeric_evidence(...)`. Therefore none of the results below
validate those final features.

## Scope

- Dataset: local LongMemEval `oracle` fixture, **50 of 500** questions.
- Sampling: runner's deterministic stratified sampling, `seed=42`.
- System under test: dirty MemKraft 3.0.1 pre-final candidate at `HEAD` `1bef5b0`, `top_k=15`.
- Retrieval configuration: `MK_SEARCH_MODE=smart` by default; `MK_NO_EXPAND` unset; `MK_PREF_BOOST=1`; `MK_AGG_BOOST=1`; `MK_AGG_KEYWORD_PASS=1`; `MK_HYBRID_ALPHA` unused.
- Answer generation: OpenAI-compatible provider configuration alias `codex-lb`, harness adapter `litellm-vhh`, model `gpt-5.6-terra`, default harness temperature `0`.
- Semantic judge: same adapter/provider configuration and model, `gpt-5.6-terra`; this is a self-judge, not an independent-model evaluation.

## Historical results (pre-final candidate only)

- Execution errors: **0 / 50**
- Automatic exact match: **6.0%** (3 / 50)
- Automatic contains match: **68.0%** (34 / 50)
- Semantic self-judge accuracy: **96.0%** (48 / 50; 50 judged; 0 judge errors)
- End-to-end per-sample latency: p50 **2,899.741 ms**; p95 **6,951.518 ms**; min **1,097.256 ms**; max **9,206.981 ms**.

The end-to-end timer surrounds each `run_sample()` call. It includes sample ingestion, retrieval, prompt construction, local temporary-file work, and answer generation; it is an observed run latency, not a portable performance claim.

## Reproduction record

The benchmark was executed with the repository root as `/Users/gimseojun/memcraft` using:

```text
PYTHONPATH=src \
MK_LME_LLM_BACKEND=litellm-vhh \
MK_LME_LLM_MODEL=gpt-5.6-terra \
MODEL=gpt-5.6-terra \
TAG=memkraft_3_0_1_oracle_n50_seed42_codexlb \
python run.py 50 oracle
```

`MK_LME_LLM_BASE_URL` and `MK_LME_LLM_API_KEY` were supplied from the operator's local `codex-lb` provider configuration and are intentionally omitted. The semantic rejudge used the same non-secret backend/model settings plus `JUDGE_MODEL=gpt-5.6-terra`.

### Source and artifact identity

- Git `HEAD`: `1bef5b0c7b971bcb187dde5bce2429f091b775bf`
- Worktree state: dirty 3.0.1 release candidate. Before this evidence manifest was added, `git diff --binary` SHA-256 was `8cd6ccdc66c0f3e8fcf6a045397fe7b9812e6b0009329119993d6c2a46bc71b5` and `git status --porcelain` SHA-256 was `ab16cc94c41d0237d949d19e2ab25ee352bbdc386cc105d0a32b759745e845e9`.
- Runtime: CPython 3.11.14; macOS 15.6; arm64.
- Oracle dataset SHA-256: `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`.
- Unjudged result SHA-256: `bcdfc2e09915140097eecdf6f94c83a54566bf87e776833465cc59b003acbcaa`.
- Judged result SHA-256: `80ccd83b4162def0275835e7f01b29ae6287467e699e22c7c3b418370ca4c4b3`.

The full result JSON is intentionally ignored by `.gitignore` with LongMemEval
data/artifacts. It has no durable public URL. Do not claim independently
inspectable external-benchmark evidence or final-code validation from this
local artifact.

## Invalid later attempt and rerun status

A later 50-sample attempt produced `APIConnectionError` for **50 / 50** samples.
That result is invalid and excluded from all reported benchmark metrics and
release evidence. A final-code rerun is currently blocked because DNS for the
`litellm-vhh` endpoint fails; no replacement final-code external benchmark is
claimed.

## Interpretation limits

- This is an `oracle` subset (`N=50`), not a full-dataset result.
- The semantic score is self-judged by the answer-generation model and may be lenient. The automatic contains-match result is retained as a separate, deterministic metric.
- Approximate 95% Wilson intervals: contains match 54.2%–79.2%; self-judge 86.5%–98.9%.
- The run is tied to a dirty pre-final candidate worktree, not the 2026-07-17
  final tree or a final release commit. It cannot validate changes made after
  that candidate, including the final evidence-context, temporal, and numeric
  behavior.
