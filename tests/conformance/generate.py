"""Deterministic generator for the MKEP/0 conformance corpus (plan §18).

The corpus is *generated*, not hand-typed, for one reason: the plan's minimums
are combinatorial — every forbidden ``(kind, from, to)`` triple, every
canonicalization vector, every idempotency shape — and a hand-typed corpus of
that size is a corpus with a typo in it. Running this module rewrites
``fixtures/0/`` byte-identically from the same source, so a regenerated tree
that differs is a real change, not churn.

Determinism rules the generator obeys, so the fixtures satisfy §18.1:

- Every ``now`` comes from the case's ``now_sequence``; nothing reads a clock.
- Every ``request_id`` and ``operation_id`` is derived from the ``case_id``.
- Seed records are synthesized with fixed ``id`` values rather than produced by
  running the kernel, so ``id`` and ``created_at`` never vary between runs.

Usage::

    python tests/conformance/generate.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "0"

MKEP = "0"
GOAL_ID = "conformance/case-goal"
GATE_ID = "tests-green"
ORIGIN_INSTANCE_ID = "0" * 31 + "1"
HANDOFF_ID = "0" * 31 + "2"
T0 = "2026-08-04T10:00:00Z"
T1 = "2026-08-04T10:00:05Z"
T2 = "2026-08-04T10:00:10Z"

#: §4.6, mirrored here on purpose. The corpus is a *language-neutral* artifact:
#: a second-runtime implementer reads these fixtures, not our Python.
GATE_STATUSES = ("pending", "passed", "failed", "waived")
GOAL_STATUSES = ("open", "satisfied", "abandoned")
HANDOFF_STATUSES = ("offered", "accepted", "completed")

#: Statuses no machine here has. They exist in the corpus because a closed enum
#: is only closed if something outside it is proven to bounce.
ALIEN_STATUSES = ("approved", "cancelled", "blocked", "done")

ALLOWED = {
    ("goal", "open", "satisfied"), ("goal", "open", "abandoned"),
    ("gate", "pending", "passed"), ("gate", "pending", "failed"),
    ("gate", "pending", "waived"), ("gate", "passed", "pending"),
    ("gate", "failed", "pending"), ("gate", "failed", "passed"),
    ("gate", "failed", "waived"),
    ("handoff", "offered", "accepted"), ("handoff", "accepted", "completed"),
}

GOAL_ARGS = {
    "title": "Conformance goal",
    "intent": "Pin protocol behaviour",
    "constraints": ["stdlib only"],
    "success_criteria": ["fixtures green"],
}


# --------------------------------------------------------------------------
# derived identities — stable functions of the case id, never random
# --------------------------------------------------------------------------

def _request_id(case_id: str, index: int = 0) -> str:
    return hashlib.sha256(("request/%s/%d" % (case_id, index)).encode()).hexdigest()[:32]


def _operation_id(case_id: str, index: int = 0) -> str:
    return hashlib.sha256(("operation/%s/%d" % (case_id, index)).encode()).hexdigest()


def _record_id(case_id: str, index: int) -> str:
    return hashlib.sha256(("record/%s/%d" % (case_id, index)).encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# envelope and seed builders
# --------------------------------------------------------------------------

def apply_request(case_id, op, target, args, *, index=0, now=T1, precondition=None):
    request = {
        "mkep": MKEP, "kind": "apply", "request_id": _request_id(case_id, index),
        "op": op, "now": now, "target": target, "args": args,
        "precondition": {"operation_id": _operation_id(case_id, index)},
    }
    if precondition is not None:
        request["precondition"].update(precondition)
    return request


def query_request(case_id, op, target, args=None, *, index=0, now=T1):
    request = {
        "mkep": MKEP, "kind": "query", "request_id": _request_id(case_id, index),
        "op": op, "target": target, "args": {} if args is None else args,
    }
    if op != "describe":
        request["now"] = now
    return request


def _seed_record(case_id, index, record_type, **fields):
    record = {
        "id": _record_id(case_id, index), "schema_version": 1,
        "record_type": record_type, "execution_schema": 1, "goal_id": GOAL_ID,
        "emitted_at": T0, "privacy": "local_private", "authority_claim": "agent",
        "authority_verified": False, "event_seq": index + 1,
        "operation_id": _operation_id("seed/%s" % case_id, index),
    }
    record.update(fields)
    return record


def goal_seed(case_id, status="open"):
    """A declared goal, optionally already moved to ``status``."""
    records = [_seed_record(case_id, 0, "goal_declared", **GOAL_ARGS)]
    if status != "open":
        records.append(_seed_record(case_id, 1, "goal_transition",
                                    to_status=status, reason="seeded"))
    return records


def gate_seed(case_id, status="pending"):
    """A declared goal plus a gate already sitting in ``status``.

    Reaching ``passed`` needs a receipt in the log as well, because the fold is
    replayed rather than re-validated but the *next* apply reads evidence.
    """
    records = goal_seed(case_id)
    records.append(_seed_record(
        case_id, len(records), "gate_declared", gate_id=GATE_ID,
        description="the suite is green", required=True, scope_key=GATE_ID,
        verification={"check_kind": "command", "check_ref": "pytest -q"},
    ))
    if status != "pending":
        verdict = "pass" if status == "passed" else "fail"
        if status != "waived":
            records.append(_seed_record(
                case_id, len(records), "evidence_receipt", gate_id=GATE_ID,
                verdict=verdict, content_sha256="1" * 64, summary="seeded",
                provenance_id="seed", observed_seq=len(records),
            ))
        records.append(_seed_record(
            case_id, len(records), "gate_transition", gate_id=GATE_ID,
            to_status=status, observed_reopened_at_seq=0,
            authority_claim="human" if status == "waived" else "agent",
        ))
    for index, record in enumerate(records):
        record["event_seq"] = index + 1
    return records


def handoff_seed(case_id, status="offered"):
    records = goal_seed(case_id)
    records.append(_seed_record(
        case_id, len(records), "handoff_declared", handoff_id=HANDOFF_ID,
        privacy="public_safe", to_actor="reviewer", payload={"note": "review"},
        payload_digest="2" * 64, payload_schema="memkraft.handoff.context/1",
    ))
    order = list(HANDOFF_STATUSES)
    for reached in order[1:order.index(status) + 1]:
        records.append(_seed_record(
            case_id, len(records), "handoff_transition",
            handoff_id=HANDOFF_ID, to_status=reached,
        ))
    for index, record in enumerate(records):
        record["event_seq"] = index + 1
    return records


# --------------------------------------------------------------------------
# case writer
# --------------------------------------------------------------------------

def write_case(case_id, *, title, level, tags, requests, expect, readme,
               seed=None, now_sequence=None, executable=True, gap=None):
    """Write one fixture directory (§18.1).

    ``executable`` and ``gap`` are a documented deviation from §18.1's schema.
    Three named cases — ``XR-01``, ``CL-01``, ``MC-01`` — depend on transports
    that Slice 9 does not ship. Their directories exist, carry their real
    requests, and are marked non-executable with the exact reason, so the corpus
    reports a known gap instead of a silent pass.
    """
    directory = FIXTURES / case_id
    directory.mkdir(parents=True, exist_ok=True)
    for stale in ("request.json", "requests.jsonl", "seed"):
        target = directory / stale
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    case = {
        "case_id": case_id,
        "title": title,
        "mkep": MKEP,
        "level": level,
        "tags": sorted(tags),
        "origin_instance_id": ORIGIN_INSTANCE_ID,
        "now_sequence": now_sequence or [
            request.get("now", T1) for request in requests
        ],
        "transports": ["python"],
        "executable": executable,
    }
    if gap is not None:
        case["gap"] = gap
    _write_json(directory / "case.json", case)

    if len(requests) == 1:
        _write_json(directory / "request.json", requests[0])
    else:
        (directory / "requests.jsonl").write_text(
            "".join(json.dumps(request, sort_keys=True) + "\n"
                    for request in requests),
            encoding="utf-8",
        )

    _write_json(directory / "expect.json", expect)

    if seed:
        (directory / "seed").mkdir(exist_ok=True)
        (directory / "seed" / "events.jsonl").write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in seed),
            encoding="utf-8",
        )

    (directory / "README.md").write_text(readme.strip() + "\n", encoding="utf-8")


def _write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rejected(code, error_class, retryable=False):
    return {"ok": False, "error_code": code, "error_class": error_class,
            "retryable": retryable}


def accepted(outcome="applied"):
    return {"ok": True, "outcome": outcome}


def final(*, log_line_count, lines_delta, consistent=True,
          rejected_transitions=0, skipped=0):
    return {"log_line_count": log_line_count, "lines_delta": lines_delta,
            "consistent": consistent, "rejected_transitions": rejected_transitions,
            "skipped": skipped, "goal_id": GOAL_ID}


# --------------------------------------------------------------------------
# family: forbidden transitions (EV-05, G2 — ≥ 40 pairs)
# --------------------------------------------------------------------------

def _forbidden_pairs():
    machines = (
        ("gate", GATE_STATUSES), ("goal", GOAL_STATUSES),
        ("handoff", HANDOFF_STATUSES),
    )
    for kind, statuses in machines:
        for from_status in statuses:
            for to_status in tuple(statuses) + ALIEN_STATUSES:
                if (kind, from_status, to_status) in ALLOWED:
                    continue
                yield kind, from_status, to_status


def generate_forbidden_transitions():
    """One directory per forbidden ``(kind, from, to)`` triple.

    ``lines_delta == 0`` on every one is the point: a rejected transition is
    rejected *before* the append, so the file is byte-unchanged.
    """
    count = 0
    for index, (kind, from_status, to_status) in enumerate(sorted(_forbidden_pairs())):
        case_id = "FT-%03d" % (index + 1)
        if kind == "gate":
            seed = gate_seed(case_id, from_status)
            request = apply_request(
                case_id, "gate.transition",
                {"goal_id": GOAL_ID, "gate_id": GATE_ID}, {"to_status": to_status},
            )
        elif kind == "goal":
            seed = goal_seed(case_id, from_status)
            request = apply_request(
                case_id, "goal.transition", {"goal_id": GOAL_ID},
                {"to_status": to_status, "reason": "conformance"},
            )
        else:
            seed = handoff_seed(case_id, from_status)
            request = apply_request(
                case_id, "handoff.transition",
                {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID},
                {"to_state": to_status},
            )
        # A handoff asked to re-enter the state it already holds is not a
        # forbidden edge but a second party claiming the same work: §10.5 makes
        # that ``E_CONFLICT`` precisely so it is legible as a conflict.
        code = ("E_CONFLICT" if kind == "handoff" and from_status == to_status
                else "E_INVALID_TRANSITION")
        write_case(
            case_id,
            title="%s %s -> %s is rejected" % (kind, from_status, to_status),
            level="L2", tags=["transition", kind, "fail-closed"],
            seed=seed, requests=[request],
            expect={"responses": [rejected(code, "state")],
                    "final": final(log_line_count=len(seed), lines_delta=0)},
            readme="Pins that the %s machine has no %s -> %s edge, and that "
                   "rejecting it leaves the log byte-unchanged (G2)."
                   % (kind, from_status, to_status),
        )
        count += 1
    return count


# --------------------------------------------------------------------------
# family: canonicalization and envelope closedness (CN — ≥ 20 vectors)
# --------------------------------------------------------------------------

_CANONICAL_VECTORS = [
    ("float_in_a_string_field", {"title": 1.5}, "E_TYPE", "input"),
    ("float_in_a_list_field", {"constraints": [1.0]}, "E_TYPE", "input"),
    ("bool_where_a_string_belongs", {"intent": True}, "E_TYPE", "input"),
    ("null_where_a_string_belongs", {"intent": None}, "E_MISSING_FIELD", "input"),
    ("object_where_a_string_belongs", {"title": {"a": "b"}}, "E_TYPE", "input"),
    ("list_where_a_string_belongs", {"title": ["a"]}, "E_TYPE", "input"),
    ("string_where_a_list_belongs", {"constraints": "a"}, "E_TYPE", "input"),
    ("nested_list_in_a_flat_list", {"constraints": [["a"]]}, "E_TYPE", "input"),
    ("string_longer_than_the_limit", {"title": "x" * 513},
     "E_LIMIT_EXCEEDED", "limits"),
    ("list_longer_than_the_limit", {"constraints": ["c"] * 33},
     "E_LIMIT_EXCEEDED", "limits"),
    ("empty_string_rejected", {"title": ""}, "E_PATTERN", "input"),
    ("lone_surrogate_rejected", {"title": "a\ud800b"}, "E_TYPE", "input"),
    ("unknown_arg_key", {"deadline": T2}, "E_UNKNOWN_FIELD", "input"),
    ("unknown_privacy_enum", {"privacy": "secret"}, "E_PATTERN", "input"),
    ("integer_where_a_string_belongs", {"summary": 3}, "E_TYPE", "input"),
]


def generate_canonicalization():
    count = 0
    for index, (name, override, code, error_class) in enumerate(_CANONICAL_VECTORS):
        case_id = "CN-%03d" % (index + 1)
        args = dict(GOAL_ARGS)
        args.update(override)
        args = {key: value for key, value in args.items() if value is not None
                or key not in GOAL_ARGS}
        if override.get("intent", "sentinel") is None:
            args.pop("intent", None)
        if "summary" in override:
            args = dict(GOAL_ARGS)
            args["title"] = "ok"
            args.pop("title")
            args = dict(GOAL_ARGS)
            args["intent"] = str(override["summary"]) if False else GOAL_ARGS["intent"]
            args["title"] = override["summary"]
        write_case(
            case_id, title=name.replace("_", " "), level="L1",
            tags=["canonicalization", "validation"], seed=goal_seed(case_id),
            requests=[apply_request(case_id, "goal.declare",
                                    {"goal_id": "conformance/second-goal"}, args)],
            expect={"responses": [rejected(code, error_class)],
                    "final": final(log_line_count=1, lines_delta=0)},
            readme="MKCJSON/1 and the typed validators reject %s before any "
                   "append happens." % name.replace("_", " "),
        )
        count += 1

    envelope_vectors = [
        ("unknown_top_level_field", {"passthrough": {}}, "E_UNKNOWN_FIELD", "input"),
        ("unknown_meta_field", {"meta": {}}, "E_UNKNOWN_FIELD", "input"),
        ("unknown_extra_field", {"extra": {}}, "E_UNKNOWN_FIELD", "input"),
        ("unknown_raw_field", {"raw": "x"}, "E_UNKNOWN_FIELD", "input"),
        ("wrong_protocol_version", {"mkep": "1"}, "E_VERSION_UNSUPPORTED",
         "negotiation"),
        ("empty_protocol_version", {"mkep": ""}, "E_VERSION_UNSUPPORTED",
         "negotiation"),
        ("unknown_operation", {"op": "goal.list"}, "E_UNKNOWN_OP", "negotiation"),
        ("graph_operation_is_not_in_the_registry", {"op": "graph.expand"},
         "E_UNKNOWN_OP", "negotiation"),
        ("kind_disagrees_with_the_registry", {"kind": "query"}, "E_PATTERN", "input"),
        ("malformed_request_id", {"request_id": "not-a-ulid"}, "E_PATTERN", "input"),
        ("capabilities_digest_drift", {"capabilities_digest": "0" * 64},
         "E_CAPABILITY_DRIFT", "negotiation"),
        ("unknown_binding_key", {"binding": {"tenant": "x"}},
         "E_UNKNOWN_FIELD", "input"),
    ]
    for index, (name, override, code, error_class) in enumerate(envelope_vectors):
        case_id = "CN-%03d" % (len(_CANONICAL_VECTORS) + index + 1)
        request = apply_request(case_id, "goal.declare",
                                {"goal_id": "conformance/second-goal"},
                                dict(GOAL_ARGS))
        request.update(override)
        write_case(
            case_id, title=name.replace("_", " "), level="L1",
            tags=["envelope", "closedness"], seed=goal_seed(case_id),
            requests=[request],
            expect={"responses": [rejected(code, error_class)],
                    "final": final(log_line_count=1, lines_delta=0)},
            readme="The envelope is closed: %s is refused rather than ignored."
                   % name.replace("_", " "),
        )
        count += 1
    return count


# --------------------------------------------------------------------------
# family: idempotency (ID — ≥ 12)
# --------------------------------------------------------------------------

_REPLAYABLE = [
    ("goal.declare", {"goal_id": "conformance/replay-goal"}, dict(GOAL_ARGS),
     "goal", None),
    ("gate.declare", {"goal_id": GOAL_ID, "gate_id": "second-gate"},
     {"description": "another gate",
      "verification": {"check_kind": "command", "check_ref": "pytest -q"}},
     "gate", None),
    ("gate.transition", {"goal_id": GOAL_ID, "gate_id": GATE_ID},
     {"to_status": "waived", "authority_claim": "human"}, "gate", "pending"),
    ("goal.transition", {"goal_id": GOAL_ID},
     {"to_status": "abandoned", "reason": "descoped"}, "goal", "open"),
    ("receipt.record", {"goal_id": GOAL_ID, "gate_id": GATE_ID},
     {"verdict": "pass", "content_sha256": "1" * 64, "summary": "green",
      "observed_at": T0, "provenance_id": "run-1"}, "gate", "pending"),
    ("lease.acquire", {"goal_id": GOAL_ID, "scope_key": GATE_ID},
     {"holder": "worker-3", "ttl_seconds": 600}, "goal", None),
    ("handoff.declare", {"goal_id": GOAL_ID},
     {"to_actor": "reviewer", "payload": {"note": "review"}}, "goal", None),
    ("handoff.transition", {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID},
     {"to_state": "accepted"}, "handoff", "offered"),
    ("assess.record", {"goal_id": GOAL_ID},
     {"assessment": {"advisory": True, "recommendation": "wait",
                     "reason_code": "blocking_gate_pending",
                     "inputs_digest": "3" * 64, "caveats": []}}, "goal", None),
]


def _seed_for(case_id, family, status):
    if family == "gate":
        return gate_seed(case_id, status or "pending")
    if family == "handoff":
        return handoff_seed(case_id, status or "offered")
    return goal_seed(case_id, status or "open")


def generate_idempotency():
    count = 0
    for index, (op, target, args, family, status) in enumerate(_REPLAYABLE):
        case_id = "ID-1%02d" % (index + 1)
        seed = _seed_for(case_id, family, status)
        first = apply_request(case_id, op, target, args, index=0)
        second = apply_request(case_id, op, target, args, index=1)
        # The same operation_id is the whole point: request_id differs, which is
        # what proves correlation is not the idempotency key (§6.1).
        second["precondition"]["operation_id"] = first["precondition"]["operation_id"]
        write_case(
            case_id, title="%s replays without appending" % op, level="L2",
            tags=["idempotency", op.split(".", 1)[0]], seed=seed,
            requests=[first, second],
            expect={"responses": [accepted("applied"), accepted("already_applied")],
                    "final": final(log_line_count=len(seed) + 1, lines_delta=1)},
            readme="§6.6 rule 3: a replayed operation_id with an equal "
                   "fingerprint appends nothing and returns the original record.",
        )
        count += 1

    mismatches = [
        ("lease.acquire", {"goal_id": GOAL_ID, "scope_key": GATE_ID},
         {"holder": "worker-3", "ttl_seconds": 600},
         {"holder": "worker-3", "ttl_seconds": 900}, "goal", None),
        ("goal.declare", {"goal_id": "conformance/replay-goal"}, dict(GOAL_ARGS),
         dict(GOAL_ARGS, title="A different title"), "goal", None),
        ("receipt.record", {"goal_id": GOAL_ID, "gate_id": GATE_ID},
         {"verdict": "pass", "content_sha256": "1" * 64, "summary": "green",
          "observed_at": T0},
         {"verdict": "pass", "content_sha256": "1" * 64, "summary": "amber",
          "observed_at": T0}, "gate", "pending"),
    ]
    for index, (op, target, first_args, second_args, family, status) \
            in enumerate(mismatches):
        case_id = "ID-2%02d" % (index + 1)
        seed = _seed_for(case_id, family, status)
        first = apply_request(case_id, op, target, first_args, index=0)
        second = apply_request(case_id, op, target, second_args, index=1)
        second["precondition"]["operation_id"] = first["precondition"]["operation_id"]
        write_case(
            case_id, title="%s reuse with different arguments is refused" % op,
            level="L2", tags=["idempotency", "conflict"], seed=seed,
            requests=[first, second],
            expect={"responses": [accepted("applied"),
                                  rejected("E_IDEMPOTENCY_MISMATCH", "idempotency")],
                    "final": final(log_line_count=len(seed) + 1, lines_delta=1)},
            readme="§6.6 rule 4: the same operation_id with a different "
                   "fingerprint is an error, not a silent overwrite, and the "
                   "line count is unchanged by the rejection.",
        )
        count += 1
    return count


# --------------------------------------------------------------------------
# family: leases and fencing (LS — ≥ 10)
# --------------------------------------------------------------------------

def generate_leases():
    cases = []

    seed = gate_seed("LS-001")
    cases.append((
        "LS-001", "a first acquisition mints fence token 1", "L2",
        seed, [apply_request("LS-001", "lease.acquire",
                             {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                             {"holder": "worker-3", "ttl_seconds": 600})],
        [accepted()], final(log_line_count=len(seed) + 1, lines_delta=1),
        "§7.1: fence_token is an output. A first acquisition cannot be asked "
        "for a token it has no way to know.",
    ))

    seed = gate_seed("LS-002")
    acquire = apply_request("LS-002", "lease.acquire",
                            {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                            {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    unfenced = apply_request("LS-002", "gate.transition",
                             {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                             {"to_status": "waived", "authority_claim": "human"},
                             index=1)
    cases.append((
        "LS-002", "a leased scope refuses an unfenced write", "L2", seed,
        [acquire, unfenced], [accepted(), rejected("E_FENCE_REQUIRED", "lease")],
        final(log_line_count=len(seed) + 1, lines_delta=1),
        "FN-01: once a scope is leased, every protected mutation presents the "
        "token or is refused.",
    ))

    seed = gate_seed("LS-003")
    fenced = apply_request("LS-003", "gate.transition",
                           {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                           {"to_status": "waived", "authority_claim": "human"},
                           index=0, precondition={"fence_token": 1})
    cases.append((
        "LS-003", "a fence token on an unleased scope is not expressible", "L2",
        seed, [fenced], [rejected("E_UNKNOWN_FIELD", "input")],
        final(log_line_count=len(seed), lines_delta=0),
        "FN-02 second half: presenting a token where no lease exists is a "
        "closed-field error, not a silently ignored hint.",
    ))

    seed = gate_seed("LS-004")
    cases.append((
        "LS-004", "expected_fence on a virgin scope is refused", "L2", seed,
        [apply_request("LS-004", "lease.acquire",
                       {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                       {"holder": "worker-3", "ttl_seconds": 600,
                        "expected_fence": 7})],
        [rejected("E_FENCE_STALE", "lease")],
        final(log_line_count=len(seed), lines_delta=0),
        "§7.2: expected_fence is checked against the scope's actual fence, so "
        "a guess is refused rather than accepted.",
    ))

    seed = gate_seed("LS-005")
    first = apply_request("LS-005", "lease.acquire",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    other = apply_request("LS-005", "lease.acquire",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"holder": "worker-9", "ttl_seconds": 600}, index=1)
    cases.append((
        "LS-005", "a held scope refuses a second holder", "L2", seed,
        [first, other], [accepted(), rejected("E_LEASE_HELD", "lease", True)],
        final(log_line_count=len(seed) + 1, lines_delta=1),
        "G4: at most one valid lease per scope_key. E_LEASE_HELD is the only "
        "retryable lease error, and it reports a holder digest, never a holder.",
    ))

    seed = gate_seed("LS-006")
    grant = apply_request("LS-006", "lease.acquire",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    parallel = apply_request("LS-006", "lease.acquire",
                             {"goal_id": GOAL_ID, "scope_key": "other-scope"},
                             {"holder": "worker-9", "ttl_seconds": 600}, index=1)
    cases.append((
        "LS-006", "distinct scopes lease in parallel", "L2", seed,
        [grant, parallel], [accepted(), accepted()],
        final(log_line_count=len(seed) + 2, lines_delta=2),
        "G4's other half: exclusivity is per scope_key, so unrelated work is "
        "not serialized by an unrelated lease.",
    ))

    seed = gate_seed("LS-007")
    cases.append((
        "LS-007", "releasing a lease that was never granted is refused", "L2",
        seed, [apply_request("LS-007", "lease.release",
                             {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                             {"lease_id": "0" * 32})],
        [rejected("E_CONFLICT", "state")],
        final(log_line_count=len(seed), lines_delta=0),
        "A release names the lease it ends, so a release with no grant behind "
        "it is a conflict rather than a no-op.",
    ))

    seed = gate_seed("LS-008")
    grant = apply_request("LS-008", "lease.acquire",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    stale = apply_request("LS-008", "lease.release",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"lease_id": "0" * 32}, index=1)
    cases.append((
        "LS-008", "a superseded lease_id cannot release the current lease", "L2",
        seed, [grant, stale], [accepted(), rejected("E_FENCE_STALE", "lease")],
        final(log_line_count=len(seed) + 1, lines_delta=1),
        "The hazard fencing exists to close: a stale holder must not cancel a "
        "lease it no longer owns.",
    ))

    seed = gate_seed("LS-009")
    cases.append((
        "LS-009", "a ttl above the advertised ceiling is refused", "L2", seed,
        [apply_request("LS-009", "lease.acquire",
                       {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                       {"holder": "worker-3", "ttl_seconds": 86401})],
        [rejected("E_LIMIT_EXCEEDED", "limits")],
        final(log_line_count=len(seed), lines_delta=0),
        "§17 rule 5: describe.limits.max_ttl_seconds is enforced, not merely "
        "advertised.",
    ))

    seed = gate_seed("LS-010")
    cases.append((
        "LS-010", "a zero ttl is refused", "L2", seed,
        [apply_request("LS-010", "lease.acquire",
                       {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                       {"holder": "worker-3", "ttl_seconds": 0})],
        [rejected("E_LIMIT_EXCEEDED", "limits")],
        final(log_line_count=len(seed), lines_delta=0),
        "A lease that expires the instant it is granted is a lease nobody "
        "holds; it is refused at the boundary rather than projected away.",
    ))

    seed = gate_seed("LS-011")
    cases.append((
        "LS-011", "fence_token is not expressible in args", "L1", seed,
        [apply_request("LS-011", "lease.acquire",
                       {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                       {"holder": "worker-3", "ttl_seconds": 600,
                        "fence_token": 1})],
        [rejected("E_UNKNOWN_FIELD", "input")],
        final(log_line_count=len(seed), lines_delta=0),
        "§6.2 note: fencing lives only in precondition, which is what makes it "
        "unforgettable rather than per-op optional.",
    ))

    for case_id, title, level, seed, requests, responses, expect_final, readme \
            in cases:
        write_case(case_id, title=title, level=level, tags=["lease", "fence"],
                   seed=seed, requests=requests,
                   expect={"responses": responses, "final": expect_final},
                   readme=readme)
    return len(cases)


# --------------------------------------------------------------------------
# family: handoff (HO — ≥ 8)
# --------------------------------------------------------------------------

def generate_handoffs():
    cases = []

    seed = goal_seed("HO-001")
    cases.append((
        "HO-001", "a handoff is declared with a caller-supplied payload", "L2",
        seed, [apply_request("HO-001", "handoff.declare", {"goal_id": GOAL_ID},
                             {"to_actor": "reviewer",
                              "payload": {"note": "please review"}})],
        [accepted()], final(log_line_count=len(seed) + 1, lines_delta=1),
        "D-25: core stores the payload verbatim. The caller already made the "
        "sharing decision by supplying it.",
    ))

    seed = handoff_seed("HO-002", "offered")
    cases.append((
        "HO-002", "an offered handoff is accepted once", "L2", seed,
        [apply_request("HO-002", "handoff.transition",
                       {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID},
                       {"to_state": "accepted"})],
        [accepted()], final(log_line_count=len(seed) + 1, lines_delta=1),
        "§4.6: offered -> accepted is the only edge out of offered.",
    ))

    seed = handoff_seed("HO-003", "accepted")
    cases.append((
        "HO-003", "a second acceptance is a conflict, not a replay", "L2", seed,
        [apply_request("HO-003", "handoff.transition",
                       {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID},
                       {"to_state": "accepted"})],
        [rejected("E_CONFLICT", "state")],
        final(log_line_count=len(seed), lines_delta=0),
        "§10.5: two parties believing they own the work is the one thing this "
        "machine exists to surface.",
    ))

    seed = goal_seed("HO-004")
    cases.append((
        "HO-004", "a transition against an undeclared handoff is refused", "L2",
        seed, [apply_request("HO-004", "handoff.transition",
                             {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID},
                             {"to_state": "accepted"})],
        [rejected("E_NOT_DECLARED", "state")],
        final(log_line_count=len(seed), lines_delta=0),
        "A handoff that was never declared has no state to leave.",
    ))

    seed = goal_seed("HO-005")
    cases.append((
        "HO-005", "an expired handoff cannot be declared into the past", "L2",
        seed, [apply_request("HO-005", "handoff.declare", {"goal_id": GOAL_ID},
                             {"to_actor": "reviewer",
                              "payload": {"note": "review"},
                              "expires_at": T0}, now=T2)],
        [accepted()], final(log_line_count=len(seed) + 1, lines_delta=1),
        "Expiry is projected, never stored as a state: declaring with a past "
        "deadline succeeds and the handoff is simply already inert (§4.6).",
    ))

    seed = goal_seed("HO-006")
    cases.append((
        "HO-006", "a payload_schema outside the grammar is refused", "L1", seed,
        [apply_request("HO-006", "handoff.declare", {"goal_id": GOAL_ID},
                       {"to_actor": "reviewer", "payload": {"note": "review"},
                        "payload_schema": "Not A Schema"})],
        [rejected("E_PATTERN", "input")],
        final(log_line_count=len(seed), lines_delta=0),
        "The envelope schema name is a closed grammar, so a recipient can rely "
        "on what it means.",
    ))

    seed = goal_seed("HO-007")
    cases.append((
        "HO-007", "exporting an undeclared handoff is refused", "L1", seed,
        [query_request("HO-007", "handoff.export",
                       {"goal_id": GOAL_ID, "handoff_id": HANDOFF_ID})],
        [rejected("E_NOT_DECLARED", "state")],
        final(log_line_count=len(seed), lines_delta=0),
        "Export reads the declaration; there is nothing to export without one, "
        "and the query appends nothing either way.",
    ))

    seed = goal_seed("HO-008")
    cases.append((
        "HO-008", "importing a tampered envelope is refused", "L2", seed,
        [apply_request("HO-008", "handoff.import", {"goal_id": GOAL_ID},
                       {"envelope": {
                           "envelope_schema": "memkraft.handoff/1",
                           "origin_instance_id": ORIGIN_INSTANCE_ID,
                           "goal_id": GOAL_ID, "handoff_id": HANDOFF_ID,
                           "payload_schema": "memkraft.handoff.context/1",
                           "payload": {"note": "tampered"},
                           "payload_digest": "4" * 64, "expires_at": None,
                           "exported_at": T0, "envelope_digest": "5" * 64}})],
        [rejected("E_DIGEST_MISMATCH", "integrity")],
        final(log_line_count=len(seed), lines_delta=0),
        "§10.4: both digests are verified before anything is written. This "
        "proves the envelope agrees with itself, not that the sender is honest.",
    ))

    seed = goal_seed("HO-009")
    cases.append((
        "HO-009", "handoff.import takes an envelope object and nothing else",
        "L1", seed,
        [apply_request("HO-009", "handoff.import", {"goal_id": GOAL_ID},
                       {"envelope": {}, "base_dir": "/tmp/other"})],
        [rejected("E_UNKNOWN_FIELD", "input")],
        final(log_line_count=len(seed), lines_delta=0),
        "IS-01's structural half: a cross-base read is inexpressible on the "
        "wire, not merely discouraged.",
    ))

    seed = goal_seed("HO-010")
    cases.append((
        "HO-010", "an envelope missing its schema is refused", "L1", seed,
        [apply_request("HO-010", "handoff.import", {"goal_id": GOAL_ID},
                       {"envelope": {"payload": {"note": "x"}}})],
        [rejected("E_MISSING_FIELD", "input")],
        final(log_line_count=len(seed), lines_delta=0),
        "The import envelope is closed too: a missing required key fails "
        "before any state is touched.",
    ))

    for case_id, title, level, seed, requests, responses, expect_final, readme \
            in cases:
        write_case(case_id, title=title, level=level, tags=["handoff"],
                   seed=seed, requests=requests,
                   expect={"responses": responses, "final": expect_final},
                   readme=readme)
    return len(cases)


# --------------------------------------------------------------------------
# family: projection inconsistency and skipped lines (IC — ≥ 6)
# --------------------------------------------------------------------------

def generate_inconsistency():
    cases = []

    for index, (name, extra, readme) in enumerate([
        ("undeclared_gate_transition",
         lambda cid, seed: seed + [_seed_record(
             cid, len(seed), "gate_transition", gate_id="never-declared",
             to_status="passed")],
         "IN-01: a transition against a gate nobody declared is rejected by the "
         "fold, sets consistent:false, and is counted."),
        ("undeclared_handoff_transition",
         lambda cid, seed: seed + [_seed_record(
             cid, len(seed), "handoff_transition", handoff_id="9" * 32,
             to_status="accepted")],
         "The same latch for handoffs: an unmatched identity is a rejected "
         "transition, not a silently created entity."),
        ("forbidden_transition_already_in_the_log",
         lambda cid, seed: seed + [_seed_record(
             cid, len(seed), "gate_transition", gate_id=GATE_ID,
             to_status="approved")],
         "History is replayed, not re-validated, so a bad line already on disk "
         "shows up as a rejected transition rather than crashing the fold."),
        ("two_rejected_transitions_are_both_counted",
         lambda cid, seed: seed + [
             _seed_record(cid, len(seed), "gate_transition",
                          gate_id="never-declared", to_status="passed"),
             _seed_record(cid, len(seed) + 1, "gate_transition",
                          gate_id="also-missing", to_status="failed")],
         "The counter is a count, not a flag: repair needs to know how much is "
         "wrong."),
    ]):
        case_id = "IC-%03d" % (index + 1)
        seed = extra(case_id, gate_seed(case_id))
        for position, record in enumerate(seed):
            record["event_seq"] = position + 1
        count = 2 if name == "two_rejected_transitions_are_both_counted" else 1
        cases.append((
            case_id, name.replace("_", " "), seed,
            [apply_request(case_id, "goal.transition", {"goal_id": GOAL_ID},
                           {"to_status": "abandoned", "reason": "descoped"})],
            [rejected("E_PROJECTION_INCONSISTENT", "state")],
            final(log_line_count=len(seed), lines_delta=0, consistent=False,
                  rejected_transitions=count),
            readme,
        ))

    # A corrupt line is an IO-layer wound, not a semantic one: it is counted as
    # skipped and the projection stays consistent (§18.2 case 30, second half).
    case_id = "IC-005"
    seed = gate_seed(case_id)
    cases.append((
        case_id, "a corrupt line is skipped and the projection stays consistent",
        seed + ["{not json"],
        [apply_request(case_id, "goal.transition", {"goal_id": GOAL_ID},
                       {"to_status": "abandoned", "reason": "descoped"})],
        [accepted()],
        final(log_line_count=len(seed) + 2, lines_delta=1, consistent=True,
              skipped=1),
        "A line the store cannot parse is damage at the IO layer. It is counted "
        "as skipped and does not make the semantic fold inconsistent.",
    ))

    case_id = "IC-006"
    seed = gate_seed(case_id)
    cases.append((
        case_id, "a read of an inconsistent goal still succeeds",
        seed + [_seed_record(case_id, len(seed), "gate_transition",
                             gate_id="never-declared", to_status="passed",
                             event_seq=len(seed) + 1)],
        [query_request(case_id, "state.read", {"goal_id": GOAL_ID})],
        [accepted("read")],
        final(log_line_count=len(seed) + 1, lines_delta=0, consistent=False,
              rejected_transitions=1),
        "Repair needs to look before it acts: a query is never blocked by the "
        "latch that blocks every apply.",
    ))

    for case_id, title, seed, requests, responses, expect_final, readme in cases:
        write_case(case_id, title=title, level="L2",
                   tags=["projection", "inconsistency"], seed=seed,
                   requests=requests,
                   expect={"responses": responses, "final": expect_final},
                   readme=readme)
    return len(cases)


# --------------------------------------------------------------------------
# family: redaction and isolation (RD — ≥ 5)
# --------------------------------------------------------------------------

def generate_redaction():
    """Export fails **closed** when a §10.1 pattern reaches an envelope.

    Every case here plants a pattern in a payload the caller supplied and then
    exports. The expectation is a refusal, not a ``[redacted]`` substitution: a
    silently scrubbed envelope teaches the caller nothing, and the next payload
    would carry the same leak.
    """
    planted = [
        ("absolute_posix_path", "/home/someone/.memkraft/notes.md"),
        ("home_reference", "$HOME/projects/memkraft"),
        ("user_reference", "$USER ran the suite"),
        ("windows_path", "C:\\Users\\someone\\memkraft"),
        ("file_url", "file:///var/lib/memkraft/events.jsonl"),
        ("tilde_path", "~/.memkraft/execution/events.jsonl"),
    ]
    for index, (name, value) in enumerate(planted):
        case_id = "RD-%03d" % (index + 1)
        seed = goal_seed(case_id)
        declare = apply_request(case_id, "handoff.declare", {"goal_id": GOAL_ID},
                                {"to_actor": "reviewer",
                                 "payload": {"note": value}}, index=0)
        write_case(
            case_id, title="export fails closed on a planted %s"
                           % name.replace("_", " "),
            level="L2", tags=["redaction", "isolation", "privacy"], seed=seed,
            requests=[declare],
            expect={"responses": [accepted()],
                    "final": final(log_line_count=len(seed) + 1, lines_delta=1),
                    "export_fails_closed": {"goal_id": GOAL_ID, "now": T2}},
            readme="§10.1: the payload is stored verbatim because the caller "
                   "chose it, but exporting it off this host is refused while "
                   "it still carries a %s." % name.replace("_", " "),
        )
    return len(planted)


# --------------------------------------------------------------------------
# the 32 named cases (§18.2)
# --------------------------------------------------------------------------

def _named(case_id, title, tags, level, requests, expect, readme,
           seed=None, executable=True, gap=None, now_sequence=None):
    write_case(case_id, title=title, level=level, tags=tags, seed=seed,
               requests=requests, expect=expect, readme=readme,
               executable=executable, gap=gap, now_sequence=now_sequence)


def generate_named():
    count = 0

    def emit(*args, **kwargs):
        _named(*args, **kwargs)

    # -- canonicalization and digest ---------------------------------------
    emit("CJ-01", "canonical key order is ascii only", ["canonicalization"], "L1",
         [apply_request("CJ-01", "handoff.declare", {"goal_id": GOAL_ID},
                        {"to_actor": "reviewer", "payload": {"ключ": "value"}})],
         {"responses": [rejected("E_PATTERN", "input")],
          "final": final(log_line_count=1, lines_delta=0)},
         "MKCJSON/1 rule 3 confines keys to ASCII, which is the only reason "
         "sort_keys is safe across Python, Go, Rust and JavaScript.",
         seed=goal_seed("CJ-01"))
    count += 1

    emit("CJ-02", "floats are rejected and integers are bounded",
         ["canonicalization"], "L1",
         [apply_request("CJ-02", "handoff.declare", {"goal_id": GOAL_ID},
                        {"to_actor": "reviewer", "payload": {"size": 1.0}})],
         {"responses": [rejected("E_TYPE", "input")],
          "final": final(log_line_count=1, lines_delta=0)},
         "1.0, 1e3 and -0 have no canonical form that agrees across languages, "
         "so the subset excludes floats entirely.",
         seed=goal_seed("CJ-02"))
    count += 1

    emit("CJ-03", "string escaping matches the golden bytes",
         ["canonicalization", "golden"], "L1",
         [apply_request("CJ-03", "handoff.declare", {"goal_id": GOAL_ID},
                        {"to_actor": "reviewer",
                         "payload": {"text": "a\"b\\c\td\u00e9\u4e2d\U0001f600"}})],
         {"responses": [accepted()],
          "final": final(log_line_count=2, lines_delta=1)},
         "Control characters, quote, backslash, CJK and an emoji survive a "
         "round trip. The second-language half of this case is a known gap.",
         seed=goal_seed("CJ-03"))
    count += 1

    emit("CJ-04", "a lone surrogate is rejected", ["canonicalization"], "L1",
         [apply_request("CJ-04", "handoff.declare", {"goal_id": GOAL_ID},
                        {"to_actor": "reviewer", "payload": {"text": "a\ud800b"}})],
         {"responses": [rejected("E_TYPE", "input")],
          "final": final(log_line_count=1, lines_delta=0)},
         "An unpaired surrogate has no UTF-8 encoding, so it cannot reach a "
         "digest that a second runtime is expected to reproduce.",
         seed=goal_seed("CJ-04"))
    count += 1

    emit("CJ-05", "nfd input is normalised to nfc", ["canonicalization"], "L1",
         [apply_request("CJ-05", "handoff.declare", {"goal_id": GOAL_ID},
                        {"to_actor": "reviewer", "payload": {"text": "e\u0301"}})],
         {"responses": [accepted()],
          "final": final(log_line_count=2, lines_delta=1),
          "payload_equals": {"text": "\u00e9"}},
         "NFD and NFC spellings of the same text must not produce two digests.",
         seed=goal_seed("CJ-05"))
    count += 1

    emit("CJ-06", "the digest is over the canonical form not the stored line",
         ["canonicalization", "digest"], "L1",
         [apply_request("CJ-06", "goal.declare",
                        {"goal_id": "conformance/digest-goal"}, dict(GOAL_ARGS))],
         {"responses": [accepted()],
          "final": final(log_line_count=1, lines_delta=1),
          "digest_is_not_over_the_raw_line": True},
         "store_core.append writes without sort_keys, so its bytes are not "
         "canonical bytes. Digesting the file line would be a different value.",
         seed=None)
    count += 1

    # -- time ---------------------------------------------------------------
    emit("TM-01", "a naive timestamp is rejected", ["time"], "L1",
         [apply_request("TM-01", "goal.declare",
                        {"goal_id": "conformance/time-goal"}, dict(GOAL_ARGS),
                        now="2026-08-04T10:00:00")],
         {"responses": [rejected("E_TIME_NAIVE", "input")],
          "final": final(log_line_count=1, lines_delta=0)},
         "An instant without an offset is not an instant. It is refused with "
         "its own code, because 'you forgot the offset' and 'this is not a "
         "timestamp' are different bugs.",
         seed=goal_seed("TM-01"), now_sequence=["2026-08-04T10:00:00"])
    count += 1

    emit("TM-02", "an offset is normalised to utc", ["time"], "L1",
         [apply_request("TM-02", "goal.declare",
                        {"goal_id": "conformance/time-goal"}, dict(GOAL_ARGS),
                        now="2026-08-04T19:00:00+09:00")],
         {"responses": [accepted()],
          "final": final(log_line_count=2, lines_delta=1),
          "emitted_at_equals": "2026-08-04T10:00:00Z"},
         "+09:00 and Z for the same instant must store identically, or two "
         "runtimes in two zones would produce two digests for one event.",
         seed=goal_seed("TM-02"), now_sequence=["2026-08-04T19:00:00+09:00"])
    count += 1

    emit("TM-03", "the z suffix parses on python 3.9", ["time"], "L1",
         [apply_request("TM-03", "goal.declare",
                        {"goal_id": "conformance/time-goal"}, dict(GOAL_ARGS),
                        now="2026-08-04T10:00:00Z")],
         {"responses": [accepted()],
          "final": final(log_line_count=2, lines_delta=1),
          "emitted_at_equals": "2026-08-04T10:00:00Z"},
         "datetime.fromisoformat does not accept Z on 3.9. This case is the "
         "trap, pinned.",
         seed=goal_seed("TM-03"), now_sequence=["2026-08-04T10:00:00Z"])
    count += 1

    # -- determinism --------------------------------------------------------
    emit("DT-01", "the projection digest is stable across 1000 reads",
         ["determinism"], "L1",
         [query_request("DT-01", "state.read", {"goal_id": GOAL_ID})],
         {"responses": [accepted("read")],
          "final": final(log_line_count=4, lines_delta=0),
          "repeat_reads": 1000},
         "G1: same log, same injected now, zero variance across 1000 calls.",
         seed=gate_seed("DT-01", "passed"))
    count += 1

    emit("DT-02", "only the injected now is read, never a wall clock",
         ["determinism"], "L1",
         [query_request("DT-02", "state.read", {"goal_id": GOAL_ID}, now=T0),
          query_request("DT-02", "state.read", {"goal_id": GOAL_ID}, index=1,
                        now=T0)],
         {"responses": [accepted("read"), accepted("read")],
          "final": final(log_line_count=4, lines_delta=0),
          "responses_identical": True},
         "The same case run hours apart with the same now yields identical "
         "digests, because nothing here reads the system clock.",
         seed=gate_seed("DT-02", "passed"))
    count += 1

    shuffled = gate_seed("DT-03", "passed")
    emit("DT-03", "ordering follows event_seq not file order", ["determinism"],
         "L1", [query_request("DT-03", "state.read", {"goal_id": GOAL_ID})],
         {"responses": [accepted("read")],
          "final": final(log_line_count=len(shuffled), lines_delta=0),
          "digest_matches_shuffled_seed": True},
         "The fold sorts by (event_seq, id). Shuffling the seed lines must not "
         "move the projection digest.",
         seed=shuffled)
    count += 1

    emit("DT-04", "created_at is excluded from the fingerprint", ["determinism"],
         "L1",
         [apply_request("DT-04", "goal.declare",
                        {"goal_id": "conformance/fingerprint-goal"},
                        dict(GOAL_ARGS), index=0),
          apply_request("DT-04", "goal.declare",
                        {"goal_id": "conformance/fingerprint-goal"},
                        dict(GOAL_ARGS), index=1)],
         {"responses": [accepted(), rejected("E_ALREADY_DECLARED", "state")],
          "final": final(log_line_count=2, lines_delta=1)},
         "ID-02's exclusion set is exactly {id, created_at, event_seq}: two "
         "records differing only in store-assigned fields fingerprint alike, so "
         "the second declaration is caught as a duplicate identity.",
         seed=goal_seed("DT-04"))
    count += 1

    # -- idempotency --------------------------------------------------------
    replay_seed = gate_seed("ID-01")
    first = apply_request("ID-01", "lease.acquire",
                          {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                          {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    replay = apply_request("ID-01", "lease.acquire",
                           {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                           {"holder": "worker-3", "ttl_seconds": 600}, index=1,
                           now=T2)
    replay["precondition"]["operation_id"] = first["precondition"]["operation_id"]
    emit("ID-01", "a replayed operation_id appends nothing and refreshes no ttl",
         ["idempotency", "lease"], "L2", [first, replay],
         {"responses": [accepted(), accepted("already_applied")],
          "final": final(log_line_count=len(replay_seed) + 1, lines_delta=1),
          "expires_at_unchanged": True},
         "§6.6 normative: already_applied must not re-run side effects and must "
         "not refresh a lease TTL. A renewal is a distinct operation_id.",
         seed=replay_seed)
    count += 1

    emit("ID-02", "the fingerprint exclusion set is exactly three fields",
         ["idempotency", "fingerprint"], "L2",
         [apply_request("ID-02", "goal.declare",
                        {"goal_id": "conformance/exclusion-goal"},
                        dict(GOAL_ARGS))],
         {"responses": [accepted()],
          "final": final(log_line_count=2, lines_delta=1),
          "fingerprint_excludes": ["id", "created_at", "event_seq"]},
         "Widening the exclusion set would let two materially different "
         "requests deduplicate into one another.",
         seed=goal_seed("ID-02"))
    count += 1

    mismatch_seed = gate_seed("ID-03")
    a = apply_request("ID-03", "lease.acquire",
                      {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                      {"holder": "worker-3", "ttl_seconds": 600}, index=0)
    b = apply_request("ID-03", "lease.acquire",
                      {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                      {"holder": "worker-3", "ttl_seconds": 900}, index=1)
    b["precondition"]["operation_id"] = a["precondition"]["operation_id"]
    emit("ID-03", "an idempotency mismatch names the differing keys",
         ["idempotency"], "L2", [a, b],
         {"responses": [accepted(),
                        rejected("E_IDEMPOTENCY_MISMATCH", "idempotency")],
          "final": final(log_line_count=len(mismatch_seed) + 1, lines_delta=1),
          "differing_keys": ["ttl_seconds"]},
         "The difference between a debuggable protocol and a 3am incident.",
         seed=mismatch_seed)
    count += 1

    # -- evidence and gates --------------------------------------------------
    emit("EV-01", "passing a gate without a receipt is refused",
         ["evidence", "gate"], "L2",
         [apply_request("EV-01", "gate.transition",
                        {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                        {"to_status": "passed"})],
         {"responses": [rejected("E_EVIDENCE_REQUIRED", "evidence")],
          "final": final(log_line_count=2, lines_delta=0)},
         "A gate that passes on an assertion is bookkeeping about nothing.",
         seed=gate_seed("EV-01"))
    count += 1

    stale_seed = gate_seed("EV-02", "passed")
    reopen = apply_request("EV-02", "gate.transition",
                           {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                           {"to_status": "pending",
                            "reopen_reason": "the suite regressed"}, index=0)
    repass = apply_request("EV-02", "gate.transition",
                           {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                           {"to_status": "passed"}, index=1, now=T2)
    emit("EV-02", "evidence from before a reopen is stale", ["evidence", "gate"],
         "L2", [reopen, repass],
         {"responses": [accepted(),
                        rejected("E_EVIDENCE_STALE", "evidence")],
          "final": final(log_line_count=len(stale_seed) + 1, lines_delta=1)},
         "§8.2's hole, closed: a receipt observed before the reopen cannot "
         "discharge the gate again afterwards.",
         seed=stale_seed)
    count += 1

    emit("EV-03", "waiving requires an unverified human claim",
         ["evidence", "authority"], "L2",
         [apply_request("EV-03", "gate.transition",
                        {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                        {"to_status": "waived"}, index=0),
          apply_request("EV-03", "gate.transition",
                        {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                        {"to_status": "waived", "authority_claim": "human"},
                        index=1, now=T2)],
         {"responses": [rejected("E_AUTHORITY_CLAIM_REQUIRED", "evidence"),
                        accepted()],
          "final": final(log_line_count=3, lines_delta=1),
          "warnings_include": "W_WAIVER_UNVERIFIED"},
         "§4.8: the claim is not verified and is never presented as if it were. "
         "It is recorded, warned about, and counted.",
         seed=gate_seed("EV-03"))
    count += 1

    emit("EV-04", "authority_verified true is not caller supplied",
         ["evidence", "authority"], "L1",
         [apply_request("EV-04", "goal.declare",
                        {"goal_id": "conformance/authority-goal"},
                        dict(GOAL_ARGS, authority_verified=True))],
         {"responses": [rejected("E_UNKNOWN_FIELD", "input")],
          "final": final(log_line_count=1, lines_delta=0)},
         "A security-shaped field nobody checks is worse than no field: it is "
         "not even expressible on the wire.",
         seed=goal_seed("EV-04"))
    count += 1

    emit("EV-05", "every forbidden transition pair is rejected",
         ["evidence", "transition", "aggregate"], "L2",
         [query_request("EV-05", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0},
          "aggregate_over": "FT-",
          "aggregate_minimum": 40},
         "G2 as an aggregate assertion over the FT- family: every forbidden "
         "triple is rejected and every one leaves lines_delta at zero.")
    count += 1

    # -- lease and fence ----------------------------------------------------
    fn_seed = gate_seed("FN-01")
    emit("FN-01", "a fence is required once the scope is leased", ["lease"], "L2",
         [apply_request("FN-01", "lease.acquire",
                        {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                        {"holder": "worker-3", "ttl_seconds": 600}, index=0),
          apply_request("FN-01", "gate.transition",
                        {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                        {"to_status": "waived", "authority_claim": "human"},
                        index=1)],
         {"responses": [accepted(), rejected("E_FENCE_REQUIRED", "lease")],
          "final": final(log_line_count=len(fn_seed) + 1, lines_delta=1)},
         "The mutation is fence-protected under the gate's declared scope_key.",
         seed=fn_seed)
    count += 1

    fn2_seed = gate_seed("FN-02")
    emit("FN-02", "a stale fence token is refused", ["lease"], "L2",
         [apply_request("FN-02", "lease.acquire",
                        {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                        {"holder": "worker-3", "ttl_seconds": 600}, index=0),
          apply_request("FN-02", "lease.acquire",
                        {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                        {"holder": "worker-3", "ttl_seconds": 600}, index=1,
                        now=T2),
          apply_request("FN-02", "gate.transition",
                        {"goal_id": GOAL_ID, "gate_id": GATE_ID},
                        {"to_status": "waived", "authority_claim": "human"},
                        index=2, now=T2, precondition={"fence_token": 1})],
         {"responses": [accepted(), accepted(),
                        rejected("E_FENCE_STALE", "lease")],
          "final": final(log_line_count=len(fn2_seed) + 2, lines_delta=2)},
         "A superseded holder presenting its old token writes nothing. This is "
         "the whole reason fencing exists.",
         seed=fn2_seed)
    count += 1

    fn3_seed = gate_seed("FN-03")
    emit("FN-03", "a reclaim is a single append carrying supersedes",
         ["lease", "reclaim"], "L2",
         [apply_request("FN-03", "lease.acquire",
                        {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                        {"holder": "worker-3", "ttl_seconds": 1}, index=0,
                        now=T0),
          apply_request("FN-03", "lease.acquire",
                        {"goal_id": GOAL_ID, "scope_key": GATE_ID},
                        {"holder": "worker-9", "ttl_seconds": 600}, index=1,
                        now=T2)],
         {"responses": [accepted(), accepted()],
          "final": final(log_line_count=len(fn3_seed) + 2, lines_delta=2),
          "supersede_reason": "expired",
          "fence_strictly_increases": True},
         "Expiry is never appended — it is a function of the injected now — so "
         "a crashed holder cannot wedge a scope forever, and the reclaim states "
         "exactly how the scope came free.",
         seed=fn3_seed)
    count += 1

    # -- isolation, advisory, cross-runtime ---------------------------------
    emit("IS-01", "no cross base read is expressible", ["isolation", "privacy"],
         "L2",
         [apply_request("IS-01", "handoff.import", {"goal_id": GOAL_ID},
                        {"envelope": {}, "path": "/tmp/other/envelope.json"})],
         {"responses": [rejected("E_UNKNOWN_FIELD", "input")],
          "final": final(log_line_count=1, lines_delta=0),
          "signature_has_no_path": True},
         "The import signature is what makes a cross-base read inexpressible "
         "rather than merely discouraged. The full IS-01 scenario — deleting "
         "the origin tree and monkeypatching open — lives in "
         "tests/test_execution_isolation.py.",
         seed=goal_seed("IS-01"))
    count += 1

    emit("AU-01", "advisory is not authorization", ["advisory", "neutrality"],
         "L2", [query_request("AU-01", "assess.run", {"goal_id": GOAL_ID})],
         {"responses": [accepted("read")],
          "final": final(log_line_count=4, lines_delta=0),
          "advisory_is_true": True,
          "forbidden_response_vocabulary": ["allow", "permit", "authoriz",
                                            "granted", "approved", "permission"]},
         "MemKraft executes nothing and authorizes nothing. A should_run "
         "recommendation carries no more force than a comment, and no response "
         "key or enum is allowed to suggest otherwise.",
         seed=gate_seed("AU-01", "passed"))
    count += 1

    emit("XR-01", "two runtime handoff", ["handoff", "cross-runtime"], "L3",
         [query_request("XR-01", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0}},
         "Runtime A exports, the harness moves the bytes, Runtime B imports and "
         "confirms in reverse. The second runtime is Go and is Slice 12 work.",
         executable=False,
         gap="requires the Go conformance runtime (plan §18.3, Slice 12)")
    count += 1

    emit("CL-01", "cli transport equivalence and stdout purity", ["transport"],
         "L2", [query_request("CL-01", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0}},
         "Every fixture must return a byte-identical result and "
         "response_digest over the CLI, with stdout parsing as JSON on every "
         "path including every error code.",
         executable=False,
         gap="requires execution_cli.py (plan §12, Slice 10)")
    count += 1

    emit("MC-01", "mcp projection equivalence and read only", ["transport"],
         "L2", [query_request("MC-01", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0}},
         "The MCP projection must match the CLI digest exactly and must not "
         "reach any apply op.",
         executable=False,
         gap="requires the read-only MCP tools (plan §13, Slice 10)")
    count += 1

    in_seed = gate_seed("IN-01") + [_seed_record(
        "IN-01", 3, "gate_transition", gate_id="never-declared",
        to_status="passed", event_seq=4)]
    emit("IN-01", "an inconsistent projection forces repair",
         ["projection", "inconsistency"], "L2",
         [query_request("IN-01", "assess.run", {"goal_id": GOAL_ID}, index=0),
          apply_request("IN-01", "goal.transition", {"goal_id": GOAL_ID},
                        {"to_status": "abandoned", "reason": "descoped"},
                        index=1)],
         {"responses": [accepted("read"),
                        rejected("E_PROJECTION_INCONSISTENT", "state")],
          "final": final(log_line_count=len(in_seed), lines_delta=0,
                         consistent=False, rejected_transitions=1),
          "recommendation": "repair",
          "reason_code": "projection_inconsistent"},
         "consistent:false is unconditional repair, and every subsequent apply "
         "is refused until it happens.",
         seed=in_seed)
    count += 1

    # -- neutrality lints ---------------------------------------------------
    emit("NS-01", "no scheduling vocabulary", ["neutrality", "lint"], "L1",
         [query_request("NS-01", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0},
          "lint_absent_from_fixtures_and_describe": [
              "next_check_at", "retry_after", "poll_interval", "cadence", "cron"]},
         "G14: MemKraft never says when to look again. The runtime knows more "
         "about what it can afford than MemKraft ever will.")
    count += 1

    emit("NS-02", "runtime neutral source", ["neutrality", "lint"], "L1",
         [query_request("NS-02", "describe", {})],
         {"responses": [accepted("read")],
          "final": {"log_line_count": 0, "lines_delta": 0, "consistent": True,
                    "rejected_transitions": 0, "skipped": 0},
          "lint_absent_from_execution_source": [
              "hermes", "openclaw", "kanban", "profile_name", "session_key",
              "work_item", "workitem", "datetime.now("]},
         "G14: no execution_* module may name a runtime or read a wall clock.")
    count += 1

    return count


# --------------------------------------------------------------------------

def main():
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    FIXTURES.mkdir(parents=True)
    totals = {
        "forbidden_transitions": generate_forbidden_transitions(),
        "canonicalization": generate_canonicalization(),
        "idempotency": generate_idempotency(),
        "leases": generate_leases(),
        "handoffs": generate_handoffs(),
        "inconsistency": generate_inconsistency(),
        "redaction": generate_redaction(),
        "named": generate_named(),
    }
    totals["directories"] = sum(1 for path in FIXTURES.iterdir() if path.is_dir())
    for name, value in sorted(totals.items()):
        print("%-24s %d" % (name, value))
    return totals


if __name__ == "__main__":
    main()
