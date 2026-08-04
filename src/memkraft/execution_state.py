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
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_projection import (
    SETTLED_GATE_STATUSES,
    _TRANSITIONS,
    project,
    project_leases,
)
from .execution_protocol import (
    ConflictError,
    EvidenceError,
    ExecutionError,
    MAX_SAFE_INTEGER,
    LeaseError,
    NotDeclaredError,
    ValidationError,
    canonical_timestamp,
    digest,
    parse_timestamp,
)
from .store_core import StoreBusy, _unlock, append, read_all

__all__ = [
    "ExecutionStateMixin",
    "ConflictError",
    "EvidenceError",
    "ExecutionError",
    "LeaseError",
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
_ALLOCATED_FIELDS = frozenset({
    "observed_seq", "fence_token", "supersedes_lease_id", "supersede_reason",
})
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
MAX_ACTIVE_LEASES_PER_GOAL = 16
_MAX_HOLDER = 256
_MIN_TTL_SECONDS = 1
_MAX_TTL_SECONDS = 86400

_VERDICT = ("pass", "fail")

#: §4.6, D-18. ``revoked`` is deliberately absent: no operation produces it, and
#: a dead enum value invites someone to invent semantics for it.
SUPERSEDE_REASONS = ("expired", "released_by_holder")

#: §7.4. Every apply path bounds its wait on the governance lock, because a
#: MemKraft write inside a fail-closed hook must fail fast rather than convoy.
EXECUTION_LOCK_TIMEOUT_S = 2.0


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


def _holder(field: str, value: Any) -> str:
    """Validate a lease holder string. Opaque to core: never parsed, only compared."""
    if not isinstance(value, str):
        raise ValidationError("E_TYPE", "%s must be a string" % field, {"path": field})
    if not value:
        raise ValidationError("E_MISSING_FIELD", "%s is required" % field, {"path": field})
    if len(value) > _MAX_HOLDER:
        raise ValidationError(
            "E_LIMIT_EXCEEDED", "%s is longer than %d" % (field, _MAX_HOLDER),
            {"path": field},
        )
    return value


def _bounded_int(field: str, value: Any, low: int, high: int) -> int:
    """Validate a closed-range integer. ``bool`` is rejected before ``int``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("E_TYPE", "%s must be an integer" % field, {"path": field})
    if not low <= value <= high:
        raise ValidationError(
            "E_LIMIT_EXCEEDED", "%s is outside [%d, %d]" % (field, low, high),
            {"path": field, "min": low, "max": high},
        )
    return value


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
                          guard=None, bind=None, fence=None) -> Dict[str, Any]:
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

        ``fence`` is called as ``fence(existing)`` before anything else, because
        a writer holding a superseded token has no business reaching the
        idempotency scan at all (§7.3).
        """
        record = dict(record)

        path = self._execution_events_path()
        try:
            try:
                fd = self._governance_lock(timeout_s=EXECUTION_LOCK_TIMEOUT_S)
            except TypeError as error:
                # Additive compatibility for existing test/plugin overrides of
                # the historical no-argument hook.  Only retry when Python says
                # the override rejected this exact new keyword; a TypeError
                # raised inside the lock implementation must still propagate.
                if "unexpected keyword argument 'timeout_s'" not in str(error):
                    raise
                fd = self._governance_lock()
        except StoreBusy:
            # §7.4: fail fast rather than convoy behind a hook that already
            # timed out. Retryable, and nothing was written.
            raise ExecutionError(
                "E_STORE_BUSY", "the execution store is locked by another writer"
            )
        try:
            existing = read_all(path, include_tombstoned=True).records
            high_water = max(
                [0] + [
                    stored["event_seq"] for stored in existing
                    if isinstance(stored.get("event_seq"), int)
                ]
            )
            if fence is not None:
                fence(existing)
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
                        "record": stored,
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
            "record": written,
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
                     now, required=True, scope_key=None, fence_token=None,
                     privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Declare a gate on a goal. Gates are advisory bookkeeping (§4.8).

        Fence-protected under the gate's ``scope_key`` (§7.3).
        """
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
            fence=_fence(now, record, record["scope_key"], fence_token),
        )

    # -- receipts ----------------------------------------------------------

    def receipt_record(self, goal_id, gate_id, verdict, content_sha256, summary, *,
                       now, fence_token=None, observed_at=None, provenance_id=None,
                       artifact_path=None, execution_run_id=None,
                       privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Record evidence for a gate. Receipts are inert: they move nothing (§8.1).

        Fence-protected under the gate's *declared* ``scope_key`` (§7.3).

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
            fence=_fence(now, record, _gate_scope(record["goal_id"], record["gate_id"]),
                         fence_token),
        )
        result["gate_status_unchanged"] = True
        result["warnings"] = (
            [] if provenance_id is not None else ["W_RECEIPT_UNPROVENANCED"]
        )
        return result

    # -- transitions -------------------------------------------------------

    def gate_transition(self, goal_id, gate_id, to_status, *, now, fence_token=None,
                        receipt_id=None, reopen_reason=None, authority_claim="agent",
                        privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Move a gate along ``_TRANSITIONS``. Gates are advisory bookkeeping (§4.8).

        Every guard is driven from the rule's metadata, so the table is the only
        authority on what a transition needs. A rejected transition raises before
        the append, leaving the file untouched.

        Fence-protected under the gate's declared ``scope_key`` (§7.3).
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
            fence=_fence(now, record, _gate_scope(record["goal_id"], record["gate_id"]),
                         fence_token),
        )
        result["gate_status"] = record["to_status"]
        result["warnings"] = warnings
        return result

    def goal_transition(self, goal_id, to_status, *, now, reason=None,
                        fence_token=None, authority_claim="agent",
                        privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """Move a goal along ``_TRANSITIONS``.

        ``open → satisfied`` discharges against the projection, not against a
        claim: every ``required`` gate must be settled, and the ones that are not
        are returned as ``details["blockers"]``.

        Fence-protected under the literal scope ``"goal"`` (§7.3). The literal is
        what makes a goal-wide mutation exclusive against a goal-wide lease
        without inventing a scope grammar for the goal itself.
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
            fence=_fence(now, record, "goal", fence_token),
        )
        result["goal_status"] = record["to_status"]
        result["warnings"] = []
        return result

    # -- leases ------------------------------------------------------------

    def lease_acquire(self, goal_id, scope_key, holder, ttl_seconds, *, now,
                      expected_fence=None, privacy="local_private",
                      operation_id=None) -> Dict[str, Any]:
        """Take or renew the exclusive lease on ``scope_key`` (§7.1, §7.2).

        ``fence_token`` is an **output**, not an input. Demanding it on a first
        acquisition would ask the caller for a value it cannot know, for a lease
        it does not hold; acquisition mints it and every lease-protected
        mutation presents it back.

        Grant, renewal, and reclaim are all *one* ``lease_grant`` line. Expiry is
        never appended — it is a function of the injected ``now`` (§4.6) — so a
        crashed holder cannot wedge a scope forever, and no ``expired`` record
        type exists to be kept consistent with anything.
        """
        record = _common_fields(
            "lease_grant", goal_id, now, privacy=privacy,
            authority_claim="agent", execution_run_id=None,
        )
        record["scope_key"] = _pattern("scope_key", scope_key, _SCOPE_KEY)
        record["holder"] = _holder("holder", holder)
        record["ttl_seconds"] = _bounded_int(
            "ttl_seconds", ttl_seconds, _MIN_TTL_SECONDS, _MAX_TTL_SECONDS
        )
        record["expires_at"] = canonical_timestamp(
            parse_timestamp(record["emitted_at"])
            + timedelta(seconds=record["ttl_seconds"])
        )
        if expected_fence is not None:
            _bounded_int("expected_fence", expected_fence, 1, MAX_SAFE_INTEGER)

        def guard(existing, high_water):
            # Projected under the append lock, so two racing acquirers cannot
            # both observe a free scope and both append (G4).
            leases = project_leases(existing, now, record["goal_id"])
            held = leases["active"].get(record["scope_key"])
            previous = held if held is not None \
                else leases["last"].get(record["scope_key"])

            if expected_fence is not None:
                current = None if previous is None else previous["fence_token"]
                if expected_fence != current:
                    raise LeaseError(
                        "E_FENCE_STALE",
                        "expected_fence does not match the scope's current fence",
                        {"scope_key": record["scope_key"],
                         "current_fence_token": leases["max_fence"],
                         "presented_fence_token": expected_fence},
                    )

            if held is not None:
                if held["holder"] != record["holder"]:
                    # SC-02: the competitor learns *that* the scope is held, but
                    # never reads a holder string it was not given. Leaking a
                    # runtime-minted holder is information disclosure.
                    raise LeaseError(
                        "E_LEASE_HELD", "the scope is held by another holder",
                        {"scope_key": record["scope_key"],
                         "holder_digest": digest({"holder": held["holder"]}),
                         "expires_at": held["expires_at"],
                         "current_fence_token": leases["max_fence"]},
                    )
                # Same holder, new operation: a renewal supersedes one's own
                # lease, and so ends it exactly the way a release does.
                reason = "released_by_holder"
            elif len(leases["active"]) >= MAX_ACTIVE_LEASES_PER_GOAL:
                raise LeaseError(
                    "E_LEASE_CAP",
                    "a goal holds at most %d active leases"
                    % MAX_ACTIVE_LEASES_PER_GOAL,
                    {"limit": MAX_ACTIVE_LEASES_PER_GOAL,
                     "active": len(leases["active"])},
                )
            elif previous is None:
                reason = None
            else:
                # A reclaim states exactly how the scope came free, so the log
                # distinguishes an orderly handback from a holder that vanished.
                reason = "released_by_holder" if previous["released"] else "expired"

            return {
                # Goal-wide and strictly monotonic: a token minted for one scope
                # is never reused for another, which is what makes comparing a
                # presented token against the goal high-water mark total.
                "fence_token": leases["max_fence"] + 1,
                "supersedes_lease_id": None if reason is None else previous["lease_id"],
                "supersede_reason": reason,
            }

        result = self._execution_append(
            record, operation_id,
            declared={"requires": ("goal_declared", ("goal_id",)), "unique": None},
            guard=guard,
        )
        return _lease_result(result, record["scope_key"])

    def lease_release(self, goal_id, scope_key, lease_id, *, now, released_by=None,
                      privacy="local_private", operation_id=None) -> Dict[str, Any]:
        """End the current lease on ``scope_key`` (§7.2).

        Only the scope's *current* lease can be released. A superseded holder
        releasing by its own stale ``lease_id`` would otherwise cancel a lease it
        no longer owns — the same hazard fencing exists to close — so it is
        refused with the same code and leaves the line count unchanged.
        """
        record = _common_fields(
            "lease_release", goal_id, now, privacy=privacy,
            authority_claim="agent", execution_run_id=None,
        )
        record["scope_key"] = _pattern("scope_key", scope_key, _SCOPE_KEY)
        record["lease_id"] = _text("lease_id", lease_id)
        if released_by is not None:
            record["released_by"] = _holder("released_by", released_by)

        def guard(existing, high_water):
            leases = project_leases(existing, now, record["goal_id"])
            current = leases["last"].get(record["scope_key"])
            if current is None:
                raise ConflictError(
                    "E_CONFLICT", "no lease has ever been granted for this scope",
                    {"scope_key": record["scope_key"]},
                )
            if current["lease_id"] != record["lease_id"]:
                raise LeaseError(
                    "E_FENCE_STALE", "this lease has been superseded",
                    {"scope_key": record["scope_key"],
                     "current_fence_token": leases["max_fence"]},
                )
            if current["released"]:
                raise ConflictError(
                    "E_CONFLICT", "this lease has already been released",
                    {"scope_key": record["scope_key"]},
                )
            if released_by is not None and released_by != current["holder"]:
                raise LeaseError(
                    "E_LEASE_HELD", "the scope is held by another holder",
                    {"scope_key": record["scope_key"],
                     "holder_digest": digest({"holder": current["holder"]}),
                     "current_fence_token": leases["max_fence"]},
                )
            return {}

        result = self._execution_append(
            record, operation_id,
            declared={"requires": ("goal_declared", ("goal_id",)), "unique": None},
            guard=guard,
        )
        return _lease_result(result, record["scope_key"])


def _lease_result(result: Dict[str, Any], scope_key: str) -> Dict[str, Any]:
    """Project an append result into the lease response shape.

    Read off the *stored* record rather than the request, so a replay reports
    the fence and expiry of the line that actually exists: an ``already_applied``
    must never look as though it refreshed a TTL it left untouched (§6.6).
    """
    stored = result["record"]
    result["scope_key"] = scope_key
    result["lease_id"] = (
        stored.get("lease_id") if stored.get("record_type") == "lease_release"
        else result["record_id"]
    )
    result["fence_token"] = stored.get("fence_token")
    result["expires_at"] = stored.get("expires_at")
    result["supersedes_lease_id"] = stored.get("supersedes_lease_id")
    result["supersede_reason"] = stored.get("supersede_reason")
    result["warnings"] = []
    return result


def _fence(now, record: Dict[str, Any], scope, fence_token: Optional[int]):
    """Return the §7.3 fence check for ``record``, to run under the append lock.

    ``scope`` is either the literal derived scope key or a callable taking the
    post-lock records and returning it — a gate's scope is declared, so it can
    only be resolved against the log. A ``None`` scope means the target was
    never declared, and the declaration check gives the better error.

    The two rules are symmetric and both load-bearing. On an *unleased* scope a
    token is a hard error rather than an ignored field, so a token that means
    nothing cannot be cargo-culted into a write; on a *leased* one it becomes
    mandatory, so forgetting it is a hard error rather than a silent bypass.
    Staleness is measured against the goal-wide high-water mark, not the scope's
    own token, because fences are minted goal-wide and strictly monotonic.
    """
    if fence_token is not None:
        _bounded_int("fence_token", fence_token, 1, MAX_SAFE_INTEGER)

    def check(existing: List[Dict[str, Any]]) -> None:
        scope_key = scope(existing) if callable(scope) else scope
        if scope_key is None:
            return
        leases = project_leases(existing, now, record["goal_id"])
        held = leases["active"].get(scope_key)
        if held is None:
            if fence_token is not None:
                raise LeaseError(
                    "E_UNKNOWN_FIELD",
                    "fence_token was supplied for a scope that holds no lease",
                    {"path": "fence_token", "scope_key": scope_key},
                )
            return
        if fence_token is None:
            raise LeaseError(
                "E_FENCE_REQUIRED",
                "this scope is leased; the write must present its fence_token",
                {"scope_key": scope_key,
                 "current_fence_token": leases["max_fence"]},
            )
        if fence_token < leases["max_fence"]:
            raise LeaseError(
                "E_FENCE_STALE", "the fence_token has been superseded",
                {"scope_key": scope_key,
                 "current_fence_token": leases["max_fence"],
                 "presented_fence_token": fence_token},
            )

    return check


def _gate_scope(goal_id: str, gate_id: str):
    """Return a resolver for the gate's *declared* ``scope_key`` (§7.3).

    The scope a gate write is fenced under is the one the declaration fixed, not
    one the caller may restate: letting a writer choose its own scope would let
    it choose an unleased one and step around the fence entirely.
    """
    def resolve(existing: List[Dict[str, Any]]) -> Optional[str]:
        for stored in existing:
            if (stored.get("record_type") == "gate_declared"
                    and stored.get("goal_id") == goal_id
                    and stored.get("gate_id") == gate_id):
                return stored.get("scope_key")
        return None

    return resolve


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
