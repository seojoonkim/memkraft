"""Predeclared, fail-closed gates for expanded ReasoningBank evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from benchmarks.reasoning_tasks import expanded_cases
except ModuleNotFoundError:  # Direct script execution.
    from reasoning_tasks import expanded_cases

_REQUIRED_TASK_FIELDS = ("case_id", "family", "difficulty", "split", "expects_injection",
    "accuracy_control", "accuracy_compact", "accuracy_full", "accuracy_compact_delta",
    "paired_compact_losses", "injection_covered", "abstained",
    "compact_vs_full_prompt_reduction_pct", "compact_vs_no_hint_prompt_overhead_tokens",
    "reasoning_change_pct", "latency_slowdown_pct")
_CIS = ("latency_task_ci_95", "prompt_overhead_task_ci_95", "total_token_delta_task_ci_95",
        "reasoning_change_task_ci_95", "hard_holdout_latency_task_ci_95")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("metric must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("metric must be finite")
    return value


def _integer(value: Any) -> int:
    number = _number(value)
    if not number.is_integer():
        raise ValueError("count must be an integer")
    return int(number)


def evaluate_gate(evidence: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "FAIL", "accepted": [], "neutral": [], "rejected": [],
        "universal_speed_claim": False,
        "claim_scope": "No universal speed claim; conclusions are bounded to evaluated tasks.",
        "malformed": False}
    def accept(name: str, detail: Any = None) -> None:
        result["accepted"].append({"metric": name, "detail": detail})
    def neutral(name: str, detail: Any = None) -> None:
        result["neutral"].append({"metric": name, "detail": detail})
    def reject(name: str, detail: Any = None) -> None:
        result["rejected"].append({"metric": name, "detail": detail})

    try:
        if not isinstance(evidence, dict) or evidence.get("schema_version") != 1 or not isinstance(evidence.get("tasks"), list):
            raise ValueError("missing tasks")
        tasks = evidence["tasks"]
        catalog = {case.case_id: case for case in expanded_cases()}
        if len(tasks) != 28:
            raise ValueError(f"requires exactly 28 tasks, found {len(tasks)}")
        ids = [task.get("case_id") if isinstance(task, dict) else None for task in tasks]
        if len(set(ids)) != 28 or set(ids) != set(catalog):
            raise ValueError("task IDs must be unique and match frozen catalog")
        for task in tasks:
            if "accuracy_regression" in task or "paired_losses" in task:
                raise ValueError("ambiguous legacy accuracy schema rejected")
            if any(field not in task for field in _REQUIRED_TASK_FIELDS):
                raise ValueError("missing required task metric")
            case = catalog[task["case_id"]]
            if any(task[key] != getattr(case, key) for key in
                   ("family", "difficulty", "split", "expects_injection")):
                raise ValueError("task metadata does not match frozen catalog")
            if not isinstance(task["injection_covered"], bool) or not isinstance(task["abstained"], bool):
                raise ValueError("boolean evidence required")
            control_accuracy = _integer(task["accuracy_control"])
            compact_accuracy = _integer(task["accuracy_compact"])
            full_accuracy = _integer(task["accuracy_full"])
            delta = _integer(task["accuracy_compact_delta"])
            repeats = 10 if case.split == "dev" else 5
            if min(control_accuracy, compact_accuracy, full_accuracy) < 0 or \
                    max(control_accuracy, compact_accuracy, full_accuracy) > repeats:
                raise ValueError("accuracy counts outside protocol repeat bounds")
            if delta != compact_accuracy - control_accuracy:
                raise ValueError("accuracy_compact_delta does not match counts")
            losses = _integer(task["paired_compact_losses"])
            minimum_losses = max(0, control_accuracy - compact_accuracy)
            maximum_losses = min(control_accuracy, repeats - compact_accuracy)
            if not minimum_losses <= losses <= maximum_losses:
                raise ValueError("paired_compact_losses inconsistent with accuracy counts")
            for field in ("compact_vs_full_prompt_reduction_pct", "compact_vs_no_hint_prompt_overhead_tokens",
                          "reasoning_change_pct", "latency_slowdown_pct"):
                _number(task[field])
        cis: dict[str, list[float]] = {}
        for field in _CIS:
            value = evidence.get(field)
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError(f"missing finite two-element CI: {field}")
            low, high = _number(value[0]), _number(value[1])
            if low > high:
                raise ValueError(f"reversed CI: {field}")
            cis[field] = [low, high]
            (neutral if low <= 0 <= high else accept)(field, [low, high])
        signs = evidence.get("family_latency_change_pct")
        if not isinstance(signs, dict) or set(signs) != {"dev", "holdout"}:
            raise ValueError("exact dev/holdout family effects required")
        for split in ("dev", "holdout"):
            if not isinstance(signs[split], dict) or set(signs[split]) != set("ABCDEF"):
                raise ValueError("exact A-F family keys required")
            for family in "ABCDEF":
                _number(signs[split][family])

        holdout = [t for t in tasks if t["split"] == "holdout"]
        relevant = [t for t in holdout if t["expects_injection"]]
        unrelated = [t for t in holdout if not t["expects_injection"]]
        if len(holdout) != 14 or len(relevant) != 12 or len(unrelated) != 2 or any(t["family"] != "G" for t in unrelated):
            raise ValueError("holdout requires 12 procedural and 2 family-G tasks")

        regressions = [t for t in holdout if _integer(t["accuracy_compact_delta"]) < 0]
        losses = [t for t in holdout if _integer(t["paired_compact_losses"]) != 0]
        (accept if not regressions and not losses else reject)("holdout_accuracy",
            {"regression_tasks": len(regressions), "paired_loss_tasks": len(losses)})
        coverage = sum(t["injection_covered"] and not t["abstained"] for t in relevant)
        (accept if coverage == 12 else reject)("selective_coverage", f"{coverage}/12")
        abstention = sum(t["abstained"] and not t["injection_covered"] for t in unrelated)
        (accept if abstention == 2 else reject)("unrelated_abstention", f"{abstention}/2")

        reductions = [_number(t["compact_vs_full_prompt_reduction_pct"]) for t in relevant]
        overheads = [_number(t["compact_vs_no_hint_prompt_overhead_tokens"]) for t in relevant]
        (accept if min(reductions) >= 35 and max(overheads) <= 160 else reject)(
            "compact_prompt_economy", {"min_full_reduction_pct": min(reductions),
                                       "max_no_hint_overhead": max(overheads)})
        hard = [t for t in relevant if t["difficulty"] == "hard"]
        easy = [t for t in relevant if t["difficulty"] == "easy"]
        if len(hard) != 6 or len(easy) != 6:
            raise ValueError("holdout requires exactly six easy and six hard procedural tasks")
        reasoning = [_number(t["reasoning_change_pct"]) for t in hard]
        nonincrease, exceeds = sum(x <= 0 for x in reasoning), sum(x > 10 for x in reasoning)
        (accept if nonincrease >= 4 and exceeds < 2 else reject)("hard_reasoning",
            {"nonincrease": nonincrease, "exceeds_plus_10_pct": exceeds})
        easy_latency = [_number(t["latency_slowdown_pct"]) for t in easy]
        over10 = sum(x > 10 for x in easy_latency)
        (accept if max(easy_latency) <= 8 else reject)("easy_latency",
            {"max_slowdown_pct": max(easy_latency), "over_10_pct": over10})
        all_latency = [_number(t["latency_slowdown_pct"]) for t in relevant]
        (accept if max(all_latency) <= 15 else reject)("per_task_latency_cap", max(all_latency))

        consistent = sum(signs["dev"][f] == 0 or signs["holdout"][f] == 0 or
                         (_number(signs["dev"][f]) < 0) == (_number(signs["holdout"][f]) < 0)
                         for f in "ABCDEF")
        reversals = sum(_number(signs["dev"][f]) < 0 and _number(signs["holdout"][f]) > 10
                        for f in "ABCDEF")
        (accept if consistent >= 4 and reversals < 2 else reject)("family_latency_sign_consistency",
            {"consistent": consistent, "dev_benefit_reversals_over_10_pct": reversals})

        hard_faster = sum(_number(t["latency_slowdown_pct"]) < 0 for t in hard)
        hard_ci = cis["hard_holdout_latency_task_ci_95"]
        if hard_faster >= 5 and hard_ci[1] < 0:
            accept("hard_speed_claim", {"faster": hard_faster, "ci": hard_ci})
        else:
            neutral("hard_speed_claim", {"faster": hard_faster, "ci": hard_ci})
    except (KeyError, TypeError, ValueError) as exc:
        result["malformed"] = True
        reject("malformed_evidence", str(exc))

    result["status"] = "PASS" if not result["rejected"] else "FAIL"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "MALFORMED", "error": type(exc).__name__}))
        return 2
    result = evaluate_gate(evidence)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["malformed"]:
        return 2
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
