import copy
import json
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

T0 = "2026-08-16T10:00:00Z"
T1 = "2026-08-16T10:01:00Z"
T2 = "2026-08-16T10:02:00Z"
T3 = "2026-08-16T10:03:00Z"
T4 = "2026-08-16T10:04:00Z"
T5 = "2026-08-16T10:05:00Z"


def store_at(path):
    store = MemKraft(base_dir=str(path))
    store.init(verbose=False)
    return store


def capture(store, **overrides):
    values = dict(correction_id="corr.alpha", user_utterance="Use UTC",
                  agent_interpretation="Render timestamps in UTC", now=T0,
                  required_evaluations=["replay"])
    values.update(overrides)
    return store.correction_capture(**values)


def log_path(store):
    return Path(store.base_dir) / ".memkraft" / "corrections.jsonl"


def records(store):
    return [json.loads(line) for line in log_path(store).read_text(encoding="utf-8").splitlines()]


def rewrite(store, values):
    log_path(store).write_text("".join(json.dumps(v, sort_keys=True) + "\n" for v in values),
                               encoding="utf-8")


def assert_corrupt_readable_write_closed(store):
    view = store.correction_project(now=T5)
    assert view["skipped"] >= 1
    with pytest.raises(ExecutionError) as error:
        capture(store, correction_id="corr.beta", now=T5)
    assert error.value.code == "E_CORRECTION_LOG_CORRUPT"


def revise(store, revision="r2", base="r1", now=T1, **kwargs):
    return store.correction_revise("corr.alpha", revision, "Use ISO UTC", "Render ISO UTC",
                                   now=now, base_revision_id=base, reason="clarify", **kwargs)


def evaluate(store, revision="r1", verdict="pass", scope="project", now=T1, **kwargs):
    return store.correction_record_evaluation("corr.alpha", revision, "replay", verdict,
                                              now=now, evaluated_scope=scope, **kwargs)


def test_revision_identity_cannot_overwrite_capture_or_prior_revision(tmp_path):
    store = store_at(tmp_path)
    capture(store)
    with pytest.raises(ExecutionError) as error:
        revise(store, revision="r1")
    assert error.value.code == "E_CORRECTION_ALREADY_EXISTS"
    revise(store)
    with pytest.raises(ExecutionError) as error:
        store.correction_revise("corr.alpha", "r1", "changed", "changed", now=T2,
                                base_revision_id="r2", reason="overwrite capture")
    assert error.value.code == "E_CORRECTION_ALREADY_EXISTS"
    projection = store.correction_project(now=T2)["policies"]["corr.alpha"]
    assert projection["revisions"]["r1"]["user_utterance"] == "Use UTC"


def test_truncated_text_idempotency_binds_full_original_input(tmp_path):
    store = store_at(tmp_path)
    prefix = "x" * 2000
    first = capture(store, user_utterance=prefix + "A", operation_id="capture-long")
    assert first["record"]["user_utterance"] == prefix
    assert first["record"]["user_utterance_input_length"] == 2001
    assert len(first["record"]["user_utterance_input_sha256"]) == 64
    assert capture(store, user_utterance=prefix + "A",
                   operation_id="capture-long")["outcome"] == "already_applied"
    with pytest.raises(ExecutionError) as error:
        capture(store, user_utterance=prefix + "B", operation_id="capture-long")
    assert error.value.code == "E_CORRECTION_IDEMPOTENCY_MISMATCH"


@pytest.mark.parametrize("mutation", [
    lambda rs: rs[0].update(extra_field="forbidden"),
    lambda rs: rs[0].pop("privacy"),
    lambda rs: rs[0].update(authority_verified=True),
    lambda rs: rs[0].update(task_ref=None, scope="task"),
    lambda rs: rs[0].update(host_authorization_ref=None, privacy="local_private", scope="shared"),
    lambda rs: rs[0].update(required_evaluations="replay"),
    lambda rs: rs[0].update(event_seq=True),
])
def test_persisted_schema_and_context_corruption_is_skipped_and_write_closed(tmp_path, mutation):
    store = store_at(tmp_path)
    capture(store)
    values = records(store)
    mutation(values)
    rewrite(store, values)
    assert_corrupt_readable_write_closed(store)


def test_duplicate_operation_id_and_noncontiguous_sequence_are_corrupt(tmp_path):
    for name, mutate in [
        ("op", lambda rs: rs[1].update(operation_id=rs[0]["operation_id"])),
        ("gap", lambda rs: rs[1].update(event_seq=3)),
    ]:
        store = store_at(tmp_path / name)
        capture(store)
        revise(store)
        values = records(store)
        mutate(values)
        rewrite(store, values)
        assert_corrupt_readable_write_closed(store)


def test_duplicate_capture_revision_and_orphan_or_out_of_order_are_corrupt(tmp_path):
    cases = {}
    store = store_at(tmp_path / "duplicate-capture")
    capture(store); values = records(store); duplicate = copy.deepcopy(values[0])
    duplicate.update(id="duplicate", operation_id="duplicate", event_seq=2)
    cases["duplicate-capture"] = (store, values + [duplicate])

    store = store_at(tmp_path / "duplicate-revision")
    capture(store); revise(store); values = records(store); duplicate = copy.deepcopy(values[1])
    duplicate.update(id="duplicate", operation_id="duplicate", event_seq=3)
    cases["duplicate-revision"] = (store, values + [duplicate])

    store = store_at(tmp_path / "orphan")
    capture(store); revise(store); values = records(store)
    cases["orphan"] = (store, [values[1], values[0]])

    store = store_at(tmp_path / "bad-base")
    capture(store); revise(store); values = records(store); values[1]["base_revision_id"] = "missing"
    cases["bad-base"] = (store, values)

    for store, values in cases.values():
        rewrite(store, values)
        assert_corrupt_readable_write_closed(store)


def test_shared_evaluation_evidence_carries_own_safety_and_authorization(tmp_path):
    store = store_at(tmp_path)
    capture(store)
    with pytest.raises(ExecutionError) as error:
        evaluate(store, scope="shared", privacy="public_safe")
    assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"
    with pytest.raises(ExecutionError) as error:
        evaluate(store, scope="shared", host_authorization_ref="grant://host/1")
    assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"
    result = evaluate(store, scope="shared", privacy="public_safe",
                      host_authorization_ref="grant://host/1")
    assert result["record"]["scope"] == "shared"
    assert result["record"]["evaluated_scope"] == "shared"



def test_widening_requires_current_revision_and_evidence_after_revision_and_transition(tmp_path):
    store = store_at(tmp_path)
    capture(store, scope="task", task_ref="task://1")
    revise(store, now=T2)
    evaluate(store, revision="r2", scope="project", now=T1)
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=T3,
                                      evidence_refs=["eval://stale"])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"
    evaluate(store, revision="r2", scope="project", now=T3, operation_id="fresh")
    store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=T3,
                                  evidence_refs=["eval://fresh"])
    store.correction_adjust_scope("corr.alpha", "r2", "project", "task", now=T4,
                                  task_ref="task://1", evidence_refs=["review://narrow"])
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=T5,
                                      evidence_refs=["eval://reused"])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"
    revise(store, revision="r3", base="r2", now=T5)
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=T5,
                                      evidence_refs=["eval://old-revision"])
    assert error.value.code == "E_CORRECTION_WIDENING_EVIDENCE"



@pytest.mark.parametrize("field,value", [
    ("user_utterance", None), ("agent_interpretation", 7),
])
def test_malformed_revise_text_types_raise_correction_error(tmp_path, field, value):
    store = store_at(tmp_path)
    capture(store)
    kwargs = {"user_utterance": "u", "agent_interpretation": "a"}
    kwargs[field] = value
    with pytest.raises(ExecutionError) as error:
        store.correction_revise("corr.alpha", "r2", kwargs["user_utterance"],
                                kwargs["agent_interpretation"], now=T1,
                                base_revision_id="r1", reason="types")
    assert error.value.code == "E_CORRECTION_VALIDATION"


def test_scope_change_validates_target_context_and_shared_authorization(tmp_path):
    store = store_at(tmp_path)
    capture(store, scope="project")
    for kwargs in [
        dict(to_scope="task"),
        dict(to_scope="task_class"),
        dict(to_scope="shared", privacy="public_safe"),
        dict(to_scope="shared", host_authorization_ref="grant://host/1"),
    ]:
        with pytest.raises(ExecutionError) as error:
            store.correction_adjust_scope("corr.alpha", "r1", "project",
                                          kwargs.pop("to_scope"), now=T1,
                                          evidence_refs=["review://1"], **kwargs)
        assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"


def test_authority_verified_true_for_any_claim_is_rejected(tmp_path):
    store = store_at(tmp_path)
    for claim in ("agent", "human", "system"):
        with pytest.raises(ExecutionError) as error:
            capture(store, correction_id="corr.%s" % claim,
                    authority_claim=claim, authority_verified=True)
        assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"



def test_duplicate_record_id_is_corrupt(tmp_path):
    store = store_at(tmp_path)
    capture(store); revise(store)
    values = records(store); values[1]["id"] = values[0]["id"]; rewrite(store, values)
    assert_corrupt_readable_write_closed(store)



def test_forged_scope_from_state_is_corrupt(tmp_path):
    store = store_at(tmp_path)
    capture(store, scope="project")
    store.correction_adjust_scope("corr.alpha", "r1", "project", "task", now=T1,
                                  task_ref="task://1", evidence_refs=["review://1"])
    values = records(store)
    values[-1]["from_scope"] = "shared"
    rewrite(store, values)
    assert_corrupt_readable_write_closed(store)


def test_forged_widening_without_fresh_target_evidence_is_corrupt(tmp_path):
    store = store_at(tmp_path)
    capture(store, scope="task", task_ref="task://1")
    revise(store, now=T1)
    store.correction_record_evaluation("corr.alpha", "r2", "replay", "pass",
                                       now=T2, evaluated_scope="project")
    store.correction_adjust_scope("corr.alpha", "r2", "task", "project", now=T3,
                                  evidence_refs=["eval://fresh"])
    values = records(store)
    values.pop(2)
    values[-1]["event_seq"] = 3
    rewrite(store, values)
    assert_corrupt_readable_write_closed(store)


def test_one_character_private_pointer_capture_projects_and_remains_writable(tmp_path):
    store = store_at(tmp_path)
    result = capture(store, user_utterance="x", agent_interpretation="y",
                     privacy="private_pointer")
    assert result["record"]["user_utterance_input_length"] == 1
    assert result["record"]["user_utterance"] == "[private_pointer]"
    view = store.correction_project(now=T1)
    assert view["skipped"] == 0
    assert view["policies"]["corr.alpha"]["privacy"] == "private_pointer"
    store.correction_revise("corr.alpha", "r2", "z", "q", now=T1,
                            base_revision_id="r1", reason="short")
    assert store.correction_project(now=T2)["skipped"] == 0


def test_private_pointer_revision_omission_never_persists_raw_text(tmp_path):
    store = store_at(tmp_path)
    capture(store, privacy="private_pointer")
    user_secret = "RAW-USER-SECRET-NEVER-PERSIST"
    agent_secret = "RAW-AGENT-SECRET-NEVER-PERSIST"
    store.correction_revise("corr.alpha", "r2", user_secret, agent_secret, now=T1,
                            base_revision_id="r1", reason="clarify")
    raw = log_path(store).read_text(encoding="utf-8")
    assert user_secret not in raw
    assert agent_secret not in raw
    revision = records(store)[-1]
    assert revision["privacy"] == "private_pointer"
    assert revision["user_utterance"] == "[private_pointer]"


def test_scope_adjustment_preserves_privacy_and_rejects_unauthorized_downgrade(tmp_path):
    store = store_at(tmp_path)
    capture(store, privacy="private_pointer")
    result = store.correction_adjust_scope(
        "corr.alpha", "r1", "project", "task", now=T1,
        task_ref="task://1", evidence_refs=["review://1"])
    assert result["record"]["privacy"] == "private_pointer"
    with pytest.raises(ExecutionError) as error:
        store.correction_adjust_scope(
            "corr.alpha", "r1", "task", "project", now=T2,
            privacy="local_private", evidence_refs=["review://2"])
    assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"


def test_compile_fails_closed_when_projection_skipped_corrupt_log(tmp_path):
    store = store_at(tmp_path)
    capture(store)
    with log_path(store).open("ab") as stream:
        stream.write(b"not-json\n")
    assert store.correction_project(now=T1)["skipped"] == 1
    with pytest.raises(ExecutionError) as error:
        store.compile_corrections(budget=100)
    assert error.value.code == "E_CORRECTION_LOG_CORRUPT"
