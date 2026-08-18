import json
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.memory_graph import (MemoryGraphCASError, MemoryGraphCorruptError,
    MemoryGraphDuplicateOperationError, MemoryGraphLimitError, MemoryGraphLockTimeoutError,
    MemoryGraphValidationError)

NOW = "2026-01-01T00:00:00Z"


def mk(tmp_path):
    value = MemKraft(base_dir=str(tmp_path)); value.init(verbose=False); return value


def claim(cid, key="alpha", scope=None, refs=(), provenance=None, start=NOW, end=None):
    return {"claim_id": cid, "canonical_key": key, "statement": key + " statement",
            "scope": scope, "artifact_refs": list(refs), "provenance": provenance,
            "valid_from": start, "valid_to": end}


def relation(rid, src, dst, kind="supports", corrections=()):
    return {"relation_id": rid, "relation_type": kind, "src_claim_id": src,
            "dst_claim_id": dst, "evidence_artifact_refs": [],
            "correction_event_refs": list(corrections), "valid_from": NOW, "valid_to": None}


def lifecycle(cid, state, start=NOW, caused_by="cause"):
    return {"claim_id": cid, "state": state, "caused_by": caused_by, "reason": "reason",
            "evidence_refs": [], "valid_from": start, "valid_to": None}


def graph_path(tmp_path): return Path(tmp_path) / ".memkraft" / "memory_graph.jsonl"


def test_append_prevalidation_limits_cas_and_idempotency(tmp_path):
    s = mk(tmp_path); c1 = claim("mgc-0000000000000001", key="  Alpha  ")
    first = s.memory_graph_append(operation_id="batch", expected_high_water=0, claims=[c1])
    assert s.memory_graph_replay_check()["high_water"] == 1
    same = s.memory_graph_append(operation_id="batch", expected_high_water=999, claims=[c1])
    assert same["replayed"] is True and same["record_ids"] == first["record_ids"]
    with pytest.raises(MemoryGraphDuplicateOperationError):
        s.memory_graph_append(operation_id="batch", expected_high_water=1,
                              claims=[claim("mgc-0000000000000002")])
    with pytest.raises(MemoryGraphCASError):
        s.memory_graph_append(operation_id="cas", expected_high_water=0)
    before = graph_path(tmp_path).read_bytes()
    with pytest.raises(MemoryGraphValidationError):
        s.memory_graph_append(operation_id="invalid", expected_high_water=1,
            claims=[claim("mgc-0000000000000002")],
            relations=[relation("mgr-0000000000000001", "missing", "mgc-0000000000000002")])
    assert graph_path(tmp_path).read_bytes() == before
    with pytest.raises(MemoryGraphLimitError):
        s.memory_graph_append(operation_id="limit", expected_high_water=1,
            claims=[claim("mgc-0000000000000002")], max_records=0)


@pytest.mark.parametrize("damage", ["malformed", "gap", "incomplete"])
def test_strict_corruption_replay(tmp_path, damage):
    s = mk(tmp_path); s.memory_graph_append(operation_id="x", expected_high_water=0,
        claims=[claim("mgc-0000000000000001")])
    path = graph_path(tmp_path)
    if damage == "malformed": path.write_bytes(path.read_bytes() + b"{torn")
    else:
        row = json.loads(path.read_text())
        if damage == "gap": row["event_seq"] = 2
        else: row["batch_size"] = 2
        path.write_text(json.dumps(row) + "\n")
    with pytest.raises(MemoryGraphCorruptError): s.memory_graph_replay_check()


def test_clean_replay_summary_and_bitemporal_state(tmp_path):
    s = mk(tmp_path); cid = "mgc-0000000000000001"
    s.memory_graph_append(operation_id="c", expected_high_water=0, claims=[claim(cid)])
    s.memory_graph_append(operation_id="l", expected_high_water=1,
        lifecycles=[lifecycle(cid, "superseded", "2027-01-01T00:00:00Z")])
    assert s.memory_graph_replay_check() == {"records": 2, "high_water": 2, "operations": 2}
    assert s.memory_graph_get_claim(cid, as_of_valid="2026-06-01T00:00:00Z")["state"] == "active"
    assert s.memory_graph_get_claim(cid, as_of_valid="2028-01-01T00:00:00Z")["state"] == "superseded"
    claim_tx = json.loads(graph_path(tmp_path).read_text().splitlines()[0])["tx_time"]
    assert s.memory_graph_get_claim(cid, as_of_valid="2028-01-01T00:00:00Z",
        as_of_tx=claim_tx)["state"] == "active"


def test_provenance_validation_recall_traversal_and_scope(tmp_path):
    s = mk(tmp_path)
    artifact = s.persist_artifact("artifact alpha exact", provenance={"session_id": "s1"})
    aid = artifact["source_handle"].split(":", 1)[1]
    c1 = claim("mgc-0000000000000001", "Alpha", "one", [aid], artifact["provenance"])
    c2 = claim("mgc-0000000000000002", "beta", "one")
    c3 = claim("mgc-0000000000000003", "gamma", "one")
    s.memory_graph_append(operation_id="graph", expected_high_water=0, claims=[c1, c2, c3],
        relations=[relation("mgr-0000000000000001", c1["claim_id"], c2["claim_id"]),
                   relation("mgr-0000000000000002", c2["claim_id"], c3["claim_id"])])
    results = s.memory_graph_recall("ALPHA", scope="one", max_hops=2)
    assert [(r["tier"], r["hops"]) for r in results] == [(0, 0), (3, 1), (4, 2)]
    assert all(r["provenance_path"] for r in results)
    assert results[0]["provenance_path"][-1]["id"] == aid
    assert s.memory_graph_recall("alpha", scope="two") == []
    with pytest.raises(MemoryGraphValidationError):
        s.memory_graph_append(operation_id="invented", expected_high_water=5,
            claims=[claim("mgc-0000000000000004", refs=[aid], provenance={"session_id": "fake"})])


def test_inactive_filter_exact_exception_and_absent_provenance(tmp_path):
    s = mk(tmp_path); cid = "mgc-0000000000000001"
    s.memory_graph_append(operation_id="x", expected_high_water=0, claims=[claim(cid, "alpha beta")],
                          lifecycles=[lifecycle(cid, "contradicted")])
    exact = s.memory_graph_recall("alpha beta")
    assert exact[0]["state"] == "contradicted"
    assert exact[0]["provenance_path"][-1] == {"kind": "artifact", "id": None,
                                               "detail": "provenance absent"}
    assert s.memory_graph_recall("beta") == []
    assert s.memory_graph_recall("beta", include_inactive=True)[0]["tier"] == 2


def test_stale_lock_file_does_not_block_and_validation_releases_lock(tmp_path):
    s = mk(tmp_path); lock = Path(tmp_path) / ".memkraft" / "memory_graph.lock"
    lock.parent.mkdir(parents=True, exist_ok=True); lock.write_text("another-pid")
    s.memory_graph_lock_timeout = 0.02
    result = s.memory_graph_append(operation_id="stale-file", expected_high_water=0,
                                   claims=[claim("mgc-0000000000000001")])
    assert result["event_seq_last"] == 1
    with pytest.raises(MemoryGraphValidationError):
        s.memory_graph_append(operation_id="invalid", expected_high_water=1,
                              claims=[claim("bad-id")])
    # The lock inode may remain as a reusable advisory-lock file; ownership is
    # released by close/UN and no stale PID contents are consulted.
    assert lock.exists()


def test_partial_write_failure_rolls_back_append(tmp_path, monkeypatch):
    s = mk(tmp_path)
    import memkraft.memory_graph as memory_graph
    real_write = memory_graph.os.write
    calls = {"count": 0}

    def fail_after_partial(fd, data):
        calls["count"] += 1
        if calls["count"] == 1:
            return real_write(fd, data[: max(1, len(data) // 2)])
        return 0

    monkeypatch.setattr(memory_graph.os, "write", fail_after_partial)
    with pytest.raises(OSError):
        s.memory_graph_append(operation_id="partial", expected_high_water=0,
                              claims=[claim("mgc-0000000000000001")])
    assert graph_path(tmp_path).read_bytes() == b""
    assert s.memory_graph_replay_check() == {"records": 0, "high_water": 0, "operations": 0}


def test_correction_refs_validate_read_only_and_legacy_graph_untouched(tmp_path):
    s = mk(tmp_path)
    correction = s.correction_capture("corr.alpha", "wrong", "right", now=NOW)
    event_id = correction["record_id"]
    ledger = Path(tmp_path) / ".memkraft" / "corrections.jsonl"
    legacy = Path(tmp_path) / "graph.db"; legacy.write_bytes(b"legacy")
    before = ledger.read_bytes()
    c1 = claim("mgc-0000000000000001"); c2 = claim("mgc-0000000000000002")
    s.memory_graph_append(operation_id="linked", expected_high_water=0, claims=[c1, c2],
        relations=[relation("mgr-0000000000000001", c1["claim_id"], c2["claim_id"],
                            corrections=[event_id])])
    assert ledger.read_bytes() == before and legacy.read_bytes() == b"legacy"
    with pytest.raises(MemoryGraphValidationError):
        s.memory_graph_append(operation_id="missing-correction", expected_high_water=3,
            relations=[relation("mgr-0000000000000002", c1["claim_id"], c2["claim_id"],
                               corrections=["missing-event"])])
