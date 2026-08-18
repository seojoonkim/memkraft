import json

import pytest

from memkraft import MemKraft
from memkraft.memory_graph import MemoryGraphValidationError


def store(tmp_path):
    value = MemKraft(base_dir=str(tmp_path))
    value.init(verbose=False)
    return value


def claim(claim_id="mgc-0000000000000001", **changes):
    value = {"claim_id": claim_id, "canonical_key": "  CAFÉ\t Policy  ",
             "statement": "Café Policy", "scope": None, "artifact_refs": [],
             "provenance": None, "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}
    value.update(changes)
    return value


def test_claim_envelope_and_canonical_normalization(tmp_path):
    s = store(tmp_path)
    result = s.memory_graph_append(operation_id="op-1", expected_high_water=0, claims=[claim()])
    row = json.loads((tmp_path / ".memkraft" / "memory_graph.jsonl").read_text().splitlines()[0])
    assert row["record_type"] == "claim" and row["schema_version"] == 1
    assert row["event_seq"] == 1 and row["batch_index"] == 0 and row["batch_size"] == 1
    assert row["claim_id"] == "mgc-0000000000000001"
    assert row["canonical_key"] == "café policy"
    assert result["record_ids"] == [row["record_id"]]


@pytest.mark.parametrize("payload,field", [
    ({"relations": [{"relation_id": "mgr-0000000000000001", "relation_type": "unknown",
       "src_claim_id": "mgc-0000000000000001", "dst_claim_id": "mgc-0000000000000001",
       "evidence_artifact_refs": [], "correction_event_refs": [],
       "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}]}, "relation_type"),
    ({"lifecycles": [{"claim_id": "mgc-0000000000000001", "state": "unknown",
       "caused_by": "cause", "reason": "bad", "evidence_refs": [],
       "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}]}, "state"),
])
def test_closed_payload_enums_rejected(tmp_path, payload, field):
    s = store(tmp_path)
    with pytest.raises(MemoryGraphValidationError, match=field):
        s.memory_graph_append(operation_id="bad", expected_high_water=0,
                              claims=[claim()], **payload)
    assert not (tmp_path / ".memkraft" / "memory_graph.jsonl").exists()


def test_replay_rejects_unknown_record_type(tmp_path):
    s = store(tmp_path)
    s.memory_graph_append(operation_id="op", expected_high_water=0, claims=[claim()])
    path = tmp_path / ".memkraft" / "memory_graph.jsonl"
    row = json.loads(path.read_text())
    row["record_type"] = "unknown"
    path.write_text(json.dumps(row) + "\n")
    from memkraft.memory_graph import MemoryGraphCorruptError
    with pytest.raises(MemoryGraphCorruptError):
        s.memory_graph_replay_check()


def test_payload_shape_is_closed(tmp_path):
    s = store(tmp_path)
    with pytest.raises(MemoryGraphValidationError):
        s.memory_graph_append(operation_id="bad", expected_high_water=0,
                              claims=[claim(extra="not allowed")])
