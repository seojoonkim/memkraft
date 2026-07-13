import json
import multiprocessing
import os
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.store_core import read_all


def _mc(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


def _compile_worker(base_dir, gate, queue):
    gate.wait()
    try:
        MemKraft(base_dir=base_dir).compile_truth(dry_run=False)
        queue.put(None)
    except BaseException as exc:
        queue.put(repr(exc))


def test_append_requires_source_and_is_canonical_append_only(tmp_path):
    mc = _mc(tmp_path)
    with pytest.raises(ValueError, match="source"):
        mc.append_event("user", "city", "Seoul")

    first = mc.append_event("user", "city", "Seoul", source="chat", valid_from="2026-01-01")
    second = mc.append_event("user", "city", "Busan", provenance="import", valid_from="2026-02-01")
    path = tmp_path / ".memkraft" / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [first, second]
    assert first["source"] == "chat"
    assert second["source"] == "import"


def test_compile_truth_dry_run_is_exact_deterministic_plan_and_does_not_write(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("user", "city", "new append", source="chat", valid_from="2026-01-01")
    mc.append_event("user", "city", "older date", source="chat", valid_from="2025-01-01")
    mc.append_event("user", "city", "tie wins", source="chat", valid_from="2026-01-01")
    events = tmp_path / ".memkraft" / "events.jsonl"
    with events.open("a") as fh:
        fh.write("{corrupt\n")

    plan = mc.compile_truth(dry_run=True)
    assert plan == mc.compile_truth(dry_run=True)
    assert plan["dry_run"] is True
    assert plan["skipped"] == 1
    assert plan["records"] == [{
        "subject_id": "user", "key": "city", "value": "tie wins",
        "source": "chat", "valid_from": "2026-01-01",
    }]
    assert not (tmp_path / ".memkraft" / "compiled_truth.jsonl").exists()


def test_apply_atomically_rebuilds_compiled_view_and_current_truth_reads_it(tmp_path, monkeypatch):
    mc = _mc(tmp_path)
    mc.append_event("user", "city", "Seoul", source="chat", valid_from="2026-01-01")
    dry = mc.compile_truth(dry_run=True)
    applied = mc.compile_truth(dry_run=False)
    assert applied == {**dry, "dry_run": False, "applied": True}
    compiled = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    before = compiled.read_bytes()
    assert mc.current_truth("user") == {"city": "Seoul"}

    mc.append_event("user", "city", "Busan", source="chat", valid_from="2026-02-01")
    assert mc.current_truth("user") == {"city": "Seoul"}  # compiled view, not events
    mc.compile_truth(dry_run=False)
    assert mc.current_truth("user") == {"city": "Busan"}
    assert compiled.read_bytes() != before
    assert not compiled.with_name("compiled_truth.jsonl.rebuild.tmp").exists()


def test_missing_valid_from_uses_append_order_deterministically(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="a")
    mc.append_event("u", "k", 2, source="b")
    assert mc.compile_truth()["records"][0]["value"] == 2


def test_current_truth_fails_closed_on_corrupt_compiled_lines(tmp_path):
    path = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{bad\n{"subject_id":"u","key":"k","value":3}\n')
    assert _mc(tmp_path).current_truth("u") == {}


def test_truth_status_on_fresh_store_is_read_only(tmp_path):
    mc = MemKraft(base_dir=tmp_path)

    status = mc.truth_status()

    assert set(status) == {
        "schema_version", "stale", "live_transaction_id",
        "applied_transaction_id", "pending_event_count",
    }
    assert status["schema_version"] == 1
    assert status["stale"] is True
    assert status["applied_transaction_id"] is None
    assert status["pending_event_count"] == 0
    assert not (tmp_path / ".memkraft").exists()


def test_truth_status_tracks_sleep_and_keeps_stale_compiled_truth(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "city", "Seoul", source="chat")
    applied = mc.sleep(dry_run=False)
    assert mc.truth_status() == {
        "schema_version": 1, "stale": False,
        "live_transaction_id": applied["transaction_id"],
        "applied_transaction_id": applied["transaction_id"],
        "pending_event_count": 0,
    }

    mc.append_event("u", "city", "Busan", source="chat")
    stale = mc.truth_status()
    assert stale == {
        "schema_version": 1, "stale": True,
        "live_transaction_id": mc.sleep()["transaction_id"],
        "applied_transaction_id": applied["transaction_id"],
        "pending_event_count": 1,
    }
    assert mc.current_truth("u") == {"city": "Seoul"}
    refreshed = mc.sleep(dry_run=False)
    assert mc.truth_status() == {
        "schema_version": 1, "stale": False,
        "live_transaction_id": refreshed["transaction_id"],
        "applied_transaction_id": refreshed["transaction_id"],
        "pending_event_count": 0,
    }
    assert mc.current_truth("u") == {"city": "Busan"}


def test_truth_status_policy_and_forget_make_stale_without_status_writes(tmp_path):
    mc = _mc(tmp_path)
    first = mc.append_event("u", "secret", 1, source="chat")
    mc.append_event("u", "public", 2, source="chat")
    mc.sleep(dry_run=False)
    mc.do_not_remember(subject="u", key="secret", dry_run=False)
    policy_status = mc.truth_status()
    assert policy_status["stale"] is True
    assert policy_status["pending_event_count"] == 0

    mc.sleep(dry_run=False)
    mc.forget(first["id"], dry_run=False)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / ".memkraft").iterdir()}
    forget_status = mc.truth_status()
    after = {p: p.stat().st_mtime_ns for p in (tmp_path / ".memkraft").iterdir()}
    assert forget_status["stale"] is True
    assert forget_status["pending_event_count"] == 0
    assert after == before


def test_truth_status_pending_uses_raw_snapshot_after_forget_and_append(tmp_path):
    mc = _mc(tmp_path)
    first = mc.append_event("u", "first", 1, source="chat")
    mc.append_event("u", "second", 2, source="chat")
    mc.sleep(dry_run=False)
    mc.forget(first["id"], dry_run=False)
    mc.append_event("u", "third", 3, source="chat")

    assert mc.truth_status()["pending_event_count"] == 1


def test_truth_status_pending_survives_forget_compaction_and_new_append(tmp_path):
    mc = _mc(tmp_path)
    first = mc.append_event("u", "first", 1, source="chat")
    mc.append_event("u", "second", 2, source="chat")
    mc.sleep(dry_run=False)
    mc.forget(first["id"], dry_run=False)
    mc.compact_memory(dry_run=False)
    mc.append_event("u", "third", 3, source="chat")

    assert mc.truth_status()["pending_event_count"] == 1


def test_sleep_reapplies_historical_transaction_when_it_is_not_latest(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "stable", 1, source="chat")
    original = mc.sleep(dry_run=False)
    transient = mc.append_event("u", "transient", 2, source="chat")
    newer = mc.sleep(dry_run=False)
    assert newer["transaction_id"] != original["transaction_id"]

    mc.forget(transient["id"], dry_run=False)
    mc.compact_memory(dry_run=False)
    assert mc.sleep()["transaction_id"] == original["transaction_id"]
    assert mc.truth_status()["stale"] is True

    reapplied = mc.sleep(dry_run=False)

    assert reapplied["status"] == "applied"
    assert reapplied["transaction_id"] == original["transaction_id"]
    assert mc.truth_status()["stale"] is False
    journal = [json.loads(line) for line in mc._sleep_journal_path().read_text().splitlines()]
    assert [row["transaction_id"] for row in journal] == [
        original["transaction_id"], newer["transaction_id"], original["transaction_id"],
    ]


def test_truth_status_old_journal_without_event_ids_uses_current_raw_count(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "first", 1, source="chat")
    mc.sleep(dry_run=False)
    journal = tmp_path / ".memkraft" / "sleep_journal.jsonl"
    row = json.loads(journal.read_text())
    row.pop("event_ids", None)
    journal.write_text(json.dumps(row) + "\n")
    mc.do_not_remember(subject="u", key="first", dry_run=False)

    status = mc.truth_status()
    assert status["stale"] is True
    assert status["pending_event_count"] == 1


def test_truth_status_reads_one_governance_snapshot_under_lock(tmp_path, monkeypatch):
    import memkraft.derived_views as derived_views

    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    original_lock = mc._governance_read_lock
    original_read_all = derived_views.read_all
    lock_acquired = False
    observed = []

    def lock_spy():
        nonlocal lock_acquired
        fd = original_lock()
        lock_acquired = True
        return fd

    def read_spy(path, *args, **kwargs):
        if path in (mc._canonical_events_path(), mc._policies_path(), mc._sleep_journal_path()):
            observed.append((path.name, lock_acquired, args))
        return original_read_all(path, *args, **kwargs)

    monkeypatch.setattr(mc, "_governance_read_lock", lock_spy)
    monkeypatch.setattr(derived_views, "read_all", read_spy)
    mc.truth_status()

    assert [name for name, _, _ in observed].count("events.jsonl") == 1
    assert {name for name, _, _ in observed} == {
        "events.jsonl", "deny_policies.jsonl", "sleep_journal.jsonl",
    }
    assert all(held for _, held, _ in observed)
    assert next(args for name, _, args in observed if name == "events.jsonl") == (True,)


def test_current_truth_snapshot_cache_cannot_resurrect_forgotten_or_denied_truth(tmp_path):
    mc = _mc(tmp_path)
    forgotten = mc.append_event("u", "forgotten", 1, source="chat")
    mc.append_event("u", "denied", 2, source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"denied": 2, "forgotten": 1}
    mc.forget(forgotten["id"], dry_run=False)
    mc.do_not_remember(subject="u", key="denied", dry_run=False)
    assert mc.current_truth("u") == {}


def test_compaction_rebuilds_existing_compiled_truth_without_forgotten_warm_cache_row(tmp_path):
    mc = _mc(tmp_path)
    forgotten = mc.append_event("u", "secret", "value", source="chat")
    mc.append_event("u", "public", "kept", source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"public": "kept", "secret": "value"}  # warm cache

    mc.forget(forgotten["id"], dry_run=False)
    assert mc.current_truth("u") == {"public": "kept"}
    result = mc.compact_memory(dry_run=False)

    assert result["status"] == "applied"
    assert mc.current_truth("u") == {"public": "kept"}
    compiled = [json.loads(line) for line in mc._compiled_truth_path().read_text().splitlines()]
    assert {(row["key"], row["value"]) for row in compiled} == {("public", "kept")}


def test_compaction_filters_snapshot_without_publishing_unslept_append(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "city", "old", source="chat")
    mc.sleep(dry_run=False)
    mc.append_event("u", "city", "new", source="chat")

    assert mc.current_truth("u") == {"city": "old"}
    mc.compact_memory(dry_run=False)

    assert mc.current_truth("u") == {"city": "old"}
    assert [row["value"] for row in read_all(mc._compiled_truth_path()).records] == ["old"]


def test_candidate_only_compaction_does_not_publish_stale_canonical_truth(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "city", "old", source="chat")
    mc.sleep(dry_run=False)
    mc.append_event("u", "city", "new", source="chat")
    candidate = mc.remember_candidate("drop", session_id="s")
    mc.forget_candidates(candidate_id=candidate["candidate_id"], dry_run=False)

    before = mc.current_truth("u")
    mc.compact_memory(dry_run=False)

    assert before == mc.current_truth("u") == {"city": "old"}


def test_current_truth_reads_governance_snapshot_under_shared_lock(tmp_path, monkeypatch):
    import memkraft.derived_views as derived_views

    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    original_lock = mc._governance_read_lock
    original_read_all = derived_views.read_all
    lock_acquired = False
    observed = []

    def lock_spy():
        nonlocal lock_acquired
        fd = original_lock()
        lock_acquired = True
        return fd

    def read_spy(path, *args, **kwargs):
        if path in (mc._canonical_events_path(), mc._policies_path(), mc._compiled_truth_path()):
            observed.append((path.name, lock_acquired))
        return original_read_all(path, *args, **kwargs)

    monkeypatch.setattr(mc, "_governance_read_lock", lock_spy)
    monkeypatch.setattr(derived_views, "read_all", read_spy)

    assert mc.current_truth("u") == {"k": 1}
    assert {name for name, _held in observed} == {
        "events.jsonl", "deny_policies.jsonl", "compiled_truth.jsonl",
    }
    assert all(held for _name, held in observed)


def test_current_truth_on_fresh_store_keeps_missing_lock_read_only(tmp_path):
    assert _mc(tmp_path).current_truth("u") == {}
    assert not (tmp_path / ".memkraft").exists()


def test_current_truth_cached_snapshot_handles_external_deletion_and_corruption(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    compiled = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    assert mc.current_truth("u") == {"k": 1}
    compiled.unlink()
    assert mc.current_truth("u") == {}
    compiled.write_text("{corrupt\n")
    assert mc.current_truth("u") == {}


def test_current_truth_cached_valid_snapshot_fails_closed_after_corrupt_append(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    compiled = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    assert mc.current_truth("u") == {"k": 1}

    with compiled.open("a") as fh:
        fh.write("{corrupt\n")

    assert mc.current_truth("u") == {}


def test_current_truth_cached_snapshot_fails_closed_on_corrupt_canonical_events(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"k": 1}

    with mc._canonical_events_path().open("a") as fh:
        fh.write("{corrupt\n")

    assert mc.current_truth("u") == {}


def test_current_truth_cached_valid_then_denied_then_corrupt_policy_fails_closed(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "secret", "v", source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"secret": "v"}
    mc.do_not_remember(subject="u", key="secret", dry_run=False)
    assert mc.current_truth("u") == {}

    mc._policies_path().write_text("{corrupt\n")

    assert mc.current_truth("u") == {}


def test_current_truth_caches_canonical_events_but_never_policy_reads(tmp_path, monkeypatch):
    import memkraft.derived_views as derived_views

    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    original = derived_views.read_all
    calls = []

    def spy(path, *args, **kwargs):
        calls.append(Path(path).name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(derived_views, "read_all", spy)
    assert mc.current_truth("u") == {"k": 1}
    assert mc.current_truth("u") == {"k": 1}

    assert calls.count("events.jsonl") == 1
    assert calls.count("compiled_truth.jsonl") == 1
    assert calls.count("deny_policies.jsonl") == 2


def test_current_truth_event_cache_invalidates_same_stamp_replaced_identity(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"k": 1}
    events = mc._canonical_events_path()
    original = events.stat()
    replacement = events.with_name("events.replacement")
    replacement.write_bytes(b"x" * original.st_size)
    os.replace(replacement, events)
    os.utime(events, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert mc.current_truth("u") == {}


def test_governance_hides_warm_snapshot_without_eager_recompile(tmp_path, monkeypatch):
    mc = _mc(tmp_path)
    forgotten = mc.append_event("u", "forgotten", 1, source="chat")
    mc.append_event("u", "denied", 2, source="chat")
    mc.sleep(dry_run=False)
    assert mc.current_truth("u") == {"denied": 2, "forgotten": 1}
    compiled_before = mc._compiled_truth_path().read_bytes()

    def unexpected_compile(*_args, **_kwargs):
        raise AssertionError("governance must not eagerly rebuild compiled truth")

    monkeypatch.setattr(mc, "compile_truth", unexpected_compile)
    mc.do_not_remember(subject="u", key="denied", dry_run=False)
    mc.forget(forgotten["id"], dry_run=False)

    assert mc._compiled_truth_path().read_bytes() == compiled_before
    assert mc.current_truth("u") == {}


def test_current_truth_cache_invalidates_same_snapshot_replaced_file_identity(tmp_path):
    mc = _mc(tmp_path)
    mc.append_event("u", "k", 1, source="chat")
    mc.sleep(dry_run=False)
    compiled = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    assert mc.current_truth("u") == {"k": 1}
    original = compiled.stat()
    replacement = compiled.with_name("compiled_truth.replacement")
    replacement.write_bytes(b"x" * original.st_size)
    os.replace(replacement, compiled)
    os.utime(compiled, ns=(original.st_atime_ns, original.st_mtime_ns))
    replaced = compiled.stat()
    assert (replaced.st_mtime_ns, replaced.st_size) == (original.st_mtime_ns, original.st_size)
    assert (replaced.st_dev, replaced.st_ino) != (original.st_dev, original.st_ino)

    assert mc.current_truth("u") == {}


def test_preview_api_does_not_replace_legacy_log_event(tmp_path):
    mc = _mc(tmp_path)
    assert MemKraft.append_event is not MemKraft.log_event
    mc.log_event("legacy remains callable")
    assert "subject_id" in MemKraft.append_event.__code__.co_varnames


def test_append_normalizes_identity_source_and_iso_valid_from(tmp_path):
    row = _mc(tmp_path).append_event(" user ", " city ", "Seoul", source=" chat ", valid_from="2026-01-02T03:04:05Z")
    assert (row["subject_id"], row["key"], row["source"]) == ("user", "city", "chat")
    assert row["valid_from"] == "2026-01-02T03:04:05+00:00"


@pytest.mark.parametrize("field,value", [("subject_id", " "), ("subject_id", []), ("key", " "), ("key", {}), ("source", " "), ("source", [])])
def test_append_rejects_invalid_identity_and_source(tmp_path, field, value):
    args = {"subject_id": "u", "key": "k", "value": 1, "source": "s"}
    args[field] = value
    with pytest.raises((TypeError, ValueError)):
        _mc(tmp_path).append_event(**args)


@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30", [], 123])
def test_append_rejects_non_iso_or_invalid_valid_from(tmp_path, value):
    with pytest.raises((TypeError, ValueError)):
        _mc(tmp_path).append_event("u", "k", 1, source="s", valid_from=value)


def test_source_and_provenance_are_preserved_separately_with_alias_compatibility(tmp_path):
    mc = _mc(tmp_path)
    both = mc.append_event("u", "both", 1, source="chat", provenance="message:7")
    source_only = mc.append_event("u", "source", 2, source="chat")
    provenance_only = mc.append_event("u", "provenance", 3, provenance="message:8")
    assert both["source"] == "chat" and both["provenance"] == "message:7"
    assert "provenance" not in source_only
    assert provenance_only["source"] == provenance_only["provenance"] == "message:8"
    compiled = mc.compile_truth()["records"]
    assert next(row for row in compiled if row["key"] == "both")["provenance"] == "message:7"


def test_compile_skips_malformed_unhashable_identity_and_invalid_dates(tmp_path):
    events = tmp_path / ".memkraft" / "events.jsonl"
    events.parent.mkdir(parents=True)
    rows = [
        {"subject_id": [], "key": "k", "value": 1, "source": "s"},
        {"subject_id": "u", "key": {}, "value": 2, "source": "s"},
        {"subject_id": "u", "key": "k", "value": 3, "source": "s", "valid_from": "later"},
        {"subject_id": " u ", "key": " k ", "value": 4, "source": " s ", "valid_from": "2026-01-01"},
    ]
    events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    plan = _mc(tmp_path).compile_truth()
    assert plan["skipped"] == 3
    assert plan["records"] == [{"subject_id": "u", "key": "k", "value": 4, "source": "s", "valid_from": "2026-01-01"}]


def test_concurrent_compile_uses_process_lock_and_unique_temps(tmp_path):
    mc = _mc(tmp_path)
    for number in range(20):
        mc.append_event("u", f"k{number}", number, source="test")
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(8)
    queue = context.Queue()
    workers = [context.Process(target=_compile_worker, args=(str(tmp_path), gate, queue)) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
    assert all(worker.exitcode == 0 for worker in workers)
    assert [queue.get() for _ in workers] == [None] * len(workers)
    assert mc.current_truth("u") == {f"k{number}": number for number in range(20)}
    assert list((tmp_path / ".memkraft").glob("compiled_truth.jsonl.*.tmp")) == []
