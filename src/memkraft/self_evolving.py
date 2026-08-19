"""MemKraft 4.0 Self-Evolving Agent Substrate contracts.

This module records sanitized experience, capability manifests, evaluator receipts,
and post-activation observations. It never executes artifacts, evaluates models,
grants authority, or mutates host state. Records are append-only and references
are opaque.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .improvement_ledger import ImprovementError
from .store_core import append, read_all

_KIND = frozenset(("memory_policy", "skill", "tool_adapter", "prompt", "router", "planner", "reviewer", "workflow", "evaluator_config"))
_SCOPE = frozenset(("session", "project", "profile", "shared"))
_SIDE_EFFECT = frozenset(("none", "local_write", "external_write"))
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX = 256


def _bad(message, path=None):
    details = {"path": path} if path else {}
    raise ImprovementError("E_IMPROVEMENT_VALIDATION", message, details)


def _str(name, value, *, pattern=None, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX:
        _bad(f"{name} must be a non-empty string of at most {_MAX} characters", name)
    if pattern and not pattern.fullmatch(value):
        _bad(f"{name} has an invalid grammar", name)
    return value


def _digest(name, value):
    return _str(name, value, pattern=_DIGEST)


def _refs(name, value, *, required=True):
    if not isinstance(value, (list, tuple)) or isinstance(value, str) or len(value) > 16:
        _bad(f"{name} must contain 0-16 references", name)
    if required and not value:
        _bad(f"{name} must contain at least one reference", name)
    return [_str(f"{name}[{i}]", item) for i, item in enumerate(value)]


class SelfEvolvingMixin:
    """Host-facing 4.0 records; all authority remains outside MemKraft."""

    def _evolving_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "self-evolving" / "events.jsonl"

    def _evolving_append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        path = self._evolving_path()
        existing = read_all(path, include_tombstoned=True)
        if getattr(existing, "skipped", 0):
            raise ImprovementError(
                "E_IMPROVEMENT_LOG_CORRUPT",
                "self-evolving log is corrupt",
                {"skipped_lines": existing.skipped},
            )
        record = dict(record)
        identity_fields = {
            "experience": ("experience_id",),
            "capability_manifest": ("artifact_id",),
            "evaluator_receipt": ("proposal_id", "evaluation_kind", "candidate_digest", "corpus_digest"),
            "activation_observation": ("observation_id",),
        }.get(record.get("record_type"), ())
        for previous in existing.records:
            if previous.get("record_type") != record.get("record_type"):
                continue
            if identity_fields and all(previous.get(key) == record.get(key) for key in identity_fields):
                previous_payload = dict(previous)
                previous_digest = previous_payload.pop("record_digest", None)
                payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
                requested_digest = hashlib.sha256(payload).hexdigest()
                if previous_digest == requested_digest:
                    return previous
                raise ImprovementError(
                    "E_IMPROVEMENT_IDEMPOTENCY_MISMATCH",
                    "self-evolving identity was already used with different arguments",
                    {"record_type": record.get("record_type"), "identity_fields": list(identity_fields)},
                )
        payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        record["record_digest"] = hashlib.sha256(payload).hexdigest()
        return append(path, record)

    def experience_record(self, experience_id, *, artifact_kind, scope, task_ref,
                          input_snapshot_ref, outcome_ref, classification,
                          replayability, privacy="local_private", now,
                          model_ref=None, tool_refs=(), evidence_refs=()):
        _str("experience_id", experience_id, pattern=_ID)
        if artifact_kind not in _KIND: _bad("artifact_kind is not supported", "artifact_kind")
        if scope not in _SCOPE: _bad("scope is not supported", "scope")
        if classification not in ("success", "failure", "correction", "regression"): _bad("invalid classification", "classification")
        if replayability not in ("replayable", "partial", "not_replayable"): _bad("invalid replayability", "replayability")
        return self._evolving_append({"record_type":"experience", "schema_version":1, "emitted_at":_str("now", now), "experience_id":experience_id, "artifact_kind":artifact_kind, "scope":scope, "task_ref":_str("task_ref", task_ref), "input_snapshot_ref":_str("input_snapshot_ref", input_snapshot_ref), "outcome_ref":_str("outcome_ref", outcome_ref), "classification":classification, "replayability":replayability, "privacy":_str("privacy", privacy), "model_ref":_str("model_ref", model_ref, optional=True), "tool_refs":[_str("tool_ref", x) for x in tool_refs], "evidence_refs":[_str("evidence_ref", x) for x in evidence_refs]})

    def artifact_capability_manifest(self, artifact_id, *, artifact_kind, requires=(), provides=(), side_effect_class="none", data_scope="project", review_required=True, rollback_supported=True, now):
        _str("artifact_id", artifact_id, pattern=_ID)
        if artifact_kind not in _KIND: _bad("artifact_kind is not supported", "artifact_kind")
        if side_effect_class not in _SIDE_EFFECT or data_scope not in _SCOPE: _bad("invalid capability scope", "capability")
        if not isinstance(review_required, bool) or not isinstance(rollback_supported, bool): _bad("capability flags must be boolean", "capability")
        return self._evolving_append({"record_type":"capability_manifest", "schema_version":1, "emitted_at":_str("now", now), "artifact_id":artifact_id, "artifact_kind":artifact_kind, "requires":list(requires), "provides":list(provides), "side_effect_class":side_effect_class, "data_scope":data_scope, "review_required":review_required, "rollback_supported":rollback_supported})

    def evaluator_receipt(self, proposal_id, *, evaluator_id, evaluator_version, evaluation_kind, candidate_digest, base_revision_id=None, corpus_digest, metric_definition_ref, baseline_result_ref, candidate_result_ref, verdict, evidence_refs, environment_ref, exit_status, now):
        _str("proposal_id", proposal_id, pattern=_ID)
        for name, value in (("evaluator_id", evaluator_id), ("evaluator_version", evaluator_version), ("evaluation_kind", evaluation_kind), ("corpus_digest", corpus_digest), ("metric_definition_ref", metric_definition_ref), ("baseline_result_ref", baseline_result_ref), ("candidate_result_ref", candidate_result_ref), ("environment_ref", environment_ref), ("exit_status", exit_status)):
            _str(name, value)
        _digest("candidate_digest", candidate_digest)
        _str("base_revision_id", base_revision_id, optional=True)
        if verdict not in ("pass", "fail", "inconclusive"): _bad("invalid verdict", "verdict")
        refs = _refs("evidence_refs", evidence_refs, required=(verdict == "pass"))
        if verdict == "pass" and not refs: _bad("a passing receipt requires evidence", "evidence_refs")
        return self._evolving_append({"record_type":"evaluator_receipt", "schema_version":1, "emitted_at":_str("now", now), "proposal_id":proposal_id, "evaluator_id":evaluator_id, "evaluator_version":evaluator_version, "evaluation_kind":evaluation_kind, "candidate_digest":candidate_digest, "base_revision_id":base_revision_id, "corpus_digest":corpus_digest, "metric_definition_ref":metric_definition_ref, "baseline_result_ref":baseline_result_ref, "candidate_result_ref":candidate_result_ref, "verdict":verdict, "evidence_refs":refs, "environment_ref":environment_ref, "exit_status":exit_status})

    def activation_observation(self, artifact_id, revision_id, *, observation_id, health_status, outcome_ref, now, evidence_refs=()):
        _str("artifact_id", artifact_id, pattern=_ID); _str("revision_id", revision_id); _str("observation_id", observation_id, pattern=_ID)
        if health_status not in ("healthy", "degraded", "regressed", "unknown"): _bad("invalid health status", "health_status")
        return self._evolving_append({"record_type":"activation_observation", "schema_version":1, "emitted_at":_str("now", now), "observation_id":observation_id, "artifact_id":artifact_id, "revision_id":revision_id, "health_status":health_status, "outcome_ref":_str("outcome_ref", outcome_ref), "evidence_refs":[_str("evidence_ref", x) for x in evidence_refs]})

    def self_evolving_project(self):
        records = read_all(self._evolving_path(), include_tombstoned=True).records
        return {"records": records, "record_count": len(records), "experience_count": sum(r.get("record_type")=="experience" for r in records), "receipt_count": sum(r.get("record_type")=="evaluator_receipt" for r in records)}


__all__ = ["SelfEvolvingMixin"]
