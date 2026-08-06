"""State Contract — deterministic project-state drift detection.

Runtime-neutral core: it evaluates a plain ``observations`` mapping against
declarative constraints and returns a deterministic report. Collecting the
observations (git, interpreter probes, package metadata) is the host adapter's
job — this module never shells out, never touches the network, and never
performs remediation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .store_core import append, read_all, _unlock

#: Severities in blocking order. Anything above ``warning`` blocks a release.
SEVERITY_ORDER = ("critical", "error", "warning")

#: Severities that make a report not release-ready.
BLOCKING_SEVERITIES = frozenset({"critical", "error"})

SUPPORTED_KINDS = ("equals", "truthy", "forbidden_path_prefixes")

#: Prefixes that mark an ephemeral / non-durable checkout location.
EPHEMERAL_PATH_PREFIXES = (
    "/private/var/folders",
    "/private/tmp",
    "/var/folders",
    "/tmp",
)

#: The four constraints that reproduce the 2026-08-06 drift incident.
DEFAULT_CONSTRAINTS = (
    {
        "id": "version_alignment",
        "kind": "equals",
        "severity": "critical",
        "paths": ("source.version", "runtime.version", "distribution.version"),
    },
    {
        "id": "durable_source_path",
        "kind": "forbidden_path_prefixes",
        "severity": "critical",
        "paths": ("runtime.source_path",),
        "prefixes": EPHEMERAL_PATH_PREFIXES,
    },
    {
        "id": "release_branch_remote_backed",
        "kind": "truthy",
        "severity": "error",
        "paths": ("git.branch_remote_backed",),
    },
    {
        "id": "working_tree_clean",
        "kind": "truthy",
        "severity": "error",
        "paths": ("git.tree_clean",),
    },
    {
        "id": "project_snapshot_current",
        "kind": "equals",
        "severity": "error",
        "paths": (
            "source.version",
            "public.version",
            "snapshot.public_version",
            "snapshot.development_version",
        ),
        "pairs": (
            ("public.version", "snapshot.public_version"),
            ("source.version", "snapshot.development_version"),
        ),
    },
)

_MISSING = object()

STATE_CONTRACT_DIR = "state_contract"
STATE_CONTRACT_LOG = "events.jsonl"


class StateContractLogCorrupt(Exception):
    """Raised when the state-contract log has unreadable lines (fail closed)."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _resolve(observations: Any, path: str) -> Any:
    node = observations
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _validate(constraint: Dict[str, Any]) -> None:
    if not constraint.get("id"):
        raise ValueError("constraint requires a non-empty 'id'")
    if constraint.get("kind") not in SUPPORTED_KINDS:
        raise ValueError("unsupported constraint kind: %r" % (constraint.get("kind"),))
    if constraint.get("severity") not in SEVERITY_ORDER:
        raise ValueError("invalid severity: %r" % (constraint.get("severity"),))
    if not constraint.get("paths"):
        raise ValueError("constraint %r requires 'paths'" % (constraint["id"],))
    if constraint["kind"] == "forbidden_path_prefixes" and not constraint.get("prefixes"):
        raise ValueError("constraint %r requires 'prefixes'" % (constraint["id"],))


def _finding(constraint: Dict[str, Any], reason: str, observed: Dict[str, Any],
             matched_prefix: Optional[str] = None) -> Dict[str, Any]:
    finding = {
        "constraint_id": constraint["id"],
        "kind": constraint["kind"],
        "severity": constraint["severity"],
        "reason": reason,
        "observed": {key: observed[key] for key in sorted(observed)},
    }
    if matched_prefix is not None:
        finding["matched_prefix"] = matched_prefix
    finding["id"] = _digest(finding)[:16]
    return finding


def _evaluate_one(constraint: Dict[str, Any], observations: Any) -> Optional[Dict[str, Any]]:
    paths = list(constraint["paths"])
    observed: Dict[str, Any] = {}
    missing: List[str] = []
    for path in paths:
        value = _resolve(observations, path)
        if value is _MISSING:
            missing.append(path)
        else:
            observed[path] = value
    if missing:
        # Fail closed: an observation we could not collect is drift, not health.
        return _finding(constraint, "missing_observation",
                        dict(observed, **{path: None for path in missing}))

    kind = constraint["kind"]
    if kind == "equals":
        pairs = constraint.get("pairs")
        mismatched = (
            any(
                _canonical(observed[left]) != _canonical(observed[right])
                for left, right in pairs
            )
            if pairs
            else len({_canonical(observed[path]) for path in paths}) > 1
        )
        if mismatched:
            return _finding(constraint, "mismatch", observed)
        return None
    if kind == "truthy":
        if not all(observed[path] for path in paths):
            return _finding(constraint, "falsy", observed)
        return None

    # forbidden_path_prefixes — longest prefix wins so the match is deterministic
    # regardless of how the prefix list is ordered.
    prefixes = sorted(constraint["prefixes"], key=lambda p: (-len(p), p))
    for path in paths:
        value = observed[path]
        if not isinstance(value, str):
            return _finding(constraint, "missing_observation", observed)
        for prefix in prefixes:
            boundary = prefix.rstrip("/")
            if value == boundary or value.startswith(boundary + "/"):
                return _finding(constraint, "forbidden_path_prefix", observed, prefix)
    return None


def evaluate(observations: Dict[str, Any],
             constraints: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Evaluate ``observations`` against ``constraints`` and return a report.

    ``observations`` is never mutated. Findings are ordered by severity rank and
    then constraint id, and both finding ids and the report digest depend only
    on values — never on mapping insertion order.
    """
    active = list(DEFAULT_CONSTRAINTS if constraints is None else constraints)
    for constraint in active:
        _validate(constraint)

    findings = []
    for constraint in active:
        finding = _evaluate_one(constraint, observations)
        if finding is not None:
            findings.append(finding)
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]), f["constraint_id"]))

    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[finding["severity"]] += 1

    report = {
        "findings": findings,
        "counts": counts,
        "observation_digest": _digest(observations),
        "release_ready": not any(f["severity"] in BLOCKING_SEVERITIES for f in findings),
    }
    report["digest"] = _digest(report)
    return report


class StateContractMixin:
    """Dry-run evaluation plus an append-only, idempotent record of reports."""

    def _state_contract_log_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / STATE_CONTRACT_DIR / STATE_CONTRACT_LOG

    def state_contract_check(self, observations, constraints=None):
        """Evaluate without writing anything — no directories, no files."""
        return evaluate(observations, constraints)

    def state_contract_history(self, limit=None):
        """Return recorded reports plus the count of unreadable log lines."""
        result = read_all(self._state_contract_log_path())
        records = list(result.records)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("limit must be a nonnegative integer")
            records = [] if limit == 0 else records[-limit:]
        return {"records": records, "skipped": result.skipped}

    def state_contract_record(self, observations, operation_id, constraints=None):
        """Append one immutable report under the governance lock.

        An exact retry of ``operation_id`` returns the existing record without
        appending; the same ``operation_id`` with a different report payload is
        a contract violation and raises ``ValueError``. A corrupt log blocks the
        write entirely.
        """
        if not operation_id:
            raise ValueError("operation_id is required")
        report = evaluate(observations, constraints)
        path = self._state_contract_log_path()

        fd = self._governance_lock()
        try:
            existing = read_all(path)
            if existing.skipped:
                raise StateContractLogCorrupt(
                    "%d unreadable line(s) in %s" % (existing.skipped, path)
                )
            for record in existing.records:
                if record.get("operation_id") == operation_id:
                    if record.get("report") != report:
                        raise ValueError(
                            "operation_id %r already recorded with a different report"
                            % (operation_id,)
                        )
                    return record
            return append(path, {"operation_id": operation_id, "report": report})
        finally:
            try:
                _unlock(fd)
            finally:
                os.close(fd)
