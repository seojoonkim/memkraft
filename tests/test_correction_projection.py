from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.correction_policy import project_corrections
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-16T10:00:00Z"
LATER = "2026-08-16T10:01:00Z"


def setup_policy(tmp_path, scope="task"):
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    kwargs = {"task_ref": "task://run/1"} if scope == "task" else {}
    store.correction_capture("corr.alpha", "Use UTC", "Use UTC", now=NOW,
                             scope=scope, required_evaluations=["replay"], **kwargs)
    return store


def test_pure_projection_is_order_independent_and_does_not_write(tmp_path):
    records = [
        {"record_type": "correction_capture", "correction_id": "corr.alpha",
         "revision_id": "r1", "user_utterance": "u", "agent_interpretation": "a",
         "required_evaluations": ["replay"], "scope": "project", "privacy": "local_private",
         "task_ref": None, "task_class": None, "topic_key": None, "truncated": False,
         "event_seq": 1, "id": "a"},
        {"record_type": "correction_status", "correction_id": "corr.alpha",
         "from_status": "captured", "to_status": "under_evaluation",
         "promoted_revision_id": None, "event_seq": 2, "id": "b"},
    ]
    assert project_corrections(records, NOW) == project_corrections(list(reversed(records)), NOW)
    store = setup_policy(tmp_path)
    log = Path(store.base_dir) / ".memkraft" / "corrections.jsonl"
    before = log.stat().st_mtime_ns
    first = store.correction_project(now=NOW)
    second = store.correction_project(now=NOW)
    assert first == second
    assert first["high_water_seq"] == 1
    assert log.stat().st_mtime_ns == before


def test_widening_requires_new_revision_and_fresh_target_scope_pass(tmp_path):
    store = setup_policy(tmp_path)
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r1", "task", "project", now=NOW,
                                      evidence_refs=["eval://scope/1"])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"
    store.correction_revise("corr.alpha", "r2", "Use UTC", "Use UTC", now=NOW,
                            base_revision_id="r1", reason="generalize")
    store.correction_record_evaluation("corr.alpha", "r2", "replay", "pass", now=LATER,
                                       evaluated_scope="task", task_ref="task://run/1")
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=NOW,
                                      evidence_refs=["eval://scope/1"])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"
    store.correction_record_evaluation("corr.alpha", "r2", "replay", "pass", now=LATER,
                                       evaluated_scope="project", operation_id="target-pass")
    result = store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=NOW,
                                           evidence_refs=["eval://scope/1"])
    assert result["outcome"] == "applied"
    policy = store.correction_project(now=NOW)["policies"]["corr.alpha"]
    assert policy["scope"] == "project"
    assert policy["status"] == "captured"
    assert policy["active_revision_id"] is None


def test_narrowing_requires_evidence_and_never_deactivates(tmp_path):
    store = setup_policy(tmp_path, scope="project")
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r1", "project", "task", now=NOW,
                                      task_ref="task://run/1", evidence_refs=[])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"
    store.correction_adjust_scope("corr.alpha", "r1", "project", "task", now=NOW,
                                  task_ref="task://run/1", evidence_refs=["review://human/1"])
    policy = store.correction_project(now=NOW)["policies"]["corr.alpha"]
    assert policy["scope"] == "task"
    assert policy["active_revision_id"] is None
