"""Slice 9 — the closed MKEP/0 dispatcher and ``describe`` (plan §6, §17).

The typed Python methods are the primary API.  The dispatcher is a *projection*
of them onto one closed wire envelope so a non-Python runtime reaches the same
semantics.  Everything here holds that closedness: exactly fifteen operations, a
closed top-level envelope, closed per-op ``target`` and ``args`` key sets, and a
response shape that never varies.

Nothing in this file re-tests kernel semantics — those live in
``test_execution_records.py``, ``test_execution_evidence.py``,
``test_execution_leases.py`` and ``test_execution_handoff.py``.  What is tested
here is only what the dispatcher itself adds: validation order, envelope
closedness, negotiation, preconditions, and the stable response.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from memkraft import MemKraft, execution_dispatch, execution_state
from memkraft.execution_dispatch import describe, dispatch
from memkraft.execution_protocol import (
    ERROR_REGISTRY,
    MAX_PROTOCOL_ARRAY_LENGTH,
    digest,
)

NOW = "2026-08-04T11:22:33Z"
LATER = "2026-08-04T12:00:00Z"
REQUEST_ID = "01JKX7Q2M0000000000000000A"
OPERATION_ID = "a" * 64
GOAL_ID = "hermes/release-3-3-0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: §6.2.  Sorted, and the sort order is part of the contract: ``describe.ops``
#: is compared byte-for-byte against this list by a second-language runtime.
OPS = [
    "assess.record", "assess.run", "describe", "gate.declare", "gate.transition",
    "goal.declare", "goal.transition", "handoff.declare", "handoff.export",
    "handoff.import", "handoff.transition", "lease.acquire", "lease.release",
    "receipt.record", "state.read",
]

#: §6.7.  Closed and additive-only; a code is never repurposed.
ERROR_CODES = [
    "E_ALREADY_DECLARED", "E_AUTHORITY_CLAIM_REQUIRED",
    "E_AUTHORITY_VERIFIED_FORBIDDEN", "E_CAPABILITY_DRIFT", "E_CONFLICT",
    "E_DIGEST_MISMATCH", "E_EVIDENCE_REQUIRED", "E_EVIDENCE_STALE",
    "E_FENCE_REQUIRED", "E_FENCE_STALE", "E_GATE_CAP", "E_HANDOFF_EXPIRED",
    "E_IDEMPOTENCY_MISMATCH", "E_INTERNAL", "E_INVALID_TRANSITION",
    "E_LEASE_CAP", "E_LEASE_HELD", "E_LIMIT_EXCEEDED", "E_MALFORMED_JSON",
    "E_MISSING_FIELD", "E_NOT_DECLARED", "E_PATTERN", "E_PRECONDITION_SEQ",
    "E_PRECONDITION_STATE", "E_PROJECTION_INCONSISTENT", "E_STORE_BUSY",
    "E_STORE_IO", "E_TIME_FORMAT", "E_TIME_NAIVE", "E_TYPE", "E_UNKNOWN_FIELD",
    "E_UNKNOWN_OP", "E_VERSION_UNSUPPORTED",
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _lines(mk):
    path = mk._execution_events_path()
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _apply(op, target, args, *, now=NOW, operation_id=OPERATION_ID, **extra):
    request = {
        "mkep": "0", "kind": "apply", "request_id": REQUEST_ID, "op": op,
        "now": now, "target": target, "args": args,
        "precondition": {"operation_id": operation_id},
    }
    request.update(extra)
    return request


def _query(op, target, args=None, **extra):
    request = {
        "mkep": "0", "kind": "query", "request_id": REQUEST_ID, "op": op,
        "now": NOW, "target": target, "args": {} if args is None else args,
    }
    request.update(extra)
    return request


GOAL_ARGS = {
    "title": "Release 3.3.0",
    "intent": "Ship the execution kernel",
    "constraints": ["stdlib only"],
    "success_criteria": ["suite green"],
}


def _declare_goal(mk, operation_id=OPERATION_ID):
    return dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS),
                               operation_id=operation_id))


def _declare_gate(mk, gate_id="tests-green", operation_id="b" * 64):
    return dispatch(mk, _apply(
        "gate.declare", {"goal_id": GOAL_ID, "gate_id": gate_id},
        {"description": "the suite is green",
         "verification": {"check_kind": "command", "check_ref": "pytest -q"}},
        operation_id=operation_id,
    ))


def _error(response):
    assert response["ok"] is False, response
    return response["error"]


# --------------------------------------------------------------------------
# §6.2 — the registry is closed at exactly fifteen entries
# --------------------------------------------------------------------------

def test_operation_registry_holds_exactly_fifteen_entries():
    """G8: ``len(_OPS) == 15``, and the names are exactly §6.2's."""
    assert len(execution_dispatch._OPS) == 15
    assert sorted(execution_dispatch._OPS) == OPS


def test_registry_kinds_are_eleven_apply_and_four_query():
    kinds = [entry.kind for entry in execution_dispatch._OPS.values()]
    assert kinds.count("apply") == 11
    assert kinds.count("query") == 4


def test_every_operation_declares_closed_target_and_args_key_sets():
    """§6.3 lock 2: keys are enumerated per operation, never open."""
    for name, entry in execution_dispatch._OPS.items():
        assert isinstance(entry.target, tuple), name
        assert isinstance(entry.required, tuple), name
        assert isinstance(entry.optional, tuple), name
        assert not set(entry.required) & set(entry.optional), name


def test_dispatch_never_turns_an_op_string_into_an_attribute_lookup():
    """§6.3 lock 1: no dynamic registration, no ``getattr`` on user input."""
    source = inspect.getsource(execution_dispatch)
    assert "getattr(" not in source
    assert "**args" not in source
    assert "**kwargs" not in source


def test_unknown_operation_is_rejected_with_the_negotiation_code(tmp_path):
    mk = _mk(tmp_path)
    response = dispatch(mk, _query("goal.list", {"goal_id": GOAL_ID}))
    error = _error(response)
    assert error["code"] == "E_UNKNOWN_OP"
    assert error["class"] == "negotiation"
    assert _lines(mk) == []


# --------------------------------------------------------------------------
# §6.1 — the envelope is closed
# --------------------------------------------------------------------------

def test_unknown_top_level_field_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["passthrough"] = {"anything": 1}
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "passthrough"
    assert _lines(mk) == []


@pytest.mark.parametrize("field", ["mkep", "kind", "request_id", "op", "target", "args"])
def test_missing_required_top_level_field_is_rejected(tmp_path, field):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    del request[field]
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_MISSING_FIELD"
    assert error["details"]["path"] == field


def test_apply_without_a_precondition_is_rejected(tmp_path):
    """§6.5: the precondition block is required on every apply."""
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    del request["precondition"]
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_MISSING_FIELD"
    assert error["details"]["path"] == "precondition"


def test_query_may_not_carry_a_precondition(tmp_path):
    mk = _mk(tmp_path)
    request = _query("state.read", {"goal_id": GOAL_ID})
    request["precondition"] = {"operation_id": OPERATION_ID}
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "precondition"


def test_kind_must_match_the_registry_kind(tmp_path):
    mk = _mk(tmp_path)
    request = _query("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_PATTERN"
    assert error["details"]["path"] == "kind"


def test_request_id_must_match_the_correlation_grammar(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["request_id"] = "not-a-ulid"
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_PATTERN"
    assert error["details"]["path"] == "request_id"


def test_unknown_target_key_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID, "gate_id": "x"},
                     dict(GOAL_ARGS))
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "target.gate_id"


def test_unknown_args_key_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    args = dict(GOAL_ARGS, deadline="2026-09-01T00:00:00Z")
    error = _error(dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID}, args)))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "args.deadline"


def test_missing_required_arg_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    args = dict(GOAL_ARGS)
    del args["intent"]
    error = _error(dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID}, args)))
    assert error["code"] == "E_MISSING_FIELD"
    assert error["details"]["path"] == "args.intent"


def test_missing_target_key_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    error = _error(dispatch(mk, _apply("goal.declare", {}, dict(GOAL_ARGS))))
    assert error["code"] == "E_MISSING_FIELD"
    assert error["details"]["path"] == "target.goal_id"


def test_fence_token_is_not_expressible_in_args(tmp_path):
    """§6.2 note: fencing lives only in ``precondition``, never in ``args``."""
    mk = _mk(tmp_path)
    for entry in execution_dispatch._OPS.values():
        assert "fence_token" not in entry.required
        assert "fence_token" not in entry.optional
    args = dict(GOAL_ARGS, fence_token=1)
    error = _error(dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID}, args)))
    assert error["code"] == "E_UNKNOWN_FIELD"


def test_unknown_precondition_key_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["precondition"]["if_match"] = "x"
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "precondition.if_match"


def test_a_non_object_request_is_malformed(tmp_path):
    mk = _mk(tmp_path)
    error = _error(dispatch(mk, ["goal.declare"]))
    assert error["code"] == "E_MALFORMED_JSON"


def test_request_larger_than_the_advertised_limit_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    args = dict(GOAL_ARGS, intent="x" * 300000)
    error = _error(dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID}, args)))
    assert error["code"] == "E_LIMIT_EXCEEDED"
    assert error["details"]["limit"] == describe()["limits"]["max_request_bytes"]


# --------------------------------------------------------------------------
# §17 — negotiation
# --------------------------------------------------------------------------

def test_a_future_protocol_version_is_refused_without_a_downgrade_dance(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["mkep"] = "1"
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_VERSION_UNSUPPORTED"
    assert error["class"] == "negotiation"
    assert error["details"]["supported"] == ["0"]


def test_capability_drift_is_reported_rather_than_silently_accepted(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["capabilities_digest"] = "0" * 64
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_CAPABILITY_DRIFT"
    assert error["details"]["expected"] == describe()["capabilities_digest"]
    assert _lines(mk) == []


def test_a_matching_capabilities_digest_is_accepted(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["capabilities_digest"] = describe()["capabilities_digest"]
    assert dispatch(mk, request)["ok"] is True


# --------------------------------------------------------------------------
# §17 — describe
# --------------------------------------------------------------------------

def test_describe_advertises_the_full_negotiation_block():
    body = describe()
    assert body["mkep"] == "0"
    assert body["implementation"] == "memkraft"
    assert body["execution_schema"] == execution_state.EXECUTION_SCHEMA
    assert body["envelope_schema_version"] == 1
    assert body["canonical_json"] == "MKCJSON/1"
    assert body["time_profile"] == "MKEP-TIME/1"
    assert body["stability"] == "preview"
    assert body["ga_decision_deadline"] == "2027-02-04"
    assert body["ops"] == OPS
    assert body["error_codes"] == ERROR_CODES
    assert body["mcp_ops"] == ["state.read", "assess.run", "handoff.export", "describe"]
    assert HEX64.match(body["capabilities_digest"])


def test_describe_limits_agree_with_the_kernel_constants():
    """§17 rule 5: an adapter enforces ``limits`` client-side, so they must be true."""
    limits = describe()["limits"]
    assert limits["max_gates_per_goal"] == execution_state.MAX_GATES_PER_GOAL
    assert limits["max_active_leases_per_goal"] == \
        execution_state.MAX_ACTIVE_LEASES_PER_GOAL
    assert limits["max_ttl_seconds"] == execution_state._MAX_TTL_SECONDS
    assert limits["max_string_len"] == execution_state._MAX_STRING
    assert limits["max_list_len"] == execution_state._MAX_LIST


def test_describe_guarantees_are_a_negative_capability_list():
    """§17: machine-readable honesty, so an adapter refuses to start rather than
    discovering the gap in production."""
    guarantees = describe()["guarantees"]
    assert guarantees["multi_host"] is False
    assert guarantees["network_filesystem"] is False
    assert guarantees["authority_verified"] is False
    assert guarantees["gates_are_advisory"] is True
    assert guarantees["should_run_is_advisory"] is True
    assert guarantees["cross_base_read"] is False
    assert guarantees["mcp_mutation"] is False
    assert guarantees["envelope_authenticity"] is False
    assert guarantees["execution_log_compaction"] is False
    assert guarantees["atomic_unit"] == "single_line_append"
    assert guarantees["clock"] == "caller_injected"
    assert guarantees["scope"] == "single_host_local_filesystem"


def test_capabilities_digest_is_over_the_body_without_itself():
    body = describe()
    expected = digest({k: v for k, v in body.items() if k != "capabilities_digest"},
                      MAX_PROTOCOL_ARRAY_LENGTH)
    assert body["capabilities_digest"] == expected


def test_describe_is_deterministic_across_calls():
    assert describe() == describe()


def test_describe_on_an_uninitialised_base_creates_zero_files(tmp_path):
    """§17: ``describe`` is safe on a base that was never ``init``-ed."""
    base = tmp_path / "cold"
    base.mkdir()
    mk = MemKraft(base_dir=str(base))
    before = sorted(p.relative_to(base).as_posix() for p in base.rglob("*"))
    response = dispatch(mk, {"mkep": "0", "kind": "query", "request_id": REQUEST_ID,
                             "op": "describe", "target": {}, "args": {}})
    after = sorted(p.relative_to(base).as_posix() for p in base.rglob("*"))
    assert before == after == []
    assert response["ok"] is True
    assert response["outcome"] == "read"
    assert response["result"] == describe()


def test_describe_takes_no_now(tmp_path):
    """§17: ``describe`` is not now-sensitive, so ``now`` is not expressible."""
    mk = _mk(tmp_path)
    request = {"mkep": "0", "kind": "query", "request_id": REQUEST_ID,
               "op": "describe", "target": {}, "args": {}, "now": NOW}
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "now"


def test_describe_target_and_args_are_empty(tmp_path):
    mk = _mk(tmp_path)
    request = {"mkep": "0", "kind": "query", "request_id": REQUEST_ID,
               "op": "describe", "target": {"goal_id": GOAL_ID}, "args": {}}
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "target.goal_id"


# --------------------------------------------------------------------------
# §6.7 — the error registry
# --------------------------------------------------------------------------

def test_error_registry_is_the_closed_thirty_three_code_set():
    assert sorted(ERROR_REGISTRY) == ERROR_CODES


def test_only_these_error_codes_are_retryable():
    retryable = sorted(code for code, (_cls, retry) in ERROR_REGISTRY.items() if retry)
    assert retryable == ["E_INTERNAL", "E_LEASE_HELD", "E_STORE_BUSY", "E_STORE_IO"]


def test_error_classes_match_the_registry_table():
    expected = {
        "E_MALFORMED_JSON": "input", "E_UNKNOWN_FIELD": "input",
        "E_MISSING_FIELD": "input", "E_TYPE": "input", "E_PATTERN": "input",
        "E_TIME_NAIVE": "input", "E_TIME_FORMAT": "input",
        "E_VERSION_UNSUPPORTED": "negotiation", "E_UNKNOWN_OP": "negotiation",
        "E_CAPABILITY_DRIFT": "negotiation",
        "E_LIMIT_EXCEEDED": "limits", "E_GATE_CAP": "limits",
        "E_PROJECTION_INCONSISTENT": "state", "E_PRECONDITION_STATE": "state",
        "E_PRECONDITION_SEQ": "state",
        "E_IDEMPOTENCY_MISMATCH": "idempotency",
        "E_DIGEST_MISMATCH": "integrity",
        "E_STORE_IO": "io", "E_INTERNAL": "io",
    }
    for code, error_class in expected.items():
        assert ERROR_REGISTRY[code][0] == error_class, code


# --------------------------------------------------------------------------
# §6.4 — the response is one shape
# --------------------------------------------------------------------------

def test_success_response_shape_is_stable(tmp_path):
    mk = _mk(tmp_path)
    response = _declare_goal(mk)
    assert sorted(response) == [
        "mkep", "ok", "op", "outcome", "request_digest", "request_id",
        "response_digest", "result", "state", "warnings",
    ]
    assert response["mkep"] == "0"
    assert response["request_id"] == REQUEST_ID
    assert response["op"] == "goal.declare"
    assert response["ok"] is True
    assert response["outcome"] == "applied"
    assert HEX64.match(response["request_digest"])
    assert HEX64.match(response["response_digest"])
    assert response["warnings"] == []
    assert response["state"]["execution_seq"] == 1
    assert HEX64.match(response["state"]["projection_digest"])
    assert response["state"]["consistent"] is True


def test_error_response_shape_is_stable(tmp_path):
    mk = _mk(tmp_path)
    response = dispatch(mk, _apply("goal.transition", {"goal_id": GOAL_ID},
                                   {"to_status": "satisfied", "reason": "done"}))
    assert sorted(response) == [
        "error", "mkep", "ok", "op", "request_digest", "request_id",
        "response_digest", "state",
    ]
    error = response["error"]
    assert sorted(error) == ["class", "code", "details", "message", "retryable"]
    assert error["code"] == "E_NOT_DECLARED"


def test_response_digest_is_over_the_response_without_itself(tmp_path):
    mk = _mk(tmp_path)
    response = _declare_goal(mk)
    expected = digest({k: v for k, v in response.items() if k != "response_digest"},
                      MAX_PROTOCOL_ARRAY_LENGTH)
    assert response["response_digest"] == expected


def test_request_digest_is_over_the_request(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    response = dispatch(mk, request)
    assert response["request_digest"] == digest(request, MAX_PROTOCOL_ARRAY_LENGTH)


def test_request_digest_is_null_when_the_request_is_not_canonicalisable(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["args"]["title"] = 1.5
    response = dispatch(mk, request)
    assert response["ok"] is False
    assert response["request_digest"] is None


def test_every_response_is_json_serialisable(tmp_path):
    mk = _mk(tmp_path)
    for response in (_declare_goal(mk), dispatch(mk, _query("describe", {})),
                     dispatch(mk, _query("bogus.op", {}))):
        json.loads(json.dumps(response))


def test_error_message_never_leaks_a_path_or_a_username(tmp_path):
    """§6.4 normative / SC-01."""
    mk = _mk(tmp_path)
    _declare_goal(mk)
    response = dispatch(mk, _apply("goal.declare", {"goal_id": GOAL_ID},
                                   dict(GOAL_ARGS), operation_id="c" * 64))
    message = response["error"]["message"]
    assert response["error"]["code"] == "E_ALREADY_DECLARED"
    assert str(tmp_path) not in message
    assert ".memkraft" not in message
    assert "/" not in message


def test_query_outcome_is_read(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    response = dispatch(mk, _query("state.read", {"goal_id": GOAL_ID}))
    assert response["ok"] is True
    assert response["outcome"] == "read"


# --------------------------------------------------------------------------
# §6.5 — preconditions
# --------------------------------------------------------------------------

def test_expect_state_mismatch_is_rejected_with_the_actual_state(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    _declare_gate(mk)
    request = _apply("gate.transition", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
                     {"to_status": "waived", "authority_claim": "human"},
                     operation_id="d" * 64)
    request["precondition"]["expect_state"] = "passed"
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_PRECONDITION_STATE"
    assert error["details"]["actual"] == "pending"
    assert len(_lines(mk)) == 2


def test_expect_state_match_is_accepted(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    _declare_gate(mk)
    request = _apply("gate.transition", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
                     {"to_status": "waived", "authority_claim": "human"},
                     operation_id="d" * 64)
    request["precondition"]["expect_state"] = "pending"
    assert dispatch(mk, request)["ok"] is True


def test_expect_state_is_not_expressible_where_there_is_no_entity_state(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["precondition"]["expect_state"] = "open"
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_UNKNOWN_FIELD"
    assert error["details"]["path"] == "precondition.expect_state"


def test_expect_execution_seq_mismatch_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    request = _apply("goal.transition", {"goal_id": GOAL_ID},
                     {"to_status": "abandoned", "reason": "descoped"},
                     operation_id="e" * 64)
    request["precondition"]["expect_execution_seq"] = 99
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_PRECONDITION_SEQ"
    assert error["details"]["actual"] == 1
    assert len(_lines(mk)) == 1


def test_expect_execution_seq_match_is_accepted(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    request = _apply("goal.transition", {"goal_id": GOAL_ID},
                     {"to_status": "abandoned", "reason": "descoped"},
                     operation_id="e" * 64)
    request["precondition"]["expect_execution_seq"] = 1
    assert dispatch(mk, request)["ok"] is True


def test_operation_id_defaults_to_the_request_digest(tmp_path):
    """§6.5: an omitted ``operation_id`` falls back to ``request_digest``."""
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS))
    request["precondition"] = {}
    response = dispatch(mk, request)
    assert response["ok"] is True
    stored = json.loads(_lines(mk)[0])
    assert stored["operation_id"] == response["request_digest"]


def test_operation_id_must_be_sixty_four_hex(tmp_path):
    mk = _mk(tmp_path)
    request = _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS),
                     operation_id="nope")
    error = _error(dispatch(mk, request))
    assert error["code"] == "E_PATTERN"
    assert error["details"]["path"] == "precondition.operation_id"


# --------------------------------------------------------------------------
# §6.6 / §18 — replay and the inconsistency latch
# --------------------------------------------------------------------------

def test_replay_of_the_same_operation_id_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    first = _declare_goal(mk)
    second = _declare_goal(mk)
    assert first["outcome"] == "applied"
    assert second["outcome"] == "already_applied"
    assert second["result"]["record_id"] == first["result"]["record_id"]
    assert len(_lines(mk)) == 1


def test_an_inconsistent_projection_blocks_every_subsequent_apply(tmp_path):
    """IN-01: a seeded undeclared-gate transition latches the goal closed."""
    mk = _mk(tmp_path)
    _declare_goal(mk)
    path = mk._execution_events_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "id": "f" * 32, "record_type": "gate_transition", "goal_id": GOAL_ID,
            "gate_id": "never-declared", "to_status": "passed", "event_seq": 2,
            "emitted_at": NOW, "execution_schema": 1,
        }) + "\n")
    before = len(_lines(mk))
    error = _error(dispatch(mk, _apply(
        "goal.transition", {"goal_id": GOAL_ID},
        {"to_status": "abandoned", "reason": "descoped"}, operation_id="e" * 64)))
    assert error["code"] == "E_PROJECTION_INCONSISTENT"
    assert error["details"]["rejected_transitions"] == 1
    assert len(_lines(mk)) == before
    read = dispatch(mk, _query("state.read", {"goal_id": GOAL_ID}))
    assert read["ok"] is True
    assert read["state"]["consistent"] is False


# --------------------------------------------------------------------------
# the fifteen adapters actually reach the typed kernel
# --------------------------------------------------------------------------

def test_full_lifecycle_through_the_wire_only(tmp_path):
    mk = _mk(tmp_path)
    assert _declare_goal(mk)["ok"] is True
    assert _declare_gate(mk)["ok"] is True

    receipt = dispatch(mk, _apply(
        "receipt.record", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
        {"verdict": "pass", "content_sha256": "1" * 64, "summary": "green",
         "observed_at": NOW, "provenance_id": "run-1"}, operation_id="1" * 64))
    assert receipt["ok"] is True, receipt
    assert HEX64.match(receipt["result"]["receipt_id"]) or \
        receipt["result"]["receipt_id"]

    passed = dispatch(mk, _apply(
        "gate.transition", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
        {"to_status": "passed"}, operation_id="2" * 64))
    assert passed["ok"] is True, passed
    assert passed["result"]["gate_status"] == "passed"

    satisfied = dispatch(mk, _apply(
        "goal.transition", {"goal_id": GOAL_ID},
        {"to_status": "satisfied", "reason": "gates settled"}, operation_id="3" * 64))
    assert satisfied["ok"] is True, satisfied

    read = dispatch(mk, _query("state.read", {"goal_id": GOAL_ID},
                               {"include": ["gates", "leases", "handoffs"]}))
    assert read["result"]["goal_status"] == "satisfied"
    assert read["result"]["gates"][0]["status"] == "passed"
    assert read["result"]["leases"] == {}
    assert read["result"]["handoffs"] == []

    assessed = dispatch(mk, _query("assess.run", {"goal_id": GOAL_ID}))
    assert assessed["ok"] is True
    assert assessed["result"]["advisory"] is True

    recorded = dispatch(mk, _apply(
        "assess.record", {"goal_id": GOAL_ID},
        {"assessment": {k: v for k, v in assessed["result"].items()
                        if k in ("advisory", "recommendation", "reason_code",
                                 "inputs_digest", "caveats")}},
        operation_id="4" * 64))
    assert recorded["ok"] is True, recorded


def test_state_read_include_is_a_closed_list(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    error = _error(dispatch(mk, _query("state.read", {"goal_id": GOAL_ID},
                                       {"include": ["receipts"]})))
    assert error["code"] == "E_PATTERN"
    assert error["details"]["path"] == "args.include[0]"


def test_lease_lifecycle_and_fencing_through_the_wire(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    _declare_gate(mk)

    acquired = dispatch(mk, _apply(
        "lease.acquire", {"goal_id": GOAL_ID, "scope_key": "tests-green"},
        {"holder": "worker-3", "ttl_seconds": 600}, operation_id="5" * 64))
    assert acquired["ok"] is True, acquired
    fence = acquired["result"]["fence_token"]
    assert fence == 1

    blocked = dispatch(mk, _apply(
        "gate.transition", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
        {"to_status": "waived", "authority_claim": "human"}, operation_id="6" * 64))
    assert _error(blocked)["code"] == "E_FENCE_REQUIRED"

    request = _apply("gate.transition", {"goal_id": GOAL_ID, "gate_id": "tests-green"},
                     {"to_status": "waived", "authority_claim": "human"},
                     operation_id="6" * 64)
    request["precondition"]["fence_token"] = fence
    assert dispatch(mk, request)["ok"] is True

    released = dispatch(mk, _apply(
        "lease.release", {"goal_id": GOAL_ID, "scope_key": "tests-green"},
        {"lease_id": acquired["result"]["lease_id"]}, operation_id="7" * 64))
    assert released["ok"] is True, released


def test_handoff_declare_transition_and_export_through_the_wire(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    declared = dispatch(mk, _apply(
        "handoff.declare", {"goal_id": GOAL_ID},
        {"to_actor": "reviewer", "payload": {"note": "please review"},
         "payload_schema": "memkraft.handoff.context/1"}, operation_id="8" * 64))
    assert declared["ok"] is True, declared
    handoff_id = declared["result"]["handoff_id"]

    exported = dispatch(mk, _query("handoff.export",
                                   {"goal_id": GOAL_ID, "handoff_id": handoff_id}))
    assert exported["ok"] is True, exported
    envelope = exported["result"]["envelope"]
    assert envelope["envelope_schema"] == "memkraft.handoff/1"

    moved = dispatch(mk, _apply(
        "handoff.transition", {"goal_id": GOAL_ID, "handoff_id": handoff_id},
        {"to_state": "accepted"}, operation_id="9" * 64))
    assert moved["ok"] is True, moved


def test_handoff_import_takes_only_an_envelope_object(tmp_path):
    """§10.4: no path, base_dir, profile, or URL is expressible on the wire."""
    entry = execution_dispatch._OPS["handoff.import"]
    assert entry.required == ("envelope",)
    assert entry.optional == ()

    origin = _mk(tmp_path / "a")
    dispatch(origin, _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS)))
    declared = dispatch(origin, _apply(
        "handoff.declare", {"goal_id": GOAL_ID},
        {"to_actor": "reviewer", "payload": {"note": "please review"}},
        operation_id="8" * 64))
    envelope = dispatch(origin, _query(
        "handoff.export",
        {"goal_id": GOAL_ID, "handoff_id": declared["result"]["handoff_id"]},
    ))["result"]["envelope"]

    target = _mk(tmp_path / "b")
    dispatch(target, _apply("goal.declare", {"goal_id": GOAL_ID}, dict(GOAL_ARGS)))
    imported = dispatch(target, _apply(
        "handoff.import", {"goal_id": GOAL_ID}, {"envelope": envelope},
        operation_id="a1" + "0" * 62))
    assert imported["ok"] is True, imported


# --------------------------------------------------------------------------
# §19.2 / NS-02 — runtime neutrality of the new source
# --------------------------------------------------------------------------

def test_dispatcher_source_is_runtime_neutral_and_clock_free():
    source = inspect.getsource(execution_dispatch).lower()
    for word in ("hermes", "openclaw", "kanban", "work_item", "workitem",
                 "profile_name", "session_key", "datetime.now("):
        assert word not in source, word


def test_dispatcher_source_carries_no_scheduling_vocabulary():
    source = inspect.getsource(execution_dispatch).lower()
    for word in ("next_check_at", "retry_after", "poll_interval", "cadence", "cron"):
        assert word not in source, word


def test_describe_output_carries_no_scheduling_vocabulary():
    rendered = json.dumps(describe()).lower()
    for word in ("next_check_at", "retry_after", "poll_interval", "cadence", "cron"):
        assert word not in rendered, word


def test_dispatch_module_parses_under_python_39():
    import ast

    path = Path(execution_dispatch.__file__)
    ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
