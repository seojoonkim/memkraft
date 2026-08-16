import hashlib

import pytest

from memkraft import MemKraft
from memkraft.correction_policy import CorrectionError, CorrectionPolicyMixin

NOW = "2026-08-16T10:00:00Z"


def corrections(revisions=None, **forged):
    policy = {
        "scope": "project", "privacy": "local_private", "task_ref": None,
        "task_class": None, "topic_key": "timezone",
        "revisions": revisions or {
            "r1": {"user_utterance": "Use UTC", "agent_interpretation": "Use UTC"},
            "r2": {"user_utterance": "Use ISO UTC", "agent_interpretation": "Use ISO UTC"},
        },
    }
    policy.update(forged)
    return {"skipped": 0, "policies": {"corr.alpha": policy}}


def improvements(*, status="promoted", promoted="r1", active="r1",
                 proposal_artifact="corr.alpha", revision_proposal="prop.alpha"):
    return {
        "skipped_lines": 0,
        "proposals": {
            "prop.alpha": {
                "artifact_id": proposal_artifact, "status": status,
                "promoted_revision_id": promoted,
            }
        },
        "artifacts": {
            "corr.alpha": {
                "active_revision_id": active,
                "revisions": {
                    "r1": {"proposal_id": revision_proposal},
                    "r2": {"proposal_id": revision_proposal},
                },
            }
        },
    }


class FakeStore(CorrectionPolicyMixin):
    def __init__(self, correction, improvement):
        self.correction = correction
        self.improvement = improvement

    def correction_project(self, **_kwargs):
        return self.correction

    def improvement_project(self, **_kwargs):
        return self.improvement


def compile_fake(correction=None, improvement=None):
    return FakeStore(correction or corrections(), improvement or improvements()).compile_corrections(1000)


def test_correction_self_lifecycle_api_is_absent():
    for name in ("correction_set_status", "correction_activate", "correction_rollback"):
        assert not hasattr(CorrectionPolicyMixin, name)


def test_forged_correction_lifecycle_fields_cannot_authorize_injection():
    result = compile_fake(
        corrections(status="promoted", promoted_revision_id="r2", active_revision_id="r2"),
        improvements(status="draft", active=None),
    )
    assert result["selected_ids"] == []


@pytest.mark.parametrize("improvement", [
    improvements(status="under_evaluation"),
    improvements(active=None),
    improvements(proposal_artifact="corr.other"),
    improvements(promoted="r2"),
    improvements(revision_proposal="prop.other"),
])
def test_incomplete_or_mismatched_ledger_lineage_is_ineligible(improvement):
    assert compile_fake(improvement=improvement)["selected_ids"] == []


def test_exact_promoted_active_link_is_selected():
    assert compile_fake()["selected_ids"] == ["corr.alpha"]


def _govern(store, revision_id, proposal_id, digest, base_revision_id=None):
    store.improvement_propose(
        proposal_id, "corr.alpha", "Correction revision", "Validated correction",
        digest, now=NOW, base_revision_id=base_revision_id,
        candidate_locator="correction://corr.alpha/%s" % revision_id,
        required_evaluations=["offline_replay"],
    )
    store.improvement_record_evaluation(
        proposal_id, "offline_replay", "pass", digest, now=NOW,
        evaluated_base_revision_id=base_revision_id,
        evidence_refs=["sha256:" + digest],
    )
    store.artifact_register_revision(
        "corr.alpha", revision_id, digest, now=NOW,
        locator="correction://corr.alpha/%s" % revision_id, proposal_id=proposal_id,
    )
    store.improvement_set_status(
        proposal_id, "draft", "under_evaluation", now=NOW,
    )
    store.improvement_set_status(
        proposal_id, "under_evaluation", "promoted", now=NOW,
        promoted_revision_id=revision_id,
    )


def test_real_ledger_rollback_switches_selected_correction_revision(tmp_path):
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    store.correction_capture("corr.alpha", "Use UTC", "Use UTC", now=NOW)
    d1 = hashlib.sha256(b"corr.alpha/r1").hexdigest()
    _govern(store, "r1", "prop.alpha-r1", d1)
    store.artifact_activate_revision("corr.alpha", "r1", now=NOW, proposal_id="prop.alpha-r1")
    store.correction_revise("corr.alpha", "r2", "Use ISO UTC", "Use ISO UTC", now=NOW,
                            base_revision_id="r1", reason="clarify")
    d2 = hashlib.sha256(b"corr.alpha/r2").hexdigest()
    _govern(store, "r2", "prop.alpha-r2", d2, base_revision_id="r1")
    store.artifact_activate_revision("corr.alpha", "r2", now=NOW,
                                     expected_active_revision_id="r1", proposal_id="prop.alpha-r2")
    assert "Use ISO UTC" in store.compile_corrections(1000)["rendered_text"]
    store.artifact_rollback_revision("corr.alpha", "r1", now=NOW,
                                     expected_active_revision_id="r2", proposal_id="prop.alpha-r1")
    rendered = store.compile_corrections(1000)["rendered_text"]
    assert "Use UTC" in rendered and "Use ISO UTC" not in rendered


@pytest.mark.parametrize(("correction_skipped", "improvement_skipped", "code"), [
    (1, 0, "E_CORRECTION_LOG_CORRUPT"),
    (0, 1, "E_CORRECTION_IMPROVEMENT_LOG_CORRUPT"),
])
def test_compile_fails_closed_on_either_corrupt_projection(
        correction_skipped, improvement_skipped, code):
    correction = corrections(); correction["skipped"] = correction_skipped
    improvement = improvements(); improvement["skipped_lines"] = improvement_skipped
    with pytest.raises(CorrectionError) as error:
        compile_fake(correction, improvement)
    assert error.value.code == code


def test_correction_projection_has_no_lifecycle_authority(tmp_path):
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    store.correction_capture("corr.alpha", "Use UTC", "Use UTC", now=NOW)
    policy = store.correction_project(now=NOW)["policies"]["corr.alpha"]
    assert "status" not in policy
    assert "promoted_revision_id" not in policy
    assert "active_revision_id" not in policy
    assert "active" not in store.correction_project(now=NOW)


def test_private_pointer_governance_uses_only_opaque_ids_and_digest(tmp_path):
    secret = "RAW PRIVATE SECRET"
    store = MemKraft(base_dir=str(tmp_path)); store.init(verbose=False)
    store.correction_capture("corr.alpha", secret, secret, now=NOW, privacy="private_pointer")
    digest = hashlib.sha256(b"corr.alpha/r1").hexdigest()
    _govern(store, "r1", "prop.alpha-r1", digest)
    ledger = store._improvement_events_path().read_text(encoding="utf-8")
    assert secret not in ledger
    assert "correction://corr.alpha/r1" in ledger
    assert digest in ledger
