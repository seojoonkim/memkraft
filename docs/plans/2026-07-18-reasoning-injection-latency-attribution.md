# Reasoning Injection Latency Attribution Implementation Plan

> **For Hermes:** Implement each task with strict RED → GREEN → adjacent verification. Do not commit until the existing compact-injection work and this slice pass the combined review gate.

**Goal:** Add backward-compatible S/M/T/V/R wall-latency attribution to the ReasoningBank paired benchmark so later optimizations can be selected and causally evaluated.

**Architecture:** Keep instrumentation inside `benchmarks/reasoning_injection_ab.py`. Record monotonic half-open spans by phase, summarize raw phase durations and their interval union to avoid overlap double-counting, and add additive row/run telemetry without changing scoring, prompts, conditions, or existing summary fields. The current benchmark has no external tool or retry loop, so T and R remain explicit zeros rather than invented measurements.

**Tech Stack:** Python stdlib (`time.perf_counter`, dataclasses/typing), pytest, existing benchmark fake client.

---

### Task 1: Define overlap-safe attribution contract

**Objective:** Specify deterministic span union, phase totals, accounted wall ratio, and target status.

**Files:**
- Modify: `tests/test_benchmark_scripts.py`
- Modify: `benchmarks/reasoning_injection_ab.py`

**Steps:**
1. Add a failing test with overlapping S/M/T spans over a fixed total interval.
2. Assert raw phase totals, union-accounted time, overlap time, unaccounted time, and ratio.
3. Run the focused test and verify failure because the helper is absent.
4. Implement the smallest pure summarizer.
5. Run the focused test and verify pass.

### Task 2: Add row and artifact telemetry

**Objective:** Instrument startup/discovery, model calls, and strict verification while preserving existing artifact fields.

**Files:**
- Modify: `tests/test_benchmark_scripts.py`
- Modify: `benchmarks/reasoning_injection_ab.py`

**Steps:**
1. Extend the fake benchmark test to require per-row `phase_ms`, one model round trip per row, and top-level latency attribution with S/M/T/V/R keys.
2. Require `accounting_target=0.95`, additive schema fields, and no credential leakage.
3. Run the focused tests and verify expected RED failures.
4. Add span recording around client/setup/reasoning hint/prompt work (S), each provider call (M), and exact scoring (V).
5. Emit T=0 and R=0 explicitly because this runner performs neither external tool execution nor retries.
6. Summarize spans against total benchmark wall time and emit target status without failing the benchmark when attribution is incomplete.
7. Run focused tests and verify GREEN.

### Task 3: Regression and real pilot gate

**Objective:** Prove compatibility and determine whether at least 95% of real pilot wall time is attributable.

**Files:**
- No additional source files unless a failing test exposes a defect.

**Steps:**
1. Run benchmark, ReasoningBank injection, and Python 3.9 grammar tests.
2. Run the full pytest suite.
3. Run a one-repeat real paired pilot with the existing provider configuration.
4. Verify exact accuracy loss is zero, errors are zero, credentials are absent, and `accounted_wall_ratio >= 0.95`.
5. Inspect which phase explains the previous compact-token/latency mismatch; if one-repeat evidence is insufficient, report it as instrumentation readiness rather than a causal conclusion.

**Non-goals:** Hermes-wide tracing, OpenTelemetry, caching, warm runtimes, workflow compilation, changing the benchmark task set, or changing production ReasoningBank defaults.
