"""Slice 3 — declarations, append, and ``event_seq`` (plan §4.1–§4.5, §5.5, §6.6).

The execution log is a single append-only JSONL file whose global order is the
precondition of a deterministic projection. These tests hold the properties that
order depends on: sequence numbers allocated under the governance lock after a
re-read, tombstoned lines still consuming numbers, no compaction, and rejected
input leaving the file byte-count unchanged.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from memkraft import MemKraft, execution_state, store_core


NOW = "2026-08-04T11:22:33Z"

GOAL = dict(
    goal_id="hermes/release-3-3-0",
    title="Release 3.3.0",
    intent="Ship the execution kernel",
    constraints=["stdlib only"],
    success_criteria=["suite green"],
)


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _lines(mk):
    path = mk._execution_events_path()
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _declare_goal(mk, **overrides):
    args = dict(GOAL)
    args.update(overrides)
    return mk.goal_declare(now=NOW, **args)


# --------------------------------------------------------------------------
# Storage layout (§4.1)
# --------------------------------------------------------------------------

def test_init_creates_the_execution_directory_but_not_origin_instance_id(tmp_path):
    """D-14: ``init()`` creates ``execution/``; the instance id stays lazy."""
    mk = _mk(tmp_path)
    assert (Path(mk.base_dir) / ".memkraft" / "execution").is_dir()
    assert not (Path(mk.base_dir) / ".memkraft" / "origin_instance_id").exists()


def test_execution_log_is_a_single_append_only_file(tmp_path):
    mk = _mk(tmp_path)
    assert mk._execution_events_path() == (
        Path(mk.base_dir) / ".memkraft" / "execution" / "events.jsonl"
    )


# --------------------------------------------------------------------------
# Declarations and common fields (§4.3, §4.4)
# --------------------------------------------------------------------------

def test_goal_declare_appends_one_record_with_the_common_fields(tmp_path):
    mk = _mk(tmp_path)
    result = _declare_goal(mk)

    assert result["outcome"] == "applied"
    assert result["event_seq"] == 1
    assert len(_lines(mk)) == 1

    record = store_core.read_all(mk._execution_events_path()).records[0]
    assert record["record_type"] == "goal_declared"
    assert record["execution_schema"] == 1
    assert record["schema_version"] == 1
    assert record["goal_id"] == GOAL["goal_id"]
    assert record["emitted_at"] == NOW
    assert record["privacy"] == "local_private"
    assert record["authority_claim"] == "agent"
    assert record["authority_verified"] is False
    assert record["event_seq"] == 1
    assert record["title"] == GOAL["title"]
    assert record["constraints"] == GOAL["constraints"]
    assert record["id"] == result["record_id"]


def test_gate_declare_requires_a_declared_goal(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(execution_state.NotDeclaredError) as excinfo:
        mk.gate_declare(GOAL["goal_id"], "tests-green", "suite is green",
                        {"check_kind": "command", "check_ref": "pytest -q"}, now=NOW)
    assert excinfo.value.code == "E_NOT_DECLARED"
    assert _lines(mk) == []


def test_gate_declare_appends_after_the_goal(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    result = mk.gate_declare(
        GOAL["goal_id"], "tests-green", "suite is green",
        {"check_kind": "command", "check_ref": "pytest -q"}, now=NOW,
    )
    assert result["event_seq"] == 2
    record = store_core.read_all(mk._execution_events_path()).records[1]
    assert record["record_type"] == "gate_declared"
    assert record["gate_id"] == "tests-green"
    assert record["required"] is True
    assert record["scope_key"] == "tests-green"


def test_duplicate_gate_id_within_a_goal_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    verification = {"check_kind": "command", "check_ref": "pytest -q"}
    mk.gate_declare(GOAL["goal_id"], "tests-green", "green", verification, now=NOW)
    before = len(_lines(mk))
    with pytest.raises(execution_state.ConflictError) as excinfo:
        mk.gate_declare(GOAL["goal_id"], "tests-green", "green", verification, now=NOW,
                        operation_id="b" * 64)
    assert excinfo.value.code == "E_ALREADY_DECLARED"
    assert len(_lines(mk)) == before


def test_duplicate_goal_id_is_rejected_and_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    before = len(_lines(mk))
    with pytest.raises(execution_state.ConflictError) as excinfo:
        _declare_goal(mk, title="A different title")
    assert excinfo.value.code == "E_ALREADY_DECLARED"
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# Input validation — nothing invalid ever reaches the log
# --------------------------------------------------------------------------

def test_naive_now_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(execution_state.ValidationError) as excinfo:
        mk.goal_declare(now="2026-08-04T11:22:33", **GOAL)
    assert excinfo.value.code == "E_TIME_NAIVE"
    assert _lines(mk) == []


@pytest.mark.parametrize("goal_id", [
    "release-3-3-0",                 # no namespace
    "/release",                      # empty namespace
    "hermes/",                       # empty name
    "Hermes/release",                # uppercase
    "hermes/release/extra",          # two separators
    "h/release",                     # namespace shorter than the minimum
    "hermes/" + "x" * 65,            # name longer than the maximum
    "hermes/rel ease",               # space
])
def test_goal_id_grammar_violations_are_rejected(tmp_path, goal_id):
    mk = _mk(tmp_path)
    with pytest.raises(execution_state.ValidationError) as excinfo:
        _declare_goal(mk, goal_id=goal_id)
    assert excinfo.value.code == "E_PATTERN"
    assert _lines(mk) == []


@pytest.mark.parametrize("gate_id", ["ab", "Tests", "tests green", "x" * 81, "-lead"])
def test_gate_id_grammar_violations_are_rejected(tmp_path, gate_id):
    mk = _mk(tmp_path)
    _declare_goal(mk)
    before = len(_lines(mk))
    with pytest.raises(execution_state.ValidationError) as excinfo:
        mk.gate_declare(GOAL["goal_id"], gate_id, "d",
                        {"check_kind": "command", "check_ref": "x"}, now=NOW)
    assert excinfo.value.code == "E_PATTERN"
    assert len(_lines(mk)) == before


@pytest.mark.parametrize("field,value", [
    ("privacy", "public"),
    ("authority_claim", "root"),
])
def test_closed_enums_are_rejected_outside_their_domain(tmp_path, field, value):
    mk = _mk(tmp_path)
    with pytest.raises(execution_state.ValidationError) as excinfo:
        _declare_goal(mk, **{field: value})
    assert excinfo.value.code == "E_PATTERN"
    assert _lines(mk) == []


def test_missing_required_declaration_field_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(execution_state.ValidationError) as excinfo:
        _declare_goal(mk, title="")
    assert excinfo.value.code == "E_MISSING_FIELD"
    assert _lines(mk) == []


def test_caller_supplied_authority_verified_true_is_a_hard_error(tmp_path):
    """§4.4: ``authority_verified`` is always forced false; ``true`` is fatal."""
    with pytest.raises(execution_state.EvidenceError) as excinfo:
        execution_state._common_fields(
            "goal_declared", GOAL["goal_id"], NOW,
            privacy="local_private", authority_claim="agent",
            execution_run_id=None, authority_verified=True,
        )
    assert excinfo.value.code == "E_AUTHORITY_VERIFIED_FORBIDDEN"


# --------------------------------------------------------------------------
# Idempotency (§5.5, §6.6)
# --------------------------------------------------------------------------

def test_fingerprint_exclusion_set_is_exactly_three_keys(tmp_path):
    assert execution_state._FINGERPRINT_EXCLUDED == frozenset(
        {"id", "created_at", "event_seq"}
    )


def test_fingerprint_ignores_id_created_at_and_event_seq(tmp_path):
    """DT-04: envelope metadata may never enter the idempotency fingerprint."""
    base = {"record_type": "goal_declared", "goal_id": GOAL["goal_id"],
            "emitted_at": NOW, "operation_id": "a" * 64}
    first = dict(base, id="1" * 32, created_at="2026-08-04T11:22:33+00:00", event_seq=1)
    second = dict(base, id="2" * 32, created_at="2026-08-04T23:00:00+00:00", event_seq=9)
    assert execution_state._record_fingerprint(first) == \
        execution_state._record_fingerprint(second)
    third = dict(first, goal_id="hermes/other")
    assert execution_state._record_fingerprint(third) != \
        execution_state._record_fingerprint(first)


def test_replay_with_the_same_operation_id_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    first = _declare_goal(mk, operation_id="c" * 64)
    before = len(_lines(mk))
    second = _declare_goal(mk, operation_id="c" * 64)
    assert second["outcome"] == "already_applied"
    assert len(_lines(mk)) == before
    assert second["record_id"] == first["record_id"]
    assert second["event_seq"] == first["event_seq"]


def test_default_operation_id_makes_an_identical_call_idempotent(tmp_path):
    mk = _mk(tmp_path)
    first = _declare_goal(mk)
    second = _declare_goal(mk)
    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    assert len(_lines(mk)) == 1


def test_same_operation_id_with_different_arguments_is_a_mismatch(tmp_path):
    mk = _mk(tmp_path)
    _declare_goal(mk, operation_id="d" * 64)
    before = len(_lines(mk))
    with pytest.raises(execution_state.ConflictError) as excinfo:
        _declare_goal(mk, operation_id="d" * 64, title="Something else")
    error = excinfo.value
    assert error.code == "E_IDEMPOTENCY_MISMATCH"
    assert error.details["differing_keys"] == ["title"]
    assert set(error.details) >= {"stored_fingerprint", "request_fingerprint",
                                  "differing_keys"}
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# event_seq allocation (§4.2)
# --------------------------------------------------------------------------

def test_event_seq_is_allocated_after_the_lock_is_acquired(tmp_path):
    """Any value read before the governance lock must be discarded."""
    mk = _mk(tmp_path)
    original = type(mk)._governance_lock

    def racing_lock(self):
        fd = original(self)
        if not getattr(self, "_raced", False):
            self._raced = True
            store_core.append(self._execution_events_path(), {
                "record_type": "goal_declared", "execution_schema": 1,
                "goal_id": "other/goal", "event_seq": 7, "emitted_at": NOW,
                "operation_id": "f" * 64, "privacy": "local_private",
                "authority_claim": "agent", "authority_verified": False,
            })
        return fd

    mk._governance_lock = racing_lock.__get__(mk, type(mk))
    result = _declare_goal(mk)
    assert result["event_seq"] == 8


def test_tombstoned_records_still_consume_sequence_numbers(tmp_path):
    mk = _mk(tmp_path)
    first = _declare_goal(mk)
    store_core.mark_tombstone(mk._execution_events_path(), first["record_id"])
    second = mk.goal_declare(now=NOW, **dict(GOAL, goal_id="hermes/second-goal"))
    assert second["event_seq"] > first["event_seq"]
    visible = store_core.read_all(mk._execution_events_path()).records
    assert [r["event_seq"] for r in visible] == [second["event_seq"]]


def test_sequence_numbers_are_dense_and_strictly_increasing(tmp_path):
    mk = _mk(tmp_path)
    verification = {"check_kind": "command", "check_ref": "pytest -q"}
    _declare_goal(mk)
    for index in range(5):
        mk.gate_declare(GOAL["goal_id"], "gate-%d" % index, "d", verification, now=NOW)
    records = store_core.read_all(mk._execution_events_path()).records
    assert [r["event_seq"] for r in records] == [1, 2, 3, 4, 5, 6]


# --------------------------------------------------------------------------
# G15 — the execution log is never compacted (§4.1, D-15)
# --------------------------------------------------------------------------

def test_compact_is_unreachable_for_the_execution_log():
    """G15: no shipped code path hands ``events.jsonl`` to ``store_core.compact``."""
    source = inspect.getsource(execution_state)
    assert "compact(" not in source

    package = Path(store_core.__file__).parent
    callers = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "compact(" in text and "execution" in text:
            callers.append(path.name)
    assert callers == []


# --------------------------------------------------------------------------
# Mixin registration (§11.4, G8)
# --------------------------------------------------------------------------

def test_execution_state_mixin_is_registered_additive_only():
    import memkraft

    assert execution_state.ExecutionStateMixin in memkraft._ADDITIVE_ONLY_MIXINS
    contributed = {
        name for name in vars(execution_state.ExecutionStateMixin)
        if not name.startswith("__")
    }
    assert contributed
    for name in contributed:
        assert getattr(MemKraft, name) is getattr(
            execution_state.ExecutionStateMixin, name
        ), "%s was shadowed by an earlier mixin" % name
