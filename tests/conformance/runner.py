"""MKEP/0 language-neutral fixture inventory and Python/L2 runner.

The JSON files are the contract.  This module deliberately compares only fields
that the fixture names: implementation-assigned ids and digests remain free to
vary, while every stable expected response and final-state field is mandatory.
Non-Python transports are reported as gaps, never counted as passes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from memkraft import MemKraft, execution_projection, store_core
from memkraft.execution_dispatch import dispatch

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "0"

NAMED_CASE_IDS = frozenset({
    "AU-01", "CJ-01", "CJ-02", "CJ-03", "CJ-04", "CJ-05", "CJ-06",
    "CL-01", "DT-01", "DT-02", "DT-03", "DT-04", "EV-01", "EV-02",
    "EV-03", "EV-04", "EV-05", "FN-01", "FN-02", "FN-03", "ID-01",
    "ID-02", "ID-03", "IN-01", "IS-01", "MC-01", "NS-01", "NS-02",
    "TM-01", "TM-02", "TM-03", "XR-01",
})

_CASE_KEYS = frozenset({
    "case_id", "title", "mkep", "level", "tags", "origin_instance_id",
    "now_sequence", "transports", "executable", "gap",
})
_CASE_REQUIRED = _CASE_KEYS - {"gap"}
_FINAL_KEYS = frozenset({
    "goal_id", "log_line_count", "lines_delta", "consistent",
    "rejected_transitions", "skipped",
})
_RESPONSE_KEYS = frozenset({
    "ok", "outcome", "error_code", "error_class", "retryable",
})


class ConformanceError(AssertionError):
    """A fixture or implementation failed the conformance contract."""


@dataclass(frozen=True)
class Case:
    path: Path
    metadata: Dict[str, Any]
    requests: Tuple[Dict[str, Any], ...]
    expected: Dict[str, Any]

    @property
    def case_id(self) -> str:
        return self.metadata["case_id"]


@dataclass(frozen=True)
class Gap:
    case_id: str
    level: str
    transports: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RunResult:
    case_id: str
    responses: int
    initial_lines: int
    final_lines: int


@dataclass(frozen=True)
class Report:
    inventory_count: int
    named_count: int
    executable_l2_count: int
    executed_count: int
    skipped_gaps: Tuple[Gap, ...]


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConformanceError("%s: invalid JSON: %s" % (path, exc))


def _load_jsonl(path: Path) -> Tuple[Dict[str, Any], ...]:
    requests = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ConformanceError("%s:%d: blank JSONL line" % (path, number))
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ConformanceError("%s:%d: invalid JSON: %s" % (path, number, exc))
        if not isinstance(value, dict):
            raise ConformanceError("%s:%d: request must be an object" % (path, number))
        requests.append(value)
    return tuple(requests)


def _object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ConformanceError("%s must be an object" % label)
    return value


def load_case(path: Path) -> Case:
    """Load and schema-check one language-neutral fixture directory."""
    required_files = ("case.json", "expect.json", "README.md")
    missing = [name for name in required_files if not (path / name).is_file()]
    if missing:
        raise ConformanceError("%s: missing %s" % (path.name, ", ".join(missing)))

    metadata = _object(_load_json(path / "case.json"), "%s/case.json" % path.name)
    keys = frozenset(metadata)
    if keys - _CASE_KEYS or _CASE_REQUIRED - keys:
        raise ConformanceError("%s: invalid case.json keys" % path.name)
    if metadata["case_id"] != path.name or metadata["mkep"] != "0":
        raise ConformanceError("%s: directory id or mkep mismatch" % path.name)
    if metadata["level"] not in ("L1", "L2", "L3"):
        raise ConformanceError("%s: invalid level" % path.name)
    if not isinstance(metadata["title"], str) or not metadata["title"]:
        raise ConformanceError("%s: title must be non-empty" % path.name)
    if not isinstance(metadata["tags"], list) or not all(
            isinstance(item, str) for item in metadata["tags"]):
        raise ConformanceError("%s: tags must be strings" % path.name)
    if not isinstance(metadata["transports"], list) or not metadata["transports"]:
        raise ConformanceError("%s: transports must be non-empty" % path.name)
    if not all(isinstance(item, str) for item in metadata["transports"]):
        raise ConformanceError("%s: transports must be strings" % path.name)
    if not isinstance(metadata["now_sequence"], list):
        raise ConformanceError("%s: now_sequence must be a list" % path.name)
    if not isinstance(metadata["executable"], bool):
        raise ConformanceError("%s: executable must be boolean" % path.name)
    if metadata["executable"] and "gap" in metadata:
        raise ConformanceError("%s: executable case cannot declare a gap" % path.name)
    if not metadata["executable"] and not metadata.get("gap"):
        raise ConformanceError("%s: non-executable case must explain its gap" % path.name)

    one = path / "request.json"
    many = path / "requests.jsonl"
    if one.is_file() == many.is_file():
        raise ConformanceError("%s: exactly one request source is required" % path.name)
    requests = (_object(_load_json(one), str(one)),) if one.is_file() else _load_jsonl(many)
    if len(metadata["now_sequence"]) != len(requests):
        raise ConformanceError("%s: now_sequence/request count mismatch" % path.name)

    expected = _object(_load_json(path / "expect.json"), "%s/expect.json" % path.name)
    if not isinstance(expected.get("responses"), list):
        raise ConformanceError("%s: responses must be a list" % path.name)
    if len(expected["responses"]) != len(requests):
        raise ConformanceError("%s: response/request count mismatch" % path.name)
    final = _object(expected.get("final"), "%s/expect.final" % path.name)
    if frozenset(final) - _FINAL_KEYS:
        raise ConformanceError("%s: unknown final-state field" % path.name)
    for index, response in enumerate(expected["responses"]):
        response = _object(response, "%s/response[%d]" % (path.name, index))
        if frozenset(response) - _RESPONSE_KEYS or "ok" not in response:
            raise ConformanceError("%s: invalid expected response fields" % path.name)
    return Case(path, metadata, requests, expected)


def inventory(root: Path = FIXTURES) -> Tuple[Case, ...]:
    """Validate every case and enforce §18's minimum and named inventory."""
    if not root.is_dir():
        raise ConformanceError("fixture root does not exist: %s" % root)
    cases = tuple(load_case(path) for path in sorted(root.iterdir()) if path.is_dir())
    if len(cases) < 100:
        raise ConformanceError("fixture inventory has %d directories; need >=100" % len(cases))
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ConformanceError("fixture case ids are not unique")
    present_named = NAMED_CASE_IDS.intersection(ids)
    if present_named != NAMED_CASE_IDS:
        missing = sorted(NAMED_CASE_IDS - present_named)
        raise ConformanceError("missing named case ids: %s" % ", ".join(missing))
    return cases


def executable_l2_cases(root: Path = FIXTURES) -> Tuple[Case, ...]:
    return tuple(case for case in inventory(root)
                 if case.metadata["level"] == "L2" and case.metadata["executable"]
                 and "python" in case.metadata["transports"])


def transport_gaps(cases: Sequence[Case]) -> Tuple[Gap, ...]:
    return tuple(Gap(case.case_id, case.metadata["level"],
                     tuple(case.metadata["transports"]), case.metadata["gap"])
                 for case in cases if not case.metadata["executable"])


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _compare_response(case: Case, index: int, expected: Dict[str, Any],
                      actual: Dict[str, Any]) -> None:
    for key, value in expected.items():
        if key == "error_code":
            observed = actual.get("error", {}).get("code")
        elif key == "error_class":
            observed = actual.get("error", {}).get("class")
        elif key == "retryable":
            observed = actual.get("error", {}).get("retryable")
        else:
            observed = actual.get(key)
        if observed != value:
            raise ConformanceError(
                "%s response %d field %s: expected %r, got %r" %
                (case.case_id, index, key, value, observed))


def _copy_seed(case: Case, events_path: Path) -> int:
    seed = case.path / "seed" / "events.jsonl"
    if not seed.exists():
        return 0
    events_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(seed), str(events_path))
    return _line_count(events_path)


def _assert_special(case: Case, responses: Sequence[Dict[str, Any]], mk: MemKraft) -> None:
    expected = case.expected
    if "warnings_include" in expected:
        warnings = [item for response in responses for item in response.get("warnings", [])]
        if expected["warnings_include"] not in warnings:
            raise ConformanceError("%s: expected warning is absent" % case.case_id)
    if "recommendation" in expected:
        if responses[0].get("result", {}).get("recommendation") != expected["recommendation"]:
            raise ConformanceError("%s: recommendation mismatch" % case.case_id)
    if "reason_code" in expected:
        if responses[0].get("result", {}).get("reason_code") != expected["reason_code"]:
            raise ConformanceError("%s: reason_code mismatch" % case.case_id)
    if expected.get("advisory_is_true"):
        if responses[0].get("result", {}).get("advisory") is not True:
            raise ConformanceError("%s: assessment is not advisory" % case.case_id)
    for fragment in expected.get("forbidden_response_vocabulary", []):
        if fragment.lower() in json.dumps(responses, sort_keys=True).lower():
            raise ConformanceError("%s: forbidden response vocabulary %r" %
                                   (case.case_id, fragment))
    if "differing_keys" in expected:
        actual = responses[-1].get("error", {}).get("details", {}).get("differing_keys")
        if actual != expected["differing_keys"]:
            raise ConformanceError("%s: differing_keys mismatch" % case.case_id)
    if "supersede_reason" in expected:
        if responses[-1].get("result", {}).get("supersede_reason") != expected["supersede_reason"]:
            raise ConformanceError("%s: supersede_reason mismatch" % case.case_id)
    if expected.get("fence_strictly_increases"):
        fences = [r.get("result", {}).get("fence_token") for r in responses]
        fences = [value for value in fences if isinstance(value, int)]
        if len(fences) < 2 or fences[-1] <= fences[-2]:
            raise ConformanceError("%s: fence did not strictly increase" % case.case_id)
    if expected.get("expires_at_unchanged"):
        first = responses[0].get("result", {}).get("expires_at")
        second = responses[1].get("result", {}).get("expires_at")
        if first != second:
            raise ConformanceError("%s: replay refreshed expiry" % case.case_id)
    if "aggregate_over" in expected:
        count = sum(1 for path in FIXTURES.iterdir()
                    if path.is_dir() and path.name.startswith(expected["aggregate_over"]))
        if count < expected["aggregate_minimum"]:
            raise ConformanceError("%s: aggregate family is too small" % case.case_id)
    if "export_fails_closed" in expected:
        record = responses[0].get("result", {})
        handoff_id = record.get("handoff_id")
        request = {
            "mkep": "0", "kind": "query", "request_id": "f" * 32,
            "op": "handoff.export", "now": expected["export_fails_closed"]["now"],
            "target": {"goal_id": expected["export_fails_closed"]["goal_id"],
                       "handoff_id": handoff_id}, "args": {},
        }
        exported = dispatch(mk, request)
        if exported.get("ok") is not False:
            raise ConformanceError("%s: export did not fail closed" % case.case_id)


def run_case(case: Case, base_dir: Path) -> RunResult:
    """Execute one Python/L2 fixture in an isolated MemKraft base."""
    if not (case.metadata["executable"] and case.metadata["level"] == "L2"
            and "python" in case.metadata["transports"]):
        raise ConformanceError("%s is not an executable Python/L2 case" % case.case_id)
    mk = MemKraft(base_dir=str(base_dir))
    mk.init(verbose=False)
    events_path = mk._execution_events_path()
    initial_lines = _copy_seed(case, events_path)
    responses = []
    for index, (request, expected) in enumerate(zip(case.requests,
                                                     case.expected["responses"])):
        response = dispatch(mk, request)
        _compare_response(case, index, expected, response)
        responses.append(response)

    final_lines = _line_count(events_path)
    expected_final = case.expected["final"]
    observed = {
        "log_line_count": final_lines,
        "lines_delta": final_lines - initial_lines,
    }
    goal_id = expected_final.get("goal_id")
    if goal_id is not None:
        read = store_core.read_all(events_path, include_tombstoned=True)
        now = case.metadata["now_sequence"][-1]
        state = execution_projection.project(read.records, now, goal_id, skipped=read.skipped)
        observed.update({
            "goal_id": goal_id,
            "consistent": state["consistent"],
            "rejected_transitions": len(state["rejected_transitions"]),
            "skipped": state["skipped"],
        })
    else:
        observed.update({"consistent": True, "rejected_transitions": 0, "skipped": 0})
    for key, value in expected_final.items():
        if observed.get(key) != value:
            raise ConformanceError("%s final field %s: expected %r, got %r" %
                                   (case.case_id, key, value, observed.get(key)))
    _assert_special(case, responses, mk)
    return RunResult(case.case_id, len(responses), initial_lines, final_lines)


def run(root: Path = FIXTURES, work_root: Optional[Path] = None) -> Report:
    """Validate inventory, run every Python/L2 case, and return explicit gaps."""
    cases = inventory(root)
    selected = tuple(case for case in cases
                     if case.metadata["level"] == "L2" and case.metadata["executable"]
                     and "python" in case.metadata["transports"])
    if work_root is None:
        with tempfile.TemporaryDirectory(prefix="memkraft-conformance-") as temporary:
            for case in selected:
                run_case(case, Path(temporary) / case.case_id)
    else:
        for case in selected:
            run_case(case, work_root / case.case_id)
    gaps = transport_gaps(cases)
    return Report(len(cases), len(NAMED_CASE_IDS), len(selected), len(selected), gaps)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    args = parser.parse_args(argv)
    report = run(args.fixtures)
    print("inventory=%d named=%d executable_l2=%d executed=%d gaps=%d" % (
        report.inventory_count, report.named_count, report.executable_l2_count,
        report.executed_count, len(report.skipped_gaps)))
    for gap in report.skipped_gaps:
        print("GAP %s [%s]: %s" % (gap.case_id, ",".join(gap.transports), gap.reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
