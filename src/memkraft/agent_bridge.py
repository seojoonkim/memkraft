"""Host-neutral bridge for agent runtimes.

This module is deliberately independent of Hermes, OpenClaw, MCP, or any
specific agent SDK. Hosts can invoke the same closed operation contract over
JSON stdio or wrap :class:`AgentBridge` directly.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from typing import Any, Dict, Optional, TextIO

from .adapter import MemoryAdapter
from . import MemKraft


_PROTOCOL = "memkraft-agent-bridge/1"
_OPERATIONS = ("feedback", "health", "recall", "remember")


class AgentBridge:
    """Closed, transport-neutral operation facade for external agents."""

    def __init__(self, memory: Any):
        self.adapter = memory if isinstance(memory, MemoryAdapter) else MemoryAdapter(memory)

    def describe(self) -> Dict[str, Any]:
        return {
            "protocol": _PROTOCOL,
            "transport": "json-stdio",
            "operations": list(_OPERATIONS),
        }

    def call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(request, dict):
            return self._error("", "E_INVALID_REQUEST", "request must be a JSON object")
        operation = request.get("operation")
        if operation == "health":
            return self.adapter.health()
        if operation == "remember":
            if not isinstance(request.get("name"), str) or not isinstance(request.get("info"), str):
                return self._error(operation, "E_INVALID_REQUEST", "remember requires string name and info")
            return self.adapter.remember(
                request["name"], request["info"], source=str(request.get("source") or "agent")
            )
        if operation == "recall":
            if not isinstance(request.get("query"), str):
                return self._error(operation, "E_INVALID_REQUEST", "recall requires string query")
            top_k = request.get("top_k")
            if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1 or top_k > 100):
                return self._error(operation, "E_INVALID_REQUEST", "top_k must be an integer from 1 to 100")
            return self.adapter.recall(request["query"], top_k=top_k)
        if operation == "feedback":
            required = ("classification", "task_ref", "outcome_ref", "input_snapshot_ref")
            missing = [key for key in required if not isinstance(request.get(key), str) or not request[key]]
            if missing:
                return self._error(operation, "E_INVALID_REQUEST", "missing required fields: " + ", ".join(missing))
            allowed = {
                "classification", "task_ref", "outcome_ref", "input_snapshot_ref", "artifact_kind",
                "replayability", "scope", "evidence_refs", "model_ref", "tool_refs", "now", "experience_id",
            }
            payload = {key: value for key, value in request.items() if key in allowed}
            return self.adapter.feedback(**payload)
        return self._error(operation or "", "E_UNKNOWN_OPERATION", "operation is not supported")

    @staticmethod
    def _error(operation: str, code: str, message: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "error": {"code": code, "message": message, "retryable": False, "details": {}},
        }


def call_stdio(
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    *,
    base_dir: Optional[str] = None,
) -> int:
    """Process exactly one JSON request and emit exactly one JSON response."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    try:
        request = json.loads(input_stream.read())
    except (UnicodeError, json.JSONDecodeError) as error:
        response = AgentBridge._error("", "E_INVALID_JSON", str(error))
        code = 1
    else:
        # Core APIs historically print human progress messages. A bridge
        # response is a machine protocol, so keep stdout strictly JSON.
        with redirect_stdout(io.StringIO()):
            response = AgentBridge(MemKraft(base_dir=base_dir)).call(request)
        code = 0 if response.get("ok") is True else 1
    output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return code


__all__ = ["AgentBridge", "call_stdio"]
