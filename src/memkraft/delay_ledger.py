"""Append-only timing and delay evidence.

The log records run timing, estimates, and an inert evidence chain. It never
starts, queues, orders, or changes work. References are opaque local data.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .execution_protocol import ExecutionError, canonical_timestamp
from .store_core import StoreBusy, _unlock, append, read_all

__all__ = ["DelayLedgerMixin", "DelayError", "DELAY_ERROR_REGISTRY"]

DELAY_LOCK_TIMEOUT_S = 2.0
DELAY_ERROR_REGISTRY = {
    "E_DELAY_VALIDATION": ("input", False),
    "E_DELAY_HIERARCHY": ("state", False),
    "E_DELAY_NOT_FOUND": ("state", False),
    "E_DELAY_FINISHED": ("state", False),
    "E_DELAY_CHAIN": ("state", False),
    "E_DELAY_IDEMPOTENCY_MISMATCH": ("idempotency", False),
    "E_DELAY_LOG_CORRUPT": ("integrity", False),
    "E_DELAY_STORE_BUSY": ("io", True),
    "E_AUTHORITY_VERIFIED_FORBIDDEN": ("evidence", False),
}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,79}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]*$")
_KINDS = ("task", "phase", "attempt")
_OUTCOMES = ("completed", "failed", "interrupted", "incomplete", "aborted")
_VERDICTS = ("pass", "fail", "inconclusive")
_MAX_TEXT = 512
_MAX_REF = 256
_MAX_REFS = 8
_FINGERPRINT_EXCLUDED = frozenset({
    "id", "created_at", "schema_version", "event_seq", "anomaly",
    "anomaly_threshold_ms",
})


class DelayError(ExecutionError):
    """Stable error for delay-ledger operations."""

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        error_class, retryable = DELAY_ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


def _fail(code: str, message: str, field: Optional[str] = None, **extra: Any) -> None:
    details = dict(extra)
    if field is not None:
        details["path"] = field
    raise DelayError(code, message, details)


def _bounded(field: str, value: Any, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        _fail("E_DELAY_VALIDATION", "%s must be a non-empty bounded string" % field,
              field)
    return value


def _identity(field: str, value: Any) -> str:
    value = _bounded(field, value, 80)
    if not _ID.fullmatch(value):
        _fail("E_DELAY_VALIDATION", "%s has an invalid form" % field, field)
    return value


def _opaque(field: str, value: Any, limit: int = _MAX_TEXT) -> str:
    value = _bounded(field, value, limit)
    if not _OPAQUE.fullmatch(value):
        _fail("E_DELAY_VALIDATION", "%s must be an opaque token" % field, field)
    return value


def _refs(field: str, value: Any) -> List[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        _fail("E_DELAY_VALIDATION", "%s must be a list" % field, field)
    if len(value) > _MAX_REFS:
        _fail("E_DELAY_VALIDATION", "%s has too many entries" % field, field)
    return [_opaque("%s[%d]" % (field, index), item, _MAX_REF)
            for index, item in enumerate(value)]


def _time(value: Any) -> str:
    try:
        return canonical_timestamp(value)
    except (ExecutionError, TypeError, ValueError):
        _fail("E_DELAY_VALIDATION", "now must be an aware ISO timestamp", "now")


def _common(record_type: str, now: Any, authority_verified: bool,
            evidence_refs=()) -> Dict[str, Any]:
    if authority_verified:
        _fail("E_AUTHORITY_VERIFIED_FORBIDDEN",
              "core cannot verify external authority", "authority_verified")
    return {
        "record_type": record_type,
        "created_at": _time(now),
        "privacy": "local_private",
        "authority_verified": False,
        "evidence_refs": _refs("evidence_refs", evidence_refs),
    }


def _fingerprint(record: Dict[str, Any]) -> str:
    body = {key: value for key, value in record.items()
            if key not in _FINGERPRINT_EXCLUDED}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _operation_id(record: Dict[str, Any], supplied: Optional[str]) -> str:
    if supplied is not None:
        return _opaque("operation_id", supplied, 128)
    return "auto:" + _fingerprint(record)


def _secure_delay_path(path: Path) -> None:
    """Create or repair the private ledger before any record is visible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = False
    if path.parent.stat().st_mode & 0o777 != 0o700:
        os.chmod(path.parent, 0o700)
        changed = True
    existed = path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if not existed or os.fstat(fd).st_mode & 0o777 != 0o600:
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            changed = True
    finally:
        os.close(fd)
    if changed:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _valid_common_record(record: Dict[str, Any]) -> bool:
    try:
        created_at = record.get("created_at")
        valid_time = isinstance(created_at, str) and canonical_timestamp(created_at) == created_at
        evidence = record.get("evidence_refs")
        valid_evidence = (isinstance(evidence, list) and len(evidence) <= _MAX_REFS
                          and all(isinstance(item, str) and len(item) <= _MAX_REF
                                  and _OPAQUE.fullmatch(item) for item in evidence))
        operation_id = record.get("operation_id")
        return bool(
            record.get("schema_version") == 1
            and valid_time
            and record.get("privacy") == "local_private"
            and record.get("authority_verified") is False
            and isinstance(operation_id, str) and len(operation_id) <= 128
            and _OPAQUE.fullmatch(operation_id)
            and valid_evidence
        )
    except (ExecutionError, TypeError, ValueError):
        return False


def _partition(result) -> Tuple[List[Dict[str, Any]], int]:
    usable = []
    corrupt = int(result.skipped)
    operation_ids = set()
    for record in result.records:
        seq = record.get("event_seq")
        operation_id = record.get("operation_id")
        if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 1
                or not isinstance(record.get("record_type"), str)
                or not _valid_common_record(record)
                or operation_id in operation_ids):
            corrupt += 1
        else:
            operation_ids.add(operation_id)
            usable.append(record)
    usable.sort(key=lambda item: item["event_seq"])
    if [item["event_seq"] for item in usable] != list(range(1, len(usable) + 1)):
        corrupt += 1
    return usable, corrupt


def _identity_fields_valid(record: Dict[str, Any]) -> bool:
    kind = record.get("record_type")
    if kind == "delay_run_start":
        parent = record.get("parent_run_id")
        return (isinstance(record.get("run_id"), str)
                and (parent is None or isinstance(parent, str)))
    if kind == "delay_run_finish":
        return isinstance(record.get("run_id"), str)
    if kind in ("delay_retrospective", "delay_action",
                "delay_application", "delay_verification"):
        predecessor = record.get("predecessor_id")
        return (isinstance(record.get("chain_id"), str)
                and (predecessor is None or isinstance(predecessor, str)))
    return False


def _fold(records: List[Dict[str, Any]]):
    runs: Dict[str, Dict[str, Any]] = {}
    chain: Dict[str, Dict[str, Any]] = {}
    corrupt = 0
    for record in records:
        kind = record.get("record_type")
        if not _identity_fields_valid(record):
            corrupt += 1
            continue
        if kind == "delay_run_start":
            run_id = record.get("run_id")
            if run_id in runs or record.get("kind") not in _KINDS:
                corrupt += 1
                continue
            parent_id = record.get("parent_run_id")
            run_kind = record.get("kind")
            if run_kind == "task":
                valid_parent = parent_id is None
            elif run_kind == "phase":
                valid_parent = parent_id in runs and runs[parent_id]["kind"] == "task"
            else:
                valid_parent = parent_id in runs and runs[parent_id]["kind"] == "phase"
            if not valid_parent:
                corrupt += 1
                continue
            runs[run_id] = {
                "kind": run_kind,
                "subject": record.get("subject"),
                "parent_run_id": parent_id,
                "start_seq": record["event_seq"],
                "finished": False,
            }
        elif kind == "delay_run_finish":
            run = runs.get(record.get("run_id"))
            elapsed = record.get("elapsed_ms")
            if (run is None or run["finished"] or not isinstance(elapsed, int)
                    or isinstance(elapsed, bool) or elapsed < 0):
                corrupt += 1
                continue
            outcome = record.get("outcome", "completed")
            if outcome not in _OUTCOMES:
                corrupt += 1
                continue
            run.update({"finished": True, "elapsed_ms": elapsed,
                        "outcome": outcome,
                        "finish_seq": record["event_seq"],
                        "finish_record": record})
        elif kind in ("delay_retrospective", "delay_action",
                      "delay_application", "delay_verification"):
            record_id = record.get("chain_id")
            if not isinstance(record_id, str) or record_id in chain:
                corrupt += 1
                continue
            predecessor_id = record.get("predecessor_id")
            expected = {
                "delay_retrospective": None,
                "delay_action": "delay_retrospective",
                "delay_application": "delay_action",
                "delay_verification": "delay_application",
            }[kind]
            if expected is not None:
                predecessor = chain.get(predecessor_id)
                if predecessor is None or predecessor.get("record_type") != expected:
                    corrupt += 1
                    continue
            chain[record_id] = record
        else:
            corrupt += 1
    return runs, chain, corrupt


def _nearest(values: List[int], quantile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _estimate_from(records: List[Dict[str, Any]], kind: str, subject: str,
                   window: int, through_seq: Optional[int]) -> Dict[str, Any]:
    runs, _, semantic_corrupt = _fold(records)
    if semantic_corrupt:
        return {"available": False, "reason": "corrupt_history"}
    samples = [run for run in runs.values()
               if run["finished"] and run.get("outcome") == "completed"
               and run["kind"] == kind and run["subject"] == subject
               and (through_seq is None or run["finish_seq"] <= through_seq)]
    samples.sort(key=lambda item: item["finish_seq"])
    values = [item["elapsed_ms"] for item in samples[-window:]]
    if len(values) < 5:
        return {"available": False, "reason": "insufficient_samples",
                "sample_count": len(values), "through_seq": through_seq}
    p50 = _nearest(values, 0.5)
    p80 = _nearest(values, 0.8)
    mad = _nearest([abs(value - p50) for value in values], 0.5)
    return {
        "available": True,
        "sample_count": len(values),
        "p50_ms": p50,
        "p80_ms": p80,
        "mad_ms": mad,
        "anomaly_threshold_ms": p50 + 4 * mad,
        "through_seq": through_seq,
    }


class DelayLedgerMixin:
    """Additive local timing and evidence API."""

    def _delay_events_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "delay" / "events.jsonl"

    def _delay_append(self, record: Dict[str, Any], operation_id: Optional[str],
                      prepare=None, snapshot=None) -> Dict[str, Any]:
        record = dict(record)
        path = self._delay_events_path()
        try:
            try:
                fd = self._governance_lock(timeout_s=DELAY_LOCK_TIMEOUT_S)
            except TypeError as error:
                if "unexpected keyword argument 'timeout_s'" not in str(error):
                    raise
                fd = self._governance_lock()
        except StoreBusy:
            raise DelayError("E_DELAY_STORE_BUSY",
                             "the delay store is locked by another writer")
        try:
            _secure_delay_path(path)
            records, corrupt = _partition(read_all(path, include_tombstoned=True))
            _, _, semantic_corrupt = _fold(records)
            if corrupt or semantic_corrupt:
                _fail("E_DELAY_LOG_CORRUPT",
                      "the delay log is inconsistent; refusing a partial-view write",
                      skipped_lines=corrupt, semantic_errors=semantic_corrupt)
            if snapshot is not None:
                snapshot(records, record)
            record["operation_id"] = _operation_id(record, operation_id)
            fingerprint = _fingerprint(record)
            for stored in records:
                if stored.get("operation_id") != record["operation_id"]:
                    continue
                if _fingerprint(stored) == fingerprint:
                    return {"outcome": "already_applied",
                            "record_id": stored.get("id"),
                            "event_seq": stored["event_seq"],
                            "operation_id": record["operation_id"],
                            "record_fingerprint": fingerprint,
                            "record": stored}
                _fail("E_DELAY_IDEMPOTENCY_MISMATCH",
                      "operation_id was used with different arguments")
            if prepare is not None:
                prepare(records, record)
            record["event_seq"] = max([0] + [item["event_seq"] for item in records]) + 1
            written = append(path, record)
            durable_fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(durable_fd)
            finally:
                os.close(durable_fd)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            _unlock(fd)
            os.close(fd)
        return {"outcome": "applied", "record_id": written["id"],
                "event_seq": written["event_seq"],
                "operation_id": written["operation_id"],
                "record_fingerprint": fingerprint, "record": written}

    def delay_run_start(self, run_id, kind, subject, *, now,
                        parent_run_id=None, context_refs=(), evidence_refs=(),
                        authority_verified=False, operation_id=None):
        record = _common("delay_run_start", now, authority_verified, evidence_refs)
        record.update({"run_id": _identity("run_id", run_id),
                       "kind": kind, "subject": _opaque("subject", subject),
                       "parent_run_id": None if parent_run_id is None else
                       _identity("parent_run_id", parent_run_id),
                       "context_refs": _refs("context_refs", context_refs)})
        if kind not in _KINDS:
            _fail("E_DELAY_VALIDATION", "kind is outside its closed domain", "kind")

        def prepare(records, candidate):
            runs, _, _ = _fold(records)
            parent = runs.get(candidate["parent_run_id"])
            valid = ((candidate["kind"] == "task" and parent is None
                      and candidate["parent_run_id"] is None)
                     or (candidate["kind"] == "phase" and parent is not None
                         and parent["kind"] == "task" and not parent["finished"])
                     or (candidate["kind"] == "attempt" and parent is not None
                         and parent["kind"] == "phase" and not parent["finished"]))
            if not valid:
                _fail("E_DELAY_HIERARCHY", "run hierarchy is invalid")
            if candidate["run_id"] in runs:
                _fail("E_DELAY_HIERARCHY", "run_id already exists", "run_id")
        return self._delay_append(record, operation_id, prepare)

    def delay_run_finish(self, run_id, elapsed_ms, *, now, outcome="completed",
                         evidence_refs=(), authority_verified=False,
                         operation_id=None):
        if (not isinstance(elapsed_ms, int) or isinstance(elapsed_ms, bool)
                or elapsed_ms < 0):
            _fail("E_DELAY_VALIDATION", "elapsed_ms must be a non-negative integer",
                  "elapsed_ms")
        if outcome not in _OUTCOMES:
            _fail("E_DELAY_VALIDATION", "outcome is outside its closed domain", "outcome")
        record = _common("delay_run_finish", now, authority_verified, evidence_refs)
        record.update({"run_id": _identity("run_id", run_id),
                       "elapsed_ms": elapsed_ms, "outcome": outcome})

        def prepare(records, candidate):
            runs, _, _ = _fold(records)
            run = runs.get(candidate["run_id"])
            if run is None:
                _fail("E_DELAY_NOT_FOUND", "run was never started", "run_id")
            if run["finished"]:
                _fail("E_DELAY_FINISHED", "run was already finished", "run_id")
            if any(not child["finished"] and child.get("parent_run_id") == candidate["run_id"]
                   for child in runs.values()):
                _fail("E_DELAY_HIERARCHY", "run has an unfinished child", "run_id")
            estimate = _estimate_from(records, run["kind"], run["subject"], 100, None)
            candidate["anomaly"] = bool(
                estimate.get("available")
                and candidate["elapsed_ms"] > estimate["anomaly_threshold_ms"])
            candidate["anomaly_threshold_ms"] = (
                estimate.get("anomaly_threshold_ms") if estimate.get("available") else None)
        return self._delay_append(record, operation_id, prepare)

    def delay_estimate(self, kind, subject, *, now, window=100, through_seq=None):
        _time(now)
        if kind not in _KINDS:
            _fail("E_DELAY_VALIDATION", "kind is outside its closed domain", "kind")
        _opaque("subject", subject)
        if (not isinstance(window, int) or isinstance(window, bool)
                or not 5 <= window <= 1000):
            _fail("E_DELAY_VALIDATION", "window must be from 5 through 1000", "window")
        if through_seq is not None and (not isinstance(through_seq, int)
                                        or isinstance(through_seq, bool)
                                        or through_seq < 1):
            _fail("E_DELAY_VALIDATION", "through_seq must be a positive integer",
                  "through_seq")
        records, corrupt = _partition(read_all(self._delay_events_path(),
                                               include_tombstoned=True))
        result = _estimate_from(records, kind, subject, window, through_seq)
        if corrupt:
            return {"available": False, "reason": "corrupt_history"}
        return result

    def delay_record_retrospective(self, retrospective_id, run_id, observation, *, now,
                                   evidence_refs=(), authority_verified=False,
                                   operation_id=None):
        record = _common("delay_retrospective", now, authority_verified, evidence_refs)
        record.update({"chain_id": _identity("retrospective_id", retrospective_id),
                       "run_id": _identity("run_id", run_id),
                       "observation": _opaque("observation", observation),
                       "predecessor_id": None})

        def snapshot(records, candidate):
            runs, _, _ = _fold(records)
            run = runs.get(candidate["run_id"])
            finish = None if run is None else run.get("finish_record")
            if (run is None or not run.get("finished")
                    or run.get("outcome") != "completed"
                    or not isinstance(finish, dict) or finish.get("anomaly") is not True):
                _fail("E_DELAY_CHAIN", "retrospective requires a completed anomalous run")
            assert isinstance(finish, dict)
            candidate.update({
                "trigger_finish_seq": run["finish_seq"],
                "trigger_elapsed_ms": run["elapsed_ms"],
                "trigger_threshold_ms": finish["anomaly_threshold_ms"],
                "trigger_algorithm": "nearest-rank-p50-plus-4mad-v1",
            })

        def prepare(records, candidate):
            _, chain, _ = _fold(records)
            if candidate["chain_id"] in chain:
                _fail("E_DELAY_CHAIN", "chain_id already exists")
        return self._delay_append(record, operation_id, prepare, snapshot)

    def _delay_chain_record(self, record_type, chain_id, predecessor_id, text_field,
                            text, *, now, evidence_refs=(), external_receipt_ref=None,
                            verdict=None, authority_verified=False, operation_id=None):
        record = _common(record_type, now, authority_verified, evidence_refs)
        record.update({"chain_id": _identity("chain_id", chain_id),
                       "predecessor_id": _identity("predecessor_id", predecessor_id),
                       text_field: _opaque(text_field, text)})
        if external_receipt_ref is not None:
            record["external_receipt_ref"] = _opaque(
                "external_receipt_ref", external_receipt_ref, _MAX_REF)
        if verdict is not None:
            if verdict not in _VERDICTS:
                _fail("E_DELAY_VALIDATION", "verdict is outside its closed domain",
                      "verdict")
            record["verdict"] = verdict

        expected = {"delay_action": "delay_retrospective",
                    "delay_application": "delay_action",
                    "delay_verification": "delay_application"}[record_type]
        def prepare(records, candidate):
            _, chain, _ = _fold(records)
            predecessor = chain.get(candidate["predecessor_id"])
            if predecessor is None or predecessor["record_type"] != expected:
                _fail("E_DELAY_CHAIN", "the predecessor type is invalid")
            if candidate["chain_id"] in chain:
                _fail("E_DELAY_CHAIN", "chain_id already exists")
        return self._delay_append(record, operation_id, prepare)

    def delay_record_action(self, action_id, retrospective_id, action, *, now,
                            evidence_refs=(), authority_verified=False,
                            operation_id=None):
        return self._delay_chain_record(
            "delay_action", action_id, retrospective_id, "action", action, now=now,
            evidence_refs=evidence_refs, authority_verified=authority_verified,
            operation_id=operation_id)

    def delay_record_application(self, application_id, action_id, claim, *, now,
                                 evidence_refs=(), external_receipt_ref=None,
                                 authority_verified=False, operation_id=None):
        return self._delay_chain_record(
            "delay_application", application_id, action_id, "claim", claim, now=now,
            evidence_refs=evidence_refs, external_receipt_ref=external_receipt_ref,
            authority_verified=authority_verified, operation_id=operation_id)

    def delay_record_verification(self, verification_id, application_id, verdict, *, now,
                                  evidence_refs=(), authority_verified=False,
                                  operation_id=None):
        return self._delay_chain_record(
            "delay_verification", verification_id, application_id, "finding", verdict,
            now=now, evidence_refs=evidence_refs, verdict=verdict,
            authority_verified=authority_verified, operation_id=operation_id)
