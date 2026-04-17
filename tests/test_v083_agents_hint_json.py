"""Tests for v0.8.3: agents-hint --format json for all targets."""
from __future__ import annotations

import json

import pytest

from memkraft import agents_hint


@pytest.mark.parametrize("target", ["claude-code", "openclaw", "openai", "cursor", "mcp", "langchain"])
def test_agents_hint_json_is_valid_json(target, tmp_path):
    base = str(tmp_path)
    out = agents_hint.render_json(target, base_dir=base)
    data = json.loads(out)
    assert data["target"] == target
    # resolved path should end with the requested base
    from pathlib import Path
    assert Path(data["base_dir"]).resolve() == Path(base).resolve()
    assert "version" in data
    assert "content" in data
    assert isinstance(data["content"], str)
    assert len(data["content"]) > 0


def test_agents_hint_json_includes_version_from_package():
    from memkraft import __version__
    out = agents_hint.render_json("claude-code")
    data = json.loads(out)
    assert data["version"] == __version__
