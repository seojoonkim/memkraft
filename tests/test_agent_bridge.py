from __future__ import annotations

import io
import json

from memkraft import MemKraft
from memkraft.agent_bridge import AgentBridge, call_stdio


def test_bridge_exposes_common_operations(tmp_path):
    bridge = AgentBridge(MemKraft(base_dir=str(tmp_path)))

    remembered = bridge.call({"operation": "remember", "name": "Simon", "info": "Prefers Korean", "source": "test"})
    recalled = bridge.call({"operation": "recall", "query": "Prefers Korean", "top_k": 3})
    healthy = bridge.call({"operation": "health"})

    assert remembered["ok"] is True
    assert recalled["ok"] is True
    assert recalled["results"]
    assert healthy["ok"] is True
    assert healthy["operation"] == "health"


def test_bridge_rejects_unknown_operation_without_dynamic_dispatch(tmp_path):
    result = AgentBridge(MemKraft(base_dir=str(tmp_path))).call({"operation": "shell"})

    assert result["ok"] is False
    assert result["error"]["code"] == "E_UNKNOWN_OPERATION"


def test_bridge_stdio_is_one_request_one_response(tmp_path):
    request = {"operation": "remember", "name": "Project", "info": "MemKraft", "source": "openclaw"}
    stdin = io.StringIO(json.dumps(request))
    stdout = io.StringIO()

    code = call_stdio(stdin=stdin, stdout=stdout, base_dir=str(tmp_path))
    response = json.loads(stdout.getvalue())

    assert code == 0
    assert response["ok"] is True
    assert response["operation"] == "remember"


def test_bridge_stdio_returns_structured_input_error(tmp_path):
    stdout = io.StringIO()

    code = call_stdio(stdin=io.StringIO('{"operation":'), stdout=stdout, base_dir=str(tmp_path))
    response = json.loads(stdout.getvalue())

    assert code == 1
    assert response["ok"] is False
    assert response["error"]["code"] == "E_INVALID_JSON"


def test_bridge_describe_is_host_neutral(tmp_path):
    result = AgentBridge(MemKraft(base_dir=str(tmp_path))).describe()

    assert result["protocol"] == "memkraft-agent-bridge/1"
    assert result["operations"] == ["feedback", "health", "recall", "remember"]
    assert result["transport"] == "json-stdio"
