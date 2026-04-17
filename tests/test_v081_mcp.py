"""v0.8.1 — MCP dispatch + extras hint."""
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


def test_dispatch_unknown_tool_raises(mk):
    with pytest.raises(ValueError):
        mcp_mod.dispatch(mk, "not-a-tool", {})


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
