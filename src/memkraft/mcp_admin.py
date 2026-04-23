"""memkraft mcp doctor / mcp test — admin + smoke-test for the MCP server.

Does NOT require the `mcp` extra for `doctor` (it checks whether the extra is
installed). ``test`` can run a local round-trip without spinning up stdio.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from . import __version__


_OK = "🟢"
_WARN = "🟡"
_ERR = "🔴"
_TIP = "💡"


def _check_mcp_extra() -> Dict[str, Any]:
    try:
        m = importlib.import_module("mcp")
        v = getattr(m, "__version__", "unknown")
        return {"ok": True, "version": v}
    except ImportError as e:
        return {"ok": False, "error": str(e)}


def _check_mcp_module_importable() -> Dict[str, Any]:
    """Import memkraft.mcp and verify _tool_schemas() returns a non-empty list."""
    try:
        from . import mcp as mcp_mod
        schemas = mcp_mod._tool_schemas()
        return {
            "ok": True,
            "tool_count": len(schemas),
            "tools": [s.get("name") for s in schemas],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_entry_point() -> Dict[str, Any]:
    """Is `python -m memkraft.mcp` wired up?"""
    try:
        spec = importlib.util.find_spec("memkraft.mcp")
        if spec is None:
            return {"ok": False, "error": "memkraft.mcp module not found"}
        return {"ok": True, "path": spec.origin or ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def doctor(verbose: bool = True) -> Dict[str, Any]:
    """Run MCP-specific health checks."""
    report: Dict[str, Any] = {
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    all_ok = True

    extra = _check_mcp_extra()
    report["mcp_extra"] = extra
    if verbose:
        if extra["ok"]:
            print(f"  {_OK} mcp package installed (v{extra['version']})")
        else:
            print(f"  {_WARN} mcp package NOT installed")
            print(f"     {_TIP} pip install 'memkraft[mcp]'")
            all_ok = False

    entry = _check_entry_point()
    report["entry_point"] = entry
    if verbose:
        if entry["ok"]:
            print(f"  {_OK} `python -m memkraft.mcp` importable ({entry.get('path','')})")
        else:
            print(f"  {_ERR} memkraft.mcp not importable: {entry.get('error')}")
            all_ok = False

    mod = _check_mcp_module_importable()
    report["module"] = mod
    if verbose:
        if mod["ok"]:
            print(f"  {_OK} tool schemas: {mod['tool_count']} tools ({', '.join(mod['tools'])})")
        else:
            print(f"  {_ERR} tool schema check failed: {mod.get('error')}")
            all_ok = False

    # Claude Desktop config path (macOS / Windows / Linux)
    home = Path.home()
    candidates = [
        home / "Library/Application Support/Claude/claude_desktop_config.json",  # macOS
        home / "AppData/Roaming/Claude/claude_desktop_config.json",              # Windows
        home / ".config/Claude/claude_desktop_config.json",                      # Linux (speculative)
    ]
    existing = [str(c) for c in candidates if c.exists()]
    report["claude_desktop_config"] = {
        "existing": existing,
        "candidates": [str(c) for c in candidates],
    }
    if verbose:
        if existing:
            print(f"  {_OK} Claude Desktop config found: {existing[0]}")
        else:
            print(f"  {_WARN} no Claude Desktop config detected (skip if not using Claude Desktop)")

    report["status"] = "ok" if all_ok else "degraded"
    if verbose:
        print()
        print(f"  {_OK if all_ok else _WARN} status: {report['status']}")
    return report


def test_roundtrip(base_dir: str = "", verbose: bool = True) -> Dict[str, Any]:
    """Smoke-test: remember → search → recall, using a temp workspace by default."""
    from .core import MemKraft
    from .mcp import dispatch

    tmp_ctx = None
    if not base_dir:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="memkraft-mcp-test-")
        base_dir = tmp_ctx.name

    os.environ["MEMKRAFT_DIR"] = str(Path(base_dir) / "memory")
    mk = MemKraft(base_dir=str(Path(base_dir) / "memory"))
    mk.init(verbose=False)

    report: Dict[str, Any] = {
        "base_dir": str(mk.base_dir),
        "steps": [],
    }

    # 1. remember (exercise the same dispatch path the MCP server uses)
    try:
        result = dispatch(
            mk,
            "remember",
            {
                "name": "MCP Smoke Test",
                "info": "This is a smoke test entry created by `memkraft mcp test`.",
                "source": "mcp-test",
                "entity_type": "concept",
            },
        )
        report["steps"].append({"step": "remember", "ok": bool(result.get("ok")), **result})
        if verbose:
            print(f"  {_OK} remember: wrote entry 'MCP Smoke Test'")
    except Exception as e:
        report["steps"].append({"step": "remember", "ok": False, "error": str(e)})
        if verbose:
            print(f"  {_ERR} remember failed: {e}")

    # 2. search
    try:
        results = mk.search("MCP Smoke Test", fuzzy=True)
        # Any non-empty result list counts as a successful round-trip
        hit = bool(results)
        report["steps"].append({"step": "search", "ok": hit, "count": len(results or [])})
        if verbose:
            status = _OK if hit else _WARN
            print(f"  {status} search: {len(results or [])} result(s)")
    except Exception as e:
        report["steps"].append({"step": "search", "ok": False, "error": str(e)})
        if verbose:
            print(f"  {_ERR} search failed: {e}")

    # 3. recall (read the live-note file we just created)
    try:
        slug = mk._slugify("MCP Smoke Test") if hasattr(mk, "_slugify") else "mcp-smoke-test"
        path = mk.live_notes_dir / f"{slug}.md"
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        ok = bool(content)
        report["steps"].append({"step": "recall", "ok": ok, "bytes": len(content)})
        if verbose:
            status = _OK if ok else _WARN
            print(f"  {status} recall: {len(content)} bytes")
    except Exception as e:
        report["steps"].append({"step": "recall", "ok": False, "error": str(e)})
        if verbose:
            print(f"  {_ERR} recall failed: {e}")

    all_ok = all(s.get("ok") for s in report["steps"])
    report["status"] = "ok" if all_ok else "failed"
    if verbose:
        print()
        print(f"  {_OK if all_ok else _ERR} status: {report['status']}")

    if tmp_ctx is not None:
        tmp_ctx.cleanup()

    return report


def cmd(args) -> int:
    sub = getattr(args, "mcp_command", "") or ""
    if sub == "doctor":
        report = doctor()
        return 0 if report["status"] == "ok" else 1
    if sub == "test":
        report = test_roundtrip(base_dir=getattr(args, "base_dir", "") or "")
        return 0 if report["status"] == "ok" else 1
    print("❌ usage: memkraft mcp {doctor|test}")
    return 2
