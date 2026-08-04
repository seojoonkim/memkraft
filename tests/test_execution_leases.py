"""Slice 6 — leases, fencing, and protected mutations (plan §7, §4.6, §4.9).

A lease here is deliberately *not* a state machine. It is a projection: a grant
is valid iff no later release and no superseding grant apply and the injected
``now`` has not reached its ``expires_at``. Nothing is ever appended to record
an expiry, which is what keeps expiry a pure function of ``now`` and keeps a
crashed holder from wedging a scope forever.

``fence_token`` is an **output**. Requiring it as an input on a first
acquisition would ask the caller for a value it cannot know, so acquisition
returns it and every lease-protected mutation presents it back. The invariant
these tests pin is the one that matters: a stale holder cannot write into a
scope that has been re-leased, and a rejected write appends nothing.
"""
from __future__ import annotations

import threading

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_projection import project_leases
from memkraft.execution_protocol import ExecutionError, digest


NOW = "2026-08-04T11:22:33Z"
LATER = "2026-08-04T11:32:33Z"           # NOW + 600s
GOAL_ID = "hermes/release-3-3-0"
SCOPE = "tests-green"
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


def _records(mk):
    return store_core.read_all(mk._execution_events_path(),
                               include_tombstoned=True).records


def _leases(mk, now=NOW):
    return project_leases(_records(mk), now, GOAL_ID)


def _acquire(mk, *, scope_key=SCOPE, holder="worker-a", ttl_seconds=600, now=NOW,
             **overrides):
    return mk.lease_acquire(GOAL_ID, scope_key, holder, ttl_seconds, now=now,
                            **overrides)


def _code(excinfo):
    return excinfo.value.code


# --------------------------------------------------------------------------
# Acquisition, fence as an output (§7.1)
# --------------------------------------------------------------------------

def test_acquire_returns_the_fence_token_rather_than_demanding_one(tmp_path):
    mk = _mk(tmp_path)
    result = _acquire(mk)

    assert result["outcome"] == "applied"
    assert result["fence_token"] == 1
    assert result["lease_id"] == result["record_id"]
    assert result["expires_at"] == "2026-08-04T11:32:33Z"
    assert result["supersedes_lease_id"] is None


def test_distinct_scopes_are_held_in_parallel(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk, scope_key="tests-green", holder="worker-a")
    second = _acquire(mk, scope_key="docs-built", holder="worker-b")

    assert first["fence_token"] == 1 and second["fence_token"] == 2
    active = _leases(mk)["active"]
    assert sorted(active) == ["docs-built", "tests-green"]


def test_a_held_scope_rejects_another_holder_and_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, holder="worker-a")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _acquire(mk, holder="worker-b")

    assert _code(excinfo) == "E_LEASE_HELD"
    assert excinfo.value.retryable is True
    assert len(_lines(mk)) == before


def test_lease_held_returns_a_holder_digest_and_never_the_holder(tmp_path):
    """SC-02: leaking a runtime-minted holder to a competitor is disclosure."""
    mk = _mk(tmp_path)
    _acquire(mk, holder="worker-a")

    with pytest.raises(ExecutionError) as excinfo:
        _acquire(mk, holder="worker-b")

    details = excinfo.value.details
    assert details["holder_digest"] == digest({"holder": "worker-a"})
    assert "worker-a" not in repr(details)
    assert "worker-a" not in str(excinfo.value)


def test_renewal_by_the_same_holder_supersedes_with_a_higher_fence(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk)
    second = _acquire(mk, operation_id="b" * 64)

    assert second["outcome"] == "applied"
    assert second["fence_token"] > first["fence_token"]
    assert second["supersedes_lease_id"] == first["lease_id"]
    assert second["supersede_reason"] == "released_by_holder"
    assert list(_leases(mk)["active"]) == [SCOPE]


def test_replayed_acquire_appends_nothing_and_does_not_refresh_the_ttl(tmp_path):
    """§6.6: ``already_applied`` must not refresh a lease. A renewal is a new op."""
    mk = _mk(tmp_path)
    first = _acquire(mk, operation_id="c" * 64)
    before = len(_lines(mk))

    replay = _acquire(mk, operation_id="c" * 64)

    assert replay["outcome"] == "already_applied"
    assert replay["record_id"] == first["record_id"]
    assert len(_lines(mk)) == before


def test_expected_fence_mismatch_is_rejected_with_zero_append(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _acquire(mk, operation_id="d" * 64, expected_fence=99)

    assert _code(excinfo) == "E_FENCE_STALE"
    assert len(_lines(mk)) == before


def test_the_seventeenth_active_lease_is_refused(tmp_path):
    mk = _mk(tmp_path)
    for index in range(16):
        _acquire(mk, scope_key="scope-%02d" % index)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _acquire(mk, scope_key="scope-16")

    assert _code(excinfo) == "E_LEASE_CAP"
    assert len(_lines(mk)) == before


@pytest.mark.parametrize("ttl_seconds", [0, -1, 86401])
def test_ttl_outside_its_range_is_refused(tmp_path, ttl_seconds):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError):
        _acquire(mk, ttl_seconds=ttl_seconds)
    assert _lines(mk)


# --------------------------------------------------------------------------
# Expiry is a projection of ``now``, never an appended transition (§4.6, §7.2)
# --------------------------------------------------------------------------

def test_expiry_is_derived_from_the_injected_now_and_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, ttl_seconds=600)
    before = len(_lines(mk))

    assert list(_leases(mk, now=NOW)["active"]) == [SCOPE]
    assert _leases(mk, now="2026-08-04T11:32:32Z")["active"]
    assert _leases(mk, now=LATER)["active"] == {}      # expires_at is exclusive
    assert len(_lines(mk)) == before


def test_expired_lease_is_reclaimed_as_exactly_one_grant(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk, holder="worker-a", ttl_seconds=600)
    before = len(_lines(mk))

    reclaim = mk.lease_acquire(GOAL_ID, SCOPE, "worker-b", 600, now=LATER)

    assert len(_lines(mk)) == before + 1
    assert reclaim["supersedes_lease_id"] == first["lease_id"]
    assert reclaim["supersede_reason"] == "expired"
    assert reclaim["fence_token"] > first["fence_token"]
    assert [r["record_type"] for r in _records(mk)].count("lease_grant") == 2


def test_released_lease_is_reclaimed_with_the_released_by_holder_reason(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk, holder="worker-a")
    mk.lease_release(GOAL_ID, SCOPE, first["lease_id"], now=NOW)

    reclaim = _acquire(mk, holder="worker-b")

    assert reclaim["supersedes_lease_id"] == first["lease_id"]
    assert reclaim["supersede_reason"] == "released_by_holder"


def test_an_unexpired_lease_cannot_be_reclaimed(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, holder="worker-a", ttl_seconds=600)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.lease_acquire(GOAL_ID, SCOPE, "worker-b", 600,
                         now="2026-08-04T11:32:32Z")

    assert _code(excinfo) == "E_LEASE_HELD"
    assert len(_lines(mk)) == before


def test_revoked_is_not_a_supersede_reason_anywhere(tmp_path):
    """D-18: a dead enum value invites someone to invent semantics for it."""
    from memkraft import execution_state

    assert "revoked" not in execution_state.SUPERSEDE_REASONS
    assert set(execution_state.SUPERSEDE_REASONS) == {"expired", "released_by_holder"}


def test_expired_is_not_a_record_type(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, ttl_seconds=600)
    _leases(mk, now=LATER)

    assert {r["record_type"] for r in _records(mk)} <= {
        "goal_declared", "lease_grant", "lease_release",
    }


# --------------------------------------------------------------------------
# Release (§7.2)
# --------------------------------------------------------------------------

def test_release_ends_the_lease_with_one_appended_line(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk)
    before = len(_lines(mk))

    result = mk.lease_release(GOAL_ID, SCOPE, first["lease_id"], now=NOW,
                              released_by="worker-a")

    assert result["outcome"] == "applied"
    assert len(_lines(mk)) == before + 1
    assert _leases(mk)["active"] == {}


def test_release_by_the_wrong_holder_is_refused_with_zero_append(tmp_path):
    mk = _mk(tmp_path)
    first = _acquire(mk, holder="worker-a")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.lease_release(GOAL_ID, SCOPE, first["lease_id"], now=NOW,
                         released_by="worker-b")

    assert _code(excinfo) == "E_LEASE_HELD"
    assert len(_lines(mk)) == before


def test_release_of_a_superseded_lease_id_is_refused_with_zero_append(tmp_path):
    mk = _mk(tmp_path)
    stale = _acquire(mk)
    _acquire(mk, operation_id="e" * 64)          # renew; ``stale`` is superseded
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.lease_release(GOAL_ID, SCOPE, stale["lease_id"], now=NOW)

    assert _code(excinfo) == "E_FENCE_STALE"
    assert len(_lines(mk)) == before


def test_release_on_an_unleased_scope_is_refused(tmp_path):
    mk = _mk(tmp_path)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.lease_release(GOAL_ID, SCOPE, "01JKX7Q2M0000000000000000A", now=NOW)

    assert _code(excinfo) == "E_CONFLICT"
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# Fence-protected mutations (§7.3)
# --------------------------------------------------------------------------

def _declare_gate(mk, gate_id="tests-green", **overrides):
    return mk.gate_declare(GOAL_ID, gate_id, "the suite is green", VERIFICATION,
                           now=NOW, **overrides)


def test_fence_on_an_unleased_scope_is_a_hard_error(tmp_path):
    """Rule 1: a token that means nothing must not be cargo-culted into a write."""
    mk = _mk(tmp_path)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _declare_gate(mk, fence_token=1)

    assert _code(excinfo) == "E_UNKNOWN_FIELD"
    assert len(_lines(mk)) == before


def test_a_leased_scope_refuses_a_write_without_a_fence(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, scope_key="tests-green")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _declare_gate(mk)

    assert _code(excinfo) == "E_FENCE_REQUIRED"
    assert len(_lines(mk)) == before


def test_a_leased_scope_accepts_the_returned_fence(tmp_path):
    mk = _mk(tmp_path)
    lease = _acquire(mk, scope_key="tests-green")
    before = len(_lines(mk))

    _declare_gate(mk, fence_token=lease["fence_token"])

    assert len(_lines(mk)) == before + 1


def test_a_stale_holder_cannot_write_into_a_re_leased_scope(tmp_path):
    """The whole point of fencing, stated as one test."""
    mk = _mk(tmp_path)
    stale = _acquire(mk, scope_key="tests-green", holder="worker-a")
    _acquire(mk, scope_key="tests-green", holder="worker-b", now=LATER)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _declare_gate(mk, fence_token=stale["fence_token"])

    assert _code(excinfo) == "E_FENCE_STALE"
    assert excinfo.value.details["scope_key"] == "tests-green"
    assert excinfo.value.details["current_fence_token"] == 2
    assert len(_lines(mk)) == before


def test_goal_transition_is_fenced_under_the_literal_goal_scope(tmp_path):
    mk = _mk(tmp_path)
    lease = _acquire(mk, scope_key="goal")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.goal_transition(GOAL_ID, "abandoned", now=NOW, reason="superseded")
    assert _code(excinfo) == "E_FENCE_REQUIRED"
    assert len(_lines(mk)) == before

    mk.goal_transition(GOAL_ID, "abandoned", now=NOW, reason="superseded",
                       fence_token=lease["fence_token"])
    assert len(_lines(mk)) == before + 1


def test_gate_writes_are_fenced_under_the_gates_declared_scope(tmp_path):
    mk = _mk(tmp_path)
    _declare_gate(mk, scope_key="ci")
    lease = _acquire(mk, scope_key="ci")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.receipt_record(GOAL_ID, "tests-green", "pass", SHA, "green", now=NOW)
    assert _code(excinfo) == "E_FENCE_REQUIRED"

    mk.receipt_record(GOAL_ID, "tests-green", "pass", SHA, "green", now=NOW,
                      fence_token=lease["fence_token"])
    mk.gate_transition(GOAL_ID, "tests-green", "passed", now=NOW,
                       fence_token=lease["fence_token"])
    assert len(_lines(mk)) == before + 2


def test_a_lease_on_one_scope_does_not_fence_another(tmp_path):
    mk = _mk(tmp_path)
    _acquire(mk, scope_key="docs-built")

    _declare_gate(mk)                     # scope "tests-green" is unleased

    assert _leases(mk)["max_fence"] == 1


# --------------------------------------------------------------------------
# Bounded, non-blocking locking (§7.4)
# --------------------------------------------------------------------------

def test_a_contended_lock_returns_the_busy_error_rather_than_waiting(tmp_path,
                                                                    monkeypatch):
    from memkraft import execution_state

    monkeypatch.setattr(execution_state, "EXECUTION_LOCK_TIMEOUT_S", 0.1)
    mk = _mk(tmp_path)
    before = len(_lines(mk))

    holding = threading.Event()
    release = threading.Event()

    def hold():
        fd = mk._governance_lock()
        holding.set()
        release.wait(10)
        store_core._unlock(fd)
        import os
        os.close(fd)

    worker = threading.Thread(target=hold)
    worker.start()
    try:
        holding.wait(10)
        with pytest.raises(ExecutionError) as excinfo:
            _acquire(mk)
        assert _code(excinfo) == "E_STORE_BUSY"
        assert excinfo.value.retryable is True
    finally:
        release.set()
        worker.join(10)

    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# Concurrency (G4, G6)
# --------------------------------------------------------------------------

WORKERS = 16
ROUNDS = 10


def test_concurrent_acquire(tmp_path):
    """Exactly one winner per contested scope, and a strictly rising fence."""
    mk = _mk(tmp_path)
    winners = []
    errors = []
    lock = threading.Lock()

    for round_index in range(ROUNDS):
        scope_key = "round-%02d" % round_index
        start = threading.Barrier(WORKERS)

        def contend(worker_index, scope_key=scope_key):
            client = MemKraft(base_dir=str(tmp_path))
            start.wait(30)
            try:
                result = client.lease_acquire(
                    GOAL_ID, scope_key, "worker-%02d" % worker_index, 600, now=NOW,
                )
            except ExecutionError as error:
                with lock:
                    errors.append(error.code)
                return
            with lock:
                winners.append(result)

        threads = [threading.Thread(target=contend, args=(index,))
                   for index in range(WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)

    assert len(winners) == ROUNDS
    assert errors == ["E_LEASE_HELD"] * (ROUNDS * (WORKERS - 1))

    fences = [winner["fence_token"] for winner in winners]
    assert fences == sorted(fences) and len(set(fences)) == len(fences)

    active = _leases(mk)["active"]
    assert len(active) == ROUNDS
    for winner in winners:
        assert active[winner["scope_key"]]["lease_id"] == winner["lease_id"]
