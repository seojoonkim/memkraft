"""Slice 7 — goal deletion and the batch tombstone path (plan §14.4, D-24).

Deleting a card does not delete a goal; ``forget({"goal_id": ...})`` is the
explicit act. The cost is the point of this slice: ``mark_tombstone`` performs a
full ``read_all(include_tombstoned=True)`` per call, so tombstoning a goal
record-by-record is O(n²) and unusable at scale. The batch path reads once and
appends every marker under a single lock acquisition.

Batching a mutation means the prevalidation has to be all-or-nothing. A batch
that discovers a missing target halfway through and leaves the first half
tombstoned is worse than no batch at all, so the target set is validated in full
before a single byte is written.
"""
from __future__ import annotations

import time

import pytest

from memkraft import MemKraft, store_core


NOW = "2026-08-04T11:22:33Z"
GOAL_ID = "hermes/release-3-3-0"
OTHER_GOAL_ID = "hermes/release-3-4-0"
SHA = "a" * 64

GOAL = dict(
    title="Release 3.3.0",
    intent="Ship the execution kernel",
    constraints=["stdlib only"],
    success_criteria=["suite green"],
)

VERIFICATION = {"check_kind": "command", "check_ref": "pytest -q"}


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    mk.goal_declare(goal_id=GOAL_ID, now=NOW, **GOAL)
    return mk


def _records(mk, include_tombstoned=True):
    return store_core.read_all(mk._execution_events_path(),
                               include_tombstoned=include_tombstoned).records


def _live_ids(mk):
    return {record["id"] for record in _records(mk, include_tombstoned=False)}


def _populate(mk, goal_id=GOAL_ID, gates=3):
    """Declare ``gates`` gates with a receipt each, and return every record id."""
    for index in range(gates):
        gate_id = "gate-%02d" % index
        mk.gate_declare(goal_id, gate_id, "check %d" % index, VERIFICATION, now=NOW)
        mk.receipt_record(goal_id, gate_id, "pass", SHA, "green", now=NOW,
                          provenance_id="run-%d" % index)
    return sorted(record["id"] for record in _records(mk)
                  if record.get("goal_id") == goal_id)


# --------------------------------------------------------------------------
# store_core.mark_tombstones — the batch primitive (D-24)
# --------------------------------------------------------------------------

def test_batch_tombstone_appends_one_marker_per_target(tmp_path):
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(5)]

    markers = store_core.mark_tombstones(path, ids)

    assert len(markers) == 5
    assert sorted(marker["tombstone_of"] for marker in markers) == sorted(ids)
    assert store_core.read_all(path).records == []


def test_batch_tombstone_reads_the_store_exactly_once(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(20)]

    calls = []
    original = store_core.read_all

    def counting(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(store_core, "read_all", counting)
    store_core.mark_tombstones(path, ids)

    assert len(calls) == 1


def test_batch_tombstone_takes_one_lock_and_syncs_once(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(20)]

    locks = []
    syncs = []
    original_lock = store_core._lock_current_inode
    original_fsync = store_core.os.fsync

    monkeypatch.setattr(store_core, "_lock_current_inode",
                        lambda *a, **k: (locks.append(a[0]), original_lock(*a, **k))[1])
    monkeypatch.setattr(store_core.os, "fsync",
                        lambda fd: (syncs.append(fd), original_fsync(fd))[1])

    store_core.mark_tombstones(path, ids)

    assert len(locks) == 1
    assert len(syncs) == 1


def test_batch_tombstone_targets_are_sorted_and_deduplicated(tmp_path):
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(5)]

    markers = store_core.mark_tombstones(path, list(reversed(ids)) + ids)

    assert [marker["tombstone_of"] for marker in markers] == sorted(ids)


def test_batch_tombstone_prevalidates_all_or_nothing(tmp_path):
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(5)]
    before = path.read_bytes()

    with pytest.raises(store_core.RecordNotFoundError):
        store_core.mark_tombstones(path, ids + ["deadbeef"])

    assert path.read_bytes() == before
    assert len(_live(path)) == 5


def _live(path):
    return store_core.read_all(path).records


def test_batch_tombstone_of_nothing_writes_nothing(tmp_path):
    path = tmp_path / "events.jsonl"
    store_core.append(path, {"n": 0})
    before = path.read_bytes()

    assert store_core.mark_tombstones(path, []) == []
    assert path.read_bytes() == before


def test_batch_tombstone_is_linear_at_ten_thousand_targets(tmp_path):
    """The D-24 gate: record-by-record tombstoning here would be O(n²)."""
    path = tmp_path / "events.jsonl"
    ids = [store_core.append(path, {"n": n})["id"] for n in range(10_000)]

    started = time.monotonic()
    markers = store_core.mark_tombstones(path, ids)
    elapsed = time.monotonic() - started

    assert len(markers) == 10_000
    assert _live(path) == []
    assert elapsed < 30.0, "batch tombstone of 10k targets took %.1fs" % elapsed


# --------------------------------------------------------------------------
# forget({"goal_id": ...}) — the explicit act (§14.4)
# --------------------------------------------------------------------------

def test_forget_goal_previews_without_writing(tmp_path):
    mk = _mk(tmp_path)
    ids = _populate(mk)
    before = mk._execution_events_path().read_bytes()

    plan = mk.forget({"goal_id": GOAL_ID})

    assert plan["status"] == "planned"
    assert plan["dry_run"] is True
    assert plan["matched"] == len(ids)
    assert plan["record_ids"] == ids
    assert mk._execution_events_path().read_bytes() == before


def test_forget_goal_tombstones_every_record_of_that_goal(tmp_path):
    mk = _mk(tmp_path)
    ids = _populate(mk)

    result = mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert result["status"] == "applied"
    assert result["matched"] == len(ids)
    assert _live_ids(mk) == set()


def test_forget_goal_leaves_other_goals_untouched(tmp_path):
    mk = _mk(tmp_path)
    _populate(mk)
    mk.goal_declare(goal_id=OTHER_GOAL_ID, now=NOW, **GOAL)
    survivors = _populate(mk, OTHER_GOAL_ID)

    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert _live_ids(mk) == set(survivors)


def test_forget_goal_record_ids_are_deterministically_sorted(tmp_path):
    mk = _mk(tmp_path)
    ids = _populate(mk)
    result = mk.forget({"goal_id": GOAL_ID}, dry_run=False)
    assert result["record_ids"] == sorted(ids) == ids


def test_forget_goal_appends_exactly_one_marker_per_record(tmp_path):
    mk = _mk(tmp_path)
    ids = _populate(mk)
    before = len(_records(mk))

    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert len(_records(mk)) == before + len(ids)


def test_forget_goal_uses_one_governance_lock(tmp_path, monkeypatch):
    mk = _mk(tmp_path)
    _populate(mk)

    locks = []
    original = type(mk)._governance_lock

    def counting(self, *args, **kwargs):
        locks.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(mk), "_governance_lock", counting)
    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert len(locks) == 1


def test_forget_goal_writes_one_audit_record(tmp_path):
    mk = _mk(tmp_path)
    _populate(mk)
    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    audit = [row for row in store_core.read_all(mk._audit_path()).records
             if row.get("action") == "forget"]
    assert len(audit) == 1
    assert audit[0]["target"] == {"goal_id": GOAL_ID}


def test_forgetting_an_unknown_goal_is_not_found(tmp_path):
    mk = _mk(tmp_path)
    before = mk._execution_events_path().read_bytes()

    result = mk.forget({"goal_id": OTHER_GOAL_ID}, dry_run=False)

    assert result["status"] == "not_found"
    assert result["record_ids"] == []
    assert mk._execution_events_path().read_bytes() == before


def test_forgetting_a_forgotten_goal_is_idempotent(tmp_path):
    mk = _mk(tmp_path)
    _populate(mk)
    mk.forget({"goal_id": GOAL_ID}, dry_run=False)
    before = len(_records(mk))

    result = mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert result["status"] == "already_forgotten"
    assert len(_records(mk)) == before


def test_a_malformed_goal_id_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ValueError):
        mk.forget({"goal_id": "Not A Goal"}, dry_run=False)


def test_forgetting_a_goal_does_not_touch_the_memory_store(tmp_path):
    """Two stores, two selectors: a goal is not a memory event (§14.4)."""
    mk = _mk(tmp_path)
    _populate(mk)
    mk.append_event("alice", "role", "engineer", source="test")
    before = mk._canonical_events_path().read_bytes()

    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert mk._canonical_events_path().read_bytes() == before


def test_the_subject_selector_still_works(tmp_path):
    """The existing selector is untouched by the new one."""
    mk = _mk(tmp_path)
    mk.append_event("alice", "role", "engineer", source="test")

    result = mk.forget({"subject": "alice"}, dry_run=False)

    assert result["status"] == "applied"
    assert store_core.read_all(mk._canonical_events_path()).records == []
