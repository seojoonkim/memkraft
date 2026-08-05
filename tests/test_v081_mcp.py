"""v0.8.1 — MCP dispatch + extras hint."""
import asyncio
import datetime
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import MemKraft
from memkraft import mcp as mcp_mod


@pytest.fixture
def mk():
    d = tempfile.mkdtemp(prefix="mk-mcp-")
    inst = MemKraft(base_dir=d)
    inst.init(verbose=False)
    yield inst
    shutil.rmtree(d, ignore_errors=True)


def test_tool_schemas_shape():
    schemas = mcp_mod._tool_schemas()
    names = {t["name"] for t in schemas}
    assert names == {"remember", "search", "recall", "link"}
    for t in schemas:
        assert "description" in t and t["description"]
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"
        assert "required" in t["inputSchema"]


def test_dispatch_remember_and_search(mk):
    result = mcp_mod.dispatch(mk, "remember",
                              {"name": "Simon Kim", "info": "CEO of Hashed", "source": "test"})
    assert result["ok"] is True
    assert result["name"] == "Simon Kim"

    hits = mcp_mod.dispatch(mk, "search", {"query": "Simon"})
    assert isinstance(hits, list)


def test_mcp_dispatch_search_does_not_pollute_stdout(mk, capsys):
    mk.track("Protocol Probe", entity_type="concept", source="test")
    mk.update("Protocol Probe", "searchable MCP result", source="test")
    capsys.readouterr()

    hits = mcp_mod._dispatch_for_mcp(mk, "search", {"query": "Protocol Probe"})

    assert hits
    assert capsys.readouterr().out == ""


def test_repeated_stdio_searches_preserve_jsonrpc_framing(mk):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for index in range(24):
        filename = f"protocol-framing-{index:02d}-{'x' * 80}.md"
        (mk.live_notes_dir / filename).write_text(
            "\n".join(
                [
                    f"# Protocol Framing Probe {index}",
                    "",
                    "## Recent Activity",
                    f"- Protocol framing search result {index}: {'detail ' * 30}",
                ]
            ),
            encoding="utf-8",
        )

    async def run_repeated_searches():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "memkraft.mcp"],
            env={
                "MEMKRAFT_DIR": str(mk.base_dir),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            },
        )
        timeout = datetime.timedelta(seconds=5)
        with tempfile.TemporaryFile(mode="w+") as errlog:
            async with stdio_client(
                params,
                errlog=errlog,
            ) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                ) as session:
                    await session.initialize()
                    for _ in range(4):
                        result = await session.call_tool(
                            "search",
                            {"query": "Protocol Framing", "fuzzy": True},
                        )
                        text = "".join(
                            item.text
                            for item in result.content
                            if hasattr(item, "text")
                        )
                        assert result.isError is False
                        assert "protocol-framing" in text

    asyncio.run(run_repeated_searches())


def test_dispatch_unknown_tool_raises(mk):
    with pytest.raises(ValueError):
        mcp_mod.dispatch(mk, "not-a-tool", {})


def test_dispatch_recall_returns_dossier_for_known_entity(mk):
    # Regression for the v0.8.3 bug where brief() returned ``None`` while
    # printing to stdout, so dispatch fell through to the ``or`` fallback and
    # reported existing entities as ``{'found': False}``.
    mk.track("NVDA", entity_type="org", source="test")
    mcp_mod.dispatch(mk, "remember",
                     {"name": "NVDA", "info": "fact about earnings beat", "source": "test"})
    result = mcp_mod.dispatch(mk, "recall", {"name": "NVDA"})
    assert result["found"] is True
    assert result["name"] == "NVDA"
    assert "fact about earnings beat" in result["text"]


def test_dispatch_recall_reports_missing_entity(mk):
    result = mcp_mod.dispatch(mk, "recall", {"name": "Never Heard Of"})
    assert result["found"] is False
    assert result["name"] == "Never Heard Of"
    # ``text`` is always present so tool adapters can surface a helpful
    # "not found" notice without a second round-trip.
    assert "text" in result


def test_dispatch_recall_does_not_pollute_stdout(mk, capsys):
    # MCP stdio transport reuses process stdout for JSON-RPC framing; brief()
    # must not emit anything to stdout during dispatch.
    mk.track("Silent Co", entity_type="org", source="test")
    mcp_mod.dispatch(mk, "remember",
                     {"name": "Silent Co", "info": "no noise please", "source": "test"})
    capsys.readouterr()  # drain track/update notifications
    mcp_mod.dispatch(mk, "recall", {"name": "Silent Co"})
    captured = capsys.readouterr()
    assert captured.out == ""


def test_require_mcp_hint_when_missing(monkeypatch, capsys):
    """If `mcp` is not installed, _require_mcp should exit with a hint."""
    # Simulate absence by blocking the import path
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *a, **kw):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        mcp_mod._require_mcp()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "memkraft[mcp]" in captured.err
