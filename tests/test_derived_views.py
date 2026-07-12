import json
import multiprocessing
from pathlib import Path

import pytest

from memkraft import MemKraft


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


def test_current_truth_skips_corrupt_compiled_lines(tmp_path):
    path = tmp_path / ".memkraft" / "compiled_truth.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{bad\n{"subject_id":"u","key":"k","value":3}\n')
    assert _mc(tmp_path).current_truth("u") == {"k": 3}


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
