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
import fcntl
import json
import math
import os
import random
import re
import statistics
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from memkraft import MemKraft, __version__ as memkraft_version

try:
    from benchmarks.reasoning_tasks import ReasoningCase, expanded_cases, seed_lessons
except ModuleNotFoundError:  # direct script execution
    from reasoning_tasks import ReasoningCase, expanded_cases, seed_lessons


EXPANDED_RETRIEVAL = {"k": 1, "min_score": 0.1, "max_chars": 900, "per_item_chars": 240}


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


def seed_expanded_reasoning(mk: Any, cases: list[ReasoningCase]) -> dict[str, str]:
    """Seed exactly one dev-derived procedural trajectory per A-F family."""
    lessons = seed_lessons(cases)
    titles = {
        "A": "Sum integers below a limit divisible by listed divisors",
        "B": "Find zeroes or prime exponent in factorial",
        "C": "Count shortest paths across grid",
        "D": "Count positive divisors",
        "E": "Sum integer squares or cubes",
        "F": "Compute powers modulo an integer",
    }
    for family in sorted(lessons):
        task_id = f"bench-expanded-seed-{family.lower()}"
        mk.trajectory_start(
            task_id,
            title=titles[family],
            tags=[],
        )
        mk.trajectory_complete(task_id, status="success", lesson=lessons[family])
    return lessons


def reserve_holdout_run(
    ledger_path: Path, artifact_path: Path, *, comparison: Optional[str] = None,
    expected_artifacts: Optional[dict[str, Path]] = None, seed: int = 42,
    repeats: int = 5, timeout_seconds: float = 120.0,
    rerun_reason: Optional[str] = None, lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Atomically reserve one member of a preregistered holdout campaign."""
    ledger_path, artifact_path = Path(ledger_path), Path(artifact_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    lock_path.touch(exist_ok=True)
    temporary = None
    lock_file = lock_path.open("a+")
    deadline = time.monotonic() + lock_timeout
    while True:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                lock_file.close()
                raise TimeoutError(f"holdout ledger lock timeout after {lock_timeout}s")
            time.sleep(0.01)
    try:
        prior_data = None
        expected: dict[str, str] = {}
        campaign_mode = comparison is not None or expected_artifacts is not None
        if campaign_mode:
            if comparison not in {"no-hint-vs-full", "no-hint-vs-compact", "full-vs-compact"}:
                raise ValueError("valid holdout comparison required")
            expected_artifacts = expected_artifacts or {}
            expected_names = {"no-hint-vs-full", "no-hint-vs-compact", "full-vs-compact"}
            if set(expected_artifacts) != expected_names:
                raise ValueError("campaign must preregister exactly three comparisons")
            expected = {name: str(Path(value).resolve()) for name, value in expected_artifacts.items()}
            if expected[comparison] != str(artifact_path.resolve()):
                raise ValueError("artifact path does not match campaign preregistration")
        if ledger_path.exists():
            try:
                prior_data = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid holdout ledger schema: {error}") from error
            if (not isinstance(prior_data, dict)
                    or prior_data.get("schema_version") not in {1, 2}
                    or (prior_data.get("schema_version") == 1 and
                        (not isinstance(prior_data.get("runs"), list) or not prior_data["runs"]
                         or any(not isinstance(run, dict) or not isinstance(run.get("current"), dict)
                                for run in prior_data["runs"])))
                    or (prior_data.get("schema_version") == 2 and
                        not isinstance(prior_data.get("generations"), list))):
                raise ValueError("invalid holdout ledger schema")
        if campaign_mode:
            generations = prior_data.get("generations", []) if prior_data else []
            if prior_data and prior_data.get("schema_version") != 2:
                raise ValueError("invalid holdout campaign ledger schema")
            current_generation = generations[-1] if generations else None
            frozen = {"seed": seed, "repeats": repeats, "timeout_seconds": timeout_seconds,
                      "temperature": 0, "reasoning_effort": "medium", "max_tokens_requested": 512,
                      "sdk_max_retries": 0, "retrieval": dict(EXPANDED_RETRIEVAL),
                      "selective_policy": "declared-family-transfer"}
            if current_generation and current_generation["expected_artifacts"] == expected and \
                    current_generation["frozen"] == frozen and \
                    current_generation["members"][comparison]["status"] == "pending":
                generation = current_generation
            else:
                if current_generation and not str(rerun_reason or "").strip():
                    raise RuntimeError("holdout campaign member already reserved; provide a rerun reason")
                prior_paths = {
                    artifact_path
                    for prior_generation in generations
                    for artifact_path in prior_generation["expected_artifacts"].values()
                }
                collisions = sorted(set(expected.values()) & prior_paths)
                if collisions:
                    raise ValueError(
                        "new holdout campaign generation requires generation-unique paths; "
                        f"paths already belong to a prior generation: {', '.join(collisions)}"
                    )
                generation = {"campaign_id": uuid.uuid4().hex, "generation": len(generations) + 1,
                    "reason": str(rerun_reason or "").strip() or None,
                    "expected_artifacts": expected, "frozen": frozen,
                    "members": {name: {"status": "pending", "artifact_identity": uuid.uuid4().hex}
                                for name in sorted(expected)}}
                generations.append(generation)
            member = generation["members"][comparison]
            if member["status"] != "pending":
                raise RuntimeError("holdout campaign member already reserved")
            member["status"] = "reserved"
            member["timestamp"] = datetime.now(timezone.utc).isoformat()
            data = {"schema_version": 2, "generations": generations}
            entry = {"campaign_id": generation["campaign_id"], "generation": generation["generation"],
                     "comparison": comparison, "artifact_path": expected[comparison],
                     "artifact_identity": member["artifact_identity"], "status": "reserved",
                     "expected_artifacts": expected, "frozen": frozen}
        else:
            if prior_data and not str(rerun_reason or "").strip():
                raise RuntimeError("holdout ledger already contains a run; provide a rerun reason")
            current = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifact_path": str(artifact_path.resolve()),
            "artifact_identity": uuid.uuid4().hex,
        }
            prior = prior_data["runs"][-1]["current"] if prior_data else None
            entry = {"prior": prior, "current": current,
                 "reason": str(rerun_reason or "").strip() or None}
            data = {"schema_version": 1,
                "runs": list(prior_data["runs"]) if prior_data else []}
            data["runs"].append(entry)
        temporary = ledger_path.with_name(f".{ledger_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, ledger_path)
        temporary = None
        return entry
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _lock_holdout_ledger(ledger_path: Path, lock_timeout: float):
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    lock_path.touch(exist_ok=True)
    lock_file = lock_path.open("a+")
    deadline = time.monotonic() + lock_timeout
    while True:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except BlockingIOError:
            if time.monotonic() >= deadline:
                lock_file.close()
                raise TimeoutError(f"holdout ledger lock timeout after {lock_timeout}s")
            time.sleep(0.01)


def _matching_generation(data: Any, holdout_run: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 2 \
            or not isinstance(data.get("generations"), list):
        raise ValueError("invalid holdout campaign ledger schema")
    matches = [generation for generation in data["generations"]
               if generation.get("campaign_id") == holdout_run.get("campaign_id")
               and generation.get("generation") == holdout_run.get("generation")]
    if len(matches) != 1:
        raise RuntimeError("campaign reservation generation not found")
    return matches[0]


def complete_holdout_run(ledger_path: Path, holdout_run: dict[str, Any], *,
                         lock_timeout: float = 5.0) -> dict[str, Any]:
    """Synchronize artifacts, then commit a matching reservation as completed."""
    ledger_path = Path(ledger_path)
    lock_file = _lock_holdout_ledger(ledger_path, lock_timeout)
    temporaries: list[Path] = []
    backups: dict[Path, Path] = {}
    replaced_artifacts: list[Path] = []
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        generation = _matching_generation(data, holdout_run)
        comparison = holdout_run.get("comparison")
        if comparison not in generation.get("members", {}):
            raise RuntimeError("campaign member is not the matching reservation")
        member = generation["members"][comparison]
        if member.get("status") != "reserved" or \
                member.get("artifact_identity") != holdout_run.get("artifact_identity"):
            raise RuntimeError("campaign member is not the matching reservation")
        if generation.get("expected_artifacts") != holdout_run.get("expected_artifacts") or \
                generation.get("frozen") != holdout_run.get("frozen") or \
                generation["expected_artifacts"].get(comparison) != holdout_run.get("artifact_path"):
            raise RuntimeError("campaign reservation contract mismatch")

        # Nothing durable is changed until every existing campaign artifact has
        # parsed, authenticated, and been successfully serialized to a temp file.
        member["status"] = "completed"
        staged_artifacts: list[tuple[Path, Path]] = []
        for artifact_comparison, artifact_name in generation["expected_artifacts"].items():
            artifact_path = Path(artifact_name)
            if not artifact_path.is_file():
                if artifact_comparison == comparison:
                    raise ValueError(f"current campaign artifact is missing: {artifact_path}")
                continue
            try:
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid campaign artifact: {artifact_path}") from error
            run = artifact.get("settings", {}).get("holdout_run")
            expected_member = generation["members"][artifact_comparison]
            if (not isinstance(run, dict)
                    or run.get("campaign_id") != generation["campaign_id"]
                    or run.get("generation") != generation["generation"]
                    or run.get("comparison") != artifact_comparison
                    or Path(run.get("artifact_path", "")).resolve() != artifact_path.resolve()
                    or run.get("artifact_identity") != expected_member["artifact_identity"]
                    or run.get("expected_artifacts") != generation["expected_artifacts"]
                    or run.get("frozen") != generation["frozen"]):
                raise RuntimeError("existing campaign artifact identity mismatch")
            run["status"] = expected_member["status"]
            run["campaign_members"] = generation["members"]
            artifact_tmp = artifact_path.with_name(f".{artifact_path.name}.{uuid.uuid4().hex}.tmp")
            temporaries.append(artifact_tmp)
            artifact_tmp.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
            staged_artifacts.append((artifact_tmp, artifact_path))

        ledger_tmp = ledger_path.with_name(f".{ledger_path.name}.{uuid.uuid4().hex}.tmp")
        temporaries.append(ledger_tmp)
        ledger_tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for _, artifact_path in staged_artifacts:
            backup = artifact_path.with_name(f".{artifact_path.name}.{uuid.uuid4().hex}.bak")
            backup.write_bytes(artifact_path.read_bytes())
            backups[artifact_path] = backup
        try:
            for artifact_tmp, artifact_path in staged_artifacts:
                os.replace(artifact_tmp, artifact_path)
                temporaries.remove(artifact_tmp)
                replaced_artifacts.append(artifact_path)
            # Ledger commit is last, after every artifact replacement succeeds.
            os.replace(ledger_tmp, ledger_path)
            temporaries.remove(ledger_tmp)
        except Exception:
            for artifact_path in reversed(replaced_artifacts):
                os.replace(backups[artifact_path], artifact_path)
                backups.pop(artifact_path)
            raise
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
    result = dict(holdout_run)
    result["status"] = "completed"
    result["campaign_members"] = generation["members"]
    return result


def fail_holdout_run(ledger_path: Path, holdout_run: dict[str, Any], *, errors: int,
                     failure_kind: Optional[str] = None, message_class: Optional[str] = None,
                     lock_timeout: float = 5.0) -> dict[str, Any]:
    """Audit a failed attempt and return its member to pending for same-generation retry."""
    ledger_path = Path(ledger_path)
    lock_file = _lock_holdout_ledger(ledger_path, lock_timeout)
    temporary = None
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        generation = _matching_generation(data, holdout_run)
        member = generation.get("members", {}).get(holdout_run.get("comparison"), {})
        if member.get("status") != "reserved" or \
                member.get("artifact_identity") != holdout_run.get("artifact_identity"):
            raise RuntimeError("campaign member is not the matching reservation")
        failure = {"timestamp": datetime.now(timezone.utc).isoformat(), "errors": errors}
        if failure_kind is not None:
            failure["failure_kind"] = failure_kind
        if message_class is not None:
            failure["message_class"] = message_class
        member.setdefault("failures", []).append(failure)
        member["status"] = "pending"
        temporary = ledger_path.with_name(f".{ledger_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, ledger_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
    result = dict(holdout_run)
    result["status"] = "failed"
    result["attempt_errors"] = errors
    return result


def preflight_benchmark(*, repeats: int, timeout: float, out: Path,
                        expanded: bool, split: str,
                        holdout_ledger: Optional[Path] = None) -> dict[str, Any]:
    """Validate deterministic local requirements before a holdout reservation."""
    if repeats <= 0:
        raise ValueError("repeats must be > 0")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if expanded and split not in {"dev", "holdout"}:
        raise ValueError("split must be dev or holdout")
    out = Path(out)
    if out.exists() and out.is_dir():
        raise ValueError("output path must not be a directory")
    if holdout_ledger is not None and out.resolve() == Path(holdout_ledger).resolve():
        raise ValueError("output and holdout ledger paths must differ")
    base_url = os.environ.get("MK_RB_BENCH_BASE_URL", "").strip()
    api_key = os.environ.get("MK_RB_BENCH_API_KEY", "").strip()
    model = os.environ.get("MK_RB_BENCH_MODEL", "").strip()
    if not base_url or not api_key or not model:
        raise RuntimeError("Set MK_RB_BENCH_BASE_URL, MK_RB_BENCH_API_KEY, and MK_RB_BENCH_MODEL")
    parsed = urlsplit(base_url)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("benchmark base URL is malformed") from error
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise ValueError("benchmark base URL must be HTTP(S), have a hostname, and contain no userinfo")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
    return {"model": model, "parsed_endpoint": parsed, "client": client}


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
    expanded: bool = False,
    split: str = "holdout",
    holdout_run: Optional[dict[str, Any]] = None,
    preflight_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    benchmark_started = time.perf_counter()
    phase_spans: list[dict[str, Union[str, float]]] = []
    comparison_styles = {
        "no-hint-vs-full": ("none", "full"),
        "no-hint-vs-compact": ("none", "compact"),
        "full-vs-compact": ("full", "compact"),
    }
    if comparison not in comparison_styles:
        raise ValueError(f"unsupported comparison: {comparison}")
    control_style, injected_style = comparison_styles[comparison]

    preflight = preflight_context or preflight_benchmark(
        repeats=repeats, timeout=timeout, out=out, expanded=expanded, split=split
    )
    model, client = preflight["model"], preflight["client"]
    parsed_endpoint = preflight["parsed_endpoint"]
    cases: Any = (
        [case for case in expanded_cases() if case.split == split]
        if expanded else benchmark_cases()
    )
    rows: list[dict[str, Any]] = []

    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="memkraft-reasoning-ab-") as tmp:
        mk: Any = MemKraft(base_dir=tmp)
        if expanded:
            seed_expanded_reasoning(mk, expanded_cases())
        else:
            seed_reasoning(mk, cases)
        schedule = [(case, repeat) for repeat in range(repeats) for case in cases]
        rng.shuffle(schedule)
        phase_spans.append({"phase": "S", "start": benchmark_started, "end": time.perf_counter()})
        for case, repeat in schedule:
            hint_started = time.perf_counter()
            if expanded:
                condition_hints = {}
                for condition, style in (("control", control_style),
                                         ("injected", injected_style)):
                    if style == "none":
                        condition_hints[condition] = ("", {
                            "family": case.family, "style": style,
                            "retrieval_attempted": False, "hint_emitted": False,
                            "abstained": False,
                            "retrieval": dict(EXPANDED_RETRIEVAL),
                        })
                    else:
                        hint, hint_meta = mk.reasoning_inject_for_task(
                            case.task, style=style, return_metadata=True,
                            **EXPANDED_RETRIEVAL,
                        )
                        if case.expects_injection and not hint:
                            raise RuntimeError(f"No {style} reasoning hint generated for {case.case_id}")
                        if not case.expects_injection and hint:
                            raise RuntimeError(f"Unexpected reasoning hint generated for {case.case_id}")
                        leak_checked_hint = re.sub(r"score=[0-9.]+", "score=<score>", hint)
                        if case.expected in leak_checked_hint or any(
                            catalog_case.case_id in hint for catalog_case in expanded_cases()
                        ):
                            raise RuntimeError(f"Rendered hint leakage for {case.case_id}")
                        hint_meta["abstained"] = not bool(hint)
                        hint_meta["retrieval_attempted"] = True
                        hint_meta["hint_emitted"] = bool(hint)
                        condition_hints[condition] = (hint, hint_meta)
            elif comparison == "no-hint-vs-full":
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
                    if expanded and (not isinstance(response_model, str) or not response_model.strip()):
                        raise RuntimeError("expanded evidence requires response model identity")
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
                    "task": case.task,
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
                    **({
                        "family": case.family,
                        "difficulty": case.difficulty,
                        "split": case.split,
                        "expects_injection": case.expects_injection,
                        "abstained": bool(condition_hint_meta["abstained"]),
                        "retrieval_attempted": bool(condition_hint_meta["retrieval_attempted"]),
                        "hint_emitted": bool(condition_hint_meta["hint_emitted"]),
                    } if expanded else {}),
                })

        benchmark_ended = time.perf_counter()

    latency_attribution = summarize_phase_spans(
        phase_spans,
        total_start=benchmark_started,
        total_end=benchmark_ended,
    )
    artifact = {
        "schema_version": 2 if expanded else 1,
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
            **({
                "expanded": True,
                "split": split,
                "retrieval": dict(EXPANDED_RETRIEVAL),
                "selective_policy": "declared-family-transfer",
                **({"holdout_run": holdout_run} if holdout_run else {}),
            } if expanded else {}),
        },
        "cases": [
            ({key: value for key, value in asdict(case).items() if key != "answer_fn"}
             if expanded else asdict(case))
            for case in cases
        ],
        "summary": paired_summary(rows),
        "latency_attribution": latency_attribution,
        "errors": sum(bool(row["error"]) for row in rows),
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(out, artifact)
    return artifact


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Replace a JSON artifact atomically without exposing a partial write."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--comparison",
        choices=("no-hint-vs-full", "no-hint-vs-compact", "full-vs-compact"),
        default="no-hint-vs-full",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--split", choices=("dev", "holdout"), default="holdout")
    parser.add_argument("--holdout-ledger", type=Path)
    parser.add_argument("--holdout-rerun-reason")
    parser.add_argument("--holdout-artifact", action="append", default=[],
                        metavar="COMPARISON=PATH")
    args = parser.parse_args()
    if args.expanded and args.split == "holdout" and args.holdout_ledger is None:
        parser.error("expanded holdout requires --holdout-ledger PATH")
    holdout_run = None
    preflight_context = None
    if args.expanded and args.split == "holdout":
        preflight_context = preflight_benchmark(
            repeats=args.repeats, timeout=args.timeout, out=args.out,
            expanded=args.expanded, split=args.split, holdout_ledger=args.holdout_ledger
        )
        try:
            expected_artifacts = {name: Path(path) for name, path in
                                  (item.split("=", 1) for item in args.holdout_artifact)}
        except ValueError:
            parser.error("--holdout-artifact must be COMPARISON=PATH")
        holdout_run = reserve_holdout_run(
            args.holdout_ledger, args.out, comparison=args.comparison,
            expected_artifacts=expected_artifacts, seed=args.seed, repeats=args.repeats,
            timeout_seconds=args.timeout, rerun_reason=args.holdout_rerun_reason
        )
    try:
        artifact = run_benchmark(
            repeats=args.repeats,
            seed=args.seed,
            timeout=args.timeout,
            out=args.out,
            comparison=args.comparison,
            expanded=args.expanded,
            split=args.split,
            holdout_run=holdout_run,
            preflight_context=preflight_context,
        )
    except Exception as error:
        if holdout_run is not None:
            fail_holdout_run(
                args.holdout_ledger, holdout_run, errors=1,
                failure_kind="benchmark_exception", message_class=type(error).__name__,
            )
        raise
    if holdout_run is not None:
        if artifact["errors"] > 0:
            result = fail_holdout_run(
                args.holdout_ledger, holdout_run, errors=artifact["errors"]
            )
            artifact["settings"]["holdout_run"] = result
            _write_json_atomic(args.out, artifact)
        else:
            try:
                complete_holdout_run(args.holdout_ledger, holdout_run)
            except Exception as error:
                fail_holdout_run(
                    args.holdout_ledger, holdout_run, errors=1,
                    failure_kind="completion_exception", message_class=type(error).__name__,
                )
                raise
    print(json.dumps({"out": str(args.out), "summary": artifact["summary"], "errors": artifact["errors"]}, ensure_ascii=False, indent=2))
    return 0 if artifact["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
