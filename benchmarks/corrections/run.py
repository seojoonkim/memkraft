#!/usr/bin/env python3
"""Frozen deterministic S7 correction-learning benchmark and release gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from memkraft import MemKraft  # noqa: E402
from memkraft.correction_policy import compile_corrections  # noqa: E402


CASES_PATH = Path(__file__).with_name("cases_v1.json")
CASES_SHA256 = "95f933be0dd0c19a572999f44fa99c4e4e90e5ef238b1174148f23562fbbb9a0"
WARM_P95_BOUND_MS = 50.0
TWO_HOST_NOT_READY = {
    "status": "not_ready",
    "evidence": None,
    "reason": "single-host deterministic benchmark cannot establish two-host evidence",
}


def _load_cases() -> Dict[str, Any]:
    raw = CASES_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CASES_SHA256:
        raise ValueError(
            "frozen cases sha256 mismatch: expected %s, got %s"
            % (CASES_SHA256, digest)
        )
    fixture = json.loads(raw.decode("utf-8"))
    if fixture.get("schema_version") != 1 or fixture.get("frozen") is not True:
        raise ValueError("cases fixture is not frozen schema version 1")
    return fixture


def _execute_recurrence_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Exercise the persisted lifecycle and return its projected outcome evidence."""
    correction_id, source = next(iter(case["policies"].items()))
    revision_id = source["active_revision_id"]
    revision = source["revisions"][revision_id]
    context = case["context"]
    now = "2026-08-16T10:00:00Z"
    envelope = {
        "scope": source["scope"],
        "task_ref": source.get("task_ref"),
        "task_class": source.get("task_class"),
    }
    with tempfile.TemporaryDirectory(prefix="memkraft-s7-") as base_dir:
        store = MemKraft(base_dir=base_dir)
        store.init(verbose=False)
        store.correction_capture(
            correction_id,
            revision["user_utterance"],
            revision["agent_interpretation"],
            now=now,
            topic_key=source.get("topic_key"),
            required_evaluations=["replay"],
            **envelope,
        )
        store.correction_set_status(
            correction_id, "captured", "under_evaluation", now=now, **envelope)
        store.correction_record_evaluation(
            correction_id, revision_id, "replay", "pass", now=now,
            evaluated_scope=source["scope"],
            **envelope,
        )
        store.correction_set_status(
            correction_id, "under_evaluation", "promoted", now=now,
            promoted_revision_id=revision_id,
            **envelope,
        )
        store.correction_activate(correction_id, revision_id, now=now, **envelope)
        actual = store.compile_corrections(
            case["budget"],
            task_ref=context.get("task_ref"),
            task_class=context.get("task_class"),
            explicit_topics=context.get("explicit_topics", []),
        )
        for replay in range(2):
            store.correction_record_outcome(
                correction_id, revision_id, "complied", now=now,
                evidence_refs=["benchmark://s7/replay/%d" % (replay + 1)],
                **envelope,
            )
        projection = store.correction_project(now=now)
        tallies = projection["policies"][correction_id]["revisions"][revision_id][
            "outcome_tallies"]
    return {"actual": actual, "outcome_tallies": tallies}


def _execute_case(case: Dict[str, Any]) -> Dict[str, Any]:
    context = case["context"]
    recurrence = case["capability"] == "recurrence_prevention"
    persisted = _execute_recurrence_case(case) if recurrence else None
    actual = persisted["actual"] if persisted else compile_corrections(
            case["policies"], case["budget"],
            task_ref=context.get("task_ref"),
            task_class=context.get("task_class"),
            explicit_topics=context.get("explicit_topics", []))
    observed = {
        "selected_ids": actual["selected_ids"],
        "omitted": actual["omitted"],
        "conflicts": actual["conflicts"],
    }
    expected = {key: case["expect"][key] for key in observed}

    if persisted:
        observed["outcome_tallies"] = persisted["outcome_tallies"]
        expected["outcome_tallies"] = case["expect"]["outcome_tallies"]

    conflict_ids = {
        correction_id
        for conflict in actual["conflicts"]
        for correction_id in conflict["correction_ids"]
    }
    unresolved_injected = len(conflict_ids.intersection(actual["selected_ids"]))
    return {
        "id": case["id"],
        "capability": case["capability"],
        "passed": observed == expected,
        "observed": observed,
        "expected": expected,
        "estimated_tokens": actual["estimated_tokens"],
        "budget": case["budget"],
        "budget_overflow": actual["estimated_tokens"] > case["budget"],
        "unresolved_conflicts_injected": unresolved_injected,
    }


def _execute_suite(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_execute_case(case) for case in cases]


def _p95(samples: List[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def validate_report(report: Dict[str, Any]) -> List[str]:
    """Return fail-closed gate errors; an empty list means the report passes."""
    errors = []
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return ["metrics are missing"]
    if metrics.get("fidelity") != 1.0:
        errors.append("fidelity must equal 1.0")
    if metrics.get("interference") != 0:
        errors.append("interference must equal 0")
    if metrics.get("budget_overflows") != 0:
        errors.append("budget overflow detected")
    if metrics.get("unresolved_conflicts_injected") != 0:
        errors.append("unresolved conflict was incorrectly injected")
    if metrics.get("recurrence_prevention_rate") != 1.0:
        errors.append("recurrence prevention rate must equal 1.0")
    if metrics.get("outcomes_observable") is not True:
        errors.append("recurrence outcomes are not observable")
    if report.get("two_host_evidence") != TWO_HOST_NOT_READY:
        errors.append("two-host evidence is fabricated or not honestly marked not_ready")

    latency = report.get("latency_ms")
    gate = report.get("gate")
    if not isinstance(latency, dict) or not isinstance(gate, dict):
        errors.append("latency gate data is missing")
    else:
        bound = gate.get("warm_p95_bound_ms")
        warm_p95 = latency.get("warm_p95")
        if (bound != WARM_P95_BOUND_MS or isinstance(warm_p95, bool)
                or not isinstance(warm_p95, (int, float))):
            errors.append("warm p95 latency semantics are invalid")
        elif warm_p95 > WARM_P95_BOUND_MS:
            errors.append("warm p95 latency exceeds the generous %.1f ms bound" % bound)
    return errors


def run_benchmark(warm_iterations: int = 100) -> Dict[str, Any]:
    if type(warm_iterations) is not int or warm_iterations <= 0:
        raise ValueError("warm_iterations must be a positive integer")
    fixture = _load_cases()
    cases = fixture["cases"]

    started = time.perf_counter_ns()
    results = _execute_suite(cases)
    cold_ms = (time.perf_counter_ns() - started) / 1_000_000

    warm_samples = []
    for _ in range(warm_iterations):
        started = time.perf_counter_ns()
        _execute_suite(cases)
        warm_samples.append((time.perf_counter_ns() - started) / 1_000_000)

    recurrence_results = [r for r in results if r["capability"] == "recurrence_prevention"]
    outcome_tallies = [
        result["observed"].get("outcome_tallies", {}) for result in recurrence_results
    ]
    complied = sum(tallies.get("complied", 0) for tallies in outcome_tallies)
    observed_outcomes = sum(sum(tallies.values()) for tallies in outcome_tallies)
    metrics = {
        "fidelity": sum(result["passed"] for result in results) / len(results),
        "interference": sum(
            len(result["observed"]["selected_ids"])
            for result in results if result["capability"] == "interference"
        ),
        "budget_overflows": sum(result["budget_overflow"] for result in results),
        "unresolved_conflicts_injected": sum(
            result["unresolved_conflicts_injected"] for result in results
        ),
        "recurrence_prevention_rate": (
            complied / observed_outcomes if observed_outcomes else 0.0
        ),
        "outcomes_observable": observed_outcomes > 0,
    }
    report = {
        "schema_version": 1,
        "benchmark": fixture["benchmark"],
        "cases_sha256": CASES_SHA256,
        "source": "repository/src",
        "case_results": results,
        "metrics": metrics,
        "two_host_evidence": dict(TWO_HOST_NOT_READY),
        "latency_ms": {
            "cold": round(cold_ms, 6),
            "warm_p50": round(sorted(warm_samples)[len(warm_samples) // 2], 6),
            "warm_p95": round(_p95(warm_samples), 6),
            "warm_iterations": warm_iterations,
        },
        "gate": {"warm_p95_bound_ms": WARM_P95_BOUND_MS, "passed": False, "errors": []},
    }
    errors = validate_report(report)
    report["gate"]["errors"] = errors
    report["gate"]["passed"] = not errors
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm-iterations", type=int, default=100)
    parser.add_argument("--validate", type=Path, metavar="REPORT")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.validate:
        try:
            report = json.loads(args.validate.read_text(encoding="utf-8"))
            errors = validate_report(report)
        except (OSError, ValueError, TypeError) as error:
            print("invalid report: %s" % error, file=sys.stderr)
            return 2
        if errors:
            for error in errors:
                print("gate: %s" % error, file=sys.stderr)
            return 1
        return 0

    try:
        report = run_benchmark(args.warm_iterations)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("benchmark error: %s" % error, file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if not report["gate"]["passed"]:
        for error in report["gate"]["errors"]:
            print("gate: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
