import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-16T10:00:00Z"


def setup_policy(tmp_path):
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    store.correction_capture("corr.alpha", "Use UTC", "Use UTC", now=NOW,
                             required_evaluations=["replay"])
    return store


def evaluate(store, revision="r1", verdict="pass", scope="project", op=None):
    return store.correction_record_evaluation(
        "corr.alpha", revision, "replay", verdict, now=NOW,
        evaluated_scope=scope, operation_id=op)


def promote(store, revision="r1"):
    return store.correction_set_status(
        "corr.alpha", "under_evaluation", "promoted", now=NOW,
        promoted_revision_id=revision)


def test_evaluation_does_not_auto_promote(tmp_path):
    store = setup_policy(tmp_path)
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=NOW)
    evaluate(store)
    assert store.correction_project(now=NOW)["policies"]["corr.alpha"]["status"] == "under_evaluation"
    assert promote(store)["outcome"] == "applied"


def test_promotion_requires_every_latest_pass_on_promoted_revision(tmp_path):
    store = setup_policy(tmp_path)
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=NOW)
    with pytest.raises(ExecutionError) as error:
        promote(store)
    assert error.value.code == "E_CORRECTION_EVALUATION_MISSING"
    evaluate(store, verdict="pass", op="pass-old")
    evaluate(store, verdict="fail", op="fail-new")
    with pytest.raises(ExecutionError) as error:
        promote(store)
    assert error.value.code == "E_CORRECTION_EVALUATION_FAILED"


def test_old_revision_evidence_is_stale_for_new_revision(tmp_path):
    store = setup_policy(tmp_path)
    evaluate(store)
    store.correction_revise("corr.alpha", "r2", "Use ISO UTC", "ISO UTC", now=NOW,
                            base_revision_id="r1", reason="clarify")
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=NOW)
    with pytest.raises(ExecutionError) as error:
        promote(store, "r2")
    assert error.value.code == "E_CORRECTION_EVALUATION_STALE"


def test_transition_matrix_rejects_every_undeclared_pair(tmp_path):
    allowed = {("captured", "under_evaluation"), ("under_evaluation", "promoted"),
               ("under_evaluation", "rejected"), ("promoted", "retired"),
               ("rejected", "under_evaluation")}
    statuses = ["captured", "under_evaluation", "promoted", "rejected", "retired"]
    for source in statuses:
        for target in statuses:
            if (source, target) in allowed:
                continue
            store = setup_policy(tmp_path / (source + "-" + target))
            # Direct pure guard via projected current state is covered by making only
            # captured reachable here; all other supplied sources must mismatch too.
            with pytest.raises(ExecutionError) as error:
                store.correction_set_status("corr.alpha", source, target, now=NOW)
            assert error.value.code == "E_CORRECTION_TRANSITION"


def test_activation_is_promoted_only_and_compare_and_swap(tmp_path):
    store = setup_policy(tmp_path)
    with pytest.raises(ExecutionError) as error:
        store.correction_activate("corr.alpha", "r1", now=NOW)
    assert error.value.code == "E_CORRECTION_TRANSITION"
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=NOW)
    evaluate(store); promote(store)
    store.correction_activate("corr.alpha", "r1", now=NOW,
                              expected_active_revision_id=None, operation_id="activate-first")
    with pytest.raises(ExecutionError) as error:
        store.correction_activate("corr.alpha", "r1", now=NOW,
                                  expected_active_revision_id=None, operation_id="activate-stale")
    assert error.value.code == "E_CORRECTION_ACTIVATION_CONFLICT"


def test_rollback_requires_previously_active_target(tmp_path):
    store = setup_policy(tmp_path)
    store.correction_revise("corr.alpha", "r2", "Use ISO UTC", "ISO UTC", now=NOW,
                            base_revision_id="r1", reason="clarify")
    store.correction_set_status("corr.alpha", "captured", "under_evaluation", now=NOW)
    evaluate(store, "r2"); promote(store, "r2")
    store.correction_activate("corr.alpha", "r2", now=NOW)
    with pytest.raises(ExecutionError) as error:
        store.correction_rollback("corr.alpha", "r1", now=NOW,
                                  expected_active_revision_id="r2")
    assert error.value.code == "E_CORRECTION_ROLLBACK_TARGET"
