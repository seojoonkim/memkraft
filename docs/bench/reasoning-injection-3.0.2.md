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

## Expanded evidence collection

The expanded harness adds 28 deterministic tasks (24 transfer tasks across six
procedural families plus four unrelated abstention tasks), explicit dev/holdout
and easy/hard metadata, and frozen selective retrieval. The nine-artifact live
campaign completed on 2026-07-18 with 1,260 model calls and zero endpoint errors.
Its predeclared gate **failed**, so compact natural-language injection is not an
accepted product optimization. From the repository root, after setting the three
credential variables above, run these exact nine collection commands. Dev uses
seeds 42 and 43 with five repeats each (ten pairs per task after pooling); holdout
uses seed 42 and five repeats.

```bash
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison no-hint-vs-full --repeats 5 --seed 42 --timeout 120 --out /tmp/rb-dev-42-no-hint-vs-full.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison no-hint-vs-full --repeats 5 --seed 43 --timeout 120 --out /tmp/rb-dev-43-no-hint-vs-full.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison no-hint-vs-compact --repeats 5 --seed 42 --timeout 120 --out /tmp/rb-dev-42-no-hint-vs-compact.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison no-hint-vs-compact --repeats 5 --seed 43 --timeout 120 --out /tmp/rb-dev-43-no-hint-vs-compact.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison full-vs-compact --repeats 5 --seed 42 --timeout 120 --out /tmp/rb-dev-42-full-vs-compact.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split dev --comparison full-vs-compact --repeats 5 --seed 43 --timeout 120 --out /tmp/rb-dev-43-full-vs-compact.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split holdout --comparison no-hint-vs-full --repeats 5 --seed 42 --timeout 120 --holdout-ledger /tmp/rb-holdout-campaign.json --holdout-artifact no-hint-vs-full=/tmp/rb-holdout-42-no-hint-vs-full.json --holdout-artifact no-hint-vs-compact=/tmp/rb-holdout-42-no-hint-vs-compact.json --holdout-artifact full-vs-compact=/tmp/rb-holdout-42-full-vs-compact.json --out /tmp/rb-holdout-42-no-hint-vs-full.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split holdout --comparison no-hint-vs-compact --repeats 5 --seed 42 --timeout 120 --holdout-ledger /tmp/rb-holdout-campaign.json --holdout-artifact no-hint-vs-full=/tmp/rb-holdout-42-no-hint-vs-full.json --holdout-artifact no-hint-vs-compact=/tmp/rb-holdout-42-no-hint-vs-compact.json --holdout-artifact full-vs-compact=/tmp/rb-holdout-42-full-vs-compact.json --out /tmp/rb-holdout-42-no-hint-vs-compact.json
PYTHONPATH=src python benchmarks/reasoning_injection_ab.py --expanded --split holdout --comparison full-vs-compact --repeats 5 --seed 42 --timeout 120 --holdout-ledger /tmp/rb-holdout-campaign.json --holdout-artifact no-hint-vs-full=/tmp/rb-holdout-42-no-hint-vs-full.json --holdout-artifact no-hint-vs-compact=/tmp/rb-holdout-42-no-hint-vs-compact.json --holdout-artifact full-vs-compact=/tmp/rb-holdout-42-full-vs-compact.json --out /tmp/rb-holdout-42-full-vs-compact.json
```

The first holdout command atomically initializes one campaign containing all
three expected paths and frozen settings; each command reserves and completes
exactly one member. The persistent `flock` lock is crash-safe and bounded when
held by a live process. A justified full-campaign rerun requires a nonempty
`--holdout-rerun-reason 'provider outage'`, creating a new generation while
preserving campaign history. All three `--holdout-artifact` values for that rerun
must use new, generation-unique paths; reusing any prior generation's path is
rejected before reservation. Pending retries within one generation continue to
use that generation's preregistered paths.

Project exactly those nine artifacts, then evaluate the holdout-only gate:

```bash
PYTHONPATH=src python benchmarks/project_reasoning_injection_gate.py \
  /tmp/rb-dev-42-no-hint-vs-full.json /tmp/rb-dev-43-no-hint-vs-full.json \
  /tmp/rb-dev-42-no-hint-vs-compact.json /tmp/rb-dev-43-no-hint-vs-compact.json \
  /tmp/rb-dev-42-full-vs-compact.json /tmp/rb-dev-43-full-vs-compact.json \
  /tmp/rb-holdout-42-no-hint-vs-full.json /tmp/rb-holdout-42-no-hint-vs-compact.json \
  /tmp/rb-holdout-42-full-vs-compact.json --bootstrap-samples 20000 --bootstrap-seed 42 \
  --out /tmp/rb-expanded-gate-input.json
PYTHONPATH=src python benchmarks/gate_reasoning_injection.py \
  /tmp/rb-expanded-gate-input.json --out /tmp/rb-expanded-gate.json
```

The four general CIs resample only the 12 relevant procedural holdout tasks; the
hard CI uses the six hard holdout tasks. Dev is used only for family-sign
comparison, and family G only for abstention. Every easy-task slowdown above 8%
is non-passing; two or more above 10% are an explicit rejection diagnostic.
Prompt overhead is serialized as `compact_vs_no_hint_prompt_overhead_tokens`.

The projector and gate fail closed when required comparability metadata or
telemetry is missing or malformed. A CI containing zero is neutral; a directional
CI is measured evidence rather than an additional performance gate, except for
the separately predeclared hard-speed claim. Gate output prohibits a universal
speed claim.

### Expanded live result

The authenticated holdout campaign completed all three comparisons in one shared
generation. Projection used 20,000 task-cluster bootstrap samples. The gate result
was `FAIL` rather than malformed:

- holdout accuracy rejected: one compact paired loss on `f-holdout-hard`;
- dev also had compact losses, concentrated on `d-dev-hard`, plus one
  `f-dev-hard` loss;
- relevant holdout latency task CI: **-3.505% to +25.536%**;
- hard holdout latency task CI: **-10.616% to +35.139%**;
- relevant holdout reasoning-change task CI: **-3.831% to +27.673%**;
- maximum easy-task slowdown: **+32.362%**;
- maximum relevant per-task slowdown: **+75.731%**;
- compact prompt rendering did pass economy checks: **47.436% minimum reduction
  versus full**, with **45 tokens maximum overhead versus no hint**;
- selective behavior was correct: **12/12** relevant holdout tasks covered and
  **2/2** unrelated tasks abstained.

Reasoning-token change uses a bounded symmetric percentage for nonnegative token
counts: `(new - baseline) / max(new, baseline) * 100`, with `0 -> 0` defined as
zero. This keeps `0 -> positive` observable as a +100% regression without division
by zero. Raw malformed, negative, nonfinite, and unrepresentable telemetry fails
closed.

The supported conclusion is therefore negative but useful: compact rendering
reduces prompt overhead and retrieves selectively, but natural-language procedure
injection did not preserve exact accuracy or establish lower reasoning use or wall
latency on the expanded transfer set.

### Deterministic execution feasibility

Disposable spikes under `spikes/001-*`, `spikes/002-*`, and `spikes/003-*` test a
different mechanism: `retrieve -> validate provenance -> execute an allowlisted
exact grammar -> fallback`. They do not execute lesson prose and do not read the
benchmark's expected-answer functions at runtime.

The production API now implements that bounded mechanism additively as
`reasoning_procedure_ref()`, `reasoning_build_authorization()`, and
`reasoning_execute()`. Existing natural-language ReasoningBank recall/injection and
schema-v1 trajectories remain compatible. It executes only six versioned,
allowlisted exact grammars; lesson prose, dynamic code, shell, `eval`, and `exec`
are never execution inputs. Authorization binds canonical task identity, exact
trajectory bytes, and registry identity with a process-local HMAC seal. File reads
use a no-follow, directory-descriptor-anchored chain from the configured base to
`.memkraft/trajectories/<task>.jsonl`, so validation, hashing, and parsing operate
on bytes from the same descriptor. Malformed recall, provenance, authorization,
grammar, or safety bounds fail closed to exactly one fallback call.

On the frozen 28-case matrix, the production API produced **24 deterministic
executor routes and four fallbacks**, preserved **28/28 accuracy**, and reduced
observed fallback/model calls from 28 to 4 (**85.714%**). Local executor latency
across the 24 A-F cases had median **0.184 ms** and mean **0.193 ms** in the
isolated local run.

A separate live sequential campaign used the same requested model alias and
OpenAI-compatible endpoint settings as the earlier benchmark, with SDK retries
disabled. It first sent all 28 tasks directly to the provider, then ran all 28
through the product router:

- model-only baseline: **28 calls, 28/28 correct, zero endpoint errors**;
  total sequential wall **305.406 s**, median call **3.632 s**, p95 **52.640 s**,
  maximum **90.351 s**;
- production router: **24 executor + 4 fallback, 28/28 correct, zero endpoint
  errors**; total sequential wall **12.894 s**;
- observed model calls: **28 -> 4 (-85.714%)**;
- router executor median: **0.577 ms**; live fallback median: **3.432 s**;
- observed sequential campaign wall reduction: **95.778%**.

The wall reduction is descriptive evidence from one campaign, not a universal
latency claim. It exceeds the call reduction because the baseline had a large
provider tail. The exact grammar set does not establish paraphrase, noisy-store,
or broad semantic coverage. Arbitrary same-process code/key access, debugger or
fork compromise, and trust persistence across process restarts remain outside the
current threat model. Production focused verification finished at **112 passed**;
the full repository suite finished at **2,240 passed / 3 skipped**. Independent
review ended with **Critical 0 / Important 0**.

Evidence artifacts (credentials are not serialized):

- `benchmarks/results/reasoning-execution-product-local-28.json`
- `benchmarks/results/reasoning-execution-product-live-28.json`

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

A stronger product-level speed claim now requires:

1. replacement of rejected free-form hint injection with a production-grade,
   versioned deterministic procedure registry and durable authorization model;
2. live paired evaluation of executor routing versus no-hint and compact arms,
   including exact fallback behavior and end-to-end latency;
3. agentic coding/tool-use workloads with end-to-end success scoring;
4. model/provider replication and attested served-model identity where possible;
5. broader paraphrase and noisy-store coverage beyond the exact grammars; and
6. preregistered hierarchical analysis when expanding beyond the implemented
   task-cluster bootstrap.
