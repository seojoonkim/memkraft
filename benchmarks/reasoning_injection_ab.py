"""Paired LLM benchmark for ReasoningBank task-context injection.

Measures whether compact prior-task lessons reduce latency or token usage without
hurting exact-answer accuracy. The benchmark uses an isolated temporary
ReasoningBank and the same ``reasoning_inject_for_task`` API used by Hermes.

Required environment variables:
  MK_RB_BENCH_BASE_URL     OpenAI-compatible /v1 endpoint
  MK_RB_BENCH_API_KEY      API key (never written to artifacts)
  MK_RB_BENCH_MODEL        model id

Example:
  python benchmarks/reasoning_injection_ab.py --repeats 3 --out result.json
"""
import argparse
import json
import math
import os
import random
import statistics
import tempfile
import time
from urllib.parse import urlsplit
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from memkraft import MemKraft, __version__ as memkraft_version


@dataclass(frozen=True)
class Case:
    case_id: str
    task: str
    expected: str
    lesson: str


def benchmark_cases() -> list[Case]:
    """Deterministic exact-answer tasks with reusable procedural lessons."""
    return [
        Case(
            "multiples-sum",
            "Find the sum of every positive integer below 10,000,000 that is divisible by 3 or 5.",
            str(sum(range(3, 10_000_000, 3)) + sum(range(5, 10_000_000, 5)) - sum(range(15, 10_000_000, 15))),
            "Use inclusion-exclusion. For divisor d below limit L, let m=floor((L-1)/d) and sum=d*m*(m+1)/2; add d=3 and d=5, then subtract d=15.",
        ),
        Case(
            "factorial-zeros",
            "How many trailing zeroes are in 100,000 factorial?",
            str(sum(100_000 // (5**k) for k in range(1, 9))),
            "Trailing zeroes of n! equal floor(n/5)+floor(n/25)+... until the quotient is zero; no factorial expansion is needed.",
        ),
        Case(
            "lattice-paths",
            "How many shortest paths are there from one corner to the opposite corner of a 30 by 30 square grid when moves are only right or down?",
            str(math.comb(60, 30)),
            "A shortest path has 60 moves with exactly 30 right moves, so the count is the binomial coefficient C(60,30).",
        ),
        Case(
            "divisor-count",
            "How many positive divisors does 2^12 * 3^7 * 5^4 have?",
            str((12 + 1) * (7 + 1) * (4 + 1)),
            "For prime factorization product p_i^a_i, the positive-divisor count is the product of (a_i+1).",
        ),
        Case(
            "squares-sum",
            "Find the exact sum of the squares of all integers from 1 through 100,000 inclusive.",
            str(100_000 * 100_001 * 200_001 // 6),
            "Use the closed form 1^2+...+n^2=n(n+1)(2n+1)/6 and perform exact integer arithmetic.",
        ),
        Case(
            "modular-power",
            "Compute the exact remainder when 7^22222 is divided by 1,000,003.",
            str(pow(7, 22_222, 1_000_003)),
            "Use modular exponentiation by repeated squaring, reducing modulo 1,000,003 after every multiply and square.",
        ),
    ]


def percentile(values: Iterable[float], q: float) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def summarise(values: Iterable[float]) -> dict[str, Union[float, int]]:
    vals = [float(v) for v in values]
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 3) if vals else 0.0,
        "median": round(statistics.median(vals), 3) if vals else 0.0,
        "p95": round(percentile(vals, 0.95), 3) if vals else 0.0,
        "min": round(min(vals), 3) if vals else 0.0,
        "max": round(max(vals), 3) if vals else 0.0,
    }


def exact_sign_test_two_sided(values: Iterable[float]) -> float:
    """Return an exact two-sided sign-test p-value, ignoring ties."""
    vals = [float(value) for value in values if float(value) != 0.0]
    if not vals:
        return 1.0
    smaller_side = min(sum(value < 0 for value in vals), sum(value > 0 for value in vals))
    tail = sum(math.comb(len(vals), i) for i in range(smaller_side + 1)) / (2 ** len(vals))
    return min(1.0, 2.0 * tail)


def bootstrap_median_ci(
    values: Iterable[float], *, seed: int = 42, samples: int = 20_000
) -> list[float]:
    """Deterministic percentile-bootstrap 95% CI for the paired median."""
    vals = [float(value) for value in values]
    if not vals:
        return [0.0, 0.0]
    rng = random.Random(seed)
    medians = sorted(statistics.median(rng.choices(vals, k=len(vals))) for _ in range(samples))
    return [round(medians[int(0.025 * samples)], 3), round(medians[int(0.975 * samples)], 3)]


def extract_usage(response: Any) -> dict[str, Optional[int]]:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        "reasoning_tokens": reasoning,
    }


def score_exact(text: str, expected: str) -> bool:
    return str(text).strip() == expected


def summarize_phase_spans(
    spans: Iterable[dict[str, Any]],
    *,
    total_start: float,
    total_end: float,
    accounting_target: float = 0.95,
) -> dict[str, Any]:
    """Summarize S/M/T/V/R spans without double-counting overlap."""
    phases = ("S", "M", "T", "V", "R")
    phase_seconds = {phase: 0.0 for phase in phases}
    intervals = []
    for span in spans:
        phase = span.get("phase")
        if phase not in phase_seconds:
            continue
        start = max(total_start, float(span["start"]))
        end = min(total_end, float(span["end"]))
        if end <= start:
            continue
        phase_seconds[phase] += end - start
        intervals.append((start, end))

    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    total_seconds = max(0.0, total_end - total_start)
    accounted_seconds = sum(end - start for start, end in merged)
    raw_seconds = sum(phase_seconds.values())
    ratio = accounted_seconds / total_seconds if total_seconds else 0.0
    return {
        "phase_ms": {
            phase: round(seconds * 1000.0, 3)
            for phase, seconds in phase_seconds.items()
        },
        "total_wall_ms": round(total_seconds * 1000.0, 3),
        "accounted_union_ms": round(accounted_seconds * 1000.0, 3),
        "overlap_ms": round(max(0.0, raw_seconds - accounted_seconds) * 1000.0, 3),
        "unaccounted_ms": round(max(0.0, total_seconds - accounted_seconds) * 1000.0, 3),
        "accounted_wall_ratio": round(ratio, 6),
        "accounting_target": accounting_target,
        "accounting_target_met": ratio >= accounting_target,
    }


def seed_reasoning(mk: Any, cases: list[Case]) -> None:
    for case in cases:
        task_id = f"bench-{case.case_id}"
        mk.trajectory_start(task_id, title=case.task, tags=["reasoning-injection-bench", case.case_id])
        mk.trajectory_complete(task_id, status="success", lesson=case.lesson)


def build_prompt(case: Case, hint: str = "") -> str:
    prompt = (
        "Solve the following problem accurately. Return only the exact integer answer, "
        "with no commas, explanation, units, or markdown.\n\n"
        f"Problem: {case.task}"
    )
    if hint:
        prompt += "\n\n" + hint
    return prompt


def paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pair = by_pair.setdefault(row["pair_id"], {})
        if row["condition"] in pair:
            raise ValueError(
                f"duplicate condition {row['condition']!r} for pair {row['pair_id']!r}"
            )
        pair[row["condition"]] = row
    complete = [pair for pair in by_pair.values() if set(pair) == {"control", "injected"}]
    latency_deltas = [p["injected"]["latency_ms"] - p["control"]["latency_ms"] for p in complete]
    token_deltas = [
        p["injected"]["usage"]["total_tokens"] - p["control"]["usage"]["total_tokens"]
        for p in complete
        if p["injected"]["usage"]["total_tokens"] is not None
        and p["control"]["usage"]["total_tokens"] is not None
    ]
    reasoning_deltas = [
        p["injected"]["usage"]["reasoning_tokens"] - p["control"]["usage"]["reasoning_tokens"]
        for p in complete
        if p["injected"]["usage"]["reasoning_tokens"] is not None
        and p["control"]["usage"]["reasoning_tokens"] is not None
    ]
    control_latency = [p["control"]["latency_ms"] for p in complete]
    injected_latency = [p["injected"]["latency_ms"] for p in complete]
    control_correct = sum(bool(p["control"]["correct"]) for p in complete)
    injected_correct = sum(bool(p["injected"]["correct"]) for p in complete)
    median_control = statistics.median(control_latency) if control_latency else 0.0
    median_delta = statistics.median(latency_deltas) if latency_deltas else 0.0
    return {
        "complete_pairs": len(complete),
        "accuracy": {
            "control": round(control_correct / len(complete), 4) if complete else 0.0,
            "injected": round(injected_correct / len(complete), 4) if complete else 0.0,
            "paired_losses": sum(p["control"]["correct"] and not p["injected"]["correct"] for p in complete),
            "paired_gains": sum(not p["control"]["correct"] and p["injected"]["correct"] for p in complete),
        },
        "latency_ms": {
            "control": summarise(control_latency),
            "injected": summarise(injected_latency),
            "paired_delta_injected_minus_control": summarise(latency_deltas),
            "median_change_pct": round(100.0 * median_delta / median_control, 3) if median_control else 0.0,
            "pairs_faster": sum(d < 0 for d in latency_deltas),
            "pairs_slower": sum(d > 0 for d in latency_deltas),
            "paired_median_bootstrap_95_ci": bootstrap_median_ci(latency_deltas),
            "exact_sign_test_two_sided_p": round(exact_sign_test_two_sided(latency_deltas), 6),
        },
        "total_tokens_paired_delta_injected_minus_control": summarise(token_deltas),
        "reasoning_tokens_paired_delta_injected_minus_control": summarise(reasoning_deltas),
        "reasoning_tokens_observable": bool(reasoning_deltas) and any(
            (p["control"]["usage"]["reasoning_tokens"] or 0) > 0
            or (p["injected"]["usage"]["reasoning_tokens"] or 0) > 0
            for p in complete
        ),
        "reasoning_tokens_paired_median_bootstrap_95_ci": bootstrap_median_ci(reasoning_deltas),
        "reasoning_tokens_exact_sign_test_two_sided_p": round(
            exact_sign_test_two_sided(reasoning_deltas), 6
        ),
    }


def run_benchmark(
    *,
    repeats: int,
    seed: int,
    timeout: float,
    out: Path,
    comparison: str = "no-hint-vs-full",
) -> dict[str, Any]:
    from openai import OpenAI

    benchmark_started = time.perf_counter()
    phase_spans: list[dict[str, Union[str, float]]] = []
    comparison_styles = {
        "no-hint-vs-full": ("none", "full"),
        "full-vs-compact": ("full", "compact"),
    }
    if comparison not in comparison_styles:
        raise ValueError(f"unsupported comparison: {comparison}")
    control_style, injected_style = comparison_styles[comparison]

    base_url = os.environ.get("MK_RB_BENCH_BASE_URL", "").strip()
    api_key = os.environ.get("MK_RB_BENCH_API_KEY", "").strip()
    model = os.environ.get("MK_RB_BENCH_MODEL", "").strip()
    if not base_url or not api_key or not model:
        raise RuntimeError("Set MK_RB_BENCH_BASE_URL, MK_RB_BENCH_API_KEY, and MK_RB_BENCH_MODEL")
    if repeats <= 0:
        raise ValueError("repeats must be > 0")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
    parsed_endpoint = urlsplit(base_url)
    if parsed_endpoint.username or parsed_endpoint.password or not parsed_endpoint.hostname:
        raise ValueError("benchmark base URL must have a hostname and must not contain userinfo")
    cases = benchmark_cases()
    rows: list[dict[str, Any]] = []
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="memkraft-reasoning-ab-") as tmp:
        mk: Any = MemKraft(base_dir=tmp)
        seed_reasoning(mk, cases)
        schedule = [(case, repeat) for repeat in range(repeats) for case in cases]
        rng.shuffle(schedule)
        phase_spans.append({"phase": "S", "start": benchmark_started, "end": time.perf_counter()})
        for case, repeat in schedule:
            hint_started = time.perf_counter()
            if comparison == "no-hint-vs-full":
                hint, hint_meta = mk.reasoning_inject_for_task(
                    case.task, k=3, max_chars=900, per_item_chars=240, return_metadata=True
                )
                if not hint:
                    raise RuntimeError(f"No reasoning hint generated for {case.case_id}")
                condition_hints = {
                    "control": ("", None),
                    "injected": (hint, hint_meta),
                }
            else:
                condition_hints = {}
                for condition, style in (
                    ("control", control_style),
                    ("injected", injected_style),
                ):
                    hint, hint_meta = mk.reasoning_inject_for_task(
                        case.task,
                        k=3,
                        max_chars=900,
                        per_item_chars=240,
                        return_metadata=True,
                        style=style,
                    )
                    if not hint:
                        raise RuntimeError(
                            f"No {style} reasoning hint generated for {case.case_id}"
                        )
                    condition_hints[condition] = (hint, hint_meta)
            phase_spans.append({"phase": "S", "start": hint_started, "end": time.perf_counter()})
            conditions = ["control", "injected"]
            if rng.random() < 0.5:
                conditions.reverse()
            pair_id = f"{case.case_id}:{repeat}"
            for order, condition in enumerate(conditions):
                row_spans: list[dict[str, Union[str, float]]] = []
                row_started = time.perf_counter()
                condition_hint, condition_hint_meta = condition_hints[condition]
                prompt = build_prompt(case, condition_hint)
                prompt_ended = time.perf_counter()
                prompt_span = {"phase": "S", "start": row_started, "end": prompt_ended}
                row_spans.append(prompt_span)
                phase_spans.append(prompt_span)
                started = time.perf_counter()
                error = ""
                text = ""
                response_model = None
                usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "reasoning_tokens": None}
                model_started = time.perf_counter()
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0,
                        max_tokens=512,
                        extra_body={"reasoning": {"effort": "medium"}},
                    )
                    text = response.choices[0].message.content or ""
                    response_model = getattr(response, "model", None)
                    usage = extract_usage(response)
                except Exception as error_value:
                    error = type(error_value).__name__
                model_ended = time.perf_counter()
                model_span = {"phase": "M", "start": model_started, "end": model_ended}
                row_spans.append(model_span)
                phase_spans.append(model_span)
                latency_ms = (time.perf_counter() - started) * 1000.0
                verify_started = time.perf_counter()
                correct = score_exact(text, case.expected) if not error else False
                verify_ended = time.perf_counter()
                verify_span = {"phase": "V", "start": verify_started, "end": verify_ended}
                row_spans.append(verify_span)
                phase_spans.append(verify_span)
                row_attribution = summarize_phase_spans(
                    row_spans,
                    total_start=row_started,
                    total_end=verify_ended,
                )
                rows.append({
                    "pair_id": pair_id,
                    "case_id": case.case_id,
                    "repeat": repeat,
                    "order": order,
                    "condition": condition,
                    "expected": case.expected,
                    "prediction": text.strip(),
                    "correct": correct,
                    "latency_ms": round(latency_ms, 3),
                    "phase_ms": row_attribution["phase_ms"],
                    "model_round_trips": 1,
                    "prompt_chars": len(prompt),
                    "hint_chars": len(condition_hint),
                    "hint_metadata": condition_hint_meta,
                    "usage": usage,
                    "response_model": response_model,
                    "error": error,
                })

        benchmark_ended = time.perf_counter()

    latency_attribution = summarize_phase_spans(
        phase_spans,
        total_start=benchmark_started,
        total_end=benchmark_ended,
    )
    artifact = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "memkraft_version": memkraft_version,
        "requested_model": model,
        "response_models": sorted({row["response_model"] for row in rows if row["response_model"]}),
        "endpoint_host": parsed_endpoint.hostname,
        "settings": {
            "repeats": repeats,
            "seed": seed,
            "temperature": 0,
            "reasoning_effort": "medium",
            "max_tokens_requested": 512,
            "timeout_seconds": timeout,
            "sdk_max_retries": 0,
            "comparison": comparison,
            "control_style": control_style,
            "injected_style": injected_style,
        },
        "cases": [asdict(case) for case in cases],
        "summary": paired_summary(rows),
        "latency_attribution": latency_attribution,
        "errors": sum(bool(row["error"]) for row in rows),
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--comparison",
        choices=("no-hint-vs-full", "full-vs-compact"),
        default="no-hint-vs-full",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = run_benchmark(
        repeats=args.repeats,
        seed=args.seed,
        timeout=args.timeout,
        out=args.out,
        comparison=args.comparison,
    )
    print(json.dumps({"out": str(args.out), "summary": artifact["summary"], "errors": artifact["errors"]}, ensure_ascii=False, indent=2))
    return 0 if artifact["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
