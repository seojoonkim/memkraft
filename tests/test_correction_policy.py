from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-16T10:00:00Z"
LATER = "2026-08-16T10:01:00Z"


def mk(tmp_path):
    value = MemKraft(base_dir=str(tmp_path))
    value.init(verbose=False)
    return value


def capture(store, **kw):
    args = dict(correction_id="corr.alpha", user_utterance="Use UTC",
                agent_interpretation="Always render timestamps in UTC",
                required_evaluations=["replay"], now=NOW)
    args.update(kw)
    return store.correction_capture(**args)


def path(store):
    return Path(store.base_dir) / ".memkraft" / "corrections.jsonl"


def test_capture_keeps_roles_separate_and_defaults_envelope(tmp_path):
    store = mk(tmp_path)
    result = capture(store)
    record = result["record"]
    assert result["event_seq"] == 1
    assert record["user_utterance"] == "Use UTC"
    assert record["agent_interpretation"] == "Always render timestamps in UTC"
    assert record["revision_id"] == "r1"
    assert record["scope"] == "project"
    assert record["privacy"] == "local_private"
    assert record["authority_verified"] is False
    assert record["truncated"] is False


def test_capture_truncates_each_role_explicitly(tmp_path):
    record = capture(mk(tmp_path), user_utterance="u" * 2001,
                     agent_interpretation="a" * 2001)["record"]
    assert len(record["user_utterance"]) == 2000
    assert len(record["agent_interpretation"]) == 2000
    assert record["truncated"] is True


def test_private_pointer_never_persists_raw_role_content(tmp_path):
    store = mk(tmp_path)
    record = capture(
        store,
        privacy="private_pointer",
        user_utterance="SECRET USER CONTENT",
        agent_interpretation="SECRET AGENT CONTENT",
    )["record"]
    assert record["user_utterance"] == "[private_pointer]"
    assert record["agent_interpretation"] == "[private_pointer]"
    raw = path(store).read_text(encoding="utf-8")
    assert "SECRET USER CONTENT" not in raw
    assert "SECRET AGENT CONTENT" not in raw


def test_scope_context_and_shared_privacy_fail_closed(tmp_path):
    store = mk(tmp_path)
    for kwargs in ({"scope": "task"}, {"scope": "task_class"},
                   {"scope": "shared", "privacy": "public_safe"},
                   {"scope": "shared", "host_authorization_ref": "grant://host/1"}):
        with pytest.raises(ExecutionError) as error:
            capture(store, correction_id="corr.%s" % len(kwargs), **kwargs)
        assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"
    assert capture(store, correction_id="corr.task", scope="task",
                   task_ref="task://run/1")["outcome"] == "applied"
    assert capture(store, correction_id="corr.class", scope="task_class",
                   task_class="deployment")["outcome"] == "applied"
    assert capture(store, correction_id="corr.shared", scope="shared",
                   privacy="public_safe", host_authorization_ref="grant://host/1")["outcome"] == "applied"


def test_idempotency_and_duplicate_identity(tmp_path):
    store = mk(tmp_path)
    first = capture(store, operation_id="capture-1")
    second = capture(store, operation_id="capture-1")
    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    with pytest.raises(ExecutionError) as error:
        capture(store, operation_id="capture-1", agent_interpretation="Different")
    assert error.value.code == "E_CORRECTION_IDEMPOTENCY_MISMATCH"
    with pytest.raises(ExecutionError) as error:
        capture(store, operation_id="capture-2")
    assert error.value.code == "E_CORRECTION_ALREADY_EXISTS"


def test_naive_clock_and_agent_verified_are_rejected(tmp_path):
    store = mk(tmp_path)
    with pytest.raises(ExecutionError) as error:
        capture(store, now="2026-08-16T10:00:00")
    assert error.value.code == "E_CORRECTION_VALIDATION"
    with pytest.raises(ExecutionError) as error:
        capture(store, authority_verified=True)
    assert error.value.code == "E_CORRECTION_SCOPE_UNAUTHORIZED"


def test_revise_is_append_only_and_requires_current_base(tmp_path):
    store = mk(tmp_path)
    capture(store)
    revised = store.correction_revise(
        "corr.alpha", "r2", "Use ISO UTC", "Render ISO-8601 UTC", now=LATER,
        base_revision_id="r1", reason="Clarified format")
    assert revised["event_seq"] == 2
    assert revised["record"]["record_type"] == "correction_revision"
    with pytest.raises(ExecutionError) as error:
        store.correction_revise(
            "corr.alpha", "r3", "x", "y", now=LATER,
            base_revision_id="r1", reason="stale")
    assert error.value.code == "E_CORRECTION_TRANSITION"


def test_corruption_is_readable_but_writes_fail_closed(tmp_path):
    store = mk(tmp_path)
    capture(store)
    with path(store).open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    view = store.correction_project(now=LATER)
    assert view["skipped"] == 1
    assert "corr.alpha" in view["policies"]
    with pytest.raises(ExecutionError) as error:
        capture(store, correction_id="corr.beta")
    assert error.value.code == "E_CORRECTION_LOG_CORRUPT"
