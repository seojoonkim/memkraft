# Spike 003: reasoning execution router

## Question

Can a standalone router use production MemKraft retrieval, validate local
trajectory provenance through spike 002, execute an allowlisted procedure, and
send unsupported or uncertain tasks to an injected model fallback while
accounting for the exact number of model calls?

## Design

`router.py` is deliberately independent of benchmark answer machinery.

1. `seed_trusted_procedures(mk)` writes exactly six successful A-F trajectories
   to the supplied MemKraft store. Their titles and lessons are frozen copies of
   the expanded benchmark seed text. Each trajectory has exactly one spike-002
   `procedure_ref`; none contains an expected answer. It returns a process-local
   HMAC-sealed `TrustedManifest` held outside the trajectory-store boundary.
2. `route_task` calls production `mk.reasoning_recall(task, top_k=1,
   status="success")`.
3. The top hit and required manifest are passed to spike 002's
   `execute_recalled_path`, which validates exact trajectory bytes, registry
   identity, and the current manifest HMAC before invoking spike
   001's exact-grammar executor.
4. A validated executable hit returns the executor answer with `model_calls=0`.
5. No hit, retrieval error, malformed hit, failed provenance, or executor
   rejection calls `fallback(task)` exactly once and returns `model_calls=1`.
   Only the original task is passed; recalled title/lesson/trajectory prose is
   never executed or included in the fallback input.
6. Fallback exceptions propagate after one call. Non-string fallback answers
   raise `TypeError`.

Every successful `RoutingResult` records the route, optional procedure and
retrieval score, elapsed route latency, model-call count, and reason.

## Strict TDD record

Tests were created before the router module.

**RED (exact):**

```text
ERROR spikes/003-reasoning-execution-router/test_router.py
ModuleNotFoundError: No module named 'router'
1 error in 0.07s
```

After the minimal implementation, one test assertion was corrected: checking
arbitrary answer substrings in raw JSON produced a false positive because
short numeric answers can occur in timestamps/digests. The contract now checks
that no trajectory record has an `expected` or `answer` field.

**GREEN for spike 003 (exact):**

```text
................                                                         [100%]
16 passed in 0.07s
```

**Combined spikes 001/002/003 (latest exact):**

```text
........................................................................ [ 80%]
..................                                                       [100%]
90 passed in 0.18s
```

**Ruff (final exact):**

```text
All checks passed!
```

## Frozen 28-case result

A real temporary MemKraft store was seeded and all cases from
`expanded_cases()` were routed. Expected values and the keyed fake fallback
exist only in the test/harness, not in production router code.

```text
{'total': 28, 'executor': 24, 'fallback': 4, 'model_calls': 4, 'baseline_model_calls': 28, 'call_reduction_pct': 85.71428571428571, 'accuracy': 1.0}
fallback_calls=4 answers_correct=28/28
```

- A-F: 24/24 exact answers, executor route, zero model calls.
- G: 4/4 exact fake-model answers, fallback route, exactly four total calls.
- Baseline: one model call per case = 28 calls.
- Routed harness: 4 calls, a reduction of 24/28 = 85.71428571428571%.

Tests additionally cover empty stores, retrieval exceptions, malformed hits,
forged references, missing/mutated manifests, post-authorization byte mutation,
symlink loops, fallback exactly-once behavior, fallback exceptions,
non-string fallback output, ignored trajectory prompt injection, accounting
invariants, and absence of `expanded_cases`, `.expected`, or `answer_fn` from
router code.

## Verdict and limits

**Feasible on the frozen matrix:** validated retrieval can route supported A-F
inputs to deterministic execution with zero model calls, while unsupported or
uncertain inputs route to a model. This gives exact 28-to-4 model-call reduction
without using expected answers in the router.

The manifest HMAC is process-local. Arbitrary code execution or memory/debugger
access inside the bridge process, trusted-code replacement, and persistence across
restarts are out of scope. This disposable spike does **not** establish live model quality, live/network
latency, production throughput, semantic robustness under paraphrase, broad
procedure coverage, retrieval quality on larger/noisier stores, or generalize
beyond the frozen exact-grammar matrix. Reported `latency_ms` is local per-route
timing only; no live latency claim is made. It also reuses spikes 001 and 002 as
local sibling modules and is not product packaging or integration code.
