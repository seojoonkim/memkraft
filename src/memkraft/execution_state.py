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

# §4.5 identity grammar.
_GOAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}/[a-z0-9][a-z0-9._-]{1,63}$")
_GATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_SCOPE_KEY = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,79}$")
_OPERATION_ID = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_RUN_ID = re.compile(r"^[a-z0-9]{8,64}$")

_PRIVACY = ("public_safe", "local_private", "private_pointer")
_AUTHORITY_CLAIM = ("agent", "human", "system")

# §4.9 caps used by this slice.
_MAX_STRING = 512
_MAX_LIST = 32


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
        if key not in _FINGERPRINT_EXCLUDED
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
        key for key in keys - _FINGERPRINT_EXCLUDED
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
                          declared: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate against the log, allocate ``event_seq``, and append one line.

        Everything after the lock is taken re-reads the log: the duplicate
        scan, the idempotency scan, and the sequence allocation all use the
        post-lock view, so a value observed before the lock can never decide
        an outcome.
        """
        record = dict(record)
        record["operation_id"] = _resolve_operation_id(record, operation_id)
        request_fingerprint = _record_fingerprint(record)

        path = self._execution_events_path()
        fd = self._governance_lock()
        try:
            existing = read_all(path, include_tombstoned=True).records

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

            record["event_seq"] = 1 + max(
                [0] + [
                    stored["event_seq"] for stored in existing
                    if isinstance(stored.get("event_seq"), int)
                ]
            )
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

        return self._execution_append(
            record, operation_id,
            declared={
                "requires": ("goal_declared", ("goal_id",)),
                "unique": ("gate_declared", ("goal_id", "gate_id")),
            },
        )
