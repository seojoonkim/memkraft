"""memkraft.mcp — MCP stdio server exposing MemKraft primitives.

Requires the `mcp` extra:

    pip install 'memkraft[mcp]'

Run:

    python -m memkraft.mcp

Exposes four tools:
    - remember(name, info, source)
    - search(query, fuzzy)
    - recall(name)
    - link(source, target)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Dict

from . import __version__


_MCP_HINT = (
    "mcp package not installed. install it with:\n"
    "    pip install 'memkraft[mcp]'\n"
    "then retry `python -m memkraft.mcp`"
)


def _require_mcp():
    try:
        import mcp  # noqa: F401
        from mcp.server import Server  # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401
        import mcp.types as types  # noqa: F401
    except ImportError:
        print(f"❌ {_MCP_HINT}", file=sys.stderr)
        sys.exit(2)


def _tool_schemas() -> list:
    """Return tool schemas shared between extras-installed and stub modes."""
    return [
        {
            "name": "remember",
            "description": "Store new information about an entity (person/org/project).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "info": {"type": "string"},
                    "source": {"type": "string", "default": "mcp"},
                },
                "required": ["name", "info"],
            },
        },
        {
            "name": "search",
            "description": "Hybrid search over all stored memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fuzzy": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
        },
        {
            "name": "recall",
            "description": "Return a dossier for a single entity.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "link",
            "description": "Create a wiki-style link between two entities.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["source", "target"],
            },
        },
        {
            "name": "memkraft_execution_query",
            "description": (
                "Read execution state, run an advisory assessment, or export a "
                "handoff. should_run is advisory and does not authorize dispatch."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "op": {"type": "string", "enum": [
                        "state.read", "assess.run", "handoff.export"
                    ]},
                    "target": {"type": "object"},
                    "args": {"type": "object"},
                    "capabilities_digest": {"type": "string"},
                },
                "required": ["request_id", "op", "target", "args"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "memkraft_execution_describe",
            "description": "Describe MKEP/0 execution capabilities without reading or writing state.",
            "inputSchema": {
                "type": "object",
                "properties": {"request_id": {"type": "string"}},
                "required": ["request_id"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]


def json_text(payload: Dict[str, Any]) -> str:
    """Deterministic JSON TextContent fallback."""
    from .execution_protocol import MAX_PROTOCOL_ARRAY_LENGTH, mkcjson
    return mkcjson(payload, MAX_PROTOCOL_ARRAY_LENGTH).decode("utf-8")


def dispatch_execution(mk, request: Dict[str, Any], now: str = "") -> Dict[str, Any]:
    """Read-only conduit to the common dispatcher; no apply op is reachable."""
    from .execution_dispatch import MCP_OPS, dispatch as dispatch_mkep
    envelope = dict(request)
    op = envelope.get("op")
    if op not in MCP_OPS:
        envelope["op"] = "__mcp_read_only__"
    if op != "describe" and "now" not in envelope:
        envelope["now"] = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return dispatch_mkep(mk, envelope)


def dispatch(mk, name: str, args: Dict[str, Any]) -> Any:
    """Pure dispatch — no MCP dependency. Unit-testable."""
    if name == "remember":
        mk.update(args["name"], args["info"], source=args.get("source", "mcp"))
        return {"ok": True, "name": args["name"]}
    if name == "search":
        return mk.search(args["query"], fuzzy=args.get("fuzzy", True))
    if name == "recall":
        brief = getattr(mk, "brief", None)
        if not callable(brief):
            return {"found": False, "name": args["name"]}
        text = brief(args["name"]) or ""
        # Existence is decided by file presence, not by truthiness of the brief
        # text. ``brief()`` always returns a non-empty dossier (even a
        # "not found" notice), so relying on the old ``or`` fallback masked
        # successful lookups as ``found: False``.
        slug = mk._slugify(args["name"])
        found = (mk.entities_dir / f"{slug}.md").exists() or (
            mk.live_notes_dir / f"{slug}.md"
        ).exists()
        return {"found": found, "name": args["name"], "text": text}
    if name == "link":
        link_add = getattr(mk, "link_add", None)
        if callable(link_add):
            link_add(args["source"], args["target"])
            return {"ok": True}
        return {"ok": False, "error": "link_add not available in this MemKraft version"}
    if name == "memkraft_execution_describe":
        return dispatch_execution(mk, {
            "mkep": "0", "kind": "query", "request_id": args.get("request_id"),
            "op": "describe", "target": {}, "args": {},
        })
    if name == "memkraft_execution_query":
        request = {
            "mkep": "0", "kind": "query", "request_id": args.get("request_id"),
            "op": args.get("op"), "target": args.get("target", {}),
            "args": args.get("args", {}),
        }
        if "capabilities_digest" in args:
            request["capabilities_digest"] = args["capabilities_digest"]
        return dispatch_execution(mk, request)
    raise ValueError(f"unknown tool: {name}")


def main() -> None:
    _require_mcp()

    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    from .core import MemKraft

    mk = MemKraft()
    server = Server("memkraft")

    @server.list_tools()
    async def _list_tools():
        tools = []
        for schema in _tool_schemas():
            kwargs = {
                "name": schema["name"], "description": schema["description"],
                "inputSchema": schema["inputSchema"],
            }
            if "annotations" in schema:
                kwargs["annotations"] = schema["annotations"]
            tools.append(types.Tool(**kwargs))
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any]):
        try:
            result = dispatch(mk, name, arguments or {})
            content = [types.TextContent(type="text", text=json_text(result))]
            if name.startswith("memkraft_execution_") and hasattr(types, "CallToolResult"):
                return types.CallToolResult(
                    content=content, structuredContent=result,
                    isError=not result.get("ok", True),
                )
            return content
        except Exception as error:
            payload = {"ok": False, "error": {"class": "io", "code": "E_INTERNAL",
                       "message": "%s" % error, "retryable": True, "details": {}}}
            return [types.TextContent(type="text", text=json_text(payload))]

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
