"""Continual Improvement Ledger (plan §4, §5, §6).

One append-only log, ``<base_dir>/.memkraft/improvement/events.jsonl``, holds the
causal chain from proposal to artifact revision. It is never compacted, so
``event_seq`` — allocated as ``high_water + 1`` *after* the governance lock is
taken and the log re-read — never decreases and is never reused.

The ledger records decisions; it never makes them. It stores no artifact body,
no diff, no patch, no command. ``locator`` and every ``*_ref`` field are opaque
strings core stores and compares but never fetches, parses, or executes.
``authority_verified`` is forced ``false`` on every record: a security-shaped
field nobody checked is worse than no field at all. ``host_authorization_ref``
is an attribution breadcrumb the host can later audit, **not** an authorization
check MemKraft performed.

Core evaluates nothing. ``evaluation_receipt.verdict`` is the caller's, and the
only thing the promotion gate (§5.4) does with it is check that every kind the
proposal declared has a latest receipt that passed, still bound to the content
and base it judged. There is no code path from a receipt to a promoted
proposal: promotion is a separate, explicit, guarded append — and it never
activates anything.

Deliberate asymmetry (§6.3): a damaged ledger stays readable and auditable but
stops accepting new decisions, because a guard reasoning over a partial view
could approve past a record it cannot see.

Python 3.9, stdlib only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_protocol import (
    ExecutionError,
    canonical_timestamp,
    digest,
)
from .store_core import StoreBusy, _unlock, append, read_all

__all__ = ["ImprovementLedgerMixin", "ImprovementError", "IMPROVEMENT_ERROR_REGISTRY"]

IMPROVEMENT_SCHEMA = 1

#: §7.4 precedent: bound the wait so a write inside a fail-closed hook fails
#: fast rather than convoying behind another writer.
IMPROVEMENT_LOCK_TIMEOUT_S = 2.0

#: §6.4. This feature's own ``(error_class, retryable)`` table. Deliberately
#: **not** merged into ``execution_protocol._ERROR_REGISTRY``: that registry is
#: part of the frozen MKEP/0 wire contract, and the improvement ledger has no
#: protocol surface. The exceptions still subclass ``ExecutionError`` so callers
#: already catching MemKraft errors keep working.
IMPROVEMENT_ERROR_REGISTRY = {
    "E_IMPROVEMENT_VALIDATION": ("input", False),
    "E_IMPROVEMENT_PATTERN": ("input", False),
    "E_IMPROVEMENT_NOT_FOUND": ("state", False),
    "E_IMPROVEMENT_ALREADY_EXISTS": ("state", False),
    "E_IMPROVEMENT_IDEMPOTENCY_MISMATCH": ("idempotency", False),
    "E_IMPROVEMENT_TRANSITION": ("state", False),
    "E_IMPROVEMENT_EVALUATION_MISSING": ("evidence", False),
    "E_IMPROVEMENT_EVALUATION_FAILED": ("evidence", False),
    "E_IMPROVEMENT_EVALUATION_STALE": ("evidence", False),
    "E_IMPROVEMENT_ACTIVATION_CONFLICT": ("state", False),
    "E_IMPROVEMENT_ROLLBACK_TARGET": ("state", False),
    "E_IMPROVEMENT_SCOPE_UNAUTHORIZED": ("evidence", False),
    "E_IMPROVEMENT_LOG_CORRUPT": ("integrity", False),
    "E_IMPROVEMENT_STORE_BUSY": ("io", True),
    "E_AUTHORITY_VERIFIED_FORBIDDEN": ("evidence", False),
}


class ImprovementError(ExecutionError):
    """An improvement-ledger error carrying its stable ``code``.

    ``ExecutionError.__init__`` resolves its class/retryable pair from the
    frozen MKEP registry, so it cannot construct these codes. This subclass
    resolves from :data:`IMPROVEMENT_ERROR_REGISTRY` instead and exposes the
    identical ``code`` / ``error_class`` / ``retryable`` / ``details`` surface,
    which is what lets it stay out of MKEP's table without callers noticing.
    """

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        error_class, retryable = IMPROVEMENT_ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


# -- grammars and bounds (§4) ------------------------------------------------

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
# Revision examples and lineage use compact ids such as ``r1``; keep the
# broader proposal/artifact grammar strict while allowing two-character
# revision ids explicitly.
_REVISION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_RUN_ID = re.compile(r"^[a-z0-9]{8,64}$")

_PRIVACY = ("public_safe", "local_private", "private_pointer")
_AUTHORITY_CLAIM = ("agent", "human", "system")
_SCOPE = ("session", "project", "profile", "shared")
#: §4.1: a bare model claim covers only the caller's own working tree.
_WIDE_SCOPES = ("profile", "shared")

_MAX_STRING = 512
_MAX_REF = 256
_MAX_LIST = 8
_MAX_EVALUATION_KIND = 80
_MAX_REQUIRED_EVALUATIONS = 8

#: §5.5 exactly, and load-bearing: the store mints a fresh ``id`` and
#: ``created_at`` on every append and core allocates ``event_seq``, so a wider
#: set would fail every honest retry and a narrower one would never match.
#: ``from_revision_id`` joins them for the same reason — it is allocated by core
#: from the value observed under the lock, never supplied, so a retry that
#: rebuilds the request cannot know it. It carries no information the
#: fingerprint loses: CAS only appends when the observed active revision equals
#: ``expected_active_revision_id``, which *is* fingerprinted.
_FINGERPRINT_EXCLUDED = frozenset({
    "id", "created_at", "event_seq", "from_revision_id",
})

_VERDICT = ("pass", "fail", "inconclusive")

#: §5.6. ``rollback`` is accepted by the schema here; the honesty guard that
#: makes the label mean something lands with the rollback wrapper.
_ACTIVATION_KIND = ("activate", "rollback")

_DRAFT = "draft"
_UNDER_EVALUATION = "under_evaluation"
_PROMOTED = "promoted"
_REJECTED = "rejected"

_STATUSES = (_DRAFT, _UNDER_EVALUATION, _PROMOTED, _REJECTED)

#: §5.3, complete. Every pair outside this set is ``E_IMPROVEMENT_TRANSITION``,
#: which is what makes ``rejected`` and ``promoted`` terminal and forbids both
#: ``draft -> promoted`` and every self-transition without a second table.
_TRANSITIONS = frozenset({
    (_DRAFT, _UNDER_EVALUATION),
    (_DRAFT, _REJECTED),
    (_UNDER_EVALUATION, _PROMOTED),
    (_UNDER_EVALUATION, _REJECTED),
})


def _fail(code: str, message: str, field: Optional[str] = None,
          **extra: Any) -> None:
    details = dict(extra)
    if field is not None:
        details["path"] = field
    raise ImprovementError(code, message, details)


def _pattern(field: str, value: Any, regex) -> str:
    if not isinstance(value, str):
        _fail("E_IMPROVEMENT_VALIDATION", "%s must be a string" % field, field)
    if not regex.match(value):
        _fail("E_IMPROVEMENT_PATTERN",
              "%s does not match its grammar" % field, field)
    return value


def _enum(field: str, value: Any, allowed) -> str:
    if value not in allowed:
        _fail("E_IMPROVEMENT_PATTERN",
              "%s is outside its closed domain" % field, field)
    return value


def _bounded(field: str, value: Any, limit: int) -> str:
    if not isinstance(value, str):
        _fail("E_IMPROVEMENT_VALIDATION", "%s must be a string" % field, field)
    if not value:
        _fail("E_IMPROVEMENT_VALIDATION", "%s is required" % field, field)
    if len(value) > limit:
        _fail("E_IMPROVEMENT_VALIDATION",
              "%s is longer than %d" % (field, limit), field)
    return value


def _text(field: str, value: Any) -> str:
    return _bounded(field, value, _MAX_STRING)


def _ref(field: str, value: Any) -> str:
    return _bounded(field, value, _MAX_REF)


def _ref_list(field: str, value: Any) -> List[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        _fail("E_IMPROVEMENT_VALIDATION", "%s must be a list" % field, field)
    if len(value) > _MAX_LIST:
        _fail("E_IMPROVEMENT_VALIDATION",
              "%s holds more than %d entries" % (field, _MAX_LIST), field)
    return [_ref("%s[%d]" % (field, index), item)
            for index, item in enumerate(value)]


def _required_evaluations(value: Any) -> List[str]:
    """Validate the 1–8 unique evaluation kinds a promotion will have to satisfy.

    Empty is rejected at declaration: P0 never permits an evidence-free
    promotion path, so a host wanting a lightweight gate must still name and
    record at least one kind (§5.1).
    """
    field = "required_evaluations"
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        _fail("E_IMPROVEMENT_VALIDATION", "%s must be a list" % field, field)
    if not value:
        _fail("E_IMPROVEMENT_VALIDATION",
              "%s must name at least one evaluation kind" % field, field)
    if len(value) > _MAX_REQUIRED_EVALUATIONS:
        _fail("E_IMPROVEMENT_VALIDATION",
              "%s holds more than %d entries" % (field, _MAX_REQUIRED_EVALUATIONS),
              field)
    kinds = [
        _bounded("%s[%d]" % (field, index), item, _MAX_EVALUATION_KIND)
        for index, item in enumerate(value)
    ]
    if len(set(kinds)) != len(kinds):
        _fail("E_IMPROVEMENT_VALIDATION",
              "%s must not repeat an evaluation kind" % field, field)
    return kinds


def _optional(validator, field: str, value: Any):
    return None if value is None else validator(field, value)


def _common_fields(record_type: str, now: Any, *, privacy: str,
                   authority_claim: str, authority_verified: bool,
                   scope: str, host_authorization_ref: Optional[str],
                   execution_run_id: Optional[str]) -> Dict[str, Any]:
    """Build the §4 fields every improvement record carries.

    Carries **no** ``goal_id``: coupling improvement lineage to a goal would
    make every improvement goal-scoped, which is wrong for profile- and
    project-level artifacts.
    """
    if authority_verified:
        _fail("E_AUTHORITY_VERIFIED_FORBIDDEN",
              "authority_verified is never caller-supplied; it is always false")

    record = {
        # Mirrors what ``store_core.append`` forces on write, so a candidate
        # record's fingerprint equals the stored record's.
        "schema_version": 1,
        "record_type": record_type,
        "improvement_schema": IMPROVEMENT_SCHEMA,
        "emitted_at": canonical_timestamp(now),
        "privacy": _enum("privacy", privacy, _PRIVACY),
        "authority_claim": _enum("authority_claim", authority_claim,
                                 _AUTHORITY_CLAIM),
        "authority_verified": False,
        "scope": _enum("scope", scope, _SCOPE),
        "host_authorization_ref": _optional(
            _ref, "host_authorization_ref", host_authorization_ref
        ),
        "execution_run_id": _optional(
            lambda field, value: _pattern(field, value, _EXECUTION_RUN_ID),
            "execution_run_id", execution_run_id,
        ),
    }
    if record["scope"] in _WIDE_SCOPES and record["host_authorization_ref"] is None:
        # Fails closed, and stays honest about what happened: core stores the
        # ref, it does not verify it, and authority_verified stays false.
        _fail("E_IMPROVEMENT_SCOPE_UNAUTHORIZED",
              "scope %r requires a host-issued host_authorization_ref"
              % record["scope"],
              "host_authorization_ref", scope=record["scope"])
    return record


def _record_fingerprint(record: Dict[str, Any]) -> str:
    return digest({
        key: value for key, value in record.items()
        if key not in _FINGERPRINT_EXCLUDED
    })


def _resolve_operation_id(record: Dict[str, Any],
                          operation_id: Optional[str]) -> str:
    """Return the caller's ``operation_id``, or the request digest by default."""
    if operation_id is None:
        return digest(record)
    if isinstance(operation_id, str) and 0 < len(operation_id) <= 128:
        return operation_id
    _fail("E_IMPROVEMENT_PATTERN",
          "operation_id must be an opaque string of at most 128 characters",
          "operation_id")


def _differing_keys(stored: Dict[str, Any],
                    request: Dict[str, Any]) -> List[str]:
    keys = set(stored) | set(request)
    return sorted(
        key for key in keys - _FINGERPRINT_EXCLUDED
        if stored.get(key) != request.get(key)
    )


_FOLD_REQUIRED_FIELDS = {
    "improvement_proposal": frozenset({"proposal_id", "artifact_id"}),
    "evaluation_receipt": frozenset({"proposal_id", "evaluation_kind"}),
    "improvement_proposal_status": frozenset({"proposal_id", "to_status"}),
    "artifact_revision": frozenset({"artifact_id", "revision_id"}),
    "artifact_activation": frozenset({"artifact_id", "to_revision_id"}),
}
_MAX_EVENT_SEQ = (1 << 63) - 1


def _structurally_valid(stored: Dict[str, Any]) -> bool:
    """Reject parseable records that cannot participate in a safe fold."""
    record_type = stored.get("record_type")
    event_seq = stored.get("event_seq")
    required = (_FOLD_REQUIRED_FIELDS.get(record_type)
                if isinstance(record_type, str) else None)
    return (required is not None
            and isinstance(event_seq, int)
            and not isinstance(event_seq, bool)
            and 0 < event_seq <= _MAX_EVENT_SEQ
            and all(field in stored and stored[field] is not None
                    for field in required))


def _partition(read_result) -> Any:
    """Split a read into usable records and corruption, including seq clashes.

    Every record sharing a duplicate sequence is excluded: selecting one by
    physical line order would make projection non-deterministic.
    """
    candidates = []
    corrupt = read_result.skipped
    for stored in read_result.records:
        if stored.get("tombstone") is True:
            continue
        if _structurally_valid(stored):
            candidates.append(stored)
        else:
            corrupt += 1

    counts: Dict[int, int] = {}
    for stored in candidates:
        event_seq = stored["event_seq"]
        counts[event_seq] = counts.get(event_seq, 0) + 1
    usable = [
        stored for stored in candidates if counts[stored["event_seq"]] == 1
    ]
    corrupt += len(candidates) - len(usable)
    return usable, corrupt


def _fold_key(stored: Dict[str, Any]):
    """The total, stable fold order of §6.2: ``(event_seq, id)``."""
    return (stored.get("event_seq") or 0, stored.get("id") or "")


# -- projection (pure) -------------------------------------------------------

def _fold(records: List[Dict[str, Any]]) -> Any:
    """Fold ``records`` into ``(proposals, artifacts)`` in ``(event_seq, id)`` order.

    The single fold the projection and every guard share. A guard that folded
    the log its own way could disagree with the view a caller was shown, so
    there is exactly one implementation and both the writers and the dry runs
    call it.
    """
    proposals: Dict[str, Any] = {}
    artifacts: Dict[str, Any] = {}

    for stored in sorted(records, key=_fold_key):
        record_type = stored.get("record_type")
        if record_type == "improvement_proposal":
            proposals[stored["proposal_id"]] = {
                "status": _DRAFT,
                "artifact_id": stored.get("artifact_id"),
                "base_revision_id": stored.get("base_revision_id"),
                "candidate_digest": stored.get("candidate_digest"),
                "required_evaluations": list(
                    stored.get("required_evaluations") or []
                ),
                "evaluations": {},
                "status_history": [],
            }
        elif record_type == "evaluation_receipt":
            proposal = proposals.get(stored.get("proposal_id"))
            if proposal is None:
                continue
            # Fold order is total, so the last write for a kind is the latest
            # receipt by ``(event_seq, id)``: an older pass never overrides a
            # newer failure.
            proposal["evaluations"][stored["evaluation_kind"]] = {
                "verdict": stored.get("verdict"),
                "evaluated_candidate_digest": stored.get(
                    "evaluated_candidate_digest"
                ),
                "evaluated_base_revision_id": stored.get(
                    "evaluated_base_revision_id"
                ),
                "event_seq": stored.get("event_seq"),
            }
        elif record_type == "improvement_proposal_status":
            proposal = proposals.get(stored.get("proposal_id"))
            if proposal is None:
                continue
            proposal["status"] = stored.get("to_status")
            proposal["status_history"].append({
                "from_status": stored.get("from_status"),
                "to_status": stored.get("to_status"),
                "event_seq": stored.get("event_seq"),
            })
        elif record_type == "artifact_revision":
            artifact = artifacts.setdefault(stored["artifact_id"], {
                "active_revision_id": None, "revisions": {}, "activations": [],
            })
            artifact["revisions"][stored["revision_id"]] = {
                "content_digest": stored.get("content_digest"),
                "parent_revision_id": stored.get("parent_revision_id"),
                "proposal_id": stored.get("proposal_id"),
                "event_seq": stored.get("event_seq"),
            }
        elif record_type == "artifact_activation":
            artifact = artifacts.setdefault(stored["artifact_id"], {
                "active_revision_id": None, "revisions": {}, "activations": [],
            })
            # §5.6: the pointer is a fold over an append-only history, so it
            # holds exactly one value at every prefix, and the history that
            # produced it stays readable in full.
            artifact["active_revision_id"] = stored.get("to_revision_id")
            artifact["activations"].append({
                "from_revision_id": stored.get("from_revision_id"),
                "to_revision_id": stored.get("to_revision_id"),
                "activation_kind": stored.get("activation_kind"),
                "proposal_id": stored.get("proposal_id"),
                "external_receipt_ref": stored.get("external_receipt_ref"),
                "event_seq": stored.get("event_seq"),
            })

    return proposals, artifacts


def _high_water(records: List[Dict[str, Any]]) -> int:
    return max([0] + [
        stored["event_seq"] for stored in records
        if isinstance(stored.get("event_seq"), int)
    ])


def project_improvement(records: List[Dict[str, Any]], now: Any,
                        skipped_lines: int = 0,
                        artifact_id: Optional[str] = None,
                        proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """Fold ``records`` into the §6.2 view. Pure: no clock, no I/O, no writes.

    Replaying the same lines in any *physical* order yields an identical view,
    because the fold is driven by ``(event_seq, id)`` and never by file order.
    """
    proposals, artifacts = _fold(records)

    if artifact_id is not None:
        artifacts = {key: value for key, value in artifacts.items()
                     if key == artifact_id}
        proposals = {key: value for key, value in proposals.items()
                     if value["artifact_id"] == artifact_id}
    if proposal_id is not None:
        proposals = {key: value for key, value in proposals.items()
                     if key == proposal_id}

    return _ProjectionView({
        "schema": IMPROVEMENT_SCHEMA,
        "generated_at": canonical_timestamp(now),
        "high_water_seq": _high_water(records),
        "skipped_lines": skipped_lines,
        "proposals": proposals,
        "artifacts": artifacts,
    })


def _entries(mapping: Dict[str, Any], id_key: str) -> List[Dict[str, Any]]:
    """Render an id-keyed mapping as an id-sorted list of objects."""
    rendered = []
    for key in sorted(mapping):
        entry = {id_key: key}
        entry.update(mapping[key])
        rendered.append(entry)
    return rendered


class _ProjectionView(dict):
    """The projection: an id-keyed mapping to read, an id-sorted list to digest.

    Callers index by id, which is the only ergonomic shape. ``mkcjson`` confines
    object keys to ASCII ``^[a-z][a-z0-9_]{0,63}$`` so its cross-language digest
    claim holds — and caller-chosen ids (``prop.retrieval-boost``) are outside
    that grammar. So the canonical *rendering* moves every id out of key
    position into a field, and ``items()`` — the only accessor the canonical
    form uses — serves that rendering. Both shapes carry identical information
    and the rendering is sorted, so the digest stays deterministic.
    """

    def items(self):  # noqa: A003 - dict protocol
        canonical = dict(self)
        canonical["proposals"] = [
            dict(entry, evaluations=_entries(entry["evaluations"],
                                             "evaluation_kind"))
            for entry in _entries(self["proposals"], "proposal_id")
        ]
        canonical["artifacts"] = [
            dict(entry, revisions=_entries(entry["revisions"], "revision_id"))
            for entry in _entries(self["artifacts"], "artifact_id")
        ]
        return canonical.items()


# -- shared guards (pure) ----------------------------------------------------
#
# §6.1: the writers and the dry runs call these same functions over the same
# fold. The writers raise the first blocker; the dry runs return the whole list
# as data. Neither has a guard the other lacks, which is the only way a dry run
# can be trusted to predict what the writer will do.

def _blocker(code: str, message: str, **details: Any) -> Dict[str, Any]:
    return {"code": code, "message": message, "details": dict(details)}


def _raise(blocker: Dict[str, Any]) -> None:
    raise ImprovementError(blocker["code"], blocker["message"],
                           blocker["details"])


def _transition_blockers(proposals: Dict[str, Any], proposal_id: str,
                         from_status: Optional[str],
                         to_status: str) -> List[Dict[str, Any]]:
    """§5.3. ``from_status is None`` means "whatever the projection says" — the
    dry run has no caller-supplied operand to disagree with."""
    proposal = proposals.get(proposal_id)
    if proposal is None:
        return [_blocker("E_IMPROVEMENT_NOT_FOUND",
                         "improvement_proposal referenced by proposal_id was"
                         " never recorded",
                         path="proposal_id",
                         record_type="improvement_proposal",
                         identity={"proposal_id": proposal_id})]

    actual = proposal["status"]
    supplied = actual if from_status is None else from_status
    if supplied != actual:
        return [_blocker("E_IMPROVEMENT_TRANSITION",
                         "from_status does not match the projected status",
                         path="from_status", actual=actual, supplied=supplied)]
    if (supplied, to_status) not in _TRANSITIONS:
        return [_blocker("E_IMPROVEMENT_TRANSITION",
                         "%s -> %s is not an allowed transition"
                         % (supplied, to_status),
                         path="to_status", actual=actual, supplied=supplied,
                         to_status=to_status)]
    return []


def _activation_blockers(artifacts: Dict[str, Any], artifact_id: str,
                         to_revision_id: str,
                         expected_active_revision_id: Optional[str],
                         activation_kind: str = "activate"
                         ) -> List[Dict[str, Any]]:
    """Return the shared CAS and rollback-honesty blockers as data."""
    artifact = artifacts.get(artifact_id) or {}
    blockers: List[Dict[str, Any]] = []

    if to_revision_id not in (artifact.get("revisions") or {}):
        blockers.append(_blocker(
            "E_IMPROVEMENT_NOT_FOUND",
            "artifact_revision referenced by to_revision_id was never recorded"
            " for this artifact",
            path="to_revision_id", record_type="artifact_revision",
            identity={"artifact_id": artifact_id,
                      "revision_id": to_revision_id}))

    active = artifact.get("active_revision_id")
    if active != expected_active_revision_id:
        blockers.append(_blocker(
            "E_IMPROVEMENT_ACTIVATION_CONFLICT",
            "the artifact's active revision is not the one this activation"
            " expected to replace",
            path="expected_active_revision_id", actual=active,
            expected=expected_active_revision_id))
    if activation_kind == "rollback" and not any(
            item.get("to_revision_id") == to_revision_id
            for item in (artifact.get("activations") or [])):
        blockers.append(_blocker(
            "E_IMPROVEMENT_ROLLBACK_TARGET",
            "a rollback target must have been active previously",
            path="to_revision_id", to_revision_id=to_revision_id))
    return blockers


def _promotion_report(proposals: Dict[str, Any], artifacts: Dict[str, Any],
                      proposal_id: str, promoted_revision_id: Optional[str],
                      from_status: Optional[str] = None) -> Dict[str, Any]:
    """§5.4, the whole promotion gate, as data.

    Order matters: the transition blocker comes first, because a proposal that
    cannot legally reach ``promoted`` should not have its evaluations audited —
    reporting a missing receipt for a terminal proposal would suggest recording
    one would help.
    """
    blockers = _transition_blockers(proposals, proposal_id, from_status,
                                    _PROMOTED)
    proposal = proposals.get(proposal_id)
    if proposal is None:
        return {"blockers": blockers, "current_status": None,
                "active_revision_id": None, "required_evaluations": []}

    artifact = artifacts.get(proposal["artifact_id"]) or {}
    active_revision_id = artifact.get("active_revision_id")
    base_revision_id = proposal["base_revision_id"]

    # §5.4.3. Guarded here, in Slice 2, even though nothing can move the active
    # pointer until activation lands: a promotion gate that grows its freshness
    # check later would have shipped a window where a promoted proposal was
    # authored against a base that is no longer live.
    base_drifted = active_revision_id != base_revision_id
    if base_drifted:
        blockers.append(_blocker(
            "E_IMPROVEMENT_EVALUATION_STALE",
            "the artifact's active revision is no longer the base this proposal"
            " was authored against; a new proposal is required",
            path="base_revision_id", actual=active_revision_id,
            expected=base_revision_id))

    rows: List[Dict[str, Any]] = []
    for kind in proposal["required_evaluations"]:
        receipt = proposal["evaluations"].get(kind)
        if receipt is None:
            rows.append({"evaluation_kind": kind, "satisfied": False,
                         "verdict": None, "stale": False})
            blockers.append(_blocker(
                "E_IMPROVEMENT_EVALUATION_MISSING",
                "required evaluation %r has no receipt" % kind,
                path="required_evaluations", evaluation_kind=kind))
            continue

        stale = (
            receipt["evaluated_candidate_digest"] != proposal["candidate_digest"]
            or receipt["evaluated_base_revision_id"] != base_revision_id
        )
        verdict = receipt["verdict"]
        if stale or base_drifted:
            blockers.append(_blocker(
                "E_IMPROVEMENT_EVALUATION_STALE",
                "the receipt for %r no longer matches the bindings it judged"
                % kind,
                path="required_evaluations", evaluation_kind=kind))
        elif verdict != "pass":
            blockers.append(_blocker(
                "E_IMPROVEMENT_EVALUATION_FAILED",
                "the latest receipt for %r is %r" % (kind, verdict),
                path="required_evaluations", evaluation_kind=kind,
                verdict=verdict))
        rows.append({
            "evaluation_kind": kind,
            "satisfied": not stale and not base_drifted and verdict == "pass",
            "verdict": verdict,
            "stale": stale,
        })

    # §5.4.4. Mandatory, and bound three ways: to the artifact, to the content
    # the evaluations judged, and to this proposal's lineage.
    if promoted_revision_id is None:
        blockers.append(_blocker(
            "E_IMPROVEMENT_VALIDATION",
            "promoted_revision_id is required when to_status is 'promoted'",
            path="promoted_revision_id"))
    else:
        revision = (artifact.get("revisions") or {}).get(promoted_revision_id)
        if revision is None:
            blockers.append(_blocker(
                "E_IMPROVEMENT_NOT_FOUND",
                "artifact_revision referenced by promoted_revision_id was never"
                " recorded for this artifact",
                path="promoted_revision_id", record_type="artifact_revision",
                identity={"artifact_id": proposal["artifact_id"],
                          "revision_id": promoted_revision_id}))
        elif revision["content_digest"] != proposal["candidate_digest"]:
            blockers.append(_blocker(
                "E_IMPROVEMENT_VALIDATION",
                "promoted_revision_id names a revision whose content_digest is"
                " not the candidate the proposal declared",
                path="promoted_revision_id",
                actual=revision["content_digest"],
                expected=proposal["candidate_digest"]))
        elif revision["proposal_id"] != proposal_id:
            blockers.append(_blocker(
                "E_IMPROVEMENT_VALIDATION",
                "promoted_revision_id names a revision that does not descend"
                " from this proposal",
                path="promoted_revision_id",
                actual=revision["proposal_id"], expected=proposal_id))

    return {"blockers": blockers, "current_status": proposal["status"],
            "active_revision_id": active_revision_id,
            "required_evaluations": rows}


class ImprovementLedgerMixin:
    """The append-only improvement ledger (plan §1.3).

    Every write goes through ``store_core.append`` under the existing
    ``_governance_lock``; there is no parallel storage layer and no new locking
    primitive. Nothing here evaluates, schedules, orchestrates, deploys, or
    authorizes: the ledger constrains and records a causal chain the caller
    produces.
    """

    def _improvement_dir(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "improvement"

    def _improvement_events_path(self) -> Path:
        """The single append-only improvement log; never compacted."""
        return self._improvement_dir() / "events.jsonl"

    # -- append path -------------------------------------------------------

    def _improvement_append(self, record: Dict[str, Any],
                            operation_id: Optional[str],
                            unique=None, guard=None) -> Dict[str, Any]:
        """Validate against the log, allocate ``event_seq``, append one line.

        Everything after the lock is taken re-reads the log — the corruption
        check, the idempotency scan, ``unique``, ``guard``, and the sequence
        allocation — so a value observed before the lock can never decide an
        outcome. Every rejection raises before the append, which is what every
        "nothing was written" guarantee rests on.
        """
        record = dict(record)
        path = self._improvement_events_path()

        try:
            try:
                fd = self._governance_lock(timeout_s=IMPROVEMENT_LOCK_TIMEOUT_S)
            except TypeError as error:
                # Additive compatibility with the historical no-argument hook.
                # Only retry when Python says the override rejected this exact
                # keyword; a TypeError from inside the lock must propagate.
                if "unexpected keyword argument 'timeout_s'" not in str(error):
                    raise
                fd = self._governance_lock()
        except StoreBusy:
            raise ImprovementError(
                "E_IMPROVEMENT_STORE_BUSY",
                "the improvement store is locked by another writer",
            )

        try:
            existing, corrupt = _partition(read_all(path, include_tombstoned=True))
            if corrupt:
                # §6.3, before any guard: a guard reasoning over a partial view
                # could approve past a record it cannot see. Reads still work
                # and still report, so the ledger stays auditable.
                _fail("E_IMPROVEMENT_LOG_CORRUPT",
                      "the improvement log holds unreadable lines; refusing to"
                      " append a decision to a partial view",
                      skipped_lines=corrupt)

            high_water = max([0] + [stored["event_seq"] for stored in existing])
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
                _fail("E_IMPROVEMENT_IDEMPOTENCY_MISMATCH",
                      "operation_id was already used with different arguments",
                      stored_fingerprint=_record_fingerprint(stored),
                      request_fingerprint=request_fingerprint,
                      differing_keys=_differing_keys(stored, record))

            if unique is not None:
                _check_unique(existing, record, unique)
            if guard is not None:
                guard(existing)

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

    # -- writers -----------------------------------------------------------

    def improvement_propose(self, proposal_id, artifact_id, summary, rationale,
                            candidate_digest, *, now, base_revision_id=None,
                            candidate_locator=None, evidence_refs=(),
                            experience_refs=(), required_evaluations=(),
                            scope="project", host_authorization_ref=None,
                            privacy="local_private", authority_claim="agent",
                            authority_verified=False, execution_run_id=None,
                            operation_id=None) -> Dict[str, Any]:
        """Declare an immutable proposal to change one artifact (§5.1).

        ``candidate_digest`` is caller-computed and validated for **format
        only** — core hashes nothing, stores no body, and never fetches
        ``candidate_locator``. ``required_evaluations`` must name 1–8 unique
        kinds; declaring none would create an evidence-free promotion path,
        which P0 does not have.
        """
        record = _common_fields(
            "improvement_proposal", now, privacy=privacy,
            authority_claim=authority_claim,
            authority_verified=authority_verified, scope=scope,
            host_authorization_ref=host_authorization_ref,
            execution_run_id=execution_run_id,
        )
        record["proposal_id"] = _pattern("proposal_id", proposal_id, _ID)
        record["artifact_id"] = _pattern("artifact_id", artifact_id, _ID)
        record["summary"] = _text("summary", summary)
        record["rationale"] = _text("rationale", rationale)
        record["candidate_digest"] = _pattern(
            "candidate_digest", candidate_digest, _SHA256
        )
        record["base_revision_id"] = _optional(
            lambda field, value: _pattern(field, value, _ID),
            "base_revision_id", base_revision_id,
        )
        record["candidate_locator"] = _optional(
            _ref, "candidate_locator", candidate_locator
        )
        record["evidence_refs"] = _ref_list("evidence_refs", evidence_refs)
        record["experience_refs"] = _ref_list("experience_refs", experience_refs)
        record["required_evaluations"] = _required_evaluations(required_evaluations)

        return self._improvement_append(
            record, operation_id,
            unique=("improvement_proposal", ("proposal_id",)),
        )

    def improvement_record_evaluation(self, proposal_id, evaluation_kind, verdict,
                                      evaluated_candidate_digest, *, now,
                                      evaluated_base_revision_id=None,
                                      evidence_refs=(), notes=None,
                                      scope="project", host_authorization_ref=None,
                                      privacy="local_private", authority_claim="agent",
                                      authority_verified=False, execution_run_id=None,
                                      operation_id=None) -> Dict[str, Any]:
        """Record one caller-supplied verdict about one proposal (§5.2).

        Core computes no verdict, no score, and no threshold. What it does check
        is *binding*: the receipt must name the exact ``candidate_digest`` and
        ``base_revision_id`` the immutable proposal declared, so a receipt can
        never be silently credited to content it did not judge. A kind outside
        ``required_evaluations`` is extra, not invalid — it is recorded and it
        satisfies nothing.
        """
        record = _common_fields(
            "evaluation_receipt", now, privacy=privacy,
            authority_claim=authority_claim,
            authority_verified=authority_verified, scope=scope,
            host_authorization_ref=host_authorization_ref,
            execution_run_id=execution_run_id,
        )
        record["proposal_id"] = _pattern("proposal_id", proposal_id, _ID)
        record["evaluation_kind"] = _bounded(
            "evaluation_kind", evaluation_kind, _MAX_EVALUATION_KIND
        )
        record["verdict"] = _enum("verdict", verdict, _VERDICT)
        record["evaluated_candidate_digest"] = _pattern(
            "evaluated_candidate_digest", evaluated_candidate_digest, _SHA256
        )
        record["evaluated_base_revision_id"] = _optional(
            lambda field, value: _pattern(field, value, _REVISION_ID),
            "evaluated_base_revision_id", evaluated_base_revision_id,
        )
        record["evidence_refs"] = _ref_list("evidence_refs", evidence_refs)
        record["notes"] = _optional(_text, "notes", notes)

        def guard(existing: List[Dict[str, Any]]) -> None:
            # Post-lock, like every other guard: the proposal and its bindings
            # are read from the same view this append will extend.
            proposals, _ = _fold(existing)
            proposal = proposals.get(record["proposal_id"])
            if proposal is None:
                _fail("E_IMPROVEMENT_NOT_FOUND",
                      "improvement_proposal referenced by proposal_id was never"
                      " recorded",
                      "proposal_id", record_type="improvement_proposal",
                      identity={"proposal_id": record["proposal_id"]})
            if record["evaluated_candidate_digest"] != proposal["candidate_digest"]:
                _fail("E_IMPROVEMENT_VALIDATION",
                      "evaluated_candidate_digest does not equal the immutable"
                      " proposal's candidate_digest",
                      "evaluated_candidate_digest",
                      expected=proposal["candidate_digest"],
                      actual=record["evaluated_candidate_digest"])
            if record["evaluated_base_revision_id"] != proposal["base_revision_id"]:
                _fail("E_IMPROVEMENT_VALIDATION",
                      "evaluated_base_revision_id does not equal the immutable"
                      " proposal's base_revision_id",
                      "evaluated_base_revision_id",
                      expected=proposal["base_revision_id"],
                      actual=record["evaluated_base_revision_id"])

        return self._improvement_append(record, operation_id, guard=guard)

    def improvement_set_status(self, proposal_id, from_status, to_status, *, now,
                               reason=None, promoted_revision_id=None,
                               scope="project", host_authorization_ref=None,
                               privacy="local_private", authority_claim="agent",
                               authority_verified=False, execution_run_id=None,
                               operation_id=None) -> Dict[str, Any]:
        """Append one guarded lifecycle transition (§5.3), promotion included.

        There is no code path from an ``evaluation_receipt`` to a promoted
        proposal: promotion is this call, made explicitly by a caller, and it
        must pass the §5.4 gate against the post-lock view. Promotion decides;
        it **never** activates anything.
        """
        record = _common_fields(
            "improvement_proposal_status", now, privacy=privacy,
            authority_claim=authority_claim,
            authority_verified=authority_verified, scope=scope,
            host_authorization_ref=host_authorization_ref,
            execution_run_id=execution_run_id,
        )
        record["proposal_id"] = _pattern("proposal_id", proposal_id, _ID)
        record["from_status"] = _enum("from_status", from_status, _STATUSES)
        record["to_status"] = _enum("to_status", to_status, _STATUSES)
        record["reason"] = _optional(_text, "reason", reason)
        record["promoted_revision_id"] = _optional(
            lambda field, value: _pattern(field, value, _REVISION_ID),
            "promoted_revision_id", promoted_revision_id,
        )
        if record["to_status"] == _PROMOTED:
            if record["promoted_revision_id"] is None:
                _fail("E_IMPROVEMENT_VALIDATION",
                      "promoted_revision_id is required when to_status is"
                      " 'promoted'", "promoted_revision_id")
        elif record["promoted_revision_id"] is not None:
            _fail("E_IMPROVEMENT_VALIDATION",
                  "promoted_revision_id is only meaningful for a promotion",
                  "promoted_revision_id", to_status=record["to_status"])

        def guard(existing: List[Dict[str, Any]]) -> None:
            proposals, artifacts = _fold(existing)
            if record["to_status"] == _PROMOTED:
                report = _promotion_report(
                    proposals, artifacts, record["proposal_id"],
                    record["promoted_revision_id"], record["from_status"],
                )
                blockers = report["blockers"]
            else:
                blockers = _transition_blockers(
                    proposals, record["proposal_id"], record["from_status"],
                    record["to_status"],
                )
            if blockers:
                _raise(blockers[0])

        return self._improvement_append(record, operation_id, guard=guard)

    def artifact_register_revision(self, artifact_id, revision_id, content_digest,
                                   *, now, locator=None, parent_revision_id=None,
                                   proposal_id=None, provenance_refs=(),
                                   scope="project", host_authorization_ref=None,
                                   privacy="local_private", authority_claim="agent",
                                   authority_verified=False, execution_run_id=None,
                                   operation_id=None) -> Dict[str, Any]:
        """Register an immutable, content-addressed revision of an artifact (§5.5).

        The body is never stored, never fetched, and never executed: ``locator``
        is opaque. Lineage fails closed — a named ``parent_revision_id`` must
        already exist *under the same artifact*, and a named ``proposal_id``
        must already have been declared.
        """
        record = _common_fields(
            "artifact_revision", now, privacy=privacy,
            authority_claim=authority_claim,
            authority_verified=authority_verified, scope=scope,
            host_authorization_ref=host_authorization_ref,
            execution_run_id=execution_run_id,
        )
        record["artifact_id"] = _pattern("artifact_id", artifact_id, _ID)
        record["revision_id"] = _pattern("revision_id", revision_id, _REVISION_ID)
        record["content_digest"] = _pattern(
            "content_digest", content_digest, _SHA256
        )
        record["locator"] = _optional(_ref, "locator", locator)
        record["parent_revision_id"] = _optional(
            lambda field, value: _pattern(field, value, _REVISION_ID),
            "parent_revision_id", parent_revision_id,
        )
        record["proposal_id"] = _optional(
            lambda field, value: _pattern(field, value, _ID),
            "proposal_id", proposal_id,
        )
        record["provenance_refs"] = _ref_list("provenance_refs", provenance_refs)

        def guard(existing: List[Dict[str, Any]]) -> None:
            if record["parent_revision_id"] is not None:
                _require(
                    existing, "artifact_revision",
                    {"artifact_id": record["artifact_id"],
                     "revision_id": record["parent_revision_id"]},
                    "parent_revision_id",
                )
            if record["proposal_id"] is not None:
                _require(
                    existing, "improvement_proposal",
                    {"proposal_id": record["proposal_id"]}, "proposal_id",
                )

        return self._improvement_append(
            record, operation_id,
            unique=("artifact_revision", ("artifact_id", "revision_id")),
            guard=guard,
        )

    def artifact_activate_revision(self, artifact_id, to_revision_id, *, now,
                                   expected_active_revision_id=None,
                                   activation_kind="activate", proposal_id=None,
                                   external_receipt_ref=None, reason=None,
                                   scope="project", host_authorization_ref=None,
                                   privacy="local_private", authority_claim="agent",
                                   authority_verified=False, execution_run_id=None,
                                   operation_id=None) -> Dict[str, Any]:
        """Compare-and-swap the artifact's active revision (§5.6).

        ``expected_active_revision_id`` is the CAS operand and ``None`` means
        "expect nothing active yet", not "don't care": a first activation that
        expects a revision is a lost race, not a no-op. The compare and the
        append happen under one lock, so no two activations can both believe
        they won and the projection holds exactly one active revision per
        artifact at every prefix of the log.

        ``from_revision_id`` is core's, allocated from the value observed under
        that lock — a caller-supplied "previous" is a claim, not an
        observation. Core moves a pointer in its own ledger and nothing else:
        it applies no change to the world, and ``external_receipt_ref`` is the
        host's opaque proof that it did.
        """
        record = _common_fields(
            "artifact_activation", now, privacy=privacy,
            authority_claim=authority_claim,
            authority_verified=authority_verified, scope=scope,
            host_authorization_ref=host_authorization_ref,
            execution_run_id=execution_run_id,
        )
        record["artifact_id"] = _pattern("artifact_id", artifact_id, _ID)
        record["to_revision_id"] = _pattern(
            "to_revision_id", to_revision_id, _REVISION_ID
        )
        record["expected_active_revision_id"] = _optional(
            lambda field, value: _pattern(field, value, _REVISION_ID),
            "expected_active_revision_id", expected_active_revision_id,
        )
        record["activation_kind"] = _enum(
            "activation_kind", activation_kind, _ACTIVATION_KIND
        )
        record["proposal_id"] = _optional(
            lambda field, value: _pattern(field, value, _ID),
            "proposal_id", proposal_id,
        )
        record["external_receipt_ref"] = _optional(
            _ref, "external_receipt_ref", external_receipt_ref
        )
        record["reason"] = _optional(_text, "reason", reason)
        # The CAS operand becomes the recorded ``from`` value only after the
        # post-lock guard proves it equals the observed active revision. Build
        # it into the candidate here because ``_improvement_append`` owns a
        # defensive copy; mutating this outer dict from the guard would not
        # update the record that is actually appended.
        record["from_revision_id"] = record["expected_active_revision_id"]

        def guard(existing: List[Dict[str, Any]]) -> None:
            _, artifacts = _fold(existing)
            blockers = _activation_blockers(
                artifacts, record["artifact_id"], record["to_revision_id"],
                record["expected_active_revision_id"],
                record["activation_kind"],
            )
            if blockers:
                _raise(blockers[0])
            if record["proposal_id"] is not None:
                _require(
                    existing, "improvement_proposal",
                    {"proposal_id": record["proposal_id"]}, "proposal_id",
                )
            # CAS success proves the stored ``from_revision_id`` above equals
            # the active revision observed under this lock.

        return self._improvement_append(record, operation_id, guard=guard)

    def artifact_rollback_revision(self, artifact_id, to_revision_id, *, now,
                                   expected_active_revision_id=None, **kwargs):
        """Append a CAS-guarded rollback without deleting prior lineage."""
        return self.artifact_activate_revision(
            artifact_id, to_revision_id, now=now,
            expected_active_revision_id=expected_active_revision_id,
            activation_kind="rollback", **kwargs
        )

    # -- reads -------------------------------------------------------------

    def improvement_project(self, *, now, artifact_id=None,
                            proposal_id=None) -> Dict[str, Any]:
        """Project the ledger. Appends nothing and creates nothing on disk.

        A base directory that has never held an improvement record projects to
        an empty view without the log or its directory being created.
        """
        existing, corrupt = _partition(
            read_all(self._improvement_events_path(), include_tombstoned=True)
        )
        return project_improvement(
            existing, now, skipped_lines=corrupt,
            artifact_id=artifact_id, proposal_id=proposal_id,
        )

    def improvement_plan_promotion(self, proposal_id, promoted_revision_id, *,
                                   now) -> Dict[str, Any]:
        """Answer "would this promotion be accepted?" without taking the lock.

        Runs :func:`_promotion_report` — the identical function the writer
        runs — against a read-only snapshot. It appends nothing, upgrades no
        lock, and never raises on a blocked plan: blockers are data, including
        for a proposal that was never declared. ``now`` is injected and used
        only to keep the read side clock-free like every other read.
        """
        existing, corrupt = _partition(
            read_all(self._improvement_events_path(), include_tombstoned=True)
        )
        proposals, artifacts = _fold(existing)
        report = _promotion_report(
            proposals, artifacts, proposal_id, promoted_revision_id,
        )
        blockers = list(report["blockers"])
        if corrupt:
            blockers.insert(0, _blocker(
                "E_IMPROVEMENT_LOG_CORRUPT",
                "the improvement log holds unreadable lines",
                skipped_lines=corrupt,
            ))
        return {
            "ok": not blockers,
            "blockers": blockers,
            "current_status": report["current_status"],
            "active_revision_id": report["active_revision_id"],
            "required_evaluations": report["required_evaluations"],
            "snapshot_event_seq": _high_water(existing),
        }

    def improvement_plan_activation(self, artifact_id, to_revision_id, *, now,
                                    expected_active_revision_id=None,
                                    activation_kind="activate") -> Dict[str, Any]:
        """Run the activation writer's guards against a read-only snapshot."""
        artifact_id = _pattern("artifact_id", artifact_id, _ID)
        to_revision_id = _pattern(
            "to_revision_id", to_revision_id, _REVISION_ID
        )
        expected_active_revision_id = _optional(
            lambda field, value: _pattern(field, value, _REVISION_ID),
            "expected_active_revision_id", expected_active_revision_id,
        )
        activation_kind = _enum(
            "activation_kind", activation_kind, _ACTIVATION_KIND
        )
        existing, corrupt = _partition(
            read_all(self._improvement_events_path(), include_tombstoned=True)
        )
        _, artifacts = _fold(existing)
        blockers = _activation_blockers(
            artifacts, artifact_id, to_revision_id,
            expected_active_revision_id, activation_kind,
        )
        if corrupt:
            blockers.insert(0, _blocker(
                "E_IMPROVEMENT_LOG_CORRUPT",
                "the improvement log holds unreadable lines",
                skipped_lines=corrupt,
            ))
        active = (artifacts.get(artifact_id) or {}).get("active_revision_id")
        return {
            "ok": not blockers,
            "blockers": blockers,
            "active_revision_id": active,
            "snapshot_event_seq": _high_water(existing),
        }


def _check_unique(existing: List[Dict[str, Any]], record: Dict[str, Any],
                  unique) -> None:
    """Reject a record whose declared identity already exists in the log."""
    record_type, keys = unique
    for stored in existing:
        if stored.get("record_type") != record_type:
            continue
        if all(stored.get(key) == record[key] for key in keys):
            _fail("E_IMPROVEMENT_ALREADY_EXISTS",
                  "%s already exists for this identity" % record_type,
                  record_type=record_type,
                  identity={key: record[key] for key in keys})


def _require(existing: List[Dict[str, Any]], record_type: str,
             identity: Dict[str, Any], field: str) -> None:
    """Fail closed unless ``identity`` was already recorded as ``record_type``."""
    for stored in existing:
        if stored.get("record_type") != record_type:
            continue
        if all(stored.get(key) == value for key, value in identity.items()):
            return
    _fail("E_IMPROVEMENT_NOT_FOUND",
          "%s referenced by %s was never recorded" % (record_type, field),
          field, record_type=record_type, identity=identity)
