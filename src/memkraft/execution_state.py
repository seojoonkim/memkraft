"""Execution declarations, the append path, and ``event_seq`` (plan §4.1–§4.5, §6.6).

One append-only log per base holds every execution record, because a total
order over all record types is the precondition of a deterministic projection.
``event_seq`` is allocated as ``max(seq over every line, tombstoned included) + 1``
*after* the governance lock is taken and the log re-read; anything read before
the lock is discarded. The log is never compacted (D-15), so that maximum never
decreases and there is no sequence-reuse hazard and no sidecar file.

Gates are advisory bookkeeping. ``authority_claim`` is not verified. A caller
with write access can waive any gate or record any receipt. Gates make what
happened *legible and attributable*; they do not make it *impossible*.

Zero dependencies — stdlib only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_projection import SETTLED_GATE_STATUSES, _TRANSITIONS, project
from .execution_protocol import (
    ConflictError,
    EvidenceError,
    ExecutionError,
    NotDeclaredError,
    ValidationError,
    canonical_timestamp,
    digest,
)
from .store_core import _unlock, append, read_all

__all__ = [
    "ExecutionStateMixin",
    "ConflictError",
    "EvidenceError",
    "ExecutionError",
    "NotDeclaredError",
    "ValidationError",
]

EXECUTION_SCHEMA = 1

# §5.5. Load-bearing and exactly three: the store mints a fresh ``id`` and
# ``created_at`` on every append, so a wider set would fail every honest retry.
_FINGERPRINT_EXCLUDED = frozenset({"id", "created_at", "event_seq"})

# Core-allocated under the same lock as ``event_seq`` and just as absent from a
# replayed request, so the fingerprint has to ignore it for the same reason
# (§8.2.1). Kept separate from ``_FINGERPRINT_EXCLUDED``, which §5.5 pins to the
# three envelope fields.
_ALLOCATED_FIELDS = frozenset({"observed_seq"})
_FINGERPRINT_IGNORED = _FINGERPRINT_EXCLUDED | _ALLOCATED_FIELDS

# §4.5 identity grammar.
_GOAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}/[a-z0-9][a-z0-9._-]{1,63}$")
_GATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_SCOPE_KEY = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,79}$")
_OPERATION_ID = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_RUN_ID = re.compile(r"^[a-z0-9]{8,64}$")

_PRIVACY = ("public_safe", "local_private", "private_pointer")
_AUTHORITY_CLAIM = ("agent", "human", "system")

# §4.9 caps used by this slice. Stated as arbitrary defaults, not derived.
_MAX_STRING = 512
_MAX_LIST = 32
MAX_GATES_PER_GOAL = 64

_VERDICT = ("pass", "fail")


def _pattern(field: str, value: Any, regex) -> str:
    """Return ``value`` when it is a string matching ``regex``, else fail."""
    if not isinstance(value, str):
        raise ValidationError("E_TYPE", "%s must be a string" % field, {"path": field})
    if not regex.match(value):
        raise ValidationError(
            "E_PATTERN", "%s does not match its grammar" % field, {"path": field}
        )
    return value


def _enum(field: str, value: Any, allowed) -> str:
    if value not in allowed:
        raise ValidationError(
            "E_PATTERN", "%s is outside its closed domain" % field, {"path": field}
        )
    return value


def _text(field: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("E_TYPE", "%s must be a string" % field, {"path": field})
    if not value:
        raise ValidationError("E_MISSING_FIELD", "%s is required" % field, {"path": field})
    if len(value) > _MAX_STRING:
        raise ValidationError(
            "E_LIMIT_EXCEEDED", "%s is longer than %d" % (field, _MAX_STRING),
            {"path": field},
        )
    return value


def _text_list(field: str, value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError("E_TYPE", "%s must be a list" % field, {"path": field})
    if len(value) > _MAX_LIST:
        raise ValidationError(
            "E_LIMIT_EXCEEDED", "%s is longer than %d" % (field, _MAX_LIST),
            {"path": field},
        )
    return [_text("%s[%d]" % (field, index), item) for index, item in enumerate(value)]


def _common_fields(record_type: str, goal_id: str, now: Any, *, privacy: str,
                   authority_claim: str, execution_run_id: Optional[str],
                   authority_verified: bool = False) -> Dict[str, Any]:
    """Build the §4.4 fields every record carries.

    ``authority_verified`` is always forced ``false``; a caller-supplied ``true``
    is a hard error, because a security-shaped field nobody checked is worse
    than no field at all.
    """
    if authority_verified:
        raise EvidenceError(
            "E_AUTHORITY_VERIFIED_FORBIDDEN",
            "authority_verified is never caller-supplied; it is always false",
        )
    record = {
        # Mirrors what ``store_core.append`` forces on write, so the fingerprint
        # of a candidate record equals the fingerprint of the stored one.
        "schema_version": 1,
        "record_type": record_type,
        "execution_schema": EXECUTION_SCHEMA,
        "goal_id": _pattern("goal_id", goal_id, _GOAL_ID),
        "emitted_at": canonical_timestamp(now),
        "privacy": _enum("privacy", privacy, _PRIVACY),
        "authority_claim": _enum("authority_claim", authority_claim, _AUTHORITY_CLAIM),
        "authority_verified": False,
    }
    if execution_run_id is not None:
        record["execution_run_id"] = _pattern(
            "execution_run_id", execution_run_id, _EXECUTION_RUN_ID
        )
    return record


def _record_fingerprint(record: Dict[str, Any]) -> str:
    """Digest the record minus exactly ``{id, created_at, event_seq}`` (§5.5)."""
    return digest({
        key: value for key, value in record.items()
        if key not in _FINGERPRINT_IGNORED
    })


def _resolve_operation_id(record: Dict[str, Any], operation_id: Optional[str]) -> str:
    """Return the caller's ``operation_id``, or the request digest by default."""
    if operation_id is None:
        return digest(record)
    if isinstance(operation_id, str) and _OPERATION_ID.match(operation_id):
        return operation_id
    if isinstance(operation_id, str) and 0 < len(operation_id) <= 128:
        return operation_id
    raise ValidationError(
        "E_PATTERN", "operation_id must be 64 lowercase hex characters or an opaque"
                     " string of at most 128 characters",
        {"path": "operation_id"},
    )


def _differing_keys(stored: Dict[str, Any], request: Dict[str, Any]) -> List[str]:
    keys = set(stored) | set(request)
    return sorted(
        key for key in keys - _FINGERPRINT_IGNORED
        if stored.get(key) != request.get(key)
    )


class ExecutionStateMixin:
    """Declarations and the sequenced append path of the execution log.

    Gates are advisory bookkeeping. ``authority_claim`` is not verified. A caller
    with write access can waive any gate or record any receipt. Gates make what
    happened *legible and attributable*; they do not make it *impossible*.
    """

    def _execution_dir(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "execution"

    def _execution_events_path(self) -> Path:
        """The single append-only execution log; it is never compacted (D-15)."""
        return self._execution_dir() / "events.jsonl"

    # -- append path -------------------------------------------------------

    def _execution_append(self, record: Dict[str, Any], operation_id: Optional[str],
                          declared: Optional[Dict[str, Any]] = None,
                          guard=None, bind=None) -> Dict[str, Any]:
        """Validate against the log, allocate ``event_seq``, and append one line.

        Everything after the lock is taken re-reads the log: the duplicate
        scan, the idempotency scan, the ``guard``, and the sequence allocation
        all use the post-lock view, so a value observed before the lock can
        never decide an outcome.

        ``guard`` is called as ``guard(existing, high_water)`` and either raises
        — leaving the file untouched, which is what every ``lines_delta == 0``
        case rests on — or returns the core-allocated fields to merge into the
        record. ``high_water`` is the sequence number the append is about to
        follow, so a guard can bind a record to the snapshot it observed (§8.2).

        ``bind`` has the same signature but runs *before* ``operation_id`` is
        resolved, so what it returns is part of both the idempotency key and the
        fingerprint. That ordering is what keeps §6.6 and §8.2 from colliding: a
        re-pass after a reopen observes a different watermark, so it is a
        different operation and reaches the guard that rejects it, while a true
        replay observes the identical watermark and still returns
        ``already_applied`` without appending. ``guard`` runs after the scan, so
        a replay is never re-guarded.
        """
        record = dict(record)

        path = self._execution_events_path()
        fd = self._governance_lock()
        try:
            existing = read_all(path, include_tombstoned=True).records
            high_water = max(
                [0] + [
                    stored["event_seq"] for stored in existing
                    if isinstance(stored.get("event_seq"), int)
                ]
            )
            if bind is not None:
                record.update(bind(existing, high_water))
            record["operation_id"] = _resolve_operation_id(record, operation_id)
            request_fingerprint = _record_fingerprint(record)

            for stored in existing:
                if stored.get("operation_id") != record["operation_id"]:
                    continue
                if _record_fingerprint(stored) == request_fingerprint:
                    return {
                        "outcome": "already_applied",
                        "record_id": stored.get("id"),
                        "event_seq": stored.get("event_seq"),
                        "operation_id": record["operation_id"],
                        "record_fingerprint": request_fingerprint,
                    }
                raise ConflictError(
                    "E_IDEMPOTENCY_MISMATCH",
                    "operation_id was already used with different arguments",
                    {
                        "stored_fingerprint": _record_fingerprint(stored),
                        "request_fingerprint": request_fingerprint,
                        "differing_keys": _differing_keys(stored, record),
                    },
                )

            if declared is not None:
                self._execution_check_declared(existing, record, declared)

            if guard is not None:
                record.update(guard(existing, high_water))
            record["event_seq"] = high_water + 1
            written = append(path, record)
        finally:
            _unlock(fd)
            os.close(fd)

        return {
            "outcome": "applied",
            "record_id": written["id"],
            "event_seq": written["event_seq"],
            "operation_id": written["operation_id"],
            "record_fingerprint": request_fingerprint,
        }

    @staticmethod
    def _execution_check_declared(existing: List[Dict[str, Any]],
                                  record: Dict[str, Any],
                                  declared: Dict[str, Any]) -> None:
        """Enforce the declaration preconditions of ``record`` against the log.

        ``declared["unique"]`` is the identity that must not already exist;
        ``declared.get("requires")`` is the identity that must.
        """
        for kind, identity in (("requires", declared.get("requires")),
                               ("unique", declared["unique"])):
            if identity is None:
                continue
            record_type, keys = identity
            present = any(
                stored.get("record_type") == record_type
                and all(stored.get(key) == record[key] for key in keys)
                for stored in existing
            )
            if kind == "requires" and not present:
                raise NotDeclaredError(
                    "E_NOT_DECLARED", "%s was never declared" % record_type,
                    {"record_type": record_type},
                )
            if kind == "unique" and present:
                raise ConflictError(
                    "E_ALREADY_DECLARED", "%s already exists" % record_type,
                    {"record_type": record_type},
                )

    # -- declarations ------------------------------------------------------

    def goal_declare(self, goal_id, title, intent, constraints, success_criteria, *,
                     now, owner_hint=None, parent_goal_id=None,
                     privacy="local_private", authority_claim="agent",
                     execution_run_id=None, operation_id=None) -> Dict[str, Any]:
        """Declare a goal. Immutable once declared; removable only by tombstone.

        ``now`` is injected and never validated against the system clock: a
        ``now`` far in the past or future is accepted and produces a
        deterministic result. That is the point.
        """
        record = _common_fields(
            "goal_declared", goal_id, now, privacy=privacy,
            authority_claim=authority_claim, execution_run_id=execution_run_id,
        )
        record["title"] = _text("title", title)
        record["intent"] = _text("intent", intent)
        record["constraints"] = _text_list("constraints", constraints)
        record["success_criteria"] = _text_list("success_criteria", success_criteria)
        if owner_hint is not None:
            record["owner_hint"] = _text("owner_hint", owner_hint)
        if parent_goal_id is not None:
            record["parent_goal_id"] = _pattern("parent_goal_id", parent_goal_id, _GOAL_ID)

        return self._execution_append(
            record, operation_id,
            declared={"unique": ("goal_declared", ("goal_id",))},
        )

    def gate_declare(self, goal_id, gate_id, description, verification, *,
                     now, required=True, scope_key=None,
                     privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Declare a gate on a goal. Gates are advisory bookkeeping (§4.8)."""
        record = _common_fields(
            "gate_declared", goal_id, now, privacy=privacy,
            authority_claim="agent", execution_run_id=None,
        )
        record["gate_id"] = _pattern("gate_id", gate_id, _GATE_ID)
        record["description"] = _text("description", description)
        if not isinstance(verification, dict):
            raise ValidationError(
                "E_TYPE", "verification must be an object", {"path": "verification"}
            )
        record["verification"] = {
            "check_kind": _text("verification.check_kind", verification.get("check_kind")),
            "check_ref": _text("verification.check_ref", verification.get("check_ref")),
        }
        if not isinstance(required, bool):
            raise ValidationError("E_TYPE", "required must be a bool", {"path": "required"})
        record["required"] = required
        record["scope_key"] = _pattern(
            "scope_key", record["gate_id"] if scope_key is None else scope_key, _SCOPE_KEY
        )

        def guard(existing, high_water):
            # Counted under the append lock against the post-lock view, so two
            # racing declarations cannot both read 63 and both append (§4.9).
            declared_gates = sum(
                1 for stored in existing
                if stored.get("record_type") == "gate_declared"
                and stored.get("goal_id") == record["goal_id"]
            )
            if declared_gates >= MAX_GATES_PER_GOAL:
                raise ValidationError(
                    "E_GATE_CAP",
                    "a goal holds at most %d gates" % MAX_GATES_PER_GOAL,
                    {"limit": MAX_GATES_PER_GOAL, "declared": declared_gates},
                )
            return {}

        return self._execution_append(
            record, operation_id,
            declared={
                "requires": ("goal_declared", ("goal_id",)),
                "unique": ("gate_declared", ("goal_id", "gate_id")),
            },
            guard=guard,
        )

    # -- receipts ----------------------------------------------------------

    def receipt_record(self, goal_id, gate_id, verdict, content_sha256, summary, *,
                       now, observed_at=None, provenance_id=None, artifact_path=None,
                       execution_run_id=None, privacy="local_private",
                       operation_id=None) -> Dict[str, Any]:
        """Record evidence for a gate. Receipts are inert: they move nothing (§8.1).

        ``content_sha256`` is validated for **format only**. Core never hashes
        anything to check it, so a caller with write access can manufacture a
        receipt; ``provenance_id`` is what makes one traceable, and its absence
        is counted rather than blocked (§4.8).
        """
        record = _common_fields(
            "evidence_receipt", goal_id, now, privacy=privacy,
            authority_claim="agent", execution_run_id=execution_run_id,
        )
        record["gate_id"] = _pattern("gate_id", gate_id, _GATE_ID)
        record["verdict"] = _enum("verdict", verdict, _VERDICT)
        record["content_sha256"] = _pattern("content_sha256", content_sha256, _SHA256)
        record["summary"] = _text("summary", summary)
        if observed_at is not None:
            record["observed_at"] = canonical_timestamp(observed_at)
        if provenance_id is not None:
            record["provenance_id"] = _text("provenance_id", provenance_id)
        if artifact_path is not None:
            # A reference, never inlined content, and local_private by default.
            record["artifact_path"] = _text("artifact_path", artifact_path)

        def guard(existing, high_water):
            # §8.2.1: the snapshot this receipt observed. Stored explicitly, not
            # derived, so the binding survives any change to allocation.
            return {"observed_seq": high_water}

        result = self._execution_append(
            record, operation_id,
            declared={
                "requires": ("gate_declared", ("goal_id", "gate_id")),
                "unique": None,
            },
            guard=guard,
        )
        result["gate_status_unchanged"] = True
        result["warnings"] = (
            [] if provenance_id is not None else ["W_RECEIPT_UNPROVENANCED"]
        )
        return result

    # -- transitions -------------------------------------------------------

    def gate_transition(self, goal_id, gate_id, to_status, *, now, receipt_id=None,
                        reopen_reason=None, authority_claim="agent",
                        privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Move a gate along ``_TRANSITIONS``. Gates are advisory bookkeeping (§4.8).

        Every guard is driven from the rule's metadata, so the table is the only
        authority on what a transition needs. A rejected transition raises before
        the append, leaving the file untouched.
        """
        record = _common_fields(
            "gate_transition", goal_id, now, privacy=privacy,
            authority_claim=authority_claim, execution_run_id=None,
        )
        record["gate_id"] = _pattern("gate_id", gate_id, _GATE_ID)
        record["to_status"] = _text("to_status", to_status)
        supplied = {}
        if reopen_reason is not None:
            supplied["reopen_reason"] = _text("reopen_reason", reopen_reason)
        if receipt_id is not None:
            supplied["receipt_id"] = _text("receipt_id", receipt_id)
        record.update(supplied)

        warnings: List[str] = []

        def bind(existing, high_water):
            # §8.2.2 made part of this record's identity: a pass observed before
            # a reopen and a pass observed after it are different operations,
            # which is what stops the second from deduplicating into the first.
            gate = _projected_gate(existing, now, record["goal_id"], record["gate_id"])
            return {"observed_reopened_at_seq": gate["reopened_at_seq"]}

        def guard(existing, high_water):
            gate = _projected_gate(existing, now, record["goal_id"], record["gate_id"])
            rule = _rule("gate", gate["status"], record["to_status"], {
                "gate_id": record["gate_id"], "from_status": gate["status"],
            })
            _require_fields(rule, supplied)
            if rule.waives:
                if record["authority_claim"] != "human":
                    raise EvidenceError(
                        "E_AUTHORITY_CLAIM_REQUIRED",
                        "waiving a gate requires an (unverified) human authority claim",
                        {"gate_id": record["gate_id"]},
                    )
                warnings.append("W_WAIVER_UNVERIFIED")
            if rule.evidence is not None:
                _select_receipt(existing, record, rule.evidence,
                                gate["reopened_at_seq"], supplied.get("receipt_id"))
            return {}

        result = self._execution_append(
            record, operation_id,
            declared={
                "requires": ("gate_declared", ("goal_id", "gate_id")),
                "unique": None,
            },
            guard=guard, bind=bind,
        )
        result["gate_status"] = record["to_status"]
        result["warnings"] = warnings
        return result

    def goal_transition(self, goal_id, to_status, *, now, reason=None,
                        authority_claim="agent", privacy="local_private",
                        operation_id=None) -> Dict[str, Any]:
        """Move a goal along ``_TRANSITIONS``.

        ``open → satisfied`` discharges against the projection, not against a
        claim: every ``required`` gate must be settled, and the ones that are not
        are returned as ``details["blockers"]``.
        """
        record = _common_fields(
            "goal_transition", goal_id, now, privacy=privacy,
            authority_claim=authority_claim, execution_run_id=None,
        )
        record["to_status"] = _text("to_status", to_status)
        supplied = {}
        if reason is not None:
            supplied["reason"] = _text("reason", reason)
        record.update(supplied)

        def guard(existing, high_water):
            state = project(existing, now, record["goal_id"])
            rule = _rule("goal", state["goal_status"], record["to_status"],
                         {"from_status": state["goal_status"]})
            _require_fields(rule, supplied)
            if rule.settles_gates:
                blockers = [
                    gate["gate_id"] for gate in state["gates"]
                    if gate["required"] and gate["status"] not in SETTLED_GATE_STATUSES
                ]
                if blockers:
                    raise ExecutionError(
                        "E_INVALID_TRANSITION",
                        "required gates are not settled",
                        {"blockers": blockers, "from_status": state["goal_status"]},
                    )
            return {}

        result = self._execution_append(
            record, operation_id,
            declared={"requires": ("goal_declared", ("goal_id",)), "unique": None},
            guard=guard,
        )
        result["goal_status"] = record["to_status"]
        result["warnings"] = []
        return result


def _rule(kind: str, from_status: Optional[str], to_status: str, details):
    """Return the ``_TRANSITIONS`` rule for the triple, or fail closed.

    Any triple absent from the table is rejected, which is what makes ``waived``
    absorbing without a single status branch.
    """
    rule = _TRANSITIONS.get((kind, from_status, to_status))
    if rule is None:
        raise ExecutionError(
            "E_INVALID_TRANSITION",
            "%s %s → %s is not a declared transition" % (kind, from_status, to_status),
            details,
        )
    return rule


def _require_fields(rule, supplied: Dict[str, Any]) -> None:
    """Enforce ``rule.requires`` — the table states the guard, not the code."""
    for field in rule.requires:
        if supplied.get(field) is None:
            raise ValidationError(
                "E_MISSING_FIELD", "%s is required for this transition" % field,
                {"path": field},
            )


def _projected_gate(existing, now, goal_id: str, gate_id: str) -> Dict[str, Any]:
    """Return the gate's current projected state, computed under the append lock."""
    state = project(existing, now, goal_id)
    for gate in state["gates"]:
        if gate["gate_id"] == gate_id:
            return gate
    raise NotDeclaredError(
        "E_NOT_DECLARED", "gate_declared was never declared",
        {"record_type": "gate_declared"},
    )


def _select_receipt(existing, record, verdict: str, reopened_at_seq: int,
                    receipt_id: Optional[str]) -> Dict[str, Any]:
    """Return the receipt discharging this transition, or fail (§8.2.3–§8.2.5).

    An explicitly named receipt is never silently replaced by a fresher one: the
    caller asserted *which* evidence they are standing on, and substituting a
    different record under that assertion is the exact dishonesty the watermark
    exists to prevent.
    """
    receipts = [
        stored for stored in existing
        if stored.get("record_type") == "evidence_receipt"
        and stored.get("goal_id") == record["goal_id"]
        and stored.get("gate_id") == record["gate_id"]
    ]
    if receipt_id is not None:
        named = [stored for stored in receipts if stored.get("id") == receipt_id]
        candidates = [stored for stored in named if stored.get("verdict") == verdict]
    else:
        candidates = [stored for stored in receipts if stored.get("verdict") == verdict]

    if not candidates:
        raise EvidenceError(
            "E_EVIDENCE_REQUIRED",
            "this transition requires a %r receipt for the gate" % verdict,
            {"gate_id": record["gate_id"], "verdict": verdict},
        )

    receipt = max(candidates, key=lambda stored: stored.get("event_seq") or 0)
    receipt_event_seq = receipt.get("event_seq") or 0
    if receipt_event_seq <= reopened_at_seq:
        raise EvidenceError(
            "E_EVIDENCE_STALE",
            "the receipt predates the gate's most recent reopen",
            {"receipt_event_seq": receipt_event_seq,
             "reopened_at_seq": reopened_at_seq},
        )
    return receipt
