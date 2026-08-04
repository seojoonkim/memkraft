"""Typed handoff: declare, transition, export, import (plan §10, D-25).

Core never scrapes records to build an export. The ``payload`` is supplied by
the caller at :meth:`~ExecutionHandoffMixin.handoff_declare`, stored verbatim,
and exported unchanged. That is the whole privacy model: there is no field-level
filtering machinery because core never assembles an envelope out of private
state, and a record-level ``privacy`` tag could not have driven field-level
filtering anyway.

The export redaction scan is a lint. It catches obvious mistakes — absolute
paths, common secret prefixes — and refuses the export when it fires. It is not
a security control, it does not detect novel secret formats, and it must not be
relied upon to make an untrusted payload safe. Deciding what is safe to share is
the caller's responsibility, discharged at ``handoff.declare`` time.

``origin_instance_id`` is a stable, per-base random identifier present in every
exported envelope. It contains no path, hostname, or username, but it is a
persistent correlator: any party that receives two envelopes from the same base
can link them, and parties that collude can join across recipients. If
unlinkability across recipients is required, do not use handoff export in 3.3.0.

Idempotency holds only against honest senders: ``origin_instance_id`` is
self-asserted, so a party that regenerates it defeats the triple-key dedupe, and
``payload_digest`` proves self-consistency, not provenance.

Zero dependencies — stdlib only.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .execution_projection import project_handoffs
from .execution_protocol import (
    ConflictError,
    ExecutionError,
    NotDeclaredError,
    ValidationError,
    canonical_timestamp,
    digest,
    mkcjson,
    parse_timestamp,
)
from .execution_state import (
    _GOAL_ID,
    _common_fields,
    _fence,
    _holder,
    _pattern,
    _rule,
    _text,
)
from .store_core import read_all

__all__ = ["ExecutionHandoffMixin", "MAX_HANDOFFS_PER_GOAL", "MAX_PAYLOAD_BYTES"]

ENVELOPE_SCHEMA = "memkraft.handoff/1"
DEFAULT_PAYLOAD_SCHEMA = "memkraft.handoff.context/1"

#: §4.9. A payload is one bounded object on one atomic line, not a transport.
MAX_PAYLOAD_BYTES = 32768

#: The §4.9 default list bound applied to handoffs per goal. Arbitrary, like
#: every other cap here: unbounded fan-out of handoffs is a work dispatcher, and
#: a dispatcher is an anti-goal.
MAX_HANDOFFS_PER_GOAL = 32

#: §7.3. ``handoff.declare`` and ``handoff.import`` are fenced goal-wide; a
#: transition is fenced against the one handoff it moves.
HANDOFF_SCOPE = "handoff"

_HANDOFF_ID = re.compile(r"^[0-9a-f]{32}$")
_ORIGIN_INSTANCE_ID = re.compile(r"^[0-9a-f]{32}$")
_PAYLOAD_SCHEMA = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}/[0-9]{1,3}$")
_BINDING_DIGEST = re.compile(r"^[0-9a-f]{64}$")

#: The envelope is closed (§10.2). Anything outside this set is
#: ``E_UNKNOWN_FIELD`` and anything missing from it is ``E_MISSING_FIELD``;
#: there is no ``extra``, ``meta``, or ``passthrough`` key, ever.
_ENVELOPE_KEYS = (
    "envelope_schema", "origin_instance_id", "goal_id", "handoff_id",
    "payload_schema", "payload", "payload_digest", "expires_at",
    "exported_at", "envelope_digest",
)

#: §10.1. Static rules of the fail-closed export lint, as ``(name, pattern)``.
#: The rule *name* is what an error reports; the matched text never is, because
#: an error is exactly the artifact a runtime is most likely to ship elsewhere.
_REDACTION_RULES = (
    ("absolute_posix_path", re.compile(r"^/")),
    ("absolute_posix_path", re.compile(r"(?<![\w-])/(?:Users|home|var|tmp|opt|etc)/")),
    ("windows_path", re.compile(r"[A-Za-z]:\\")),
    ("home_reference", re.compile(r"(?:^|[^A-Za-z0-9_])\$HOME(?:\b|/)")),
    ("user_reference", re.compile(r"(?:^|[^A-Za-z0-9_])\$USER\b")),
    ("tilde_path", re.compile(r"(?:^|\s)~/")),
    ("file_url", re.compile(r"file://")),
    ("secret_prefix", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("secret_prefix", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("secret_prefix", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("secret_prefix", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret_prefix", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
)

#: An ancestor this short is a mount point every path shares, so treating it as
#: a leak would refuse every export rather than the ones that leak.
_MIN_ANCESTOR_PARTS = 3


class _AlreadyImported(Exception):
    """Internal signal: this exact envelope is already in the log (§10.4 rule 3).

    Raised from the append guard, so it fires under the same lock that would
    have written the line and leaves the file untouched. Deliberately not an
    :class:`ExecutionError`: a re-delivered envelope is a success, not a fault.
    """

    def __init__(self, record: Dict[str, Any]):
        super().__init__("this envelope has already been imported")
        self.record = record


class ExecutionHandoffMixin:
    """Handoff declaration, transition, export, and import (§10).

    Origin and copy are independent state machines. Exporting does not move the
    origin, and a recipient accepting does not reach back — the origin reaches
    ``completed`` only when a reverse confirmation envelope is imported at the
    origin, which is an ordinary declare-and-export in the other direction.
    There is no distributed transaction, no two-phase commit, and no automatic
    reconciliation.
    """

    # -- declaration -------------------------------------------------------

    def handoff_declare(self, goal_id, to_actor, payload, *, now,
                        payload_schema=DEFAULT_PAYLOAD_SCHEMA, expires_at=None,
                        fence_token=None, binding_digest=None,
                        operation_id=None) -> Dict[str, Any]:
        """Offer ``payload`` to ``to_actor``. The caller decides what is shareable.

        The payload is stored verbatim and tagged ``public_safe``, because the
        caller already made the sharing decision by supplying it. Core adds
        nothing to it and removes nothing from it (D-25).

        Fence-protected under the literal scope ``"handoff"`` (§7.3).
        """
        record = _common_fields(
            "handoff_declared", goal_id, now, privacy="public_safe",
            authority_claim="agent", execution_run_id=None,
        )
        record["to_actor"] = _holder("to_actor", to_actor)
        record["payload"] = _payload("payload", payload)
        record["payload_digest"] = digest(record["payload"])
        record["payload_schema"] = _pattern("payload_schema", payload_schema,
                                            _PAYLOAD_SCHEMA)
        if expires_at is not None:
            record["expires_at"] = canonical_timestamp(expires_at)
        if binding_digest is not None:
            # Opaque audit identity, computed by the adapter. Core supports
            # exactly one operation on it — equality — and never parses it.
            record["binding_digest"] = _pattern("binding_digest", binding_digest,
                                                _BINDING_DIGEST)

        result = self._execution_append(
            record, operation_id,
            declared={"requires": ("goal_declared", ("goal_id",)), "unique": None},
            guard=_handoff_cap(record["goal_id"]),
            fence=_fence(now, record, HANDOFF_SCOPE, fence_token),
        )
        return _handoff_result(result)

    # -- transition --------------------------------------------------------

    def handoff_transition(self, goal_id, handoff_id, to_state, *, now,
                           fence_token=None, operation_id=None) -> Dict[str, Any]:
        """Move a handoff along ``offered → accepted → completed`` (§4.6).

        Reaching a state the handoff already holds is ``E_CONFLICT`` rather than
        a forbidden transition: a second accept means two parties believe they
        own the work, which is the one thing this machine exists to surface. A
        *replay* — the same ``operation_id`` — never reaches this guard and
        returns ``already_applied``, which is what makes re-firing a hook safe
        without weakening the invariant (§10.5).

        Fence-protected under the scope of this handoff alone (§7.3).
        """
        record = _common_fields(
            "handoff_transition", goal_id, now, privacy="local_private",
            authority_claim="agent", execution_run_id=None,
        )
        record["handoff_id"] = _pattern("handoff_id", handoff_id, _HANDOFF_ID)
        record["to_status"] = _text("to_state", to_state)

        def guard(existing, high_water):
            handoff = _projected_handoff(existing, now, record["goal_id"],
                                         record["handoff_id"])
            if handoff["expired"]:
                raise ExecutionError(
                    "E_HANDOFF_EXPIRED", "this handoff expired before the transition",
                    {"handoff_id": record["handoff_id"],
                     "expires_at": handoff["expires_at"]},
                )
            if handoff["status"] == record["to_status"]:
                raise ConflictError(
                    "E_CONFLICT", "this handoff already reached that state",
                    {"handoff_id": record["handoff_id"],
                     "status": handoff["status"]},
                )
            _rule("handoff", handoff["status"], record["to_status"],
                  {"handoff_id": record["handoff_id"],
                   "from_status": handoff["status"]})
            return {}

        result = self._execution_append(
            record, operation_id,
            declared={"requires": ("goal_declared", ("goal_id",)), "unique": None},
            guard=guard,
            fence=_fence(now, record,
                         "%s:%s" % (HANDOFF_SCOPE, record["handoff_id"]),
                         fence_token),
        )
        result["handoff_id"] = record["handoff_id"]
        result["handoff_status"] = record["to_status"]
        result["warnings"] = []
        return result

    # -- export ------------------------------------------------------------

    def handoff_export(self, goal_id, handoff_id, *, now) -> Dict[str, Any]:
        """Return the §10.2 envelope for a handoff. Appends nothing.

        Re-export is byte-identical for the same ``now``, because delivery is
        runtime-owned and a lost delivery must be retryable without inventing a
        second envelope for one handoff (§10.5).
        """
        goal_id = _pattern("goal_id", goal_id, _GOAL_ID)
        handoff_id = _pattern("handoff_id", handoff_id, _HANDOFF_ID)
        records = read_all(self._execution_events_path()).records
        declaration = _declaration(records, goal_id, handoff_id)

        envelope = {
            "envelope_schema": ENVELOPE_SCHEMA,
            "origin_instance_id": self._origin_instance_id(),
            "goal_id": goal_id,
            "handoff_id": handoff_id,
            "payload_schema": declaration["payload_schema"],
            "payload": declaration["payload"],
            "payload_digest": declaration["payload_digest"],
            "expires_at": declaration.get("expires_at"),
            "exported_at": canonical_timestamp(now),
        }
        self._handoff_redaction_lint(envelope)
        envelope["envelope_digest"] = digest(envelope)
        return {"outcome": "read", "envelope": envelope,
                "origin_instance_id": envelope["origin_instance_id"],
                "warnings": []}

    # -- import ------------------------------------------------------------

    def handoff_import(self, goal_id, envelope, *, now, fence_token=None,
                       binding_digest=None, operation_id=None) -> Dict[str, Any]:
        """Import an envelope **object** supplied by the caller (§10.4).

        There is no path, base_dir, profile, or URL parameter here, and there
        never will be in any release: the signature is what makes a cross-base
        read inexpressible rather than merely discouraged.

        Both digests are verified before anything is written, and the origin
        triple deduplicates a re-delivery. Neither proves provenance — a digest
        proves the envelope agrees with itself, and ``origin_instance_id`` is
        self-asserted — so this is safe against lost acks, not against a hostile
        sender.

        Fence-protected under the literal scope ``"handoff"`` (§7.3).
        """
        checked = _check_envelope(envelope)

        record = _common_fields(
            "handoff_imported", goal_id, now, privacy="local_private",
            authority_claim="agent", execution_run_id=None,
        )
        record["payload"] = checked["payload"]
        record["payload_digest"] = checked["payload_digest"]
        record["payload_schema"] = checked["payload_schema"]
        if checked["expires_at"] is not None:
            record["expires_at"] = checked["expires_at"]
            if parse_timestamp(checked["expires_at"]) <= parse_timestamp(now):
                raise ExecutionError(
                    "E_HANDOFF_EXPIRED", "this envelope expired before it arrived",
                    {"expires_at": checked["expires_at"]},
                )
        record["imported_from"] = {
            "origin_instance_id": checked["origin_instance_id"],
            "handoff_id": checked["handoff_id"],
            "payload_digest": checked["payload_digest"],
        }
        if binding_digest is not None:
            record["binding_digest"] = _pattern("binding_digest", binding_digest,
                                                _BINDING_DIGEST)

        def guard(existing, high_water):
            _check_origin_triple(existing, record["imported_from"])
            return _handoff_cap(record["goal_id"])(existing, high_water)

        try:
            result = self._execution_append(
                record, operation_id,
                declared={"requires": ("goal_declared", ("goal_id",)),
                          "unique": None},
                guard=guard,
                fence=_fence(now, record, HANDOFF_SCOPE, fence_token),
            )
        except _AlreadyImported as already:
            stored = already.record
            result = {
                "outcome": "already_applied",
                "record_id": stored.get("id"),
                "event_seq": stored.get("event_seq"),
                "operation_id": stored.get("operation_id"),
                "record": stored,
            }
        return _handoff_result(result)

    # -- origin identity and the export lint --------------------------------

    def _origin_instance_id(self) -> str:
        """Return this base's ``origin_instance_id``, minting it on first export.

        Random, written once, and **never** derived from ``base_dir``, hostname,
        or username — deriving it would put a path back into every envelope, and
        removing paths from envelopes is the point of having it at all. Created
        lazily here rather than at ``init()`` so an install that never exports a
        handoff never grows the file (§19.4).
        """
        path = Path(self.base_dir) / ".memkraft" / "origin_instance_id"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL: two concurrent first exports must agree on one identity,
            # and the loser reads the winner's rather than overwriting it.
            try:
                handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    os.write(handle, uuid.uuid4().hex.encode("ascii"))
                finally:
                    os.close(handle)
        return _pattern("origin_instance_id",
                        path.read_text(encoding="utf-8").strip(),
                        _ORIGIN_INSTANCE_ID)

    def _handoff_redaction_lint(self, envelope: Dict[str, Any]) -> None:
        """Refuse the export when a §10.1 pattern fires. A lint, not a control.

        Fails closed rather than substituting ``[redacted]``: a silently
        redacted envelope looks complete while having had semantics quietly
        removed, and the exporter never learns it published something broken.
        """
        rules = list(_REDACTION_RULES) + _base_rules(self.base_dir)
        for path, value in _strings(envelope):
            for name, pattern in rules:
                if pattern.search(value):
                    raise ValidationError(
                        "E_PATTERN",
                        "the export lint refused this payload; "
                        "rule %r fired" % name,
                        {"rule": name, "path": path},
                    )


def _payload(field: str, payload: Any) -> Dict[str, Any]:
    """Validate a caller-supplied handoff payload against §5.2 and §4.9.

    Canonicalizing it here is the validation: MKCJSON/1 already rejects floats,
    non-ASCII keys, lone surrogates, and over-deep nesting, and the byte length
    it produces is the size the cap is defined over.
    """
    if not isinstance(payload, dict):
        raise ValidationError("E_TYPE", "%s must be an object" % field,
                              {"path": field})
    size = len(mkcjson(payload))
    if size > MAX_PAYLOAD_BYTES:
        raise ValidationError(
            "E_LIMIT_EXCEEDED",
            "%s is larger than %d canonical bytes" % (field, MAX_PAYLOAD_BYTES),
            {"path": field, "limit": MAX_PAYLOAD_BYTES, "size": size},
        )
    return payload


def _handoff_cap(goal_id: str):
    """Return the §4.9 per-goal handoff cap check, to run under the append lock."""
    def guard(existing: List[Dict[str, Any]], high_water: int) -> Dict[str, Any]:
        declared = sum(
            1 for stored in existing
            if stored.get("goal_id") == goal_id
            and stored.get("record_type") in ("handoff_declared", "handoff_imported")
        )
        if declared >= MAX_HANDOFFS_PER_GOAL:
            raise ValidationError(
                "E_LIMIT_EXCEEDED",
                "a goal holds at most %d handoffs" % MAX_HANDOFFS_PER_GOAL,
                {"limit": MAX_HANDOFFS_PER_GOAL, "declared": declared},
            )
        return {}

    return guard


def _handoff_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Project an append result into the handoff response shape.

    ``handoff_id`` is the stored line's ``id``, so a replay reports the identity
    of the handoff that exists rather than minting a second one.
    """
    stored = result["record"]
    result["handoff_id"] = stored.get("id")
    result["payload_digest"] = stored.get("payload_digest")
    result["imported_from"] = stored.get("imported_from")
    result["warnings"] = []
    return result


def _projected_handoff(existing, now, goal_id: str, handoff_id: str) -> Dict[str, Any]:
    """Return the handoff's projected state, computed under the append lock.

    A handoff declared under a different goal is *not declared* here. Guessing
    an id must not reach across a goal boundary, so the lookup is scoped rather
    than global.
    """
    handoff = project_handoffs(existing, now, goal_id).get(handoff_id)
    if handoff is None:
        raise NotDeclaredError(
            "E_NOT_DECLARED", "handoff_declared was never declared for this goal",
            {"record_type": "handoff_declared"},
        )
    return handoff


def _declaration(records, goal_id: str, handoff_id: str) -> Dict[str, Any]:
    """Return the live line that declared or imported ``handoff_id``."""
    for stored in records:
        if (stored.get("record_type") in ("handoff_declared", "handoff_imported")
                and stored.get("goal_id") == goal_id
                and stored.get("id") == handoff_id):
            return stored
    raise NotDeclaredError(
        "E_NOT_DECLARED", "handoff_declared was never declared for this goal",
        {"record_type": "handoff_declared"},
    )


def _check_envelope(envelope: Any) -> Dict[str, Any]:
    """Validate a §10.2 envelope and both of its digests, or fail.

    Digest verification happens before any state is consulted: an envelope that
    does not agree with itself is not evidence of anything, and checking it
    first keeps a corrupted one from ever being compared against the log.
    """
    if not isinstance(envelope, dict):
        raise ValidationError("E_TYPE", "envelope must be an object",
                              {"path": "envelope"})
    unknown = sorted(set(envelope) - set(_ENVELOPE_KEYS))
    if unknown:
        raise ValidationError(
            "E_UNKNOWN_FIELD", "the handoff envelope is closed",
            {"path": "envelope.%s" % unknown[0]},
        )
    missing = [key for key in _ENVELOPE_KEYS if key not in envelope]
    if missing:
        raise ValidationError(
            "E_MISSING_FIELD", "the handoff envelope is incomplete",
            {"path": "envelope.%s" % missing[0]},
        )
    if envelope["envelope_schema"] != ENVELOPE_SCHEMA:
        raise ValidationError(
            "E_PATTERN", "unrecognized envelope schema",
            {"path": "envelope.envelope_schema"},
        )

    checked = {
        "origin_instance_id": _pattern("envelope.origin_instance_id",
                                       envelope["origin_instance_id"],
                                       _ORIGIN_INSTANCE_ID),
        "handoff_id": _pattern("envelope.handoff_id", envelope["handoff_id"],
                               _HANDOFF_ID),
        "payload_schema": _pattern("envelope.payload_schema",
                                   envelope["payload_schema"], _PAYLOAD_SCHEMA),
        "payload": _payload("envelope.payload", envelope["payload"]),
        "payload_digest": _pattern("envelope.payload_digest",
                                   envelope["payload_digest"], _BINDING_DIGEST),
        "expires_at": (None if envelope["expires_at"] is None
                       else canonical_timestamp(envelope["expires_at"])),
    }
    _pattern("envelope.goal_id", envelope["goal_id"], _GOAL_ID)
    canonical_timestamp(envelope["exported_at"])
    _pattern("envelope.envelope_digest", envelope["envelope_digest"],
             _BINDING_DIGEST)

    expected = digest({key: value for key, value in envelope.items()
                       if key != "envelope_digest"})
    if expected != envelope["envelope_digest"]:
        raise ExecutionError(
            "E_DIGEST_MISMATCH", "the envelope does not match its own digest",
            {"path": "envelope.envelope_digest", "computed_digest": expected},
        )
    payload_digest = digest(checked["payload"])
    if payload_digest != checked["payload_digest"]:
        raise ExecutionError(
            "E_DIGEST_MISMATCH", "the payload does not match its declared digest",
            {"path": "envelope.payload_digest", "computed_digest": payload_digest},
        )
    return checked


def _check_origin_triple(existing, imported_from: Dict[str, Any]) -> None:
    """Apply §10.4 rules 3 and 4 against the log, under the append lock.

    An identical triple is a re-delivery and costs nothing. The *same* origin
    handoff carrying a *different* payload is the tamper-or-fork signal, and
    accepting it silently would let two contradictory copies of one handoff live
    in the same log with nothing marking which is which.
    """
    for stored in existing:
        origin = stored.get("imported_from")
        if not isinstance(origin, dict):
            continue
        if (origin.get("origin_instance_id") != imported_from["origin_instance_id"]
                or origin.get("handoff_id") != imported_from["handoff_id"]):
            continue
        if origin.get("payload_digest") == imported_from["payload_digest"]:
            raise _AlreadyImported(stored)
        raise ConflictError(
            "E_CONFLICT",
            "this origin handoff was already imported with a different payload",
            {"stored_payload_digest": origin.get("payload_digest"),
             "envelope_payload_digest": imported_from["payload_digest"]},
        )


def _strings(value: Any, path: str = "envelope"):
    """Yield every ``(path, string)`` in ``value``, depth-first."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            for found in _strings(child, "%s.%s" % (path, key)):
                yield found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for found in _strings(child, "%s[%d]" % (path, index)):
                yield found


def _base_rules(base_dir: Any) -> List[Any]:
    """Return the per-base lint rules: this base's path, its ancestors, the user.

    Ancestors stop short of the shallow ones every path on the machine shares:
    a rule matching ``/`` would refuse every export instead of the leaking ones,
    which is how a fail-closed lint becomes a lint nobody can keep enabled.
    """
    rules = []
    base = Path(str(base_dir)).resolve()
    for candidate in [base] + list(base.parents):
        if len(candidate.parts) < _MIN_ANCESTOR_PARTS:
            break
        rules.append(("base_dir", re.compile(re.escape(str(candidate)))))
    for name in ("USER", "LOGNAME"):
        user = os.environ.get(name) or ""
        if len(user) >= 3:
            rules.append(("username", re.compile(re.escape(user))))
    return rules
