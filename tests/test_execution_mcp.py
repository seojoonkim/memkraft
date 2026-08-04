"""Slice 10 RED tests for the read-only MKEP/0 MCP projection (plan §13)."""
from __future__ import annotations

import inspect
import json

from memkraft import MemKraft
from memkraft import mcp
from memkraft.execution_dispatch import MCP_OPS, dispatch as dispatch_mkep

NOW = "2026-08-04T11:22:33Z"
REQUEST_ID = "01JKX7Q2M0000000000000000A"


def _query(op, target=None, args=None):
    request = {
        "mkep": "0", "kind": "query", "request_id": REQUEST_ID,
        "op": op, "target": target or {}, "args": args or {},
    }
    if op != "describe":
        request["now"] = NOW
    return request


def test_existing_four_schema_objects_are_byte_equivalent():
    expected = [
        {"name": "remember", "description": "Store new information about an entity (person/org/project).", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "info": {"type": "string"}, "source": {"type": "string", "default": "mcp"}}, "required": ["name", "info"]}},
        {"name": "search", "description": "Hybrid search over all stored memory.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "fuzzy": {"type": "boolean", "default": True}}, "required": ["query"]}},
        {"name": "recall", "description": "Return a dossier for a single entity.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "link", "description": "Create a wiki-style link between two entities.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}},
    ]
    assert mcp._tool_schemas()[:4] == expected


def test_two_read_only_execution_tools_cover_exactly_four_query_ops():
    schemas = mcp._tool_schemas()
    execution = schemas[4:]
    assert [tool["name"] for tool in execution] == [
        "memkraft_execution_query", "memkraft_execution_describe"
    ]
    assert all(tool["annotations"] == {"readOnlyHint": True} for tool in execution)
    assert set(execution[0]["inputSchema"]["properties"]["op"]["enum"]) == {
        "state.read", "assess.run", "handoff.export"
    }
    assert set(MCP_OPS) == {"describe", "state.read", "assess.run", "handoff.export"}


def test_mcp_dispatch_is_direct_dispatch_equivalent(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    request = _query("state.read", {"goal_id": "hermes/mcp-transport"})
    assert mcp.dispatch_execution(mk, request, now=NOW) == dispatch_mkep(mk, request)


def test_describe_tool_and_query_tool_cannot_reach_apply(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    described = mcp.dispatch(mk, "memkraft_execution_describe", {"request_id": REQUEST_ID})
    assert described["ok"] is True
    rejected = mcp.dispatch(mk, "memkraft_execution_query", {
        "op": "goal.declare", "target": {"goal_id": "hermes/no-apply"},
        "args": {"title": "x"},
    })
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "E_UNKNOWN_OP"
    assert list(tmp_path.iterdir()) == []


def test_json_text_fallback_is_deterministic_and_not_python_repr():
    payload = {"ok": True, "nested": {"z": False, "a": None}}
    text = mcp.json_text(payload)
    assert json.loads(text) == payload
    assert text == '{"nested":{"a":null,"z":false},"ok":true}'
    source = inspect.getsource(mcp)
    assert "str(result)" not in source
    assert 'f"error:' not in source
