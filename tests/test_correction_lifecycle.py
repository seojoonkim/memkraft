import hashlib
import json

import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-16T10:00:00Z"


def setup_policy(tmp_path):
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    store.correction_capture("corr.alpha", "Use UTC", "Use UTC", now=NOW)
    return store


def digest_for(revision_id, label):
    metadata = {"artifact_id": "corr.alpha", "revision_id": revision_id, "label": label}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True).encode("utf-8")).hexdigest()


def propose(store, revision_id, proposal_id, label, base=None, verdict="pass",
            evaluated_digest=None):
    candidate = digest_for(revision_id, label)
    store.improvement_propose(
        proposal_id, "corr.alpha", "Correction revision", "Host-reviewed correction",
        candidate, now=NOW, base_revision_id=base,
        candidate_locator="correction://corr.alpha/%s" % revision_id,
        required_evaluations=["offline_replay"],
    )
    store.improvement_set_status(proposal_id, "draft", "under_evaluation", now=NOW)
    store.artifact_register_revision(
        "corr.alpha", revision_id, candidate, now=NOW, parent_revision_id=base,
        proposal_id=proposal_id, locator="correction://corr.alpha/%s" % revision_id,
    )
    if verdict is not None:
        store.improvement_record_evaluation(
            proposal_id, "offline_replay", verdict, evaluated_digest or candidate, now=NOW,
            evaluated_base_revision_id=base, evidence_refs=["eval://offline"],
        )
    return candidate


def promote(store, proposal_id, revision_id):
    return store.improvement_set_status(
        proposal_id, "under_evaluation", "promoted", now=NOW,
        promoted_revision_id=revision_id,
    )


def test_evaluation_does_not_auto_promote_or_activate(tmp_path):
    store = setup_policy(tmp_path)
    propose(store, "r1", "prop.alpha-r1", "utc")
    proposal = store.improvement_project(now=NOW)["proposals"]["prop.alpha-r1"]
    assert proposal["status"] == "under_evaluation"
    assert store.compile_corrections(1000)["selected_ids"] == []
    promote(store, "prop.alpha-r1", "r1")
    assert store.compile_corrections(1000)["selected_ids"] == []


def test_promotion_uses_ledger_evidence_gate(tmp_path):
    store = setup_policy(tmp_path)
    propose(store, "r1", "prop.alpha-r1", "utc", verdict=None)
    with pytest.raises(ExecutionError) as error:
        promote(store, "prop.alpha-r1", "r1")
    assert error.value.code == "E_IMPROVEMENT_EVALUATION_MISSING"
    candidate = digest_for("r1", "utc")
    store.improvement_record_evaluation(
        "prop.alpha-r1", "offline_replay", "fail", candidate, now=NOW,
        evidence_refs=["eval://failed"],
    )
    with pytest.raises(ExecutionError) as error:
        promote(store, "prop.alpha-r1", "r1")
    assert error.value.code == "E_IMPROVEMENT_EVALUATION_FAILED"


def test_wrong_revision_cannot_be_promoted(tmp_path):
    store = setup_policy(tmp_path)
    propose(store, "r1", "prop.alpha-r1", "utc")
    other = digest_for("r2", "other")
    store.artifact_register_revision("corr.alpha", "r2", other, now=NOW,
                                     proposal_id="prop.alpha-r1")
    with pytest.raises(ExecutionError) as error:
        promote(store, "prop.alpha-r1", "r2")
    assert error.value.code == "E_IMPROVEMENT_VALIDATION"


def test_activation_is_cas_and_compile_follows_ledger_pointer(tmp_path):
    store = setup_policy(tmp_path)
    propose(store, "r1", "prop.alpha-r1", "utc"); promote(store, "prop.alpha-r1", "r1")
    store.artifact_activate_revision("corr.alpha", "r1", now=NOW,
                                     proposal_id="prop.alpha-r1")
    assert store.compile_corrections(1000)["selected_ids"] == ["corr.alpha"]
    with pytest.raises(ExecutionError) as error:
        store.artifact_activate_revision("corr.alpha", "r1", now=NOW,
                                         expected_active_revision_id=None,
                                         proposal_id="prop.alpha-r1",
                                         operation_id="stale-activation")
    assert error.value.code == "E_IMPROVEMENT_ACTIVATION_CONFLICT"


def test_rollback_to_prior_ledger_target_changes_compiled_revision(tmp_path):
    store = setup_policy(tmp_path)
    propose(store, "r1", "prop.alpha-r1", "utc"); promote(store, "prop.alpha-r1", "r1")
    store.artifact_activate_revision("corr.alpha", "r1", now=NOW,
                                     proposal_id="prop.alpha-r1")
    store.correction_revise("corr.alpha", "r2", "Use ISO UTC", "Use ISO UTC", now=NOW,
                            base_revision_id="r1", reason="clarify")
    propose(store, "r2", "prop.alpha-r2", "iso-utc", base="r1")
    promote(store, "prop.alpha-r2", "r2")
    store.artifact_activate_revision("corr.alpha", "r2", now=NOW,
                                     expected_active_revision_id="r1",
                                     proposal_id="prop.alpha-r2")
    assert "Use ISO UTC" in store.compile_corrections(1000)["rendered_text"]
    store.artifact_rollback_revision("corr.alpha", "r1", now=NOW,
                                     expected_active_revision_id="r2",
                                     proposal_id="prop.alpha-r1")
    rendered = store.compile_corrections(1000)["rendered_text"]
    assert "Use UTC" in rendered and "Use ISO UTC" not in rendered
