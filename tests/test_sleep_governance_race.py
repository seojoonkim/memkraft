"""Regression tests for sleep plan/apply race handling."""

from memkraft import MemKraft


def test_sleep_apply_replans_after_acquiring_governance_lock(tmp_path, monkeypatch):
    """An event arriving between preview and lock acquisition must not be omitted."""
    memory = MemKraft(base_dir=str(tmp_path))
    memory.append_event("u", "a", 1, source="test")
    original_lock = memory._governance_lock
    inserted = False

    def lock_after_concurrent_append():
        nonlocal inserted
        if not inserted:
            inserted = True
            memory.append_event("u", "b", 2, source="test")
        return original_lock()

    monkeypatch.setattr(memory, "_governance_lock", lock_after_concurrent_append)
    applied = memory.sleep(dry_run=False)

    assert applied["record_count"] == 2
    assert memory.current_truth("u") == {"a": 1, "b": 2}
    assert memory.sleep()["transaction_id"] == applied["transaction_id"]
