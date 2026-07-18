# Spike 001: validated ReasoningBank path executor

## Status and scope

**Disposable spike — not production code.** This directory tests one narrow question: given a retrieved ReasoningBank trajectory whose trusted provenance has already been mapped to an allowlisted `procedure_id`, can an exact family-specific parser bind task inputs and execute the procedure deterministically, while refusing everything else so the caller can fall back to an LLM?

**Verdict: VALIDATED for deterministic executor feasibility.** The frozen matrix executes all 24 supported A–F benchmark tasks with exact expected answers, and all four unsupported G tasks fall back. This says nothing about retrieval quality, provenance validation, live-model behavior, integration safety, or end-to-end latency. No model speedup is claimed.

## Trust model

The caller owns provenance verification and supplies `trusted=True` only after mapping a retrieved trajectory to an allowlisted immutable procedure ID. This spike does not inspect or execute trajectory text. It treats trajectory prose as data outside this API.

The executor applies two gates:

1. `trusted` must be true.
2. `procedure_id` must be one of exactly six IDs, and the entire task must match that ID's grammar.

Any failed gate, parse, domain check, or resource bound returns `ExecutionResult(status="fallback", answer=None, ...)`. An unknown ID omits the ID from the result; a known ID is retained for diagnostics. `trusted=False` always falls back, including for otherwise valid input.

## Allowlist and supported grammar

The punctuation, capitalization, spacing, and final period below are significant because parsing uses `re.fullmatch`:

- `A.inclusion_exclusion_sum`: `Sum positive integers below N divisible by d or d.` or the benchmark Oxford-comma form `d, d, or d.` Uses inclusion–exclusion and arithmetic-series formulas.
- `B.legendre_factorial_exponent`: `Find the trailing zeroes in N factorial.` (prime 5), or `Find the exponent of p in the prime factorization of N factorial.` Uses Legendre's formula.
- `C.shortest_grid_paths`: `Count shortest right/down paths across a m by n grid.` Uses a binomial coefficient.
- `D.divisor_count_prime_powers`: `Count positive divisors of p^e * p^e.` Multiplies `(e + 1)` terms.
- `E.sum_squares_or_cubes`: `Sum the squares from 1 through N.` or the corresponding `cubes` form. Uses exact closed forms.
- `F.modular_exponentiation`: `Compute a^b modulo m.` Uses Python's bounded-memory modular `pow`.

The implementation does not import `benchmarks.reasoning_tasks`, expected answers, or `answer_fn`. Only the matrix test imports `expanded_cases()`; it passes `case.task` to the implementation and uses `case.expected` solely as a test assertion.

## Safety constraints

- No `eval`, `exec`, shell, dynamic import, expected-answer lookup, or benchmark callback.
- ASCII decimal literals only, maximum 100 digits.
- Inputs are positive or nonnegative according to the procedure; modulus must exceed 1.
- At most eight explicit divisors/factors; duplicates are rejected.
- Declared B/D primes are verified by deterministic trial division and capped at 1,000,000.
- Grid sides are capped at 1,000.
- Malformed expressions, extra text, multiple problems, prompt-injection prefixes/suffixes, negative values, and cross-family procedure IDs fall back.

## TDD evidence

Tests were written before `executor.py` existed.

**RED command**

```text
python -m pytest -q spikes/001-reasoning-path-executor/test_executor.py
```

**RED summary (exact)**

```text
ERROR spikes/001-reasoning-path-executor/test_executor.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.05s
```

The collection error was `ModuleNotFoundError: No module named 'executor'`, proving the missing module/API made the initial contract red.

After the minimum implementation and one parser correction discovered by the matrix test:

**GREEN summary (exact)**

```text
.........................                                                [100%]
25 passed in 0.01s
```

**Static check**

```text
ruff check spikes/001-reasoning-path-executor
All checks passed!
```

## Measurable acceptance result

- Supported frozen A–F matrix: **24/24 executed with exact expected answers**.
- Unsupported frozen G matrix: **4/4 fell back**.
- Test suite: **25/25 passed**.
- Ruff: **passed**.

## Limitations and recommendation

This is deliberately brittle rather than linguistically flexible. Paraphrases, harmless whitespace changes, alternate list punctuation, signed integers, or new task forms fall back. Bounds are policy choices for a spike, not production-reviewed limits. Trial-division primality is practical only because declared primes are capped. Python big integers can still produce large results within accepted 100-digit inputs, although formulas avoid input-sized loops and A caps combinatorial terms.

**Recommendation:** retain the architecture for a production experiment only if procedure IDs come from an authenticated/versioned registry, parsers are versioned alongside frozen grammars, fallback is fail-closed, and integration adds observability plus resource controls. Validate retrieval/provenance and benchmark end-to-end latency separately; this spike establishes only that strict deterministic execution is feasible once trust and procedure selection are already established.
