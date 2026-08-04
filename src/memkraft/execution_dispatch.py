"""The closed MKEP/0 operation registry, dispatcher, and ``describe`` (§6, §17).

The typed methods on :class:`~memkraft.execution_state.ExecutionStateMixin` and
:class:`~memkraft.execution_handoff.ExecutionHandoffMixin` are the primary API.
This module is a **projection** of exactly fifteen of them onto one wire
envelope, so a non-Python runtime reaches identical semantics. A Python caller
never constructs an envelope.

Four structural locks keep this from becoming a generic command bus (§6.3):

1. ``_OPS`` is a literal dict of fifteen entries. There is no dynamic
   registration, no plugin hook, and an ``op`` string never becomes an
   attribute lookup — every operation has a hand-written adapter function.
2. ``target`` and ``args`` keys are enumerated per operation. No argument
   dictionary is ever splatted into a kernel method.
3. ``args`` values are scalars, flat string lists, and one bounded handoff
   payload. There are no selectors, globs, or filters beyond ``include`` from a
   closed list.
4. The envelope is closed at the top level too: there is no ``extra``, ``meta``,
   ``passthrough``, or ``raw`` field, and there never will be.

Growth inside ``mkep: "0"`` is additive only — a new operation, error code, or
optional argument changes ``capabilities_digest`` and nothing else. A breaking
change requires ``mkep: "1"`` served alongside ``"0"``.

Runtime-neutral: nothing here reads a wall clock, a hostname, an environment
variable, or a runtime-specific identifier. Every instant is caller-injected.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

from . import execution_handoff, execution_state
from .execution_projection import EXECUTION_SCHEMA, project, project_leases
from .execution_protocol import (
    ERROR_REGISTRY,
    MAX_PROTOCOL_ARRAY_LENGTH,
    ExecutionError,
    ValidationError,
    canonical_timestamp,
    digest,
)
from .store_core import read_all

__all__ = ["describe", "dispatch", "MKEP_VERSION", "ENVELOPE_SCHEMA_VERSION"]

MKEP_VERSION = "0"
ENVELOPE_SCHEMA_VERSION = 1

#: §17. Preview, with a dated decision so "preview" cannot quietly become
#: permanent. This is a promise about *when the question is answered*, not about
#: what the answer will be.
STABILITY = "preview"
GA_DECISION_DEADLINE = "2027-02-04"

#: §6.1. Correlation only — never an idempotency key. ULID or 32-hex.
_REQUEST_ID = re.compile(r"^(?:[0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{32})$")
_OPERATION_ID = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

#: §6.1. The complete top-level key set. Anything else is ``E_UNKNOWN_FIELD``.
_ENVELOPE_KEYS = (
    "mkep", "kind", "request_id", "op", "now", "target", "args",
    "precondition", "binding", "capabilities_digest",
)
_ENVELOPE_REQUIRED = ("mkep", "kind", "request_id", "op", "target", "args")

#: §6.5. The only optimistic-concurrency mechanism there is.
_PRECONDITION_KEYS = ("operation_id", "expect_state", "expect_execution_seq",
                      "fence_token")

#: §4.5. Opaque to core: carried for audit, never parsed for meaning.
_BINDING_KEYS = ("execution_run_id", "parent_execution_run_id", "holder",
                 "binding_digest")

#: §6.2 note on ``state.read``. Closed, because "no expression surface" means
#: the caller cannot describe a slice core did not anticipate.
_INCLUDE = ("gates", "handoffs", "leases")

#: §6.4.
_OUTCOMES = ("applied", "already_applied", "no_op", "read")

MAX_REQUEST_BYTES = 262144
MAX_RESPONSE_BYTES = 1048576


class _Op(NamedTuple):
    """One row of the closed registry (§6.2).

    ``adapter`` is hand-written per operation; ``result_keys`` is the closed set
    of extra keys that operation contributes to ``result``, which is what stops
    a store record — and the ``local_private`` content in it — from reaching the
    wire because someone returned the whole dict.
    """

    kind: str
    target: Tuple[str, ...]
    required: Tuple[str, ...]
    optional: Tuple[str, ...]
    fenced: bool
    state_of: Optional[str]
    adapter: Callable[..., Dict[str, Any]]
    result_keys: Tuple[str, ...] = ()


def _fail(code: str, message: str, **details) -> None:
    raise ValidationError(code, message, details)


# --------------------------------------------------------------------------
# adapters — one hand-written function per operation (§6.3 lock 2)
# --------------------------------------------------------------------------

def _goal_declare(store, target, args, now, fence_token, operation_id):
    return store.goal_declare(
        target["goal_id"], args["title"], args["intent"], args["constraints"],
        args["success_criteria"], now=now, owner_hint=args.get("owner_hint"),
        parent_goal_id=args.get("parent_goal_id"),
        privacy=args.get("privacy", "local_private"), operation_id=operation_id,
    )


def _goal_transition(store, target, args, now, fence_token, operation_id):
    return store.goal_transition(
        target["goal_id"], args["to_status"], now=now, reason=args.get("reason"),
        fence_token=fence_token,
        authority_claim=args.get("authority_claim", "agent"),
        operation_id=operation_id,
    )


def _gate_declare(store, target, args, now, fence_token, operation_id):
    return store.gate_declare(
        target["goal_id"], target["gate_id"], args["description"],
        args["verification"], now=now, required=args.get("required", True),
        scope_key=args.get("scope_key"), fence_token=fence_token,
        operation_id=operation_id,
    )


def _gate_transition(store, target, args, now, fence_token, operation_id):
    return store.gate_transition(
        target["goal_id"], target["gate_id"], args["to_status"], now=now,
        fence_token=fence_token, receipt_id=args.get("receipt_id"),
        reopen_reason=args.get("reopen_reason"),
        authority_claim=args.get("authority_claim", "agent"),
        operation_id=operation_id,
    )


def _receipt_record(store, target, args, now, fence_token, operation_id):
    # §6.2 note: ``receipt_id`` is core-allocated, so it is not a target key —
    # the receipt reuses the envelope ``id`` and it comes back in ``result``.
    result = store.receipt_record(
        target["goal_id"], target["gate_id"], args["verdict"],
        args["content_sha256"], args["summary"], now=now,
        fence_token=fence_token, observed_at=args["observed_at"],
        provenance_id=args.get("provenance_id"),
        artifact_path=args.get("artifact_path"), operation_id=operation_id,
    )
    result["receipt_id"] = result["record_id"]
    return result


def _lease_acquire(store, target, args, now, fence_token, operation_id):
    return store.lease_acquire(
        target["goal_id"], target["scope_key"], args["holder"],
        args["ttl_seconds"], now=now, expected_fence=args.get("expected_fence"),
        operation_id=operation_id,
    )


def _lease_release(store, target, args, now, fence_token, operation_id):
    return store.lease_release(
        target["goal_id"], target["scope_key"], args["lease_id"], now=now,
        released_by=args.get("released_by"), operation_id=operation_id,
    )


def _handoff_declare(store, target, args, now, fence_token, operation_id):
    schema = args.get("payload_schema", execution_handoff.DEFAULT_PAYLOAD_SCHEMA)
    return store.handoff_declare(
        target["goal_id"], args["to_actor"], args["payload"], now=now,
        payload_schema=schema, expires_at=args.get("expires_at"),
        fence_token=fence_token, operation_id=operation_id,
    )


def _handoff_transition(store, target, args, now, fence_token, operation_id):
    return store.handoff_transition(
        target["goal_id"], target["handoff_id"], args["to_state"], now=now,
        fence_token=fence_token, operation_id=operation_id,
    )


def _handoff_import(store, target, args, now, fence_token, operation_id):
    # §10.4: an envelope object and nothing else. No path, base_dir, profile, or
    # URL parameter exists here, which is what makes a cross-base read
    # inexpressible rather than merely discouraged.
    return store.handoff_import(
        target["goal_id"], args["envelope"], now=now, fence_token=fence_token,
        operation_id=operation_id,
    )


def _assess_record(store, target, args, now, fence_token, operation_id):
    return store.assess_record(
        target["goal_id"], args["assessment"], now=now, operation_id=operation_id,
    )


def _assess_run(store, target, args, now, fence_token, operation_id):
    # A query (§6.2 note): a heartbeat-driven runtime polls this forever without
    # growing the log. What comes back is advisory and is not an authorization.
    return {"outcome": "read", "result": store.assess_run(target["goal_id"], now=now)}


def _handoff_export(store, target, args, now, fence_token, operation_id):
    result = store.handoff_export(target["goal_id"], target["handoff_id"], now=now)
    return {"outcome": "read",
            "result": {"envelope": result["envelope"],
                       "origin_instance_id": result["origin_instance_id"]}}


def _state_read(store, target, args, now, fence_token, operation_id):
    goal_id = target["goal_id"]
    include = args.get("include")
    include = _INCLUDE if include is None else tuple(include)
    read = read_all(store._execution_events_path())
    state = project(read.records, now, goal_id, read.skipped)
    result = {
        "execution_schema": state["execution_schema"],
        "goal_id": goal_id,
        "goal_status": state["goal_status"],
        "execution_seq": state["execution_seq"],
        "unverified_waivers": state["unverified_waivers"],
        "receipts_without_provenance": state["receipts_without_provenance"],
        "skipped": state["skipped"],
        "rejected_transitions": len(state["rejected_transitions"]),
        "consistent": state["consistent"],
        "projection_digest": state["digest"],
        "evaluated_at": state["evaluated_at"],
    }
    if "gates" in include:
        result["gates"] = state["gates"]
    if "handoffs" in include:
        result["handoffs"] = state["handoffs"]
    if "leases" in include:
        leases = project_leases(read.records, now, goal_id)
        result["leases"] = {
            scope_key: {"lease_id": held["lease_id"], "holder": held["holder"],
                        "fence_token": held["fence_token"],
                        "expires_at": held["expires_at"]}
            for scope_key, held in sorted(leases["active"].items())
        }
        result["max_fence_token"] = leases["max_fence"]
    return {"outcome": "read", "result": result}


def _describe_op(store, target, args, now, fence_token, operation_id):
    return {"outcome": "read", "result": describe()}


#: §6.2 — closed, exactly fifteen entries: 11 apply + 4 query. Adding one
#: requires a ``capabilities_digest`` change *and* a conformance fixture.
_OPS: Dict[str, _Op] = {
    "goal.declare": _Op(
        "apply", ("goal_id",),
        ("title", "intent", "constraints", "success_criteria"),
        ("owner_hint", "parent_goal_id", "privacy"),
        False, None, _goal_declare,
    ),
    "goal.transition": _Op(
        "apply", ("goal_id",), ("to_status", "reason"), ("authority_claim",),
        True, "goal", _goal_transition, ("goal_status",),
    ),
    "gate.declare": _Op(
        "apply", ("goal_id", "gate_id"), ("description", "verification"),
        ("required", "scope_key"), True, None, _gate_declare,
    ),
    "gate.transition": _Op(
        "apply", ("goal_id", "gate_id"), ("to_status",),
        ("receipt_id", "reopen_reason", "authority_claim"),
        True, "gate", _gate_transition, ("gate_status",),
    ),
    "receipt.record": _Op(
        "apply", ("goal_id", "gate_id"),
        ("verdict", "content_sha256", "summary", "observed_at"),
        ("provenance_id", "artifact_path"),
        True, None, _receipt_record, ("receipt_id",),
    ),
    "lease.acquire": _Op(
        "apply", ("goal_id", "scope_key"), ("holder", "ttl_seconds"),
        ("expected_fence",), False, None, _lease_acquire,
        ("scope_key", "lease_id", "fence_token", "expires_at",
         "supersedes_lease_id", "supersede_reason"),
    ),
    "lease.release": _Op(
        "apply", ("goal_id", "scope_key"), ("lease_id",), ("released_by",),
        False, None, _lease_release,
        ("scope_key", "lease_id", "fence_token", "expires_at",
         "supersedes_lease_id", "supersede_reason"),
    ),
    "handoff.declare": _Op(
        "apply", ("goal_id",), ("to_actor", "payload"),
        ("payload_schema", "expires_at"), True, None, _handoff_declare,
        ("handoff_id", "payload_digest"),
    ),
    "handoff.transition": _Op(
        "apply", ("goal_id", "handoff_id"), ("to_state",), (),
        True, "handoff", _handoff_transition, ("handoff_id", "handoff_status"),
    ),
    "handoff.import": _Op(
        "apply", ("goal_id",), ("envelope",), (), True, None, _handoff_import,
        ("handoff_id", "payload_digest", "imported_from"),
    ),
    "assess.record": _Op(
        "apply", ("goal_id",), ("assessment",), (), False, None, _assess_record,
    ),
    "assess.run": _Op("query", ("goal_id",), (), (), False, None, _assess_run),
    "state.read": _Op("query", ("goal_id",), (), ("include",), False, None,
                      _state_read),
    "handoff.export": _Op("query", ("goal_id", "handoff_id"), (), (), False,
                          None, _handoff_export),
    "describe": _Op("query", (), (), (), False, None, _describe_op),
}

#: §17. ``describe`` is the only operation that needs no ``now``: it reads no
#: log, touches no clock, and is safe on a base that was never initialised.
_NOW_FREE = ("describe",)

#: §13.1. Read-only in preview; no MCP tool can reach an ``apply`` op.
MCP_OPS = ("state.read", "assess.run", "handoff.export", "describe")


# --------------------------------------------------------------------------
# §17 — describe
# --------------------------------------------------------------------------

def describe() -> Dict[str, Any]:
    """Return the capability and version block (§17). Reads and writes nothing.

    ``guarantees`` is deliberately a **negative capability list**. An adapter
    that requires multi-host coordination, verified authority, or enforcing
    gates refuses to start on reading it, rather than discovering the gap in
    production.
    """
    body = {
        "mkep": MKEP_VERSION,
        "implementation": "memkraft",
        "implementation_version": _implementation_version(),
        "execution_schema": EXECUTION_SCHEMA,
        "envelope_schema_version": ENVELOPE_SCHEMA_VERSION,
        "canonical_json": "MKCJSON/1",
        "time_profile": "MKEP-TIME/1",
        "stability": STABILITY,
        "ga_decision_deadline": GA_DECISION_DEADLINE,
        "ops": sorted(_OPS),
        "error_codes": sorted(ERROR_REGISTRY),
        "transports": ["python"],
        "mcp_ops": list(MCP_OPS),
        "limits": {
            "max_record_bytes": 16384,
            "max_request_bytes": MAX_REQUEST_BYTES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "max_handoff_bytes": execution_handoff.MAX_PAYLOAD_BYTES,
            "max_gates_per_goal": execution_state.MAX_GATES_PER_GOAL,
            "max_active_leases_per_goal": execution_state.MAX_ACTIVE_LEASES_PER_GOAL,
            "max_string_len": execution_state._MAX_STRING,
            "max_list_len": execution_state._MAX_LIST,
            "max_ttl_seconds": execution_state._MAX_TTL_SECONDS,
            "max_payload_depth": 8,
        },
        "guarantees": {
            "atomic_unit": "single_line_append",
            "clock": "caller_injected",
            "scope": "single_host_local_filesystem",
            "multi_host": False,
            "network_filesystem": False,
            "authority_verified": False,
            "gates_are_advisory": True,
            "should_run_is_advisory": True,
            "cross_base_read": False,
            "mcp_mutation": False,
            "envelope_authenticity": False,
            "execution_log_compaction": False,
        },
    }
    body["capabilities_digest"] = digest(body, MAX_PROTOCOL_ARRAY_LENGTH)
    return body


def _implementation_version() -> str:
    """Read the package version without importing the package at module scope."""
    from . import __version__

    return __version__


# --------------------------------------------------------------------------
# §6.1 — envelope validation
# --------------------------------------------------------------------------

def _validate_envelope(request: Dict[str, Any]) -> _Op:
    """Enforce §6.1 in a fixed order, returning the registry entry.

    The order is part of the contract: a client that fixes one complaint must
    not be handed a different code for the same envelope on the next attempt.
    Negotiation is settled first (version, then operation), because a client
    speaking the wrong protocol should learn that rather than learn about a
    field that may not exist in the version it wanted.
    """
    unknown = sorted(set(request) - set(_ENVELOPE_KEYS))
    if unknown:
        _fail("E_UNKNOWN_FIELD", "the envelope is closed", path=unknown[0])

    for field in _ENVELOPE_REQUIRED:
        if field not in request:
            _fail("E_MISSING_FIELD", "a required envelope field is absent", path=field)

    if request["mkep"] != MKEP_VERSION:
        raise ValidationError(
            "E_VERSION_UNSUPPORTED", "this implementation speaks MKEP/0 only",
            {"path": "mkep", "supported": [MKEP_VERSION]},
        )

    entry = _OPS.get(request["op"]) if isinstance(request["op"], str) else None
    if entry is None:
        raise ValidationError(
            "E_UNKNOWN_OP", "the operation registry is closed",
            {"path": "op", "supported": sorted(_OPS)},
        )

    if request["kind"] != entry.kind:
        _fail("E_PATTERN", "kind does not match the operation's registry kind",
              path="kind", expected=entry.kind)

    if not isinstance(request["request_id"], str) \
            or not _REQUEST_ID.match(request["request_id"]):
        _fail("E_PATTERN", "request_id is not a ULID or a 32-hex string",
              path="request_id")

    supplied = request.get("capabilities_digest")
    if supplied is not None:
        expected = describe()["capabilities_digest"]
        if supplied != expected:
            raise ValidationError(
                "E_CAPABILITY_DRIFT",
                "the pinned capability set is not the one this build serves",
                {"path": "capabilities_digest", "expected": expected},
            )

    if request["op"] in _NOW_FREE:
        if "now" in request:
            _fail("E_UNKNOWN_FIELD", "this operation is not clock-sensitive",
                  path="now")
    elif "now" not in request:
        _fail("E_MISSING_FIELD", "an injected now is required", path="now")

    _check_object("target", request["target"])
    _check_object("args", request["args"])
    _check_keys("target", request["target"], entry.target, ())
    _check_keys("args", request["args"], entry.required, entry.optional)

    if entry.kind == "apply":
        if "precondition" not in request:
            _fail("E_MISSING_FIELD", "every apply carries a precondition",
                  path="precondition")
        _check_object("precondition", request["precondition"])
        _check_keys("precondition", request["precondition"], (), _PRECONDITION_KEYS)
    elif "precondition" in request:
        _fail("E_UNKNOWN_FIELD", "a query has no precondition", path="precondition")

    if "binding" in request:
        _check_object("binding", request["binding"])
        _check_keys("binding", request["binding"], (), _BINDING_KEYS)

    return entry


def _check_object(path: str, value: Any) -> None:
    if not isinstance(value, dict):
        _fail("E_TYPE", "this field is an object", path=path)


def _check_keys(path: str, supplied: Dict[str, Any],
                required: Tuple[str, ...], optional: Tuple[str, ...]) -> None:
    unknown = sorted(set(supplied) - set(required) - set(optional))
    if unknown:
        _fail("E_UNKNOWN_FIELD", "this key set is closed per operation",
              path="%s.%s" % (path, unknown[0]))
    for field in required:
        if field not in supplied:
            _fail("E_MISSING_FIELD", "a required key is absent",
                  path="%s.%s" % (path, field))


def _validate_include(args: Dict[str, Any]) -> None:
    include = args.get("include")
    if include is None:
        return
    if not isinstance(include, list):
        _fail("E_TYPE", "include is a list", path="args.include")
    for index, name in enumerate(include):
        if name not in _INCLUDE:
            _fail("E_PATTERN", "include is a closed list",
                  path="args.include[%d]" % index, supported=list(_INCLUDE))


def _validate_precondition(entry: _Op, precondition: Dict[str, Any]) -> None:
    operation_id = precondition.get("operation_id")
    if operation_id is not None and (
            not isinstance(operation_id, str) or not _OPERATION_ID.match(operation_id)):
        _fail("E_PATTERN", "operation_id is a 64-character hex digest",
              path="precondition.operation_id")
    if "expect_state" in precondition and entry.state_of is None:
        # §6.5 stays honest: an operation with no entity state has no state to
        # expect, and silently ignoring the field would make the precondition
        # look enforced when it was never read.
        _fail("E_UNKNOWN_FIELD", "this operation has no entity state to expect",
              path="precondition.expect_state")
    if "fence_token" in precondition and not entry.fenced:
        _fail("E_UNKNOWN_FIELD", "this operation is not fence-protected",
              path="precondition.fence_token")
    sequence = precondition.get("expect_execution_seq")
    if sequence is not None and (isinstance(sequence, bool)
                                 or not isinstance(sequence, int)):
        _fail("E_TYPE", "expect_execution_seq is an integer",
              path="precondition.expect_execution_seq")


# --------------------------------------------------------------------------
# §6.5 / §18 — preconditions checked against the projection
# --------------------------------------------------------------------------

def _entity_status(state: Dict[str, Any], entry: _Op,
                   target: Dict[str, Any]) -> Optional[str]:
    if entry.state_of == "goal":
        return state["goal_status"]
    if entry.state_of == "gate":
        for gate in state["gates"]:
            if gate["gate_id"] == target.get("gate_id"):
                return gate["status"]
        return None
    for handoff in state["handoffs"]:
        if handoff["handoff_id"] == target.get("handoff_id"):
            return handoff["status"]
    return None


def _check_preconditions(state: Dict[str, Any], entry: _Op,
                         target: Dict[str, Any],
                         precondition: Dict[str, Any]) -> None:
    if not state["consistent"]:
        # IN-01: an inconsistent projection is repaired, not written over. Every
        # apply is refused until it is, because appending onto a log whose fold
        # already disagrees with itself buries the disagreement.
        raise ExecutionError(
            "E_PROJECTION_INCONSISTENT",
            "the goal's projection does not fold cleanly and needs repair",
            {"rejected_transitions": len(state["rejected_transitions"]),
             "skipped": state["skipped"]},
        )
    if "expect_state" in precondition:
        actual = _entity_status(state, entry, target)
        if actual != precondition["expect_state"]:
            raise ExecutionError(
                "E_PRECONDITION_STATE", "the entity is not in the expected state",
                {"actual": actual, "expected": precondition["expect_state"]},
            )
    expected_seq = precondition.get("expect_execution_seq")
    if expected_seq is not None and expected_seq != state["execution_seq"]:
        raise ExecutionError(
            "E_PRECONDITION_SEQ", "the goal moved since this request was formed",
            {"actual": state["execution_seq"], "expected": expected_seq},
        )


def _state_block(store, entry: _Op, target: Dict[str, Any],
                 now: Any) -> Optional[Dict[str, Any]]:
    """Return the §6.4 ``state`` block, or ``None`` when it cannot be computed.

    ``None`` is the honest answer for ``describe``, for an envelope whose
    ``goal_id`` never parsed, and for one whose ``now`` is unusable: reporting a
    projection would mean inventing the instant it was evaluated at.
    """
    goal_id = target.get("goal_id") if isinstance(target, dict) else None
    if not isinstance(goal_id, str) or now is None:
        return None
    try:
        canonical_timestamp(now)
        read = read_all(store._execution_events_path())
        state = project(read.records, now, goal_id, read.skipped)
    except (ExecutionError, OSError, ValueError):
        return None
    return {"execution_seq": state["execution_seq"],
            "projection_digest": state["digest"],
            "consistent": state["consistent"]}


# --------------------------------------------------------------------------
# §6.4 — the one response shape
# --------------------------------------------------------------------------

def _safe_digest(obj: Dict[str, Any]) -> Optional[str]:
    """Digest ``obj``, or ``None`` when it is outside MKCJSON/1.

    A malformed request still gets a response, and that response says plainly
    that the request had no canonical form rather than inventing one.
    """
    try:
        return digest(obj, MAX_PROTOCOL_ARRAY_LENGTH)
    except ExecutionError:
        return None


def _respond(request: Any, op: Optional[str], request_digest: Optional[str],
             state: Optional[Dict[str, Any]], *, ok: bool,
             outcome: Optional[str] = None, result: Optional[Dict[str, Any]] = None,
             warnings: Optional[list] = None,
             error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    request_id = None
    if isinstance(request, dict) and isinstance(request.get("request_id"), str):
        request_id = request["request_id"]
    response: Dict[str, Any] = {
        "mkep": MKEP_VERSION,
        "request_id": request_id,
        "op": op,
        "ok": ok,
    }
    if ok:
        response["outcome"] = outcome
        response["result"] = result
        response["warnings"] = list(warnings or [])
    else:
        response["error"] = error
    response["state"] = state
    response["request_digest"] = request_digest
    response["response_digest"] = None
    response["response_digest"] = _safe_digest(
        {key: value for key, value in response.items() if key != "response_digest"}
    )
    return response


def _error_body(error: ExecutionError) -> Dict[str, Any]:
    """Project an ``ExecutionError`` onto §6.4's error object.

    ``message`` and ``details`` are what the kernel already produced: core never
    puts a path, ``base_dir``, hostname, or username into either, and
    ``details`` carries digests, counts, and key paths — never values — so
    ``local_private`` content cannot leak into a log the runtime ships onward.
    """
    return {"code": error.code, "message": str(error), "class": error.error_class,
            "retryable": error.retryable, "details": dict(error.details)}


def _projected_result(entry: _Op, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a kernel result to the operation's closed wire key set."""
    result = {
        "record_id": raw.get("record_id"),
        "event_seq": raw.get("event_seq"),
        "operation_id": raw.get("operation_id"),
        "record_fingerprint": raw.get("record_fingerprint"),
    }
    for key in entry.result_keys:
        result[key] = raw.get(key)
    return result


def dispatch(store, request: Any) -> Dict[str, Any]:
    """Execute one MKEP/0 request against ``store`` and return one response.

    ``store`` is any object carrying the execution mixins — the dispatcher calls
    the typed methods by name and never by attribute lookup on caller input.

    This never raises for a protocol-level problem: an error is a response, so a
    subprocess or CLI transport has exactly one thing to serialize.
    """
    if not isinstance(request, dict):
        return _respond(request, None, None, None, ok=False, error=_error_body(
            ValidationError("E_MALFORMED_JSON", "a request is a JSON object", {})))

    op = request.get("op") if isinstance(request.get("op"), str) else None
    request_digest = _safe_digest(request)

    try:
        encoded = len(json.dumps(request, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return _respond(request, op, request_digest, None, ok=False,
                        error=_error_body(ValidationError(
                            "E_MALFORMED_JSON", "the request is not JSON", {})))
    if encoded > MAX_REQUEST_BYTES:
        return _respond(request, op, request_digest, None, ok=False,
                        error=_error_body(ValidationError(
                            "E_LIMIT_EXCEEDED", "the request is larger than the limit",
                            {"limit": MAX_REQUEST_BYTES})))

    try:
        entry = _validate_envelope(request)
    except ExecutionError as error:
        return _respond(request, op, request_digest, None, ok=False,
                        error=_error_body(error))

    target = request["target"]
    args = request["args"]
    now = request.get("now")
    precondition = request.get("precondition") or {}

    try:
        if entry.kind == "apply":
            _validate_precondition(entry, precondition)
        if request["op"] == "state.read":
            _validate_include(args)
        if entry.kind == "apply":
            state = _state_block(store, entry, target, now)
            if state is not None:
                read = read_all(store._execution_events_path())
                _check_preconditions(
                    project(read.records, now, target["goal_id"], read.skipped),
                    entry, target, precondition,
                )
        raw = entry.adapter(store, target, args, now,
                            precondition.get("fence_token"),
                            precondition.get("operation_id") or request_digest)
    except ExecutionError as error:
        return _respond(request, op, request_digest,
                        _state_block(store, entry, target, now),
                        ok=False, error=_error_body(error))

    if entry.kind == "query":
        outcome, result, warnings = raw["outcome"], raw["result"], []
    else:
        outcome = raw["outcome"]
        result = _projected_result(entry, raw)
        warnings = raw.get("warnings") or []

    if outcome not in _OUTCOMES:  # pragma: no cover - registry invariant
        raise ExecutionError("E_INTERNAL", "an adapter produced an unknown outcome")

    return _respond(request, op, request_digest,
                    _state_block(store, entry, target, now),
                    ok=True, outcome=outcome, result=result, warnings=warnings)
