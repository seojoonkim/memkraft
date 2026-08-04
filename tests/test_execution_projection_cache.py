"""Correctness locks for the local execution projection sidecar cache."""
from __future__ import annotations

import json
import os
from pathlib import Path

from memkraft import MemKraft
from memkraft.execution_dispatch import dispatch
from memkraft.execution_projection_cache import cache_path, read_projection
from memkraft.store_core import append

NOW = "2026-08-04T11:22:33Z"
GOAL_ID = "cache/projection"
REQUEST_ID = "01JKX7Q2M0000000000000000A"


def _record(seq, record_type="goal_declared", **extra):
    row = {
        "schema_version": 1, "execution_schema": 1,
        "record_type": record_type, "goal_id": GOAL_ID,
        "event_seq": seq, "id": "%032x" % seq,
        "emitted_at": NOW, "operation_id": "%064x" % seq,
        "privacy": "local_private", "authority_claim": "agent",
        "authority_verified": False,
    }
    if record_type == "goal_declared":
        row.update(title="cached", intent="test", constraints=[], success_criteria=[])
    row.update(extra)
    return row


def _query():
    return {
        "mkep": "0", "kind": "query", "request_id": REQUEST_ID,
        "op": "state.read", "now": NOW, "target": {"goal_id": GOAL_ID},
        "args": {"include": ["gates", "handoffs", "leases"]},
    }


def test_delete_cache_recomputes_byte_identical_state_read(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    append(mk._execution_events_path(), _record(1))
    first = dispatch(mk, _query())
    sidecar = cache_path(mk._execution_events_path(), NOW, GOAL_ID)
    assert sidecar.is_file()
    sidecar.unlink()
    second = dispatch(mk, _query())
    assert json.dumps(second, sort_keys=True, separators=(",", ":")) == \
        json.dumps(first, sort_keys=True, separators=(",", ":"))


def test_append_invalidates_cached_projection(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    first = read_projection(path, NOW, GOAL_ID)
    append(path, _record(2, "goal_transition", to_status="abandoned"))
    second = read_projection(path, NOW, GOAL_ID)
    assert first.state["goal_status"] == "open"
    assert second.state["goal_status"] == "abandoned"
    assert second.state["execution_seq"] == 2


def test_inode_replacement_never_serves_stale_content(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    original_stat = path.stat()
    assert read_projection(path, NOW, GOAL_ID).state["goal_status"] == "open"

    replacement = path.with_name("replacement.jsonl")
    replacement.write_text(json.dumps(_record(1, "goal_transition", to_status="abandoned")) + "\n",
                           encoding="utf-8")
    # Keep size and mtime equal where possible: inode alone must invalidate it.
    data = replacement.read_bytes()
    old_size = original_stat.st_size
    replacement.write_bytes(data[:old_size].ljust(old_size, b" "))
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    os.replace(replacement, path)
    state = read_projection(path, NOW, GOAL_ID).state
    assert state["goal_status"] is None


def test_corrupt_cache_fails_closed_and_recovers(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    expected = read_projection(path, NOW, GOAL_ID)
    sidecar = cache_path(path, NOW, GOAL_ID)
    sidecar.write_text("{broken", encoding="utf-8")
    recovered = read_projection(path, NOW, GOAL_ID)
    assert recovered == expected
    assert json.loads(sidecar.read_text(encoding="utf-8"))["state"] == expected.state


def test_stable_projection_cache_is_reused_across_injected_instants(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    before = read_projection(path, NOW, GOAL_ID)

    import memkraft.execution_projection_cache as module

    def unexpected_read(_path):
        raise AssertionError("unchanged stable projection must reuse its sidecar")

    monkeypatch.setattr(module, "read_all", unexpected_read)
    after = read_projection(path, "2026-08-04T13:00:00Z", GOAL_ID)
    assert before.state["digest"] == after.state["digest"]
    assert before.state["evaluated_at"] != after.state["evaluated_at"]
    assert cache_path(path, NOW, GOAL_ID) == cache_path(
        path, "2026-08-04T13:00:00Z", GOAL_ID)


def test_lease_projection_reuses_timeline_and_rechecks_injected_now(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    append(path, _record(2, "lease_grant", scope_key="work", holder="worker",
                         fence_token=1, expires_at="2026-08-04T12:00:00Z"))
    before = read_projection(path, NOW, GOAL_ID, include_leases=True)

    import memkraft.execution_projection_cache as module

    def unexpected_read(_path):
        raise AssertionError("unchanged lease timeline must reuse its sidecar")

    monkeypatch.setattr(module, "read_all", unexpected_read)
    after = read_projection(path, "2026-08-04T13:00:00Z", GOAL_ID,
                            include_leases=True)
    assert "work" in before.leases["active"]
    assert after.leases["active"] == {}


def test_cache_write_is_atomic_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    import memkraft.execution_projection_cache as module
    real_replace = module.os.replace
    observed = []

    def checked_replace(source, destination):
        observed.append((Path(source), Path(destination)))
        assert Path(source).is_file()
        assert Path(destination) == cache_path(path, NOW, GOAL_ID)
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", checked_replace)
    read_projection(path, NOW, GOAL_ID)
    assert len(observed) == 1
    assert not observed[0][0].exists()
    assert json.loads(observed[0][1].read_text(encoding="utf-8"))["cache_schema"] == 1
    assert list(observed[0][1].parent.glob("*.tmp")) == []


def test_cache_fingerprint_records_required_file_identity_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, _record(1))
    read_projection(path, NOW, GOAL_ID)
    payload = json.loads(cache_path(path, NOW, GOAL_ID).read_text(encoding="utf-8"))
    assert set(payload["fingerprint"]) >= {"device", "inode", "size", "mtime_ns"}
    assert payload["goal_id"] == GOAL_ID
    assert payload["now"] == NOW
