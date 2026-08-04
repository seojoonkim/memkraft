"""Slice 10 RED tests for the MKEP/0 CLI transport (plan §12)."""
from __future__ import annotations

import io
import json
from pathlib import Path

from memkraft import MemKraft
from memkraft import execution_cli, execution_state
from memkraft.execution_dispatch import dispatch

REQUEST_ID = "01JKX7Q2M0000000000000000A"
NOW = "2026-08-04T11:22:33Z"


def _request(op="describe"):
    request = {
        "mkep": "0", "kind": "query", "request_id": REQUEST_ID,
        "op": op, "target": {}, "args": {},
    }
    if op != "describe":
        request["now"] = NOW
        request["target"] = {"goal_id": "hermes/cli-transport"}
    return request


def _run(tmp_path, payload, *extra):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = execution_cli.main(
        ["--base-dir", str(tmp_path), *extra],
        stdin=io.StringIO(payload), stdout=stdout, stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_success_is_one_json_response_and_empty_stderr(tmp_path):
    code, stdout, stderr = _run(tmp_path, json.dumps(_request()))
    assert code == 0
    assert stderr == ""
    assert stdout.count("\n") == 1
    assert json.loads(stdout)["ok"] is True
    assert list(tmp_path.iterdir()) == []  # describe does not initialise the base


def test_malformed_json_is_machine_readable_and_diagnostic_is_stderr(tmp_path):
    code, stdout, stderr = _run(tmp_path, "{")
    response = json.loads(stdout)
    assert code == 1
    assert response["ok"] is False
    assert response["error"]["code"] == "E_MALFORMED_JSON"
    assert stderr
    assert stdout.count("\n") == 1


def test_exit_mapping_and_direct_dispatch_equivalence(tmp_path):
    request = _request("assess.run")
    direct = dispatch(MemKraft(base_dir=str(tmp_path)), request)
    code, stdout, stderr = _run(tmp_path, json.dumps(request))
    assert code == 2  # undeclared goal is a state error
    assert stderr
    assert json.loads(stdout) == direct


def test_base_dir_and_lock_timeout_are_honored(tmp_path, monkeypatch):
    seen = {}

    class FakeMemKraft:
        def __init__(self, base_dir=None):
            seen["base_dir"] = base_dir

    def fake_dispatch(store, request):
        seen["timeout"] = execution_state.EXECUTION_LOCK_TIMEOUT_S
        return {"ok": True}

    monkeypatch.setattr(execution_cli, "MemKraft", FakeMemKraft)
    monkeypatch.setattr(execution_cli, "dispatch", fake_dispatch)
    code, stdout, _ = _run(tmp_path, json.dumps(_request()), "--lock-timeout", "0.25")
    assert code == 0
    assert json.loads(stdout) == {"ok": True}
    assert seen == {"base_dir": str(tmp_path), "timeout": 0.25}
    assert execution_state.EXECUTION_LOCK_TIMEOUT_S == 2.0


def test_usage_error_is_64_and_writes_no_stdout(tmp_path):
    code, stdout, stderr = _run(tmp_path, "", "--lock-timeout", "nope")
    assert code == 64
    assert stdout == ""
    assert stderr


def test_cli_wires_exec_call(monkeypatch):
    from memkraft import cli
    seen = {}
    def fake_main(argv=None, **kw):
        seen["argv"] = argv
        return 0
    monkeypatch.setattr(execution_cli, "main", fake_main)
    assert cli.main(["exec", "call", "--base-dir", "/chosen", "--lock-timeout", "0.5"]) == 0
    assert seen["argv"] == ["--base-dir", "/chosen", "--lock-timeout", "0.5"]


def test_error_class_exit_mapping():
    assert execution_cli.exit_code({"ok": True}) == 0
    for cls in ("input", "negotiation", "limits"):
        assert execution_cli.exit_code({"ok": False, "error": {"class": cls}}) == 1
    for cls in ("state", "evidence", "idempotency", "lease", "integrity"):
        assert execution_cli.exit_code({"ok": False, "error": {"class": cls}}) == 2
    assert execution_cli.exit_code({"ok": False, "error": {"class": "io"}}) == 3
