import json

import pytest

from memkraft.development_experience import compile_development_experience


def _tool_call(call_id, name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_compile_records_failed_route_and_later_verified_success_without_secrets():
    secret = "sk-test-super-secret-value"
    messages = [
        {"role": "user", "content": "Fix checkout retry regression"},
        _tool_call(
            "call-fail",
            "terminal",
            {
                "command": "API_KEY={} python3 -m pytest tests/test_checkout.py -q".format(secret),
                "workdir": "/Users/alice/private/checkout",
            },
        ),
        {
            "role": "tool",
            "tool_call_id": "call-fail",
            "content": json.dumps({"exit_code": 1, "output": "AssertionError: expected retry"}),
        },
        _tool_call(
            "call-patch",
            "patch",
            {"path": "/Users/alice/private/checkout/retry.py", "old_string": "x", "new_string": "y"},
        ),
        {
            "role": "tool",
            "tool_call_id": "call-patch",
            "content": json.dumps({"success": True}),
        },
        _tool_call(
            "call-pass",
            "terminal",
            {"command": "python3 -m pytest tests/test_checkout.py -q", "workdir": "/Users/alice/private/checkout"},
        ),
        {
            "role": "tool",
            "tool_call_id": "call-pass",
            "content": json.dumps({"exit_code": 0, "output": "1 passed"}),
        },
        {"role": "assistant", "content": "Fixed and verified."},
    ]

    episodes = compile_development_experience(
        "Fix checkout retry regression",
        messages,
        session_id="session-1",
    )

    assert [episode.status for episode in episodes] == ["failure", "success"]
    assert episodes[0].route == "terminal:python3 -m pytest"
    assert episodes[0].error_signature == "AssertionError"
    assert episodes[1].route == "patch -> terminal:python3 -m pytest"
    assert episodes[1].verified_by == "terminal:python3 -m pytest"
    serialized = "\n".join(episode.lesson for episode in episodes)
    assert secret not in serialized
    assert "/Users/alice" not in serialized


def test_compile_does_not_promote_an_unverified_failure():
    messages = [
        {"role": "user", "content": "Fix checkout retry regression"},
        _tool_call("call-fail", "terminal", {"command": "python3 -m pytest tests/test_checkout.py -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-fail",
            "content": json.dumps({"exit_code": 1, "output": "AssertionError"}),
        },
        {"role": "assistant", "content": "Still investigating."},
    ]

    assert compile_development_experience(
        "Fix checkout retry regression", messages, session_id="session-1"
    ) == []


def test_compile_does_not_promote_when_the_last_verification_failed():
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail-1", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail-1", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-pass", "content": '{"exit_code": 0, "output": "passed"}'},
        _tool_call("call-fail-2", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail-2", "content": '{"exit_code": 1, "output": "failed"}'},
    ]

    assert compile_development_experience(task, messages, session_id="session-1") == []


def test_compile_scopes_failures_to_the_current_user_turn():
    messages = [
        {"role": "user", "content": "Old checkout task"},
        _tool_call("old-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "old-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        {"role": "assistant", "content": "Not done."},
        {"role": "user", "content": "New unrelated task"},
        _tool_call("new-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "new-pass", "content": '{"exit_code": 0, "output": "passed"}'},
    ]

    assert compile_development_experience(
        "New unrelated task", messages, session_id="session-1"
    ) == []


def test_compile_rejects_a_mutation_after_the_successful_verifier():
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-pass", "content": '{"exit_code": 0, "output": "passed"}'},
        _tool_call("late-patch", "patch", {"path": "retry.py", "old_string": "x", "new_string": "y"}),
        {"role": "tool", "tool_call_id": "late-patch", "content": '{"success": true}'},
    ]

    assert compile_development_experience(task, messages, session_id="session-1") == []


@pytest.mark.parametrize("non_verifier", ["npm install", "cargo fmt", "go env"])
def test_compile_does_not_treat_tool_family_names_as_verifiers(non_verifier):
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-success", "terminal", {"command": non_verifier}),
        {"role": "tool", "tool_call_id": "call-success", "content": '{"exit_code": 0, "output": "ok"}'},
    ]

    assert compile_development_experience(task, messages, session_id="session-1") == []


def test_compile_redacts_spaced_bearer_credentials():
    secret = "super-secret-bearer-value"
    task = "Fix auth with Bearer " + secret
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-pass", "content": '{"exit_code": 0, "output": "passed"}'},
    ]

    episodes = compile_development_experience(task, messages, session_id="session-1")
    assert secret not in json.dumps([episode.__dict__ for episode in episodes])


def test_capture_is_idempotent_and_recall_injects_avoid_and_reuse(tmp_path):
    from memkraft import MemKraft

    store = MemKraft(base_dir=str(tmp_path / "memkraft"))
    store.init(verbose=False)
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "python3 -m pytest tests/test_checkout.py -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-fail",
            "content": json.dumps({"exit_code": 1, "output": "AssertionError"}),
        },
        _tool_call("call-patch", "patch", {"path": "retry.py", "old_string": "x", "new_string": "y"}),
        {"role": "tool", "tool_call_id": "call-patch", "content": json.dumps({"success": True})},
        _tool_call("call-pass", "terminal", {"command": "python3 -m pytest tests/test_checkout.py -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-pass",
            "content": json.dumps({"exit_code": 0, "output": "1 passed"}),
        },
    ]

    first = store.development_capture_turn(task, messages, session_id="session-1")
    second = store.development_capture_turn(task, messages, session_id="session-1")

    assert first["captured"] == 2
    assert second["captured"] == 0
    assert second["duplicates"] == 2
    assert store.reasoning_stats()["total"] == 2
    context = store.development_inject_for_task("checkout retry regression")
    assert "avoid:" in context
    assert "reuse:" in context
    assert "terminal:python3 -m pytest" in context

    replay_with_new_call_ids = json.loads(json.dumps(messages).replace("call-", "repeat-call-"))
    repeated = store.development_capture_turn(
        task, replay_with_new_call_ids, session_id="session-1"
    )
    assert repeated["captured"] == 0
    assert repeated["duplicates"] == 2
    assert store.reasoning_stats()["total"] == 2


def test_provider_round_trip_learns_without_a_hermes_core_hook(tmp_path):
    pytest.importorskip("agent.memory_provider")
    from memkraft.hermes_provider import MemKraftMemoryProvider

    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "python3 -m pytest tests/test_checkout.py -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-fail",
            "content": json.dumps({"exit_code": 1, "output": "AssertionError"}),
        },
        _tool_call("call-patch", "patch", {"path": "retry.py", "old_string": "x", "new_string": "y"}),
        {"role": "tool", "tool_call_id": "call-patch", "content": json.dumps({"success": True})},
        _tool_call("call-pass", "terminal", {"command": "python3 -m pytest tests/test_checkout.py -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-pass",
            "content": json.dumps({"exit_code": 0, "output": "1 passed"}),
        },
        {"role": "assistant", "content": "Fixed and verified."},
    ]
    first = MemKraftMemoryProvider()
    first.initialize("session-1", hermes_home=str(tmp_path))
    first.sync_turn(task, "Fixed and verified.", session_id="session-1", messages=messages)

    restarted = MemKraftMemoryProvider()
    restarted.initialize("session-2", hermes_home=str(tmp_path))
    context = restarted.prefetch("checkout retry regression", session_id="session-2")

    assert "ReasoningBank task context" in context
    assert "Avoid repeating" in context
    assert "Reuse" in context


def test_provider_can_disable_automatic_development_experience(tmp_path, monkeypatch):
    pytest.importorskip("agent.memory_provider")
    from memkraft.hermes_provider import MemKraftMemoryProvider

    monkeypatch.setenv("MEMKRAFT_HERMES_DEV_EXPERIENCE", "off")
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-pass", "content": '{"exit_code": 0, "output": "passed"}'},
    ]
    provider = MemKraftMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path))
    provider.sync_turn(task, "Fixed.", session_id="session-1", messages=messages)

    assert provider._store.reasoning_stats()["total"] == 0
    assert "ReasoningBank task context" not in provider.prefetch(
        "checkout retry regression", session_id="session-2"
    )


def test_compile_fails_closed_for_dict_verifier_without_explicit_status():
    task = "Fix checkout retry regression"
    messages = [
        {"role": "user", "content": task},
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-unknown", "terminal", {"command": "pytest -q"}),
        {
            "role": "tool",
            "tool_call_id": "call-unknown",
            "content": '{"output": "Traceback (most recent call last): AssertionError"}',
        },
    ]

    assert compile_development_experience(task, messages, session_id="session-1") == []


def test_compile_skips_malformed_messages_and_redacts_platform_paths():
    task = r"Fix C:\Users\alice\private\checkout and /private/var/folders/ab/secret"
    messages = [
        "malformed",
        {"role": "user", "content": task},
        None,
        _tool_call("call-fail", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-fail", "content": '{"exit_code": 1, "output": "failed"}'},
        _tool_call("call-pass", "terminal", {"command": "pytest -q"}),
        {"role": "tool", "tool_call_id": "call-pass", "content": '{"exit_code": 0, "output": "passed"}'},
    ]

    episodes = compile_development_experience(task, messages, session_id="session-1")
    serialized = json.dumps([episode.__dict__ for episode in episodes])
    assert episodes
    assert "alice" not in serialized
    assert "/private/var/folders" not in serialized
