"""Evaluation Corpus v0 — runtime-neutral reusable eval cases.

One append-only log, ``<base_dir>/.memkraft/evaluation/events.jsonl``, holds the
corpus: registered cases, human labels, and externally produced run results.

The corpus stores *what to test* and *what someone observed*, never the raw
conversation. Every field is an id, a closed enum, a bounded string, or an
opaque ref core stores and compares but never fetches, parses, or executes. The
writer signatures are closed, so there is no keyword through which raw content
could arrive at all.

Core executes no evaluator. ``evaluation_run`` is a verbatim record of a verdict
the caller reached elsewhere, and ``authority_verified`` is forced ``false`` on
every record: a security-shaped field nobody checked is worse than no field at
all. ``proposal_id`` and ``evaluation_kind`` on a run are *correlation only* —
recording one mints no improvement-ledger receipt and touches no promotion
state.

Same deliberate asymmetry as the improvement ledger: a damaged log stays
readable and auditable but stops accepting new writes, because a guard reasoning
over a partial view could accept past a record it cannot see.

Python 3.9, stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .execution_protocol import ExecutionError, canonical_timestamp
from .store_core import StoreBusy, _unlock, append

__all__ = [
    "EvaluationCorpusMixin",
    "EvaluationCorpusError",
    "EVALUATION_CORPUS_ERROR_REGISTRY",
]

EVALUATION_SCHEMA = 1

#: Bound the wait so a write inside a fail-closed hook fails fast rather than
#: convoying behind another writer (mirrors the improvement ledger).
EVALUATION_LOCK_TIMEOUT_S = 2.0

#: This feature's own ``(error_class, retryable)`` table. Deliberately **not**
#: merged into ``execution_protocol._ERROR_REGISTRY``: that registry is part of
#: the frozen MKEP/0 wire contract and the corpus has no protocol surface.
EVALUATION_CORPUS_ERROR_REGISTRY = {
    "E_EVALCORPUS_VALIDATION": ("input", False),
    "E_EVALCORPUS_PATTERN": ("input", False),
    "E_EVALCORPUS_NOT_FOUND": ("state", False),
    "E_EVALCORPUS_ALREADY_EXISTS": ("state", False),
    "E_EVALCORPUS_IDEMPOTENCY_MISMATCH": ("idempotency", False),
    "E_EVALCORPUS_LOG_CORRUPT": ("integrity", False),
    "E_EVALCORPUS_STORE_BUSY": ("io", True),
    "E_AUTHORITY_VERIFIED_FORBIDDEN": ("evidence", False),
}


class EvaluationCorpusError(ExecutionError):
    """An evaluation-corpus error carrying its stable ``code``."""

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        error_class, retryable = EVALUATION_CORPUS_ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


# -- grammars and bounds -----------------------------------------------------

#: ``\Z``, never ``$``: Python's ``$`` also matches just before a trailing
#: newline, so ``$`` would admit ``"corpus://anon/0001\n"`` — a control
#: character inside a value whose whole purpose is to be inert and opaque.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}\Z")
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z")

#: Refs are *opaque handles*, never a channel for prose. The grammar is closed
#: on both ends: a fixed scheme vocabulary, an id-shaped authority, and
#: id-shaped path segments. Anything expressive enough to carry a sentence — a
#: space, a control character, a leading ``/``, a bare filesystem path, a query
#: string — is outside the grammar and is rejected, not truncated.
_REF_SCHEMES = (
    "corpus", "trace", "note", "report", "artifact", "run", "case", "eval",
    "label", "bundle",
)
_REF_URI = re.compile(
    r"^(?:%s)://[a-z0-9][a-z0-9._-]{0,63}(?:/[A-Za-z0-9._-]{1,64}){1,8}\Z"
    % "|".join(_REF_SCHEMES)
)
#: A content address for something held locally; the digest *is* the reference.
_REF_LOCAL_DIGEST = re.compile(r"^local:sha256:[0-9a-f]{64}\Z")

_FAILURE_TYPE = (
    "stale_recall", "missing_recall", "wrong_recall", "over_recall",
    "privacy_leak", "contradiction", "other",
    # Runtime-neutral event kinds emitted by the Hermes Eval Bridge. Keeping
    # these as explicit values preserves a closed taxonomy without collapsing
    # operational failures into an unhelpful generic bucket.
    "user_correction", "delegated_failed", "delegated_partial",
    "delegated_timed_out", "tool_error", "routing_error",
    "unverified_completion",
)
_PRIVACY_SCOPE = ("public_safe", "local_private", "private_pointer")
_LABEL = ("pass", "fail", "inconclusive")
_VERDICT = ("pass", "fail", "inconclusive")
_EVALUATOR_KIND = ("offline_replay", "human_review", "live_shadow", "other")
_AUTHORITY_CLAIM = ("agent", "human", "system")

_MAX_STRING = 512
_MAX_REF = 256
_MAX_VERSION = 80
_MAX_EVALUATION_KIND = 80

#: The store mints a fresh ``id`` and ``created_at`` on every append and core
#: allocates ``event_seq``, so a wider set would fail every honest retry and a
#: narrower one would never match.
_FINGERPRINT_EXCLUDED = frozenset({"id", "created_at", "event_seq"})

_MAX_EVENT_SEQ = (1 << 63) - 1


# -- persisted-record validation ---------------------------------------------
#
# A guard is only as trustworthy as the records it can see. Checking a couple of
# fields would let a record that is *parseable but incomplete* — half a case, a
# run with no verdict, an ``authority_verified: true`` line someone hand-edited
# in — sit in the log looking usable. So the log is validated against the full
# envelope a writer here could actually have produced: every field present,
# every type right, every enum closed, nothing extra. Anything else is
# corruption, which keeps reads working and stops writes.

def _is_str_bounded(limit: int):
    return lambda value: isinstance(value, str) and 0 < len(value) <= limit


def _is_pattern(regex):
    return lambda value: isinstance(value, str) and bool(regex.match(value))


def _is_enum(allowed):
    return lambda value: value in allowed


def _is_exactly(expected: int):
    return lambda value: (isinstance(value, int) and not isinstance(value, bool)
                          and value == expected)


def _is_event_seq(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 < value <= _MAX_EVENT_SEQ)


def _nullable(check):
    return lambda value: value is None or check(value)


_IS_ID = _is_pattern(_ID)
# Resolve `_ref_ok` when validation runs, after the function is defined below.
_IS_REF = lambda value: _ref_ok(value)

#: Filled by ``store_core.append`` or ``_common_fields`` on every record.
_ENVELOPE_SPEC = {
    "id": _is_str_bounded(128),
    "created_at": _is_str_bounded(64),
    "schema_version": _is_exactly(1),
    "evaluation_schema": _is_exactly(EVALUATION_SCHEMA),
    "emitted_at": _is_str_bounded(64),
    "privacy_scope": _is_enum(_PRIVACY_SCOPE),
    "authority_claim": _is_enum(_AUTHORITY_CLAIM),
    # Never caller-supplied and never true. A stored ``true`` means the line was
    # written by something other than this module.
    "authority_verified": lambda value: value is False,
    "operation_id": _is_str_bounded(128),
    "event_seq": _is_event_seq,
}

_RECORD_SPECS = {
    "evaluation_case": {
        "case_id": _IS_ID,
        "failure_type": _is_enum(_FAILURE_TYPE),
        "anonymized_input_ref": _IS_REF,
        "expected_behavior": _is_str_bounded(_MAX_STRING),
        "forbidden_behavior": _is_str_bounded(_MAX_STRING),
        "trace_ref": _nullable(_IS_REF),
        "source_profile": _nullable(_IS_ID),
    },
    "evaluation_label": {
        "case_id": _IS_ID,
        "label": _is_enum(_LABEL),
        "evidence_ref": _nullable(_IS_REF),
    },
    "evaluation_run": {
        "case_id": _IS_ID,
        "evaluator_kind": _is_enum(_EVALUATOR_KIND),
        "evaluator_version": _is_str_bounded(_MAX_VERSION),
        "verdict": _is_enum(_VERDICT),
        "candidate_digest": _is_pattern(_SHA256),
        "evidence_ref": _nullable(_IS_REF),
        "proposal_id": _nullable(_IS_ID),
        "evaluation_kind": _nullable(_is_str_bounded(_MAX_EVALUATION_KIND)),
    },
}


def _fail(code: str, message: str, field: Optional[str] = None,
          **extra: Any) -> None:
    details = dict(extra)
    if field is not None:
        details["path"] = field
    raise EvaluationCorpusError(code, message, details)


def _pattern(field: str, value: Any, regex) -> str:
    if not isinstance(value, str):
        _fail("E_EVALCORPUS_VALIDATION", "%s must be a string" % field, field)
    if not regex.match(value):
        _fail("E_EVALCORPUS_PATTERN",
              "%s does not match its grammar" % field, field)
    return value


def _enum(field: str, value: Any, allowed) -> str:
    if value not in allowed:
        _fail("E_EVALCORPUS_PATTERN",
              "%s is outside its closed domain" % field, field)
    return value


def _bounded(field: str, value: Any, limit: int) -> str:
    if not isinstance(value, str):
        _fail("E_EVALCORPUS_VALIDATION", "%s must be a string" % field, field)
    if not value:
        _fail("E_EVALCORPUS_VALIDATION", "%s is required" % field, field)
    if len(value) > limit:
        _fail("E_EVALCORPUS_VALIDATION",
              "%s is longer than %d" % (field, limit), field)
    return value


def _text(field: str, value: Any) -> str:
    return _bounded(field, value, _MAX_STRING)


def _ref_ok(value: Any) -> bool:
    """True when ``value`` is an opaque reference under the closed grammar."""
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_REF:
        return False
    if _REF_LOCAL_DIGEST.match(value):
        return True
    if not _REF_URI.match(value):
        return False
    # ``.`` and ``..`` are the one thing the segment class still admits that
    # could read as path traversal to a naive consumer downstream.
    body = value.split("://", 1)[1]
    return all(segment not in (".", "..") for segment in body.split("/")[1:])


def _ref(field: str, value: Any) -> str:
    _bounded(field, value, _MAX_REF)
    if not _ref_ok(value):
        _fail("E_EVALCORPUS_PATTERN",
              "%s must be an opaque reference (scheme://authority/path or"
              " local:sha256:<64hex>), not free text" % field, field)
    return value


def _optional(validator, field: str, value: Any):
    return None if value is None else validator(field, value)


def _common_fields(record_type: str, now: Any, *, privacy_scope: str,
                   authority_claim: str,
                   authority_verified: bool) -> Dict[str, Any]:
    if authority_verified:
        _fail("E_AUTHORITY_VERIFIED_FORBIDDEN",
              "authority_verified is never caller-supplied; it is always false")
    return {
        # Mirrors what ``store_core.append`` forces on write, so a candidate
        # record's fingerprint equals the stored record's.
        "schema_version": 1,
        "record_type": record_type,
        "evaluation_schema": EVALUATION_SCHEMA,
        "emitted_at": canonical_timestamp(now),
        "privacy_scope": _enum("privacy_scope", privacy_scope, _PRIVACY_SCOPE),
        "authority_claim": _enum("authority_claim", authority_claim,
                                 _AUTHORITY_CLAIM),
        "authority_verified": False,
    }


def _payload(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items()
            if key not in _FINGERPRINT_EXCLUDED}


def _record_fingerprint(record: Dict[str, Any]) -> str:
    """An *exact* fingerprint of the request payload.

    Deliberately not ``execution_protocol.digest``: MKCJSON canonicalization
    applies Unicode NFC, so two genuinely different requests — say a case whose
    ref differs only by composed versus decomposed form — hash identically.
    That is correct for a wire digest and wrong for idempotency, where it would
    answer ``already_applied`` to a request that was never applied. This hashes
    the payload's own bytes, with no normalization step in between.
    """
    return hashlib.sha256(json.dumps(
        _payload(record), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _resolve_operation_id(record: Dict[str, Any],
                          operation_id: Optional[str]) -> str:
    if operation_id is None:
        return _record_fingerprint(record)
    if isinstance(operation_id, str) and 0 < len(operation_id) <= 128:
        return operation_id
    _fail("E_EVALCORPUS_PATTERN",
          "operation_id must be an opaque string of at most 128 characters",
          "operation_id")


def _differing_keys(stored: Dict[str, Any],
                    request: Dict[str, Any]) -> List[str]:
    keys = set(stored) | set(request)
    return sorted(key for key in keys - _FINGERPRINT_EXCLUDED
                  if stored.get(key) != request.get(key))


def _record_valid(stored: Dict[str, Any]) -> bool:
    """True when ``stored`` is a complete record this module could have written.

    Presence is checked as strictly as type: an optional field is optional in
    *value* (it may be ``null``), never in *key*, because every writer here
    always sets it. An unknown key means the line came from somewhere else.
    """
    if not isinstance(stored.get("record_type"), str):
        return False
    spec = _RECORD_SPECS.get(stored["record_type"])
    if spec is None:
        return False
    allowed = set(spec) | set(_ENVELOPE_SPEC) | {"record_type"}
    if set(stored) != allowed:
        return False
    for field, check in _ENVELOPE_SPEC.items():
        if not check(stored[field]):
            return False
    return all(check(stored[field]) for field, check in spec.items())


def _read_evaluation_lines(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Read the log byte-wise so damage can never raise on the read path.

    ``store_core.read_all`` decodes the file as text, so a line containing
    invalid UTF-8 raises out of the iterator and takes the *whole* projection
    with it — the exact failure mode the corruption asymmetry exists to prevent.
    Decoding per line keeps a damaged log readable and counts the damage.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], 0

    records: List[Dict[str, Any]] = []
    skipped = 0
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        records.append(obj)
    return records, skipped


def _partition(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Split the log into usable records and corruption.

    Beyond malformed lines, this treats *append-only impossibilities* as
    corruption rather than quietly folding around them. A duplicate sequence, a
    case declared twice at different sequences, a label or run for a case that
    was never declared, a tombstone in a log that is never compacted — none of
    these can arise from this module's writers, so each one means the log no
    longer describes a history core produced, and a guard reading it would be
    reasoning over someone else's edits.
    """
    records, corrupt = _read_evaluation_lines(path)

    candidates = []
    for stored in records:
        if stored.get("tombstone") is not None or "tombstone_of" in stored:
            # This log is append-only and never compacted; nothing here writes
            # a tombstone, so its presence is an outside edit, not a deletion.
            corrupt += 1
        elif _record_valid(stored):
            candidates.append(stored)
        else:
            corrupt += 1

    # Duplicate sequences: choosing one by physical line order would make the
    # projection depend on file layout, so every clashing record is excluded.
    seq_counts: Dict[int, int] = {}
    for stored in candidates:
        seq_counts[stored["event_seq"]] = seq_counts.get(
            stored["event_seq"], 0) + 1
    kept = [stored for stored in candidates
            if seq_counts[stored["event_seq"]] == 1]
    corrupt += len(candidates) - len(kept)

    # The locked writer always allocates high_water + 1. A clean history is
    # therefore contiguous from one; gaps or a non-one start are outside edits.
    actual_sequences = sorted(stored["event_seq"] for stored in kept)
    if actual_sequences != list(range(1, len(actual_sequences) + 1)):
        corrupt += len(kept)
        return [], corrupt

    # An exact retry never appends, so operation IDs are globally unique among
    # physical records. A duplicate makes idempotency ambiguous.
    operation_counts: Dict[str, int] = {}
    for stored in kept:
        operation_counts[stored["operation_id"]] = operation_counts.get(
            stored["operation_id"], 0) + 1
    duplicated_operations = {
        operation_id for operation_id, count in operation_counts.items()
        if count > 1
    }
    if duplicated_operations:
        kept_without_duplicates = [
            stored for stored in kept
            if stored["operation_id"] not in duplicated_operations
        ]
        corrupt += len(kept) - len(kept_without_duplicates)
        kept = kept_without_duplicates

    # A case is declared exactly once; two declarations at different sequences
    # are two different histories and there is no basis to prefer either.
    case_counts: Dict[str, int] = {}
    for stored in kept:
        if stored["record_type"] == "evaluation_case":
            case_counts[stored["case_id"]] = case_counts.get(
                stored["case_id"], 0) + 1
    declared = {
        stored["case_id"]: stored["event_seq"] for stored in kept
        if stored["record_type"] == "evaluation_case"
        and case_counts[stored["case_id"]] == 1
    }

    usable = []
    for stored in kept:
        if stored["record_type"] == "evaluation_case":
            ok = case_counts[stored["case_id"]] == 1
        else:
            # An orphan label or run cannot happen: writers require the case to
            # already exist under the same lock that allocates the sequence.
            case_seq = declared.get(stored["case_id"])
            ok = case_seq is not None and case_seq < stored["event_seq"]
        if ok:
            usable.append(stored)
        else:
            corrupt += 1
    return usable, corrupt


def _fold_key(stored: Dict[str, Any]):
    """The total, stable fold order: ``(event_seq, id)``."""
    return (stored.get("event_seq") or 0, stored.get("id") or "")


def _high_water(records: List[Dict[str, Any]]) -> int:
    return max([0] + [stored["event_seq"] for stored in records
                      if isinstance(stored.get("event_seq"), int)])


# -- projection (pure) -------------------------------------------------------

def _fold(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold ``records`` into the case view in ``(event_seq, id)`` order.

    Replaying the same lines in any *physical* order yields an identical view,
    because the fold is driven by ``(event_seq, id)`` and never by file order.
    """
    cases: Dict[str, Any] = {}
    for stored in sorted(records, key=_fold_key):
        record_type = stored.get("record_type")
        if record_type == "evaluation_case":
            cases[stored["case_id"]] = {
                "failure_type": stored.get("failure_type"),
                "anonymized_input_ref": stored.get("anonymized_input_ref"),
                "expected_behavior": stored.get("expected_behavior"),
                "forbidden_behavior": stored.get("forbidden_behavior"),
                "trace_ref": stored.get("trace_ref"),
                "source_profile": stored.get("source_profile"),
                "privacy_scope": stored.get("privacy_scope"),
                "event_seq": stored.get("event_seq"),
                "labels": [],
                "runs": [],
            }
        elif record_type == "evaluation_label":
            case = cases.get(stored.get("case_id"))
            if case is None:
                continue
            case["labels"].append({
                "label": stored.get("label"),
                "authority_claim": stored.get("authority_claim"),
                "evidence_ref": stored.get("evidence_ref"),
                "event_seq": stored.get("event_seq"),
            })
        elif record_type == "evaluation_run":
            case = cases.get(stored.get("case_id"))
            if case is None:
                continue
            case["runs"].append({
                "verdict": stored.get("verdict"),
                "evaluator_kind": stored.get("evaluator_kind"),
                "evaluator_version": stored.get("evaluator_version"),
                "candidate_digest": stored.get("candidate_digest"),
                "evidence_ref": stored.get("evidence_ref"),
                "proposal_id": stored.get("proposal_id"),
                "evaluation_kind": stored.get("evaluation_kind"),
                "event_seq": stored.get("event_seq"),
            })
    return cases


def project_evaluation(records: List[Dict[str, Any]], now: Any,
                       skipped_lines: int = 0,
                       case_id: Optional[str] = None) -> Dict[str, Any]:
    """Fold ``records`` into the corpus view. Pure: no clock, no I/O, no writes."""
    cases = _fold(records)
    if case_id is not None:
        cases = {key: value for key, value in cases.items() if key == case_id}
    return {
        "schema": EVALUATION_SCHEMA,
        "generated_at": canonical_timestamp(now),
        "high_water_seq": _high_water(records),
        "skipped_lines": skipped_lines,
        "cases": cases,
    }


class EvaluationCorpusMixin:
    """The append-only evaluation corpus.

    Every write goes through ``store_core.append`` under the existing
    ``_governance_lock``; there is no parallel storage layer and no new locking
    primitive. Nothing here runs an evaluator or mutates improvement state.
    """

    def _evaluation_events_path(self) -> Path:
        """The single append-only evaluation log; never compacted."""
        return Path(self.base_dir) / ".memkraft" / "evaluation" / "events.jsonl"

    # -- append path -------------------------------------------------------

    def _evaluation_append(self, record: Dict[str, Any],
                           operation_id: Optional[str],
                           unique=None, guard=None) -> Dict[str, Any]:
        """Validate against the log, allocate ``event_seq``, append one line.

        Everything after the lock is taken re-reads the log, so a value observed
        before the lock can never decide an outcome. Every rejection raises
        before the append.
        """
        record = dict(record)
        path = self._evaluation_events_path()

        try:
            try:
                fd = self._governance_lock(timeout_s=EVALUATION_LOCK_TIMEOUT_S)
            except TypeError as error:
                # Additive compatibility with the historical no-argument hook.
                if "unexpected keyword argument 'timeout_s'" not in str(error):
                    raise
                fd = self._governance_lock()
        except StoreBusy:
            raise EvaluationCorpusError(
                "E_EVALCORPUS_STORE_BUSY",
                "the evaluation store is locked by another writer",
            )

        try:
            existing, corrupt = _partition(path)
            if corrupt:
                # Before any guard: a guard reasoning over a partial view could
                # accept past a record it cannot see. Reads still work.
                _fail("E_EVALCORPUS_LOG_CORRUPT",
                      "the evaluation log holds unreadable lines; refusing to"
                      " append to a partial view",
                      skipped_lines=corrupt)

            high_water = max([0] + [stored["event_seq"] for stored in existing])
            record["operation_id"] = _resolve_operation_id(record, operation_id)
            request_fingerprint = _record_fingerprint(record)

            for stored in existing:
                if stored.get("operation_id") != record["operation_id"]:
                    continue
                # Exact value equality, not just digest equality: two payloads
                # may only be replayed as one if they are literally the same
                # request.
                if _payload(stored) == _payload(record):
                    return {
                        "outcome": "already_applied",
                        "record_id": stored.get("id"),
                        "event_seq": stored.get("event_seq"),
                        "operation_id": record["operation_id"],
                        "record_fingerprint": request_fingerprint,
                        "record": stored,
                    }
                _fail("E_EVALCORPUS_IDEMPOTENCY_MISMATCH",
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

    def evaluation_register_case(self, case_id, failure_type,
                                 anonymized_input_ref, expected_behavior,
                                 forbidden_behavior, *, now, trace_ref=None,
                                 source_profile=None,
                                 privacy_scope="local_private",
                                 authority_claim="agent",
                                 authority_verified=False,
                                 operation_id=None) -> Dict[str, Any]:
        """Register one immutable, reusable evaluation case.

        The signature is closed on purpose: there is no keyword through which a
        prompt, a conversation, or any other raw content could arrive.
        ``anonymized_input_ref`` and ``trace_ref`` are opaque — core stores and
        compares them, and never fetches or parses them.
        """
        record = _common_fields(
            "evaluation_case", now, privacy_scope=privacy_scope,
            authority_claim=authority_claim,
            authority_verified=authority_verified,
        )
        record["case_id"] = _pattern("case_id", case_id, _ID)
        record["failure_type"] = _enum("failure_type", failure_type,
                                       _FAILURE_TYPE)
        record["anonymized_input_ref"] = _ref("anonymized_input_ref",
                                              anonymized_input_ref)
        record["expected_behavior"] = _text("expected_behavior",
                                            expected_behavior)
        record["forbidden_behavior"] = _text("forbidden_behavior",
                                             forbidden_behavior)
        record["trace_ref"] = _optional(_ref, "trace_ref", trace_ref)
        record["source_profile"] = _optional(
            lambda field, value: _pattern(field, value, _ID),
            "source_profile", source_profile,
        )
        return self._evaluation_append(
            record, operation_id,
            unique=("evaluation_case", ("case_id",)),
        )

    def evaluation_add_label(self, case_id, label, *, now, evidence_ref=None,
                             authority_claim="human",
                             privacy_scope="local_private",
                             authority_verified=False,
                             operation_id=None) -> Dict[str, Any]:
        """Append one human (or agent) label about a registered case.

        Labels accumulate; nothing is ever overwritten, so a disagreement stays
        visible as history rather than being resolved by the last writer.
        ``authority_claim`` records *who claims* to have judged;
        ``authority_verified`` stays false because core verified nothing.
        """
        record = _common_fields(
            "evaluation_label", now, privacy_scope=privacy_scope,
            authority_claim=authority_claim,
            authority_verified=authority_verified,
        )
        record["case_id"] = _pattern("case_id", case_id, _ID)
        record["label"] = _enum("label", label, _LABEL)
        record["evidence_ref"] = _optional(_ref, "evidence_ref", evidence_ref)

        def guard(existing: List[Dict[str, Any]]) -> None:
            _require_case(existing, record["case_id"])

        return self._evaluation_append(record, operation_id, guard=guard)

    def evaluation_record_run(self, case_id, evaluator_kind, evaluator_version,
                              verdict, candidate_digest, *, now,
                              evidence_ref=None, proposal_id=None,
                              evaluation_kind=None,
                              privacy_scope="local_private",
                              authority_claim="agent",
                              authority_verified=False,
                              operation_id=None) -> Dict[str, Any]:
        """Record one externally produced run result, verbatim.

        Core runs no evaluator: ``verdict`` is the caller's and is stored as
        given. ``proposal_id`` and ``evaluation_kind`` are **correlation only** —
        they let a host join this run to an improvement proposal later, and
        recording one mints no ``evaluation_receipt`` and moves no promotion
        state.
        """
        record = _common_fields(
            "evaluation_run", now, privacy_scope=privacy_scope,
            authority_claim=authority_claim,
            authority_verified=authority_verified,
        )
        record["case_id"] = _pattern("case_id", case_id, _ID)
        record["evaluator_kind"] = _enum("evaluator_kind", evaluator_kind,
                                         _EVALUATOR_KIND)
        record["evaluator_version"] = _bounded(
            "evaluator_version", evaluator_version, _MAX_VERSION
        )
        record["verdict"] = _enum("verdict", verdict, _VERDICT)
        record["candidate_digest"] = _pattern(
            "candidate_digest", candidate_digest, _SHA256
        )
        record["evidence_ref"] = _optional(_ref, "evidence_ref", evidence_ref)
        record["proposal_id"] = _optional(
            lambda field, value: _pattern(field, value, _ID),
            "proposal_id", proposal_id,
        )
        record["evaluation_kind"] = _optional(
            lambda field, value: _bounded(field, value, _MAX_EVALUATION_KIND),
            "evaluation_kind", evaluation_kind,
        )

        def guard(existing: List[Dict[str, Any]]) -> None:
            _require_case(existing, record["case_id"])

        return self._evaluation_append(record, operation_id, guard=guard)

    # -- reads -------------------------------------------------------------

    def evaluation_project(self, *, now, case_id=None) -> Dict[str, Any]:
        """Project the corpus. Appends nothing and creates nothing on disk."""
        existing, corrupt = _partition(self._evaluation_events_path())
        return project_evaluation(existing, now, skipped_lines=corrupt,
                                  case_id=case_id)


def _check_unique(existing: List[Dict[str, Any]], record: Dict[str, Any],
                  unique) -> None:
    """Reject a record whose declared identity already exists in the log."""
    record_type, keys = unique
    for stored in existing:
        if stored.get("record_type") != record_type:
            continue
        if all(stored.get(key) == record[key] for key in keys):
            _fail("E_EVALCORPUS_ALREADY_EXISTS",
                  "%s already exists for this identity" % record_type,
                  record_type=record_type,
                  identity={key: record[key] for key in keys})


def _require_case(existing: List[Dict[str, Any]], case_id: str) -> None:
    """Fail closed unless ``case_id`` was already registered."""
    for stored in existing:
        if (stored.get("record_type") == "evaluation_case"
                and stored.get("case_id") == case_id):
            return
    _fail("E_EVALCORPUS_NOT_FOUND",
          "evaluation_case referenced by case_id was never recorded",
          "case_id", record_type="evaluation_case",
          identity={"case_id": case_id})
