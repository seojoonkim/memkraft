"""Regression tests for final review findings."""
import json
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-06T09:00:00Z"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _path(mk):
    return Path(mk.base_dir) / ".memkraft" / "improvement" / "events.jsonl"


def _append(mk, record):
    path = _path(mk)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _propose(mk):
    return mk.improvement_propose(
        "prop.review-one", "artifact.review-one", "Review proposal",
        "Regression coverage", DIGEST_A, now=NOW,
        required_evaluations=["suite-green"],
    )


@pytest.mark.parametrize("record", [
    {"record_type": "improvement_proposal", "event_seq": 1},
    {"record_type": "unknown_future_record", "event_seq": 1},
    {"record_type": "artifact_revision", "event_seq": 0,
     "artifact_id": "artifact.one", "revision_id": "r1"},
    {"record_type": "artifact_revision", "event_seq": -1,
     "artifact_id": "artifact.one", "revision_id": "r1"},
    {"record_type": "artifact_revision", "event_seq": True,
     "artifact_id": "artifact.one", "revision_id": "r1"},
])
def test_parseable_malformed_records_are_corruption(tmp_path, record):
    mk = _mk(tmp_path)
    _append(mk, record)
    assert mk.improvement_project(now=NOW)["skipped_lines"] == 1
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"


def test_duplicate_event_sequences_are_all_corrupt(tmp_path):
    mk = _mk(tmp_path)
    for revision_id, content_digest in (("r1", DIGEST_A), ("r2", DIGEST_B)):
        _append(mk, {
            "record_type": "artifact_revision", "event_seq": 1,
            "artifact_id": "artifact.one", "revision_id": revision_id,
            "content_digest": content_digest,
        })
    view = mk.improvement_project(now=NOW)
    assert view["skipped_lines"] == 2
    assert view["artifacts"] == {}
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"


def test_promotion_plan_reports_corruption_like_writer(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    mk.improvement_set_status(
        "prop.review-one", "draft", "under_evaluation", now=NOW,
    )
    mk.artifact_register_revision(
        "artifact.review-one", "r1", DIGEST_A, now=NOW,
        proposal_id="prop.review-one",
    )
    mk.improvement_record_evaluation(
        "prop.review-one", "suite-green", "pass", DIGEST_A,
        now=NOW, evidence_refs=["test:green"],
    )
    _append(mk, {"record_type": "unknown_future_record", "event_seq": 99})
    before = _path(mk).read_bytes()
    with pytest.raises(ExecutionError) as excinfo:
        mk.improvement_set_status(
            "prop.review-one", "under_evaluation", "promoted", now=NOW,
            promoted_revision_id="r1",
        )
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"
    plan = mk.improvement_plan_promotion("prop.review-one", "r1", now=NOW)
    assert plan["ok"] is False
    assert plan["blockers"][0]["code"] == "E_IMPROVEMENT_LOG_CORRUPT"
    assert _path(mk).read_bytes() == before


def test_documented_preview_methods_exist(tmp_path):
    mk = _mk(tmp_path)
    for name in (
        "improvement_propose", "artifact_register_revision",
        "improvement_record_evaluation", "improvement_set_status",
        "artifact_activate_revision", "artifact_rollback_revision",
        "improvement_project", "improvement_plan_promotion",
        "improvement_plan_activation",
    ):
        assert callable(getattr(mk, name))
