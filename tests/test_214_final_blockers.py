"""Final 2.14 review-blocker regressions (security and interleavings)."""
import threading

import pytest

from memkraft import MemKraft
from memkraft.store_core import append, read_all


def mk(path):
    return MemKraft(base_dir=str(path))


def test_stale_compiled_newer_tombstone_never_falls_back_to_same_key(tmp_path):
    m = mk(tmp_path)
    m.append_event("u", "role", "old", source="s", valid_from="2025-01-01")
    newer = m.append_event("u", "role", "secret", source="s", valid_from="2026-01-01")
    m.compile_truth(False)
    # Simulate a crash after canonical tombstone but before derived rebuild.
    from memkraft.store_core import mark_tombstone
    mark_tombstone(m._canonical_events_path(), newer["id"])
    assert m.current_truth("u") == {}


def test_append_policy_check_and_write_share_governance_lock(tmp_path, monkeypatch):
    m = mk(tmp_path)
    policy_written = threading.Event()
    release = threading.Event()
    original_append = append

    def paused_append(path, row):
        result = original_append(path, row)
        if path == m._policies_path():
            policy_written.set()
            release.wait(2)
        return result

    monkeypatch.setattr("memkraft.derived_views.append", paused_append)
    policy = threading.Thread(target=lambda: m.do_not_remember(subject="u", key="secret", dry_run=False))
    policy.start()
    assert policy_written.wait(2)
    writer = threading.Thread(target=lambda: pytest.raises(PermissionError, m.append_event, "u", "secret", "x", "s"))
    writer.start()
    assert writer.is_alive()  # blocked behind policy mutation + audit
    release.set()
    writer.join(2); policy.join(2)
    assert not writer.is_alive() and not policy.is_alive()
    assert read_all(m._canonical_events_path(), True).records == []


def test_forget_selector_is_recomputed_under_lock(tmp_path, monkeypatch):
    m = mk(tmp_path)
    m.append_event("u", "x", 1, source="s")
    original = m._governance_lock
    inserted = False
    def lock_after_append():
        nonlocal inserted
        if not inserted:
            inserted = True
            # bypass public lock only to model append completed before acquisition
            append(m._canonical_events_path(), {"subject_id":"u", "key":"x", "value":2, "source":"s"})
        return original()
    monkeypatch.setattr(m, "_governance_lock", lock_after_append)
    result = m.forget({"subject":"u", "key":"x"}, dry_run=False)
    assert result["matched"] == 2
    assert m.export_memory() == []


def test_policy_retry_repairs_missing_audit_idempotently(tmp_path):
    m = mk(tmp_path)
    plan = m.do_not_remember(subject="u", key="x")
    append(m._policies_path(), plan["policy"])
    assert m.audit_log(action="do_not_remember") == []
    assert m.do_not_remember(subject="u", key="x", dry_run=False)["status"] == "already_applied"
    rows = m.audit_log(action="do_not_remember")
    assert len(rows) == 1 and rows[0]["operation_id"] == plan["policy"]["id"]
    m.do_not_remember(subject="u", key="x", dry_run=False)
    assert len(m.audit_log(action="do_not_remember")) == 1


def test_forget_retry_repairs_missing_audit_idempotently(tmp_path):
    m = mk(tmp_path); event = m.append_event("u", "x", 1, source="s")
    from memkraft.store_core import mark_tombstone
    mark_tombstone(m._canonical_events_path(), event["id"])
    assert m.audit_log(action="forget") == []
    assert m.forget(event["id"], dry_run=False)["status"] == "already_forgotten"
    rows = m.audit_log(action="forget")
    assert len(rows) == 1 and rows[0]["operation_id"]
    m.forget(event["id"], dry_run=False)
    assert len(m.audit_log(action="forget")) == 1


def test_forget_selector_retry_after_partial_tombstones_uses_complete_stable_set(tmp_path, monkeypatch):
    m = mk(tmp_path)
    events = [m.append_event("u", "x", value, source="s") for value in (1, 2, 3)]
    selector = {"subject": "u", "key": "x"}
    original = __import__("memkraft.derived_views", fromlist=["mark_tombstone"]).mark_tombstone
    calls = 0

    def fail_after_first(path, record_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated partial tombstone failure")
        return original(path, record_id)

    monkeypatch.setattr("memkraft.derived_views.mark_tombstone", fail_after_first)
    with pytest.raises(OSError, match="simulated partial tombstone failure"):
        m.forget(selector, dry_run=False)
    assert m.audit_log(action="forget") == []

    monkeypatch.setattr("memkraft.derived_views.mark_tombstone", original)
    repaired = m.forget(selector, dry_run=False)
    expected_ids = sorted(event["id"] for event in events)
    assert repaired["status"] == "applied"
    assert sorted(repaired["record_ids"]) == expected_ids

    all_rows = read_all(m._canonical_events_path(), True).records
    markers = [row for row in all_rows if row.get("tombstone") is True]
    assert sorted(row["tombstone_of"] for row in markers) == expected_ids
    assert len(markers) == len(events)

    audits = m.audit_log(action="forget")
    assert len(audits) == 1
    assert sorted(audits[0]["record_ids"]) == expected_ids
    expected_operation_id = __import__("memkraft.derived_views", fromlist=["_digest"])._digest(
        {"action": "forget", "record_ids": expected_ids}
    )
    assert audits[0]["operation_id"] == expected_operation_id

    again = m.forget(selector, dry_run=False)
    assert again["status"] == "already_forgotten"
    assert sorted(again["record_ids"]) == expected_ids
    assert len([r for r in read_all(m._canonical_events_path(), True).records if r.get("tombstone") is True]) == len(events)
    assert len(m.audit_log(action="forget")) == 1


def test_forget_selector_retry_repairs_audit_without_duplicate_tombstones(tmp_path, monkeypatch):
    m = mk(tmp_path)
    events = [m.append_event("u", "x", value, source="s") for value in (1, 2)]
    original = m._append_audit
    calls = 0

    def fail_first_audit(row):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated audit append failure")
        return original(row)

    monkeypatch.setattr(m, "_append_audit", fail_first_audit)
    selector = {"subject": "u", "key": "x"}
    with pytest.raises(OSError, match="simulated audit append failure"):
        m.forget(selector, dry_run=False)

    after_failure = read_all(m._canonical_events_path(), True).records
    markers = [r for r in after_failure if r.get("tombstone") is True]
    assert sorted(r["tombstone_of"] for r in markers) == sorted(e["id"] for e in events)
    assert m.audit_log(action="forget") == []

    repaired = m.forget(selector, dry_run=False)
    assert repaired["status"] == "already_forgotten"
    assert sorted(repaired["record_ids"]) == sorted(e["id"] for e in events)
    assert len([r for r in read_all(m._canonical_events_path(), True).records if r.get("tombstone") is True]) == 2
    audits = m.audit_log(action="forget")
    assert len(audits) == 1
    assert sorted(audits[0]["record_ids"]) == sorted(e["id"] for e in events)

    m.forget(selector, dry_run=False)
    assert len([r for r in read_all(m._canonical_events_path(), True).records if r.get("tombstone") is True]) == 2
    assert len(m.audit_log(action="forget")) == 1


def test_sleep_lock_snapshot_blocks_public_append_until_journal(tmp_path, monkeypatch):
    m = mk(tmp_path); m.append_event("u", "a", 1, source="s")
    compiling = threading.Event(); release = threading.Event()
    original = m.compile_truth
    def paused(dry_run=True):
        if dry_run is False:
            compiling.set(); release.wait(2)
        return original(dry_run)
    monkeypatch.setattr(m, "compile_truth", paused)
    sleeper = threading.Thread(target=lambda: m.sleep(dry_run=False)); sleeper.start()
    assert compiling.wait(2)
    writer = threading.Thread(target=lambda: m.append_event("u", "b", 2, source="s")); writer.start()
    assert writer.is_alive()
    release.set(); sleeper.join(2); writer.join(2)
    assert len(m.audit_log(action="sleep")) == 1
    assert m.current_truth("u") == {"a": 1}


def test_windows_locking_path_smoke_without_real_msvcrt(monkeypatch, tmp_path):
    import memkraft.store_core as core
    calls = []
    class Fake:
        LK_LOCK=1; LK_UNLCK=2
        @staticmethod
        def locking(fd, mode, count): calls.append((mode, count))
    monkeypatch.setattr(core, "fcntl", None)
    monkeypatch.setattr(core, "msvcrt", Fake, raising=False)
    p = tmp_path / "windows.jsonl"
    core.append(p, {"x": 1})
    assert [c[0] for c in calls] == [Fake.LK_LOCK, Fake.LK_UNLCK]
    assert core.read_all(p).records[0]["x"] == 1
