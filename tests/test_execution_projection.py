"""Slice 4 — the pure projection and the single transition table (plan §4.2, §4.6, §4.7).

The projection is a pure function of the log records and the injected ``now``.
Its determinism is what makes replay, idempotency, and every later guard
meaningful, so these tests pin ordering, digest stability, the two never-merged
counters, and the exit criterion that the state machines live in exactly one
data structure rather than in branching code.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import random
from pathlib import Path

import pytest

from memkraft import execution_projection, store_core


NOW = "2026-08-04T11:22:33Z"
GOAL_ID = "hermes/release-3-3-0"

GOAL_STATUSES = ("open", "satisfied", "abandoned")
GATE_STATUSES = ("pending", "passed", "failed", "waived")
HANDOFF_STATUSES = ("offered", "accepted", "completed")
ALL_STATUSES = GOAL_STATUSES + GATE_STATUSES + HANDOFF_STATUSES

ALLOWED = {
    ("goal", "open", "satisfied"),
    ("goal", "open", "abandoned"),
    ("gate", "pending", "passed"),
    ("gate", "pending", "failed"),
    ("gate", "pending", "waived"),
    ("gate", "passed", "pending"),
    ("gate", "failed", "pending"),
    ("gate", "failed", "passed"),
    ("gate", "failed", "waived"),
    ("handoff", "offered", "accepted"),
    ("handoff", "accepted", "completed"),
}

_seq = itertools.count(1)


def _record(record_type, **fields):
    seq = next(_seq)
    record = {
        "schema_version": 1,
        "execution_schema": 1,
        "record_type": record_type,
        "goal_id": GOAL_ID,
        "event_seq": seq,
        "id": "%032x" % seq,
        "emitted_at": NOW,
        "operation_id": "%064x" % seq,
        "privacy": "local_private",
        "authority_claim": "agent",
        "authority_verified": False,
    }
    record.update(fields)
    return record


def _goal(**fields):
    return _record("goal_declared", title="t", intent="i", constraints=[],
                   success_criteria=[], **fields)


def _gate(gate_id, required=True, **fields):
    return _record("gate_declared", gate_id=gate_id, description="d",
                   required=required, scope_key=gate_id,
                   verification={"check_kind": "command", "check_ref": "pytest"},
                   **fields)


def _project(records, now=NOW, skipped=0):
    return execution_projection.project(records, now, GOAL_ID, skipped=skipped)


# --------------------------------------------------------------------------
# Determinism (§4.2, DT-01, DT-03)
# --------------------------------------------------------------------------

def test_projection_is_a_pure_function_of_records_and_now():
    records = [_goal(), _gate("tests-green")]
    first = _project(records)
    assert first["goal_status"] == "open"
    assert [gate["gate_id"] for gate in first["gates"]] == ["tests-green"]
    assert first["consistent"] is True
    assert first["evaluated_at"] == NOW
    assert first == _project(list(records))


def test_projection_digest_is_stable_over_1000_calls():
    """G1: 1000 calls, identical ``projection_digest``, zero failures."""
    records = [_goal(), _gate("tests-green"), _gate("docs-written", required=False)]
    digests = {_project(records)["digest"] for _ in range(1000)}
    assert len(digests) == 1


def test_projection_digest_excludes_evaluated_at_and_itself():
    records = [_goal()]
    assert _project(records)["digest"] == _project(records, now="2027-01-01T00:00:00Z")["digest"]


def test_ordering_is_by_event_seq_and_id_not_file_order():
    """DT-03: shuffled input lines produce an identical projection."""
    records = [_goal(), _gate("tests-green")]
    records.append(_record("gate_transition", gate_id="tests-green", to_status="waived"))
    expected = _project(records)
    shuffler = random.Random(20260804)
    for _ in range(20):
        shuffled = list(records)
        shuffler.shuffle(shuffled)
        assert _project(shuffled) == expected


def test_records_of_other_goals_are_not_projected():
    other = _goal(goal_id="hermes/other-goal")
    assert _project([_goal(), other])["gates"] == []
    assert _project([other])["goal_status"] is None


# --------------------------------------------------------------------------
# skipped vs rejected_transitions — two counters, never merged (§4.7)
# --------------------------------------------------------------------------

def test_corrupt_lines_are_skipped_and_leave_the_projection_consistent(tmp_path):
    path = tmp_path / "events.jsonl"
    store_core.append(path, _goal())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    store_core.append(path, _gate("tests-green"))

    result = store_core.read_all(path, include_tombstoned=True)
    projection = _project(result.records, skipped=result.skipped)
    assert projection["skipped"] == 1
    assert projection["consistent"] is True
    assert projection["rejected_transitions"] == []
    assert len(projection["gates"]) == 1


def test_transition_against_an_undeclared_gate_is_rejected():
    records = [_goal(), _record("gate_transition", gate_id="ghost", to_status="passed")]
    projection = _project(records)
    assert projection["consistent"] is False
    assert projection["skipped"] == 0
    assert len(projection["rejected_transitions"]) == 1
    rejected = projection["rejected_transitions"][0]
    assert rejected["reason"] == "undeclared_gate"
    assert rejected["record_id"] == records[1]["id"]


def test_transition_against_an_undeclared_goal_is_rejected():
    records = [_record("goal_transition", to_status="satisfied", reason="done")]
    projection = _project(records)
    assert projection["consistent"] is False
    assert projection["rejected_transitions"][0]["reason"] == "undeclared_goal"
    assert projection["goal_status"] is None


def test_a_rejected_transition_does_not_change_the_entity_status():
    records = [
        _goal(),
        _gate("tests-green"),
        _record("gate_transition", gate_id="tests-green", to_status="waived"),
        _record("gate_transition", gate_id="tests-green", to_status="passed"),
    ]
    projection = _project(records)
    assert projection["gates"][0]["status"] == "waived"
    assert projection["consistent"] is False
    assert projection["rejected_transitions"][0]["reason"] == "forbidden_transition"


def test_allowed_transitions_are_applied():
    records = [_goal(), _gate("tests-green")]
    records.append(_record("gate_transition", gate_id="tests-green", to_status="passed"))
    records.append(_record("gate_transition", gate_id="tests-green",
                           to_status="pending", reopen_reason="regression"))
    records.append(_record("goal_transition", to_status="abandoned", reason="descoped"))
    projection = _project(records)
    assert projection["gates"][0]["status"] == "pending"
    assert projection["goal_status"] == "abandoned"
    assert projection["consistent"] is True


def test_handoff_states_project_from_declaration_to_completed():
    declared = _record("handoff_declared", handoff_id="a" * 32, to_actor="worker-3")
    records = [_goal(), declared]
    assert _project(records)["handoffs"][0]["status"] == "offered"
    records.append(_record("handoff_transition", handoff_id="a" * 32, to_status="accepted"))
    records.append(_record("handoff_transition", handoff_id="a" * 32, to_status="completed"))
    projection = _project(records)
    assert projection["handoffs"][0]["status"] == "completed"
    assert projection["consistent"] is True


# --------------------------------------------------------------------------
# The forbidden pairs (§4.6, G2, EV-05)
# --------------------------------------------------------------------------

def _forbidden_pairs():
    pairs = []
    for kind, statuses in (("goal", GOAL_STATUSES), ("gate", GATE_STATUSES),
                           ("handoff", HANDOFF_STATUSES)):
        for from_status in statuses:
            for to_status in ALL_STATUSES:
                if (kind, from_status, to_status) not in ALLOWED:
                    pairs.append((kind, from_status, to_status))
    return pairs


def test_the_forbidden_pair_corpus_is_at_least_forty_pairs():
    assert len(_forbidden_pairs()) >= 40


def _seed_at(kind, from_status):
    """Return records placing ``kind`` in ``from_status``, or None if unreachable."""
    records = [_goal()]
    if kind == "goal":
        if from_status != "open":
            records.append(_record("goal_transition", to_status=from_status,
                                   reason="seeded"))
        return records
    if kind == "gate":
        records.append(_gate("tests-green"))
        if from_status != "pending":
            records.append(_record("gate_transition", gate_id="tests-green",
                                   to_status=from_status))
        return records
    records.append(_record("handoff_declared", handoff_id="a" * 32, to_actor="w"))
    for state in ("accepted", "completed"):
        if HANDOFF_STATUSES.index(state) <= HANDOFF_STATUSES.index(from_status):
            records.append(_record("handoff_transition", handoff_id="a" * 32,
                                   to_status=state))
    return records


@pytest.mark.parametrize("kind,from_status,to_status", _forbidden_pairs())
def test_every_forbidden_pair_is_rejected(kind, from_status, to_status):
    records = _seed_at(kind, from_status)
    baseline = _project(records)
    assert baseline["consistent"] is True, "the seed itself must be a legal path"

    extra = {"gate_id": "tests-green"} if kind == "gate" else {}
    if kind == "handoff":
        extra = {"handoff_id": "a" * 32}
    records = records + [_record("%s_transition" % kind, to_status=to_status, **extra)]
    projection = _project(records)

    assert projection["consistent"] is False
    assert projection["rejected_transitions"][-1]["reason"] == "forbidden_transition"
    assert projection["gates"] == baseline["gates"]
    assert projection["goal_status"] == baseline["goal_status"]
    assert projection["handoffs"] == baseline["handoffs"]


# --------------------------------------------------------------------------
# REFACTOR exit criterion — one table, no branch chain (§4.6)
# --------------------------------------------------------------------------

def test_transitions_is_exactly_one_dict_covering_every_declared_rule():
    """§19.2 suggests ``len(_TRANSITIONS) >= 12``; §4.6 enumerates exactly 11
    rules (2 goal + 7 gate + 2 handoff). The normative enumeration wins, and
    pinning the exact key set is a strictly stronger check than a count.
    """
    table = execution_projection._TRANSITIONS
    assert isinstance(table, dict)
    assert set(table) == ALLOWED


def test_module_contains_no_branch_chain_over_to_status():
    """Hard exit criterion: the state machines are data, not control flow."""
    source = inspect.getsource(execution_projection)
    tree = ast.parse(source, filename="execution_projection.py",
                     feature_version=(3, 9))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        dumped = ast.dump(node.test)
        mentions_status = "to_status" in dumped or "from_status" in dumped
        literals = [
            child.value for child in ast.walk(node.test)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]
        if mentions_status and any(value in ALL_STATUSES for value in literals):
            offenders.append(node.lineno)
    assert offenders == [], "branch chain over transition literals at %s" % offenders


def test_only_one_transition_table_is_defined():
    source = Path(execution_projection.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 9))
    tables = [
        target.id
        for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and "TRANSITION" in target.id.upper()
    ]
    assert tables == ["_TRANSITIONS"]
