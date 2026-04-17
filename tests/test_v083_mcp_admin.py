"""Tests for v0.8.3: `memkraft mcp doctor` / `memkraft mcp test`."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import mcp_admin


SRC = str(Path(__file__).resolve().parents[1] / "src")


def _cli(*args, env=None):
    e = os.environ.copy()
    e["PYTHONPATH"] = SRC + ":" + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", *args],
        capture_output=True,
        text=True,
        env=e,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_mcp_doctor_returns_report_dict():
    report = mcp_admin.doctor(verbose=False)
    assert "status" in report
    assert "mcp_extra" in report
    assert "entry_point" in report
    assert "module" in report
    # entry_point should exist — memkraft.mcp module is in-tree
    assert report["entry_point"]["ok"] is True
    # Tool schemas should be non-empty
    assert report["module"]["ok"] is True
    assert report["module"]["tool_count"] >= 4


def test_mcp_test_roundtrip_succeeds(tmp_path):
    report = mcp_admin.test_roundtrip(base_dir=str(tmp_path), verbose=False)
    assert report["status"] == "ok", report
    assert all(s.get("ok") for s in report["steps"])


def test_cli_mcp_doctor_runs():
    rc, out, err = _cli("mcp", "doctor")
    # rc 0 or 1 both acceptable (mcp extra may or may not be installed)
    assert rc in (0, 1)
    assert "tool schemas" in out or "schemas" in out or "module" in out.lower()


def test_cli_mcp_test_runs(tmp_path):
    rc, out, err = _cli("mcp", "test", "--base-dir", str(tmp_path))
    assert rc == 0, err
    assert "remember" in out.lower()
    assert "search" in out.lower()
    assert "recall" in out.lower()


def test_mcp_config_snippet_is_valid_json():
    """The `agents-hint mcp` output JSON should contain a parseable config snippet."""
    from memkraft import agents_hint
    out = agents_hint.render_json("mcp")
    envelope = json.loads(out)
    assert "content" in envelope
    # content should contain `mcpServers` keyword (template-defined)
    assert "mcpServers" in envelope["content"] or "mcp" in envelope["content"].lower()
