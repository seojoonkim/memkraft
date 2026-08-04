"""Slice 7 — advisory assessment (plan §9, §4.7, §5.5).

Two properties carry this slice. The first is that ``assess_run`` is a *query*:
it computes a recommendation and appends nothing, so a heartbeat-driven runtime
can poll it forever without growing the log (§9.1, D-09). The second is that it
is *pure*: the same log bytes and the same injected ``now`` produce the same
``inputs_digest`` and the same recommendation, which is what makes an
after-the-fact audit of a recorded assessment possible at all.

Everything else here is closure. The recommendation/reason pairs are a closed
table (§9.2) and anything outside it is rejected; an inconsistent projection
dominates every other signal unconditionally (I-D2); and a recommendation that
leaned on an unverified waiver says so in ``caveats`` (I-D3).
"""
from __future__ import annotations

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_protocol import ExecutionError


NOW = "2026-08-04T11:22:33Z"
LATER = "2026-08-04T13:22:33Z"           # NOW + 2h, past a 600 s lease
GOAL_ID = "hermes/release-3-3-0"
SHA = "a" * 64

GOAL = dict(
    goal_id=GOAL_ID,
    title="Release 3.3.0",
    intent="Ship the execution kernel",
    constraints=["stdlib only"],
    success_criteria=["suite green"],
)

VERIFICATION = {"check_kind": "command", "check_ref": "pytest -q"}


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    mk.goal_declare(now=NOW, **GOAL)
    return mk


def _lines(mk):
    path = mk._execution_events_path()
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _declare_gate(mk, gate_id="tests-green", required=True):
    mk.gate_declare(GOAL_ID, gate_id, "the suite is green", VERIFICATION,
                    now=NOW, required=required)
    return gate_id


def _pass_gate(mk, gate_id="tests-green"):
    mk.receipt_record(GOAL_ID, gate_id, "pass", SHA, "green", now=NOW,
                      provenance_id="run-1")
    mk.gate_transition(GOAL_ID, gate_id, "passed", now=NOW)


def _waive_gate(mk, gate_id="tests-green"):
    mk.gate_transition(GOAL_ID, gate_id, "waived", now=NOW,
                       authority_claim="human")


def _assessment(mk, **overrides):
    """A valid recorded assessment, computed from the live projection."""
    run = mk.assess_run(GOAL_ID, now=NOW)
    assessment = {
        "advisory": True,
        "recommendation": run["recommendation"],
        "reason_code": run["reason_code"],
        "inputs_digest": run["inputs_digest"],
    }
    assessment.update(overrides)
    return assessment


def _code(excinfo):
    return excinfo.value.code


# --------------------------------------------------------------------------
# I-D4 / I-D1 — a query, and a pure one (§9.1, §9.3)
# --------------------------------------------------------------------------

def test_assess_run_appends_nothing_across_a_hundred_calls(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = _lines(mk)

    for _ in range(100):
        mk.assess_run(GOAL_ID, now=NOW)

    assert _lines(mk) == before


def test_assess_run_leaves_every_gate_byte_identical(tmp_path):
    """AU-01: an assessment observes; it never moves a gate."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = mk._execution_events_path().read_bytes()

    mk.assess_run(GOAL_ID, now=NOW)

    assert mk._execution_events_path().read_bytes() == before


def test_same_log_and_same_now_reproduce_the_inputs_digest(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)

    first = mk.assess_run(GOAL_ID, now=NOW)
    second = mk.assess_run(GOAL_ID, now=NOW)

    assert first["inputs_digest"] == second["inputs_digest"]
    assert first["recommendation"] == second["recommendation"]
    assert first["reason_code"] == second["reason_code"]


def test_the_inputs_digest_moves_when_the_log_moves(tmp_path):
    mk = _mk(tmp_path)
    before = mk.assess_run(GOAL_ID, now=NOW)["inputs_digest"]
    _declare_gate(mk)
    assert mk.assess_run(GOAL_ID, now=NOW)["inputs_digest"] != before


def test_every_assessment_is_marked_advisory(tmp_path):
    mk = _mk(tmp_path)
    assert mk.assess_run(GOAL_ID, now=NOW)["advisory"] is True


def test_assess_run_on_an_undeclared_goal_is_not_declared(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_run(GOAL_ID, now=NOW)
    assert _code(excinfo) == "E_NOT_DECLARED"


# --------------------------------------------------------------------------
# I-D2 — inconsistency dominates, unconditionally (§4.7, §9.3)
# --------------------------------------------------------------------------

def _seed_undeclared_transition(mk):
    """Append a transition against a gate nobody declared (§4.7)."""
    store_core.append(mk._execution_events_path(), {
        "record_type": "gate_transition", "execution_schema": 1,
        "goal_id": GOAL_ID, "gate_id": "never-declared",
        "to_status": "passed", "event_seq": 9000,
        "emitted_at": NOW, "operation_id": "seeded",
    })


def test_inconsistent_projection_forces_repair(tmp_path):
    mk = _mk(tmp_path)
    _seed_undeclared_transition(mk)

    run = mk.assess_run(GOAL_ID, now=NOW)

    assert run["recommendation"] == "repair"
    assert run["reason_code"] == "projection_inconsistent"


def test_repair_dominates_a_satisfied_goal(tmp_path):
    """No competing signal wins: I-D2 is unconditional, not a tie-break."""
    mk = _mk(tmp_path)
    mk.goal_transition(GOAL_ID, "satisfied", now=NOW)
    _seed_undeclared_transition(mk)

    run = mk.assess_run(GOAL_ID, now=NOW)

    assert (run["recommendation"], run["reason_code"]) == (
        "repair", "projection_inconsistent")


def test_a_corrupt_line_is_io_damage_and_does_not_force_repair(tmp_path):
    """``skipped`` and ``rejected_transitions`` are never merged (§4.7)."""
    mk = _mk(tmp_path)
    with open(mk._execution_events_path(), "a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    assert mk.assess_run(GOAL_ID, now=NOW)["recommendation"] != "repair"


# --------------------------------------------------------------------------
# The closed recommendation table (§9.2)
# --------------------------------------------------------------------------

def test_a_settled_goal_stops(tmp_path):
    mk = _mk(tmp_path)
    mk.goal_transition(GOAL_ID, "satisfied", now=NOW)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == ("stop", "goal_satisfied")


def test_an_abandoned_goal_stops(tmp_path):
    mk = _mk(tmp_path)
    mk.goal_transition(GOAL_ID, "abandoned", now=NOW, reason="superseded")
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == ("stop", "goal_abandoned")


def test_a_pending_required_gate_waits_and_names_its_blockers(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == (
        "wait", "blocking_gate_pending")
    assert run["blockers"] == ["tests-green"]


def test_an_optional_pending_gate_is_not_a_blocker(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk, "changelog", required=False)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert run["blockers"] == []
    assert (run["recommendation"], run["reason_code"]) == ("should_run", "gates_open")


def test_blockers_are_sorted_deterministically(tmp_path):
    mk = _mk(tmp_path)
    for gate_id in ("zeta-gate", "alpha-gate", "mid-gate"):
        _declare_gate(mk, gate_id)
    assert mk.assess_run(GOAL_ID, now=NOW)["blockers"] == [
        "alpha-gate", "mid-gate", "zeta-gate"]


def test_a_failed_required_gate_asks_a_human(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    mk.receipt_record(GOAL_ID, "tests-green", "fail", SHA, "red", now=NOW)
    mk.gate_transition(GOAL_ID, "tests-green", "failed", now=NOW)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == (
        "ask_human", "evidence_inconclusive")


def test_an_active_lease_waits(tmp_path):
    mk = _mk(tmp_path)
    mk.lease_acquire(GOAL_ID, "tests-green", "worker-a", 600, now=NOW)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == (
        "wait", "lease_held_by_other")


def test_an_expired_unreleased_lease_is_repairable_state(tmp_path):
    mk = _mk(tmp_path)
    mk.lease_acquire(GOAL_ID, "tests-green", "worker-a", 600, now=NOW)
    run = mk.assess_run(GOAL_ID, now=LATER)
    assert (run["recommendation"], run["reason_code"]) == (
        "repair", "stale_lease_detected")


def test_a_released_lease_leaves_no_repair_behind(tmp_path):
    mk = _mk(tmp_path)
    grant = mk.lease_acquire(GOAL_ID, "tests-green", "worker-a", 600, now=NOW)
    mk.lease_release(GOAL_ID, "tests-green", grant["lease_id"], now=NOW)
    run = mk.assess_run(GOAL_ID, now=LATER)
    assert (run["recommendation"], run["reason_code"]) == ("should_run", "gates_open")


def test_an_open_goal_with_every_gate_passed_may_run(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _pass_gate(mk)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == ("should_run", "gates_open")


def test_no_recommendation_carries_a_timing_hint(tmp_path):
    """I-D5: ``wait`` never says when. There is no ``next_check_at``."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    assert "next_check_at" not in mk.assess_run(GOAL_ID, now=NOW)


def test_no_response_key_reads_as_authorization(tmp_path):
    """AU-02 in miniature: the surface must not sound like a permission."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    run = mk.assess_run(GOAL_ID, now=NOW)
    forbidden = ("allow", "permit", "authoriz", "granted", "approved", "permission")
    for key in run:
        assert not any(token in key.lower() for token in forbidden), key


# --------------------------------------------------------------------------
# I-D3 — the waiver caveat (§4.8, §9.3)
# --------------------------------------------------------------------------

def test_a_recommendation_leaning_on_a_waiver_says_so(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _waive_gate(mk)
    run = mk.assess_run(GOAL_ID, now=NOW)
    assert (run["recommendation"], run["reason_code"]) == ("should_run", "gates_open")
    assert run["caveats"] == ["waiver_unverified"]


def test_an_unwaived_evaluation_carries_no_caveat(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _pass_gate(mk)
    assert mk.assess_run(GOAL_ID, now=NOW)["caveats"] == []


# --------------------------------------------------------------------------
# assess_record — the explicit append (§9.1)
# --------------------------------------------------------------------------

def test_assess_record_appends_exactly_one_line(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = len(_lines(mk))

    result = mk.assess_record(GOAL_ID, _assessment(mk), now=NOW)

    assert result["outcome"] == "applied"
    assert len(_lines(mk)) == before + 1
    assert result["record"]["record_type"] == "run_assessment"


def test_a_replayed_assessment_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    assessment = _assessment(mk)
    first = mk.assess_record(GOAL_ID, assessment, now=NOW)
    before = len(_lines(mk))

    second = mk.assess_record(GOAL_ID, assessment, now=NOW)

    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    assert len(_lines(mk)) == before


def test_a_recorded_assessment_stays_inert_in_the_projection(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = mk.assess_run(GOAL_ID, now=NOW)
    mk.assess_record(GOAL_ID, _assessment(mk), now=NOW)
    after = mk.assess_run(GOAL_ID, now=NOW)
    assert (after["recommendation"], after["reason_code"], after["blockers"]) == (
        before["recommendation"], before["reason_code"], before["blockers"])


def test_assess_record_on_an_undeclared_goal_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    assessment = _assessment(mk)
    other = MemKraft(base_dir=str(tmp_path / "other"))
    other.init(verbose=False)
    with pytest.raises(ExecutionError) as excinfo:
        other.assess_record(GOAL_ID, assessment, now=NOW)
    assert _code(excinfo) == "E_NOT_DECLARED"


@pytest.mark.parametrize("recommendation,reason_code", [
    ("should_run", "gates_open"),
    ("should_run", "lease_acquired"),
    ("wait", "lease_held_by_other"),
    ("wait", "blocking_gate_pending"),
    ("wait", "cooldown_not_elapsed"),
    ("ask_human", "waiver_required"),
    ("ask_human", "constraint_conflict"),
    ("ask_human", "evidence_inconclusive"),
    ("stop", "goal_satisfied"),
    ("stop", "goal_abandoned"),
    ("stop", "quota_exhausted"),
    ("stop", "max_runs_exceeded"),
    ("repair", "stale_lease_detected"),
    ("repair", "projection_inconsistent"),
    ("repair", "handoff_incomplete"),
    ("repair", "phases_incomplete"),
])
def test_every_allowed_pair_records(tmp_path, recommendation, reason_code):
    mk = _mk(tmp_path)
    result = mk.assess_record(
        GOAL_ID,
        _assessment(mk, recommendation=recommendation, reason_code=reason_code),
        now=NOW,
    )
    assert result["outcome"] == "applied"


@pytest.mark.parametrize("recommendation,reason_code", [
    ("should_run", "goal_satisfied"),
    ("wait", "gates_open"),
    ("stop", "projection_inconsistent"),
    ("repair", "quota_exhausted"),
    ("proceed", "gates_open"),
    ("should_run", "looks_fine"),
])
def test_a_pair_outside_the_table_is_rejected(tmp_path, recommendation, reason_code):
    mk = _mk(tmp_path)
    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(
            GOAL_ID,
            _assessment(mk, recommendation=recommendation, reason_code=reason_code),
            now=NOW,
        )
    assert _code(excinfo) == "E_PATTERN"
    assert len(_lines(mk)) == before


@pytest.mark.parametrize("advisory", [False, None, "true", 1])
def test_an_assessment_that_is_not_advisory_is_rejected(tmp_path, advisory):
    mk = _mk(tmp_path)
    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(GOAL_ID, _assessment(mk, advisory=advisory), now=NOW)
    assert _code(excinfo) == "E_PATTERN"
    assert len(_lines(mk)) == before


def test_an_assessment_without_an_inputs_digest_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    assessment = _assessment(mk)
    del assessment["inputs_digest"]
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(GOAL_ID, assessment, now=NOW)
    assert _code(excinfo) in ("E_MISSING_FIELD", "E_PATTERN")


def test_an_inputs_digest_that_is_not_a_digest_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(GOAL_ID, _assessment(mk, inputs_digest="nope"), now=NOW)
    assert _code(excinfo) == "E_PATTERN"


def test_an_unknown_assessment_field_is_rejected(tmp_path):
    """The envelope is closed: an unread field is a silently ignored claim."""
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(GOAL_ID, _assessment(mk, next_check_at=NOW), now=NOW)
    assert _code(excinfo) == "E_UNKNOWN_FIELD"


def test_a_non_object_assessment_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.assess_record(GOAL_ID, "should_run", now=NOW)
    assert _code(excinfo) == "E_TYPE"


def test_assess_record_does_not_reverify_against_the_projection(tmp_path):
    """§9.1: it records what a runtime concluded, not what core would conclude.

    ``inputs_digest`` is what makes the disagreement checkable afterwards; core
    refusing the append would just move the audit trail out of the log.
    """
    mk = _mk(tmp_path)
    _declare_gate(mk)                                   # ⇒ blocking_gate_pending
    result = mk.assess_record(
        GOAL_ID,
        _assessment(mk, recommendation="should_run", reason_code="gates_open"),
        now=NOW,
    )
    assert result["outcome"] == "applied"


def test_a_recorded_assessment_carries_the_common_fields(tmp_path):
    mk = _mk(tmp_path)
    record = mk.assess_record(GOAL_ID, _assessment(mk), now=NOW)["record"]
    assert record["authority_verified"] is False
    assert record["goal_id"] == GOAL_ID
    assert record["emitted_at"] == NOW
    assert record["event_seq"] >= 1
    assert record["advisory"] is True
