"""Project the exact nine-artifact expanded ReasoningBank protocol."""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

try:
    from benchmarks.reasoning_tasks import expanded_cases
except ModuleNotFoundError:  # Direct script execution.
    from reasoning_tasks import expanded_cases

COMPARISON_STYLES = {"no-hint-vs-full": ("none", "full"),
                     "no-hint-vs-compact": ("none", "compact"),
                     "full-vs-compact": ("full", "compact")}
COMPARISONS = set(COMPARISON_STYLES)
SPLITS = {"dev", "holdout"}
PROTOCOL_KEYS = ({("dev", seed, comparison) for seed in (42, 43) for comparison in COMPARISONS}
                 | {("holdout", 42, comparison) for comparison in COMPARISONS})
TELEMETRY = ("prompt_tokens", "total_tokens", "reasoning_tokens")
FROZEN_RETRIEVAL = {"k": 1, "min_score": 0.1, "max_chars": 900, "per_item_chars": 240}
FROZEN_CONTRACT = {"temperature": 0, "reasoning_effort": "medium",
                   "max_tokens_requested": 512, "sdk_max_retries": 0,
                   "selective_policy": "declared-family-transfer"}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"missing or invalid telemetry: {name}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"missing or invalid telemetry: {name}") from exc
    if not math.isfinite(number):
        raise ValueError(f"missing or invalid telemetry: {name}")
    return number


def _nonnegative(value: Any, name: str) -> float:
    number = _number(value, name)
    if number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


def _derived(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"derived {name} must be finite")
    return value


def _median(values: list[float]) -> float:
    if not values:
        raise ValueError("median requires values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return ordered[middle - 1] / 2 + ordered[middle] / 2


def _ci(values: list[float], samples: int, seed: int) -> list[float]:
    if not values or samples <= 0:
        raise ValueError("bootstrap requires tasks and positive samples")
    rng = random.Random(seed)
    draws = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return [round(draws[int(.025 * samples)], 3),
            round(draws[min(samples - 1, int(.975 * samples))], 3)]


def _pct(new: float, baseline: float, name: str) -> float:
    if baseline == 0:
        if new == 0:
            return 0.0
        raise ValueError(f"zero baseline for {name}")
    return 100 * (new - baseline) / baseline


def _reasoning_change_pct(new: float, baseline: float) -> float:
    """Bound nonnegative token change to [-100, 100], including zero baselines."""
    if new < 0 or baseline < 0:
        raise ValueError("reasoning tokens must be nonnegative")
    scale = max(new, baseline)
    return 0.0 if scale == 0 else ((new - baseline) / scale) * 100


def project_evidence(paths: list[Path], *, bootstrap_samples: int = 20_000,
                     bootstrap_seed: int = 42) -> dict[str, Any]:
    if len(paths) != 9:
        raise ValueError("exactly nine artifacts required")
    expected_cases = {case.case_id: case for case in expanded_cases()}
    artifacts: dict[tuple[str, int, str], dict[str, Any]] = {}
    model = None
    endpoint_host = None
    response_models = None
    campaign_id = None
    campaign_generation = None
    holdout_identities: set[str] = set()
    campaign_members: dict[str, dict[str, Any]] = {}
    canonical_expected_artifacts = None
    canonical_campaign_member_map = None
    canonical_frozen = None
    for path in paths:
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load artifact: {path.name}") from exc
        if not isinstance(artifact, dict):
            raise ValueError("artifact root must be an object")
        settings = artifact.get("settings")
        if artifact.get("schema_version") != 2 or not isinstance(settings, dict) or settings.get("expanded") is not True:
            raise ValueError("expanded schema_version 2 required")
        split, comparison = settings.get("split"), settings.get("comparison")
        seed = settings.get("seed")
        key = (split, seed, comparison)
        if key not in PROTOCOL_KEYS or key in artifacts:
            raise ValueError("duplicate or invalid comparison/split coverage")
        requested_model = artifact.get("requested_model")
        if not isinstance(requested_model, str) or not requested_model:
            raise ValueError("requested_model required")
        model = requested_model if model is None else model
        if requested_model != model:
            raise ValueError("requested_model mismatch")
        if artifact.get("errors") != 0:
            raise ValueError("artifact errors must be zero")
        expected_styles = COMPARISON_STYLES[comparison]
        if (settings.get("control_style"), settings.get("injected_style")) != expected_styles:
            raise ValueError("comparison style mapping mismatch")
        if settings.get("retrieval") != FROZEN_RETRIEVAL:
            raise ValueError("retrieval does not match frozen plan")
        if any(settings.get(key) != value for key, value in FROZEN_CONTRACT.items()):
            raise ValueError("expanded execution contract mismatch")
        timeout = _number(settings.get("timeout_seconds"), "timeout_seconds")
        if timeout != 120.0:
            raise ValueError("timeout_seconds does not match frozen plan")
        host, served = artifact.get("endpoint_host"), artifact.get("response_models")
        if not isinstance(host, str) or not host:
            raise ValueError("endpoint_host required")
        if not isinstance(served, list) or not served or len(served) != len(set(served)) or \
                any(not isinstance(item, str) or not item for item in served):
            raise ValueError("nonempty response_models required")
        endpoint_host = host if endpoint_host is None else endpoint_host
        response_models = served if response_models is None else response_models
        if host != endpoint_host or served != response_models:
            raise ValueError("endpoint/model identity mismatch")
        if split == "holdout":
            holdout_run = settings.get("holdout_run")
            required = {"campaign_id", "generation", "comparison", "artifact_path",
                        "artifact_identity", "status", "expected_artifacts", "frozen",
                        "campaign_members"}
            if not isinstance(holdout_run, dict) or not required <= set(holdout_run):
                raise ValueError("valid holdout_run campaign metadata required")
            campaign_value = holdout_run["campaign_id"]
            generation_value = holdout_run["generation"]
            if not isinstance(campaign_value, str) or not campaign_value:
                raise ValueError("holdout campaign_id must be a nonempty string")
            if isinstance(generation_value, bool) or not isinstance(generation_value, int) \
                    or generation_value <= 0:
                raise ValueError("holdout campaign generation must be a positive integer")
            if holdout_run["status"] != "completed" or holdout_run["comparison"] != comparison:
                raise ValueError("holdout campaign member must be completed")
            if Path(holdout_run["artifact_path"]).resolve() != path.resolve():
                raise ValueError("holdout_run artifact_path does not match input")
            campaign_id = holdout_run["campaign_id"] if campaign_id is None else campaign_id
            campaign_generation = (holdout_run["generation"] if campaign_generation is None
                                   else campaign_generation)
            if holdout_run["campaign_id"] != campaign_id:
                raise ValueError("holdout artifacts must share one campaign")
            if holdout_run["generation"] != campaign_generation:
                raise ValueError("holdout artifacts must share one campaign generation")
            if set(holdout_run["expected_artifacts"]) != COMPARISONS or \
                    Path(holdout_run["expected_artifacts"][comparison]).resolve() != path.resolve():
                raise ValueError("holdout campaign expected members mismatch")
            identity = holdout_run["artifact_identity"]
            if not isinstance(identity, str) or not identity or identity in holdout_identities:
                raise ValueError("holdout artifact identity must be unique")
            holdout_identities.add(identity)
            expected_frozen = {"seed": 42, "repeats": 5, "timeout_seconds": 120.0,
                "temperature": 0, "reasoning_effort": "medium", "max_tokens_requested": 512,
                "sdk_max_retries": 0, "retrieval": FROZEN_RETRIEVAL,
                "selective_policy": "declared-family-transfer"}
            if holdout_run["frozen"] != expected_frozen:
                raise ValueError("holdout campaign frozen settings mismatch")
            members = holdout_run["campaign_members"]
            if not isinstance(members, dict) or set(members) != COMPARISONS or any(
                    not isinstance(member, dict) or member.get("status") != "completed"
                    or not isinstance(member.get("artifact_identity"), str)
                    for member in members.values()
            ) or members[comparison]["artifact_identity"] != identity:
                raise ValueError("holdout campaign members must all be completed and authenticated")
            canonical_expected_artifacts = (holdout_run["expected_artifacts"]
                if canonical_expected_artifacts is None else canonical_expected_artifacts)
            canonical_campaign_member_map = (members if canonical_campaign_member_map is None
                                             else canonical_campaign_member_map)
            canonical_frozen = (holdout_run["frozen"] if canonical_frozen is None
                                else canonical_frozen)
            if holdout_run["expected_artifacts"] != canonical_expected_artifacts or \
                    members != canonical_campaign_member_map or \
                    holdout_run["frozen"] != canonical_frozen:
                raise ValueError("holdout campaign metadata mappings must be identical")
            campaign_members[comparison] = holdout_run
        if settings.get("repeats") != 5:
            raise ValueError("frozen protocol requires five repeats")
        rows = artifact.get("rows")
        if not isinstance(rows, list):
            raise ValueError("rows required")
        pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        pair_owners: dict[str, str] = {}
        expected_ids = {cid for cid, case in expected_cases.items() if case.split == split}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("malformed row")
            cid, pair_id, condition = row.get("case_id"), row.get("pair_id"), row.get("condition")
            if cid not in expected_ids or not isinstance(pair_id, str) or condition not in {"control", "injected"}:
                raise ValueError("unexpected task or pair")
            if pair_id in pair_owners and pair_owners[pair_id] != cid:
                raise ValueError("duplicate pair ID across tasks")
            pair_owners[pair_id] = cid
            case = expected_cases[cid]
            if any(row.get(key) != getattr(case, key) for key in
                   ("family", "difficulty", "split", "expects_injection")):
                raise ValueError("task metadata mismatch")
            style = expected_styles[0 if condition == "control" else 1]
            hint_metadata = row.get("hint_metadata")
            if not isinstance(hint_metadata, dict) or hint_metadata.get("style") != style:
                raise ValueError("row style does not match arm")
            if row.get("error") != "":
                raise ValueError("row errors are not admissible")
            attempted, emitted, abstained = (row.get("retrieval_attempted"),
                                             row.get("hint_emitted"), row.get("abstained"))
            if not all(isinstance(value, bool) for value in (attempted, emitted, abstained)):
                raise ValueError("retrieval telemetry booleans required")
            expected_state = ((False, False, False) if style == "none" else
                              (True, case.expects_injection, not case.expects_injection))
            if (attempted, emitted, abstained) != expected_state:
                raise ValueError("retrieval/hint/abstention telemetry inconsistent")
            row_model = row.get("response_model")
            if not isinstance(row_model, str) or row_model not in served:
                raise ValueError("row response model identity mismatch")
            _nonnegative(row.get("latency_ms"), "latency_ms")
            expected, prediction = row.get("expected"), row.get("prediction")
            if not isinstance(expected, str) or expected != case.expected or \
                    not isinstance(prediction, str) or row.get("correct") is not (prediction.strip() == expected):
                raise ValueError("authenticated outcome mismatch")
            if "task" in row and row["task"] != case.task:
                raise ValueError("task text mismatch")
            usage = row.get("usage")
            if not isinstance(usage, dict):
                raise ValueError("usage telemetry required")
            for metric in (*TELEMETRY, "completion_tokens"):
                _nonnegative(usage.get(metric), metric)
            pair = pairs.setdefault((cid, pair_id), {})
            if condition in pair:
                raise ValueError("duplicate arm")
            pair[condition] = row
        repeats = settings["repeats"]
        if set(cid for cid, _ in pairs) != expected_ids or any(set(pair) != {"control", "injected"} for pair in pairs.values()) or \
                any(sum(cid == key[0] for key in pairs) != repeats for cid in expected_ids):
            raise ValueError("duplicate or incomplete pair coverage")
        artifact["_pairs"] = pairs
        artifacts[key] = artifact
    if set(artifacts) != PROTOCOL_KEYS or set(campaign_members) != COMPARISONS:
        raise ValueError("exact nine-artifact protocol coverage required")
    actual_holdout_paths = {
        comparison: str(Path(run["artifact_path"]).resolve())
        for comparison, run in campaign_members.items()
    }
    if canonical_expected_artifacts != actual_holdout_paths:
        raise ValueError("holdout campaign expected artifacts do not match inputs")
    if canonical_campaign_member_map is None:
        raise ValueError("holdout campaign member mapping required")
    if any(canonical_campaign_member_map[comparison]["artifact_identity"] !=
           run["artifact_identity"] for comparison, run in campaign_members.items()):
        raise ValueError("holdout campaign member identities do not match artifacts")

    tasks = []
    bootstrap: dict[str, list[float]] = {key: [] for key in ("latency", "prompt", "total", "reasoning")}
    hard_latency = []
    for cid, case in sorted(expected_cases.items()):
        seeds = (42, 43) if case.split == "dev" else (42,)
        def pooled(comparison: str) -> list[dict[str, Any]]:
            return [pair for seed in seeds for (task, _), pair in
                    artifacts[(case.split, seed, comparison)]["_pairs"].items() if task == cid]
        compact_pairs = pooled("no-hint-vs-compact")
        full_pairs = pooled("no-hint-vs-full")
        full_compact_pairs = pooled("full-vs-compact")
        control_latency = _median([_number(p["control"]["latency_ms"], "latency") for p in compact_pairs])
        compact_latency = _median([_number(p["injected"]["latency_ms"], "latency") for p in compact_pairs])
        def usage_median(pairs: list[dict[str, Any]], arm: str, metric: str) -> float:
            return _median([_number(p[arm]["usage"][metric], metric) for p in pairs])
        no_prompt = usage_median(compact_pairs, "control", "prompt_tokens")
        compact_prompt = usage_median(compact_pairs, "injected", "prompt_tokens")
        full_prompt = usage_median(full_compact_pairs, "control", "prompt_tokens")
        if compact_prompt != usage_median(full_compact_pairs, "injected", "prompt_tokens"):
            raise ValueError("compact arm telemetry mismatch across comparisons")
        if full_prompt != usage_median(full_pairs, "injected", "prompt_tokens"):
            raise ValueError("full arm telemetry mismatch across comparisons")
        compact_overhead = _derived(compact_prompt - no_prompt, "compact prompt overhead")
        full_overhead = _derived(full_prompt - no_prompt, "full prompt overhead")
        if full_overhead <= 0:
            reduction = 0.0 if not case.expects_injection else (_ for _ in ()).throw(ValueError("nonpositive full overhead"))
        else:
            reduction = _derived(
                ((full_overhead - compact_overhead) / full_overhead) * 100,
                "prompt reduction",
            )
        no_reasoning = usage_median(compact_pairs, "control", "reasoning_tokens")
        compact_reasoning = usage_median(compact_pairs, "injected", "reasoning_tokens")
        latency_pct = _derived(_pct(compact_latency, control_latency, "latency"), "latency change")
        reasoning_pct = _derived(
            _reasoning_change_pct(compact_reasoning, no_reasoning), "reasoning change"
        )
        total_delta = _derived(
            usage_median(compact_pairs, "injected", "total_tokens")
            - usage_median(compact_pairs, "control", "total_tokens"),
            "total token delta",
        )
        task = {"case_id": cid, "family": case.family, "difficulty": case.difficulty,
                "split": case.split, "expects_injection": case.expects_injection,
                "accuracy_control": sum(p["control"]["correct"] for p in compact_pairs),
                "accuracy_compact": sum(p["injected"]["correct"] for p in compact_pairs),
                "accuracy_full": sum(p["injected"]["correct"] for p in full_pairs),
                "accuracy_compact_delta": sum(int(p["injected"]["correct"]) - int(p["control"]["correct"]) for p in compact_pairs),
                "paired_compact_losses": sum(p["control"]["correct"] and not p["injected"]["correct"] for p in compact_pairs),
                "injection_covered": all(not p["injected"]["abstained"] for p in compact_pairs),
                "abstained": all(p["injected"]["abstained"] for p in compact_pairs),
                "compact_vs_full_prompt_reduction_pct": round(reduction, 3),
                "compact_vs_no_hint_prompt_overhead_tokens": round(compact_overhead, 3),
                "reasoning_change_pct": round(reasoning_pct, 3),
                "latency_slowdown_pct": round(latency_pct, 3)}
        tasks.append(task)
        for key, value in (("latency", latency_pct), ("prompt", compact_overhead),
                           ("total", total_delta), ("reasoning", reasoning_pct)):
            if case.split == "holdout" and case.expects_injection:
                bootstrap[key].append(value)
        if case.split == "holdout" and case.expects_injection and case.difficulty == "hard":
            hard_latency.append(latency_pct)
    family = {split: {fam: round(_median([t["latency_slowdown_pct"] for t in tasks
                                          if t["split"] == split and t["family"] == fam]), 3)
                      for fam in "ABCDEF"} for split in ("dev", "holdout")}
    return {"schema_version": 1, "tasks": tasks, "family_latency_change_pct": family,
            "latency_task_ci_95": _ci(bootstrap["latency"], bootstrap_samples, bootstrap_seed),
            "prompt_overhead_task_ci_95": _ci(bootstrap["prompt"], bootstrap_samples, bootstrap_seed),
            "total_token_delta_task_ci_95": _ci(bootstrap["total"], bootstrap_samples, bootstrap_seed),
            "reasoning_change_task_ci_95": _ci(bootstrap["reasoning"], bootstrap_samples, bootstrap_seed),
            "hard_holdout_latency_task_ci_95": _ci(hard_latency, bootstrap_samples, bootstrap_seed)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--bootstrap-seed", type=int, default=42)
    args = parser.parse_args()
    try:
        result = project_evidence(args.inputs, bootstrap_samples=args.bootstrap_samples,
                                  bootstrap_seed=args.bootstrap_seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "MALFORMED", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
