"""Slice 5 — evidence receipts, snapshot binding, and gate guards (plan §8, §4.6, §4.8).

A gate that can be passed by pointing at any receipt that ever existed is not a
gate. §8.2 closes that hole by binding every receipt to the sequence number it
observed and evaluating every pass against the gate's reopen watermark, so the
comparison is between two ``event_seq`` values allocated under one lock on a
log that is never compacted — no wall clock, no second file, no caller-supplied
value.

The other half of this slice is honesty: waiving is allowed, unverified, and
counted. These tests pin both — the guard that holds, and the counter that
records what the guard deliberately let through.
"""
from __future__ import annotations

import pytest

from memkraft import MemKraft, store_core


NOW = "2026-08-04T11:22:33Z"
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
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _declare_gate(mk, gate_id="tests-green", **overrides):
    return mk.gate_declare(GOAL_ID, gate_id, "the suite is green", VERIFICATION,
                           now=NOW, **overrides)


def _receipt(mk, gate_id="tests-green", verdict="pass", **overrides):
    return mk.receipt_record(GOAL_ID, gate_id, verdict, SHA, "270 passed",
                             now=NOW, **overrides)


def _state(mk):
    result = store_core.read_all(mk._execution_events_path(), include_tombstoned=True)
    from memkraft import execution_projection
    return execution_projection.project(result.records, NOW, GOAL_ID,
                                        skipped=result.skipped)


def _gate_of(mk, gate_id="tests-green"):
    return next(gate for gate in _state(mk)["gates"] if gate["gate_id"] == gate_id)


# --------------------------------------------------------------------------
# Receipts are inert (§8.1)
# --------------------------------------------------------------------------

def test_receipt_record_appends_one_record_and_never_moves_the_gate(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = len(_lines(mk))

    result = _receipt(mk, provenance_id="run-7")

    assert result["outcome"] == "applied"
    assert result["gate_status_unchanged"] is True
    assert len(_lines(mk)) == before + 1
    assert _gate_of(mk)["status"] == "pending"


def test_receipt_observed_seq_is_the_sequence_it_was_appended_after(tmp_path):
    """§8.2.1: ``observed_seq`` is stored explicitly and equals ``event_seq - 1``."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    result = _receipt(mk)

    record = next(r for r in store_core.read_all(mk._execution_events_path()).records
                  if r["record_type"] == "evidence_receipt")
    assert record["observed_seq"] == result["event_seq"] - 1
    assert record["verdict"] == "pass"
    assert record["content_sha256"] == SHA


def test_receipt_for_an_undeclared_gate_is_rejected(tmp_path):
    """I-E6."""
    mk = _mk(tmp_path)
    before = len(_lines(mk))
    with pytest.raises(Exception) as excinfo:
        _receipt(mk, gate_id="never-declared")
    assert excinfo.value.code == "E_NOT_DECLARED"
    assert len(_lines(mk)) == before


def test_a_receipt_without_provenance_is_counted_and_warned(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    result = _receipt(mk)
    assert "W_RECEIPT_UNPROVENANCED" in result["warnings"]
    assert _state(mk)["receipts_without_provenance"] == 1

    provenanced = _receipt(mk, provenance_id="run-7")
    assert provenanced["warnings"] == []
    assert _state(mk)["receipts_without_provenance"] == 1


# --------------------------------------------------------------------------
# EV-01 — a gate does not pass on assertion (§8.3 I-E1, I-E2)
# --------------------------------------------------------------------------

def test_pass_without_a_receipt_is_rejected_and_appends_nothing(tmp_path):
    """EV-01: ``E_EVIDENCE_REQUIRED``, ``lines_delta == 0``."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = len(_lines(mk))

    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)

    assert excinfo.value.code == "E_EVIDENCE_REQUIRED"
    assert len(_lines(mk)) == before
    assert _gate_of(mk)["status"] == "pending"


def test_a_fail_receipt_does_not_satisfy_a_pass_transition(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _receipt(mk, verdict="fail")
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    assert excinfo.value.code == "E_EVIDENCE_REQUIRED"


def test_pass_with_a_fresh_receipt_moves_the_gate(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    receipt = _receipt(mk)
    result = mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                                receipt_id=receipt["record_id"])
    assert result["gate_status"] == "passed"
    assert _gate_of(mk)["status"] == "passed"


def test_fail_requires_a_fresh_fail_receipt(tmp_path):
    """I-E2."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "failed", now=NOW)
    assert excinfo.value.code == "E_EVIDENCE_REQUIRED"
    _receipt(mk, verdict="fail")
    assert mk.gate_transition(GOAL_ID, "tests-green", "failed",
                              now=NOW)["gate_status"] == "failed"


# --------------------------------------------------------------------------
# EV-02 — the temporal hole (§8.2). This is the case the slice exists for.
# --------------------------------------------------------------------------

def test_re_passing_after_a_reopen_with_the_pre_reopen_receipt_is_stale(tmp_path):
    """EV-02: pass with R, reopen, re-pass with the same R ⇒ ``E_EVIDENCE_STALE``."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    receipt = _receipt(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                       receipt_id=receipt["record_id"])
    reopen = mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW,
                                reopen_reason="a regression landed")
    assert _gate_of(mk)["reopened_at_seq"] == reopen["event_seq"]
    before = len(_lines(mk))

    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                           receipt_id=receipt["record_id"])

    error = excinfo.value
    assert error.code == "E_EVIDENCE_STALE"
    assert error.details["receipt_event_seq"] == receipt["event_seq"]
    assert error.details["reopened_at_seq"] == reopen["event_seq"]
    assert len(_lines(mk)) == before
    assert _gate_of(mk)["status"] == "pending"


def test_re_passing_after_a_reopen_without_naming_a_receipt_is_stale(tmp_path):
    """The implicit path must be guarded exactly like the explicit one."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    receipt = _receipt(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW,
                       reopen_reason="a regression landed")

    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    assert excinfo.value.code == "E_EVIDENCE_STALE"
    assert excinfo.value.details["receipt_event_seq"] == receipt["event_seq"]


def test_a_fresh_receipt_after_a_reopen_lets_the_gate_pass_again(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _receipt(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW,
                       reopen_reason="a regression landed")
    _receipt(mk, operation_id="fresh-run-2")
    assert mk.gate_transition(GOAL_ID, "tests-green", "passed",
                              now=NOW)["gate_status"] == "passed"


def test_an_explicit_stale_receipt_id_is_never_silently_upgraded(tmp_path):
    """§8.2.5: a fresher receipt exists, and the named stale one still fails."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    stale = _receipt(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW,
                       reopen_reason="a regression landed")
    _receipt(mk, operation_id="fresh-run-2")  # a fresh receipt is available

    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                           receipt_id=stale["record_id"])
    assert excinfo.value.code == "E_EVIDENCE_STALE"
    assert _gate_of(mk)["status"] == "pending"


def test_an_unknown_receipt_id_is_evidence_required(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _receipt(mk)
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                           receipt_id="f" * 32)
    assert excinfo.value.code == "E_EVIDENCE_REQUIRED"


# --------------------------------------------------------------------------
# Reopen and the failed → passed flip (§8.3 I-E3, I-E4)
# --------------------------------------------------------------------------

def test_reopen_requires_a_reopen_reason(tmp_path):
    """I-E3."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _receipt(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    before = len(_lines(mk))
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW)
    assert excinfo.value.code == "E_MISSING_FIELD"
    assert len(_lines(mk)) == before


def test_failed_to_passed_requires_a_fresh_receipt_and_a_reopen_reason(tmp_path):
    """I-E4: a failed gate cannot be flipped silently."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    _receipt(mk, verdict="fail")
    mk.gate_transition(GOAL_ID, "tests-green", "failed", now=NOW)

    with pytest.raises(Exception) as excinfo:  # no pass receipt at all
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                           reopen_reason="fixed the regression")
    assert excinfo.value.code == "E_EVIDENCE_REQUIRED"

    _receipt(mk, operation_id="fix-run")
    with pytest.raises(Exception) as excinfo:  # receipt now fresh, reason missing
        mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    assert excinfo.value.code == "E_MISSING_FIELD"

    result = mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                                reopen_reason="fixed the regression")
    assert result["gate_status"] == "passed"


def test_waived_is_absorbing(tmp_path):
    """§4.6: every outbound transition from ``waived`` is rejected."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    mk.gate_transition(GOAL_ID, "tests-green", "waived", now=NOW,
                       authority_claim="human")
    before = len(_lines(mk))
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "pending", now=NOW,
                           reopen_reason="changed my mind")
    assert excinfo.value.code == "E_INVALID_TRANSITION"
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# EV-03 — waiving is allowed, unverified, and counted (§4.8, I-E5)
# --------------------------------------------------------------------------

def test_waive_requires_a_human_authority_claim(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk)
    before = len(_lines(mk))
    with pytest.raises(Exception) as excinfo:
        mk.gate_transition(GOAL_ID, "tests-green", "waived", now=NOW)
    assert excinfo.value.code == "E_AUTHORITY_CLAIM_REQUIRED"
    assert len(_lines(mk)) == before


def test_waive_with_a_human_claim_succeeds_and_counts_as_unverified(tmp_path):
    """EV-03: the claim is a string nobody checked, so the projection says so."""
    mk = _mk(tmp_path)
    _declare_gate(mk)
    result = mk.gate_transition(GOAL_ID, "tests-green", "waived", now=NOW,
                                authority_claim="human")
    assert result["gate_status"] == "waived"
    assert "W_WAIVER_UNVERIFIED" in result["warnings"]
    projection = _state(mk)
    assert projection["unverified_waivers"] == 1
    assert projection["gates"][0]["status"] == "waived"


# --------------------------------------------------------------------------
# EV-04 — a goal is not satisfied while a required gate is pending (I-E7)
# --------------------------------------------------------------------------

def test_satisfy_is_blocked_by_a_pending_required_gate_and_lists_blockers(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk, "tests-green")
    _declare_gate(mk, "docs-written")
    _receipt(mk, "tests-green")
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)
    before = len(_lines(mk))

    with pytest.raises(Exception) as excinfo:
        mk.goal_transition(GOAL_ID, "satisfied", now=NOW, reason="shipping")

    assert excinfo.value.code == "E_INVALID_TRANSITION"
    assert excinfo.value.details["blockers"] == ["docs-written"]
    assert len(_lines(mk)) == before
    assert _state(mk)["goal_status"] == "open"


def test_an_optional_pending_gate_does_not_block_satisfy(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk, "tests-green")
    _declare_gate(mk, "docs-written", required=False)
    _receipt(mk, "tests-green")
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW)

    result = mk.goal_transition(GOAL_ID, "satisfied", now=NOW, reason="shipping")
    assert result["goal_status"] == "satisfied"


def test_a_waived_required_gate_does_not_block_satisfy(tmp_path):
    """§4.8 stated plainly: any caller can satisfy any goal by waiving."""
    mk = _mk(tmp_path)
    _declare_gate(mk, "tests-green")
    mk.gate_transition(GOAL_ID, "tests-green", "waived", now=NOW,
                       authority_claim="human")
    assert mk.goal_transition(GOAL_ID, "satisfied", now=NOW,
                              reason="shipping")["goal_status"] == "satisfied"


def test_abandon_requires_a_reason(tmp_path):
    mk = _mk(tmp_path)
    before = len(_lines(mk))
    with pytest.raises(Exception) as excinfo:
        mk.goal_transition(GOAL_ID, "abandoned", now=NOW)
    assert excinfo.value.code == "E_MISSING_FIELD"
    assert len(_lines(mk)) == before
    assert mk.goal_transition(GOAL_ID, "abandoned", now=NOW,
                              reason="descoped")["goal_status"] == "abandoned"


def test_a_transition_on_an_undeclared_goal_is_not_declared(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    with pytest.raises(Exception) as excinfo:
        mk.goal_transition(GOAL_ID, "abandoned", now=NOW, reason="descoped")
    assert excinfo.value.code == "E_NOT_DECLARED"


# --------------------------------------------------------------------------
# EV-05 — caps (§4.9, I-E8)
# --------------------------------------------------------------------------

def test_the_sixty_fifth_gate_is_rejected_and_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    for index in range(64):
        _declare_gate(mk, "gate-%02d" % index)
    before = len(_lines(mk))

    with pytest.raises(Exception) as excinfo:
        _declare_gate(mk, "gate-64")

    assert excinfo.value.code == "E_GATE_CAP"
    assert excinfo.value.error_class == "limits"
    assert len(_lines(mk)) == before
