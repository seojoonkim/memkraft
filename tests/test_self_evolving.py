from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.improvement_ledger import ImprovementError

NOW = "2026-08-20T09:00:00Z"
DIGEST = "a" * 64


def mk(tmp_path):
    obj = MemKraft(base_dir=str(tmp_path))
    obj.init(verbose=False)
    return obj


def test_experience_and_projection_are_append_only(tmp_path):
    obj = mk(tmp_path)
    result = obj.experience_record(
        "exp.correction-001", artifact_kind="memory_policy", scope="project",
        task_ref="run:001", input_snapshot_ref="snapshot:001",
        outcome_ref="outcome:001", classification="correction",
        replayability="replayable", now=NOW, evidence_refs=["evidence:001"],
    )
    assert result["record_type"] == "experience"
    view = obj.self_evolving_project()
    assert view["experience_count"] == 1
    assert view["record_count"] == 1
    assert not (Path(tmp_path) / "entities" / "secret").exists()


def test_passing_receipt_requires_evidence_and_preserves_bindings(tmp_path):
    obj = mk(tmp_path)
    with pytest.raises(ImprovementError):
        obj.evaluator_receipt(
            "prop.example", evaluator_id="eval", evaluator_version="1",
            evaluation_kind="replay", candidate_digest=DIGEST,
            corpus_digest=DIGEST, metric_definition_ref="metric:1",
            baseline_result_ref="result:base", candidate_result_ref="result:candidate",
            verdict="pass", evidence_refs=[], environment_ref="env:1",
            exit_status="ok", now=NOW,
        )
    result = obj.evaluator_receipt(
        "prop.example", evaluator_id="eval", evaluator_version="1",
        evaluation_kind="replay", candidate_digest=DIGEST,
        corpus_digest=DIGEST, metric_definition_ref="metric:1",
        baseline_result_ref="result:base", candidate_result_ref="result:candidate",
        verdict="pass", evidence_refs=["receipt:1"], environment_ref="env:1",
        exit_status="ok", now=NOW,
    )
    assert result["candidate_digest"] == DIGEST
    assert obj.self_evolving_project()["receipt_count"] == 1


def test_capability_manifest_is_descriptive_not_authority(tmp_path):
    obj = mk(tmp_path)
    result = obj.artifact_capability_manifest(
        "artifact.skill", artifact_kind="skill", requires=["memory.read"],
        provides=["deploy.kubernetes"], side_effect_class="external_write",
        data_scope="project", review_required=True, rollback_supported=True,
        now=NOW,
    )
    assert result["review_required"] is True
    assert "authority" not in result
    assert "permission" not in result


def test_invalid_kind_and_unreplayable_experience_fail_closed(tmp_path):
    obj = mk(tmp_path)
    with pytest.raises(ImprovementError):
        obj.experience_record(
            "exp.bad", artifact_kind="agent_code", scope="project",
            task_ref="run:1", input_snapshot_ref="snap:1", outcome_ref="out:1",
            classification="failure", replayability="replayable", now=NOW,
        )
    with pytest.raises(ImprovementError):
        obj.experience_record(
            "exp.bad", artifact_kind="skill", scope="project",
            task_ref="run:1", input_snapshot_ref="snap:1", outcome_ref="out:1",
            classification="failure", replayability="wat", now=NOW,
        )


def test_activation_observation_records_regression_without_rollback(tmp_path):
    obj = mk(tmp_path)
    result = obj.activation_observation(
        "artifact.skill", "r2", observation_id="obs.regression",
        health_status="regressed", outcome_ref="outcome:regression",
        evidence_refs=["trace:1"], now=NOW,
    )
    assert result["health_status"] == "regressed"
    assert obj.self_evolving_project()["record_count"] == 1


def test_corrupt_self_evolving_log_rejects_new_records(tmp_path):
    obj = mk(tmp_path)
    path = Path(tmp_path) / ".memkraft" / "self-evolving" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ImprovementError) as exc:
        obj.activation_observation(
            "artifact.skill", "r1", observation_id="obs.1",
            health_status="unknown", outcome_ref="outcome:1", now=NOW,
        )
    assert exc.value.code == "E_IMPROVEMENT_LOG_CORRUPT"


def test_no_execution_or_authority_methods_are_exposed_by_substrate(tmp_path):
    obj = mk(tmp_path)
    assert not hasattr(obj, "execute_artifact")
    assert not hasattr(obj, "grant_permission")
    assert not hasattr(obj, "schedule_evolution")
    assert not hasattr(obj, "deploy_artifact")


def test_observation_rejects_unknown_health_status(tmp_path):
    obj = mk(tmp_path)
    with pytest.raises(ImprovementError):
        obj.activation_observation(
            "artifact.skill", "r1", observation_id="obs.bad",
            health_status="auto_rollback", outcome_ref="outcome:1", now=NOW,
        )


def test_experience_never_stores_raw_body_fields(tmp_path):
    obj = mk(tmp_path)
    obj.experience_record(
        "exp.safe", artifact_kind="prompt", scope="project", task_ref="run:1",
        input_snapshot_ref="snapshot:1", outcome_ref="outcome:1",
        classification="success", replayability="partial", now=NOW,
    )
    record = obj.self_evolving_project()["records"][0]
    assert "prompt" not in record
    assert "raw_tool_payload" not in record
    assert "command" not in record
    assert "secret" not in record


def test_duplicate_experience_id_does_not_silently_overwrite(tmp_path):
    obj = mk(tmp_path)
    kwargs = dict(
        artifact_kind="skill", scope="project", task_ref="run:1",
        input_snapshot_ref="snapshot:1", outcome_ref="outcome:1",
        classification="success", replayability="replayable", now=NOW,
    )
    first = obj.experience_record("exp.same", **kwargs)
    second = obj.experience_record("exp.same", **kwargs)
    assert second["id"] == first["id"]
    assert obj.self_evolving_project()["record_count"] == 1


def test_duplicate_experience_identity_with_changed_payload_fails_closed(tmp_path):
    obj = mk(tmp_path)
    kwargs = dict(
        artifact_kind="skill", scope="project", task_ref="run:1",
        input_snapshot_ref="snapshot:1", outcome_ref="outcome:1",
        classification="success", replayability="replayable", now=NOW,
    )
    obj.experience_record("exp.same", **kwargs)
    with pytest.raises(ImprovementError) as exc:
        obj.experience_record("exp.same", **dict(kwargs, outcome_ref="outcome:2"))
    assert exc.value.code == "E_IMPROVEMENT_IDEMPOTENCY_MISMATCH"
    assert obj.self_evolving_project()["record_count"] == 1


def test_scope_is_closed_domain(tmp_path):
    obj = mk(tmp_path)
    with pytest.raises(ImprovementError):
        obj.artifact_capability_manifest(
            "artifact.skill", artifact_kind="skill", data_scope="global", now=NOW,
        )


def test_receipt_inconclusive_is_visible(tmp_path):
    obj = mk(tmp_path)
    result = obj.evaluator_receipt(
        "prop.example", evaluator_id="eval", evaluator_version="1",
        evaluation_kind="replay", candidate_digest=DIGEST,
        corpus_digest=DIGEST, metric_definition_ref="metric:1",
        baseline_result_ref="result:base", candidate_result_ref="result:candidate",
        verdict="inconclusive", evidence_refs=[], environment_ref="env:1",
        exit_status="timeout", now=NOW,
    )
    assert result["verdict"] == "inconclusive"
    assert obj.self_evolving_project()["receipt_count"] == 1


def test_digest_is_stable_for_same_payload(tmp_path):
    first = mk(tmp_path / "a").experience_record(
        "exp.digest", artifact_kind="skill", scope="project", task_ref="run:1",
        input_snapshot_ref="snapshot:1", outcome_ref="outcome:1",
        classification="success", replayability="replayable", now=NOW,
    )
    second = mk(tmp_path / "b").experience_record(
        "exp.digest", artifact_kind="skill", scope="project", task_ref="run:1",
        input_snapshot_ref="snapshot:1", outcome_ref="outcome:1",
        classification="success", replayability="replayable", now=NOW,
    )
    assert first["record_digest"] == second["record_digest"]


def test_shared_scope_manifest_does_not_grant_access(tmp_path):
    obj = mk(tmp_path)
    result = obj.artifact_capability_manifest(
        "artifact.shared", artifact_kind="workflow", data_scope="shared",
        side_effect_class="external_write", now=NOW,
    )
    assert result["data_scope"] == "shared"
    assert "authorization" not in result
