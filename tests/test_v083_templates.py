"""Tests for v0.8.3: `init --template` + `templates list`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import templates_pkg


SRC = str(Path(__file__).resolve().parents[1] / "src")


def _cli(*args, cwd=None, env=None):
    """Run the memkraft CLI from source and return (rc, stdout, stderr)."""
    import os
    e = os.environ.copy()
    e["PYTHONPATH"] = SRC + ":" + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=e,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_templates_available_returns_five():
    names = [t["name"] for t in templates_pkg.available()]
    assert set(names) >= {"claude-code", "cursor", "mcp", "minimal", "rag"}


def test_templates_load_unknown_raises():
    with pytest.raises(ValueError):
        templates_pkg.load("nonexistent-template-xyz")


def test_templates_load_claude_code_has_files():
    manifest = templates_pkg.load("claude-code")
    assert manifest["name"] == "claude-code"
    assert any(f["path"] == "CLAUDE.md" for f in manifest["files"])


def test_init_template_claude_code(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "claude-code")
    assert rc == 0, err
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "memory" / "entities" / "_example-person.md").exists()
    assert "MemKraft" in (tmp_path / "CLAUDE.md").read_text()


def test_init_template_cursor(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "cursor")
    assert rc == 0, err
    assert (tmp_path / ".cursorrules").exists()
    assert (tmp_path / "memory").is_dir()


def test_init_template_mcp(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "mcp")
    assert rc == 0, err
    assert (tmp_path / "claude_desktop_config.snippet.json").exists()
    # Snippet must be valid JSON
    snippet = json.loads((tmp_path / "claude_desktop_config.snippet.json").read_text())
    assert "mcpServers" in snippet
    assert "memkraft" in snippet["mcpServers"]


def test_init_template_minimal(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "minimal")
    assert rc == 0, err
    assert (tmp_path / "memory" / "entities").is_dir()


def test_init_template_rag(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "rag")
    assert rc == 0, err
    assert (tmp_path / "retrieval" / "README.md").exists()
    assert (tmp_path / "retrieval" / "example.py").exists()


def test_init_template_idempotent_no_overwrite(tmp_path):
    # first pass
    rc1, out1, _ = _cli("init", "--path", str(tmp_path), "--template", "claude-code")
    assert rc1 == 0
    # modify a file
    claude_md = tmp_path / "CLAUDE.md"
    original = claude_md.read_text()
    claude_md.write_text("# USER EDITED — do not overwrite\n")
    # second pass
    rc2, out2, _ = _cli("init", "--path", str(tmp_path), "--template", "claude-code")
    assert rc2 == 0
    # User edit must be preserved
    assert claude_md.read_text() == "# USER EDITED — do not overwrite\n"


def test_init_template_unknown_returns_error(tmp_path):
    rc, out, err = _cli("init", "--path", str(tmp_path), "--template", "does-not-exist")
    assert rc != 0


def test_templates_list_command_prints_all():
    rc, out, _ = _cli("templates", "list")
    assert rc == 0
    for name in ("claude-code", "cursor", "mcp", "minimal", "rag"):
        assert name in out
