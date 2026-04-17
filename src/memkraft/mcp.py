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
    ]


def dispatch(mk, name: str, args: Dict[str, Any]) -> Any:
    """Pure dispatch — no MCP dependency. Unit-testable."""
    if name == "remember":
        mk.update(args["name"], args["info"], source=args.get("source", "mcp"))
        return {"ok": True, "name": args["name"]}
    if name == "search":
        return mk.search(args["query"], fuzzy=args.get("fuzzy", True))
    if name == "recall":
        brief = getattr(mk, "brief", None)
        if callable(brief):
            return brief(args["name"]) or {"found": False, "name": args["name"]}
        return {"found": False, "name": args["name"]}
    if name == "link":
        link_add = getattr(mk, "link_add", None)
        if callable(link_add):
            link_add(args["source"], args["target"])
            return {"ok": True}
        return {"ok": False, "error": "link_add not available in this MemKraft version"}
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
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _tool_schemas()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any]):
        try:
            result = dispatch(mk, name, arguments or {})
            return [types.TextContent(type="text", text=str(result))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"error: {e}")]

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
