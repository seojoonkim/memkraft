# ReasoningBank injection paired A/B (MemKraft 3.0.2)

## Result

This benchmark provides **bounded evidence**, not a universal speed claim.
For six exact procedural tasks sent to an OpenAI-compatible endpoint using the
requested model alias `gpt-5.6-sol`, injecting an exact-task procedural lesson
through `reasoning_inject_for_task()` produced:

- observed accuracy: **60/60 control and 60/60 injected**; paired losses: **0**
- descriptive call-level paired median latency delta: **-281.6 ms (-6.85%)**
- descriptive call-level mean latency delta: **-1,083.9 ms**
- faster/slower calls: **35 / 25**
- descriptive reported reasoning-token mean delta: **-55.3 tokens**
- descriptive reported reasoning-token median delta: **-1 token**

The six unique tasks are the correct unit for generalization. At that level,
**3/6 tasks had lower median latency and 3/6 had higher median latency**. Benefits
were concentrated in the more deliberative modular-power, multiples-sum, and
lattice-path tasks. The benchmark therefore supports this narrow statement:

> An applicable compact prior procedure can reduce endpoint-reported reasoning
> and latency on some reasoning-heavy exact tasks without an observed error in
> this run.

It does **not** establish that ReasoningBank makes every request, every task, or
every model faster. No call-level p-value is used as inferential evidence because
repeated calls over six tasks are not 60 independent task samples.

## Design

- requested model alias: `gpt-5.6-sol`
- served model identity: not independently attested by the custom endpoint
- temperature: `0`
- requested reasoning effort: `medium`
- conditions: identical task prompt with ReasoningBank injection off/on
- task set: 6 deterministic procedural integer problems
- repetitions: 10 per task across two independently randomized schedules
- schedule seeds: 42 and 43
- total: 6 unique tasks, 60 pairs, 120 model calls
- ordering: condition order randomized inside each pair
- memory: isolated temporary MemKraft store seeded with exact-task lessons
- retrieval: production `reasoning_inject_for_task()` API used by Hermes
- scoring: `prediction.strip() == expected`
- endpoint errors: 0

The exact-task seeding deliberately tests the best-case mechanism—reuse when an
applicable prior conclusion exists. It does not test transfer from loosely similar
tasks or noisy real-world memories.

## Reproduction

Set credentials outside the repository:

```bash
export MK_RB_BENCH_BASE_URL='https://example.invalid/v1'
export MK_RB_BENCH_API_KEY='***'
export MK_RB_BENCH_MODEL='gpt-5.6-sol'
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py \
  --repeats 5 --seed 42 \
  --out benchmarks/results/reasoning-injection-ab-seed42.json
```

Repeat with seed 43, then aggregate without collapsing duplicate pair IDs:

```bash
PYTHONPATH=src python benchmarks/analyze_reasoning_injection_ab.py \
  benchmarks/results/reasoning-injection-ab-seed42.json \
  benchmarks/results/reasoning-injection-ab-seed43.json \
  --out benchmarks/results/reasoning-injection-ab-summary.json
```

Evidence artifacts:

- `benchmarks/results/reasoning-injection-ab-gpt-5.6-sol-n30.json`
- `benchmarks/results/reasoning-injection-ab-gpt-5.6-sol-n30-seed43.json`
- `benchmarks/results/reasoning-injection-ab-gpt-5.6-sol-n60-summary.json`

The runner records only the parsed endpoint hostname and exception class names.
New artifacts record the requested model separately from any response model string.
They never serialize the API key.

## Token trade-off

The hint adds prompt context. Call-level paired median total-token delta was
**+237 tokens**, while the median endpoint-reported completion/reasoning-token
delta was **-1 token**. This is not a total-token optimization in the tested prompt
format. It is a possible exchange of reusable input context for less model-side
work and lower latency on tasks where the retrieved procedure is directly useful.
Endpoint-reported reasoning tokens are provider telemetry, not an independently
validated measure of hidden computation.

## Full vs compact follow-up and latency attribution

A separate one-repeat pilot compared the same six tasks with full and compact
ReasoningBank rendering. This is a **12-call diagnostic pilot**, not an inferential
speed benchmark. The OpenAI client used `max_retries=0`, so each row represents
exactly one provider attempt and the explicit retry phase is absent.

- exact accuracy: **6/6 full and 6/6 compact**; paired losses: **0**
- total-token paired median delta, compact minus full: **-129.5 tokens**
- reasoning-token paired median delta: **-12.5 tokens**; bootstrap interval includes zero
- latency paired median delta: **+950.2 ms (+17.1%)**
- faster/slower pairs: **2 / 4**; bootstrap interval includes zero
- accounted wall ratio: **0.999997**, above the predefined 0.95 instrumentation target
- phase totals: S **61.956 ms**, M **178,268.497 ms**, T **0 ms**, V **0.023 ms**, R **0 ms**
- unaccounted wall time: **0.507 ms**

The supported conclusion is narrow: compact rendering reduced total tokens in this
pilot without an observed exact-answer loss, but it did **not** demonstrate a wall-
latency improvement. Model/provider time (M) accounted for approximately 99.97% of
the measured wall time, so prompt construction and local verification are not the
next meaningful latency bottlenecks. Endpoint-reported token telemetry and this
small task set do not establish a universal token or reasoning reduction.

Evidence artifact:

- `benchmarks/results/reasoning-injection-full-vs-compact-attributed-pilot.json`

The runner preserves schema version 1 and adds latency fields additively. It records
S/M/T/V/R phase durations, interval-union accounted time to avoid overlap double-
counting, and one model round trip per row under the retry-disabled client contract.

## Remaining evidence needed

A stronger product-level speed claim requires:

1. substantially more **unique** tasks rather than more repeats of the same six;
2. similar-but-not-identical prior tasks, not only exact-task oracle lessons;
3. agentic coding/tool-use workloads with end-to-end success scoring;
4. model/provider replication and attested served-model identity where possible;
5. task-clustered or hierarchical analysis defined before data collection.
