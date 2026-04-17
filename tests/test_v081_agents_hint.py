"""v0.8.1 — agents-hint CLI."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import __version__
from memkraft import agents_hint


@pytest.mark.parametrize("target", [
    "claude-code",
    "openclaw",
    "openai",
    "cursor",
    "mcp",
    "langchain",
])
def test_render_each_target(target):
    out = agents_hint.render(target, base_dir="/tmp/memkraft-fixture")
    assert out, f"empty render for {target}"
    assert "{BASE_DIR}" not in out, f"{target} still has placeholder"
    assert "{VERSION}" not in out, f"{target} still has version placeholder"
    assert "/tmp/memkraft-fixture" in out or target == "mcp"  # mcp doesn't always inline the path in plain text paragraphs but templates all do


def test_version_substituted():
    out = agents_hint.render("claude-code")
    assert __version__ in out


def test_unknown_target_raises():
    with pytest.raises(ValueError):
        agents_hint.render("definitely-not-a-framework")


def test_cyrillic_openclaw_alias():
    # the durable MEMORY block contains "оpenclaw" with a cyrillic 'о'
    out = agents_hint.render("оpenclaw", base_dir="/x")
    assert "MemKraft" in out
    assert "/x" in out


def test_render_json_envelope():
    raw = agents_hint.render_json("claude-code", base_dir="/tmp/mk-j")
    doc = json.loads(raw)
    assert doc["target"] == "claude-code"
    assert doc["version"] == __version__
    assert doc["base_dir"].endswith("mk-j")
    assert "{BASE_DIR}" not in doc["content"]


def test_cmd_exit_code_unknown(capsys):
    class A:
        target = "not-a-real-target"
        base_dir = ""
        format = "markdown"
    rc = agents_hint.cmd(A())
    assert rc == 2
    captured = capsys.readouterr()
    assert "unknown target" in captured.out
