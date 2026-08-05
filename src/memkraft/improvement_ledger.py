"""Continual Improvement Ledger — Slice 1 substrate (plan §4, §5.1, §5.5, §6.2, §6.3).

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
_FINGERPRINT_EXCLUDED = frozenset({"id", "created_at", "event_seq"})

_DRAFT = "draft"


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


def _structurally_valid(stored: Dict[str, Any]) -> bool:
    """A structurally parseable line is not yet a structurally valid record.

    A record without a ``record_type`` cannot be folded and one without an
    ``event_seq`` cannot be ordered, so either is corruption rather than data.
    """
    return (isinstance(stored.get("record_type"), str)
            and isinstance(stored.get("event_seq"), int)
            and not isinstance(stored.get("event_seq"), bool))


def _partition(read_result) -> Any:
    """Split a read into (usable records, total corrupt-line count)."""
    usable = []
    corrupt = read_result.skipped
    for stored in read_result.records:
        if stored.get("tombstone") is True:
            continue
        if _structurally_valid(stored):
            usable.append(stored)
        else:
            corrupt += 1
    return usable, corrupt


def _fold_key(stored: Dict[str, Any]):
    """The total, stable fold order of §6.2: ``(event_seq, id)``."""
    return (stored.get("event_seq") or 0, stored.get("id") or "")


# -- projection (pure) -------------------------------------------------------

def project_improvement(records: List[Dict[str, Any]], now: Any,
                        skipped_lines: int = 0,
                        artifact_id: Optional[str] = None,
                        proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """Fold ``records`` into the §6.2 view. Pure: no clock, no I/O, no writes.

    Replaying the same lines in any *physical* order yields an identical view,
    because the fold is driven by ``(event_seq, id)`` and never by file order.
    """
    proposals: Dict[str, Any] = {}
    artifacts: Dict[str, Any] = {}

    for stored in sorted(records, key=_fold_key):
        record_type = stored.get("record_type")
        if record_type == "improvement_proposal":
            proposals[stored["proposal_id"]] = {
                "status": _DRAFT,
                "artifact_id": stored.get("artifact_id"),
                "candidate_digest": stored.get("candidate_digest"),
                "required_evaluations": list(
                    stored.get("required_evaluations") or []
                ),
                "evaluations": {},
                "status_history": [],
            }
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

    if artifact_id is not None:
        artifacts = {key: value for key, value in artifacts.items()
                     if key == artifact_id}
        proposals = {key: value for key, value in proposals.items()
                     if value["artifact_id"] == artifact_id}
    if proposal_id is not None:
        proposals = {key: value for key, value in proposals.items()
                     if key == proposal_id}

    high_water = max([0] + [
        stored["event_seq"] for stored in records
        if isinstance(stored.get("event_seq"), int)
    ])
    return _ProjectionView({
        "schema": IMPROVEMENT_SCHEMA,
        "generated_at": canonical_timestamp(now),
        "high_water_seq": high_water,
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
