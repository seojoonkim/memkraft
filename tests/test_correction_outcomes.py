import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-16T10:00:00Z"
LATER = "2026-08-16T10:01:00Z"


def mk(tmp_path):
    store = MemKraft(base_dir=str(tmp_path))
    store.init(verbose=False)
    store.correction_capture(
        "corr.alpha", "Use UTC", "Render timestamps in UTC", now=NOW,
        required_evaluations=["replay"],
    )
    return store


def log_path(store):
    return Path(store.base_dir) / ".memkraft" / "corrections.jsonl"


def test_record_outcome_has_exact_schema_and_revision_tallies_only(tmp_path):
    store = mk(tmp_path)
    store.correction_revise(
        "corr.alpha", "r2", "Use ISO UTC", "Render ISO UTC", now=LATER,
        base_revision_id="r1", reason="clarified",
    )
    before = store.correction_project(now=LATER)["policies"]["corr.alpha"]
    result = store.correction_record_outcome(
        "corr.alpha", "r2", "complied", now=LATER,
        evidence_refs=["trace://run/0002"], operation_id="outcome-1",
    )
    record = result["record"]
    assert record["record_type"] == "correction_outcome"
    assert set(record) == {
        "id", "created_at", "schema_version", "correction_schema", "record_type",
        "emitted_at", "scope", "privacy", "task_ref", "task_class",
        "host_authorization_ref", "authority_claim", "authority_verified",
        "operation_id", "event_seq", "correction_id", "revision_id", "outcome",
        "evidence_refs",
    }
    policy = store.correction_project(now=LATER)["policies"]["corr.alpha"]
    assert policy["revisions"]["r1"]["outcome_tallies"] == {
        "complied": 0, "violated": 0, "not_applicable": 0,
    }
    assert policy["revisions"]["r2"]["outcome_tallies"] == {
        "complied": 1, "violated": 0, "not_applicable": 0,
    }
    for field in ("status", "scope", "promoted_revision_id", "active_revision_id",
                  "evaluations"):
        assert policy[field] == before[field]


def test_outcome_enum_unknown_target_and_idempotency(tmp_path):
    store = mk(tmp_path)
    with pytest.raises(ExecutionError) as bad:
        store.correction_record_outcome("corr.alpha", "r1", "maybe", now=LATER)
    assert bad.value.code == "E_CORRECTION_PATTERN"
    with pytest.raises(ExecutionError) as missing_revision:
        store.correction_record_outcome("corr.alpha", "r9", "violated", now=LATER)
    assert missing_revision.value.code == "E_CORRECTION_NOT_FOUND"
    with pytest.raises(ExecutionError) as missing_correction:
        store.correction_record_outcome("corr.missing", "r1", "violated", now=LATER)
    assert missing_correction.value.code == "E_CORRECTION_NOT_FOUND"

    first = store.correction_record_outcome(
        "corr.alpha", "r1", "not_applicable", now=LATER, operation_id="outcome-1")
    retry = store.correction_record_outcome(
        "corr.alpha", "r1", "not_applicable", now=LATER, operation_id="outcome-1")
    assert retry["outcome"] == "already_applied"
    assert retry["record_id"] == first["record_id"]
    with pytest.raises(ExecutionError) as mismatch:
        store.correction_record_outcome(
            "corr.alpha", "r1", "complied", now=LATER, operation_id="outcome-1")
    assert mismatch.value.code == "E_CORRECTION_IDEMPOTENCY_MISMATCH"


def test_persisted_invalid_or_orphan_outcome_is_corruption(tmp_path):
    store = mk(tmp_path)
    base = json.loads(log_path(store).read_text(encoding="utf-8").splitlines()[0])
    for mutation in ("invalid", "orphan"):
        record = dict(base)
        record.update({
            "id": "manual-" + mutation, "record_type": "correction_outcome",
            "event_seq": 2, "operation_id": "manual-" + mutation,
            "revision_id": "r1", "outcome": "complied", "evidence_refs": [],
        })
        for key in list(record):
            if key not in {
                "id", "created_at", "schema_version", "correction_schema", "record_type",
                "emitted_at", "scope", "privacy", "task_ref", "task_class",
                "host_authorization_ref", "authority_claim", "authority_verified",
                "operation_id", "event_seq", "correction_id", "revision_id", "outcome",
                "evidence_refs",
            }:
                del record[key]
        if mutation == "invalid":
            record["outcome"] = "unknown"
        else:
            record["revision_id"] = "r9"
        with log_path(store).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        view = store.correction_project(now=LATER)
        assert view["skipped"] == 1
        with pytest.raises(ExecutionError) as corrupt:
            store.correction_record_outcome("corr.alpha", "r1", "complied", now=LATER)
        assert corrupt.value.code == "E_CORRECTION_LOG_CORRUPT"
        lines = log_path(store).read_text(encoding="utf-8").splitlines()
        log_path(store).write_text(lines[0] + "\n", encoding="utf-8")


def test_concurrent_outcomes_are_serialized_without_lost_tallies(tmp_path):
    store = mk(tmp_path)

    def record(index):
        return store.correction_record_outcome(
            "corr.alpha", "r1", "complied", now=LATER,
            operation_id="outcome-%d" % index,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(record, range(8)))
    assert {result["outcome"] for result in results} == {"applied"}
    assert sorted(result["event_seq"] for result in results) == list(range(2, 10))
    policy = store.correction_project(now=LATER)["policies"]["corr.alpha"]
    assert policy["revisions"]["r1"]["outcome_tallies"]["complied"] == 8


def test_outcome_does_not_satisfy_evaluation_gate(tmp_path):
    store = mk(tmp_path)
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=LATER)
    store.correction_record_outcome("corr.alpha", "r1", "complied", now=LATER)
    with pytest.raises(ExecutionError) as error:
        store.correction_set_status(
            "corr.alpha", "under_evaluation", "promoted", now=LATER,
            promoted_revision_id="r1")
    assert error.value.code == "E_CORRECTION_EVALUATION_MISSING"


def test_evaluation_corpus_bridge_accepts_correction_trace_ref(tmp_path):
    store = mk(tmp_path)
    result = store.evaluation_register_case(
        "case.corr-alpha-r2", "user_correction", "corpus://anon/0001",
        "comply with the correction", "repeat the corrected behavior", now=LATER,
        trace_ref="correction://corr.alpha/r2",
    )
    assert result["record"]["trace_ref"] == "correction://corr.alpha/r2"
    assert result["record"]["failure_type"] == "user_correction"
    assert store.correction_project(now=LATER)["high_water_seq"] == 1


# no cross-log transaction: corpus registration neither requires nor creates a correction

def test_evaluation_bridge_is_only_an_opaque_reference(tmp_path):
    store = MemKraft(base_dir=str(tmp_path))
    store.init(verbose=False)
    result = store.evaluation_register_case(
        "case.orphan-correction", "user_correction", "corpus://anon/0001",
        "comply", "violate", now=NOW, trace_ref="correction://corr.unknown/r9")
    assert result["outcome"] == "applied"
    assert not (Path(store.base_dir) / ".memkraft" / "corrections.jsonl").exists()
