"""memkraft agents-hint — generate copy-paste integration snippets for popular agents.

Usage:
    memkraft agents-hint claude-code
    memkraft agents-hint openclaw > /tmp/block.md
    memkraft agents-hint mcp --format json
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from . import __version__

# Canonical alias map — lowercase, ascii-first. Hard to type target names get
# sensible aliases (e.g. the Cyrillic "о" that shows up in "оpenclaw").
_ALIASES: Dict[str, str] = {
    "claude-code": "claude-code",
    "claude": "claude-code",
    "claudecode": "claude-code",
    "cc": "claude-code",

    "openclaw": "openclaw",
    "оpenclaw": "openclaw",  # cyrillic о
    "claw": "openclaw",

    "openai": "openai",
    "gpt": "openai",
    "chatgpt": "openai",

    "cursor": "cursor",

    "mcp": "mcp",
    "model-context-protocol": "mcp",

    "langchain": "langchain",
    "lc": "langchain",
}

VALID_TARGETS: List[str] = sorted(set(_ALIASES.values()))


def _templates_dir() -> Path:
    return Path(__file__).parent / "prompts" / "templates"


def _resolve_base_dir(base_dir_override: str = "") -> str:
    if base_dir_override:
        return str(Path(base_dir_override).expanduser().resolve())
    env = os.environ.get("MEMKRAFT_DIR")
    if env:
        return str(Path(env).expanduser().resolve())
    # default: ~/memory (portable suggestion, not Path.cwd() which changes)
    return str(Path.home() / "memory")


def resolve_target(raw: str) -> str:
    """Normalize any alias into a canonical target key.

    Raises ValueError with a helpful hint if unknown.
    """
    if not raw:
        raise ValueError("target is required")
    key = raw.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(
        f"unknown target: {raw!r}. valid: {', '.join(VALID_TARGETS)}"
    )


def render(target: str, base_dir: str = "", version: str = __version__) -> str:
    """Render a template for ``target`` with placeholders filled in.

    Raises:
        ValueError: if target unknown.
        FileNotFoundError: if template file missing (packaging bug).
    """
    canonical = resolve_target(target)
    tpl_path = _templates_dir() / f"{canonical}.md"
    if not tpl_path.exists():
        raise FileNotFoundError(
            f"template missing: {tpl_path}. "
            "this is a packaging bug — please file an issue."
        )
    raw = tpl_path.read_text(encoding="utf-8")
    return (raw
            .replace("{BASE_DIR}", _resolve_base_dir(base_dir))
            .replace("{VERSION}", version))


def render_json(target: str, base_dir: str = "") -> str:
    """Render as a JSON envelope (useful for tooling that wants metadata)."""
    canonical = resolve_target(target)
    body = render(canonical, base_dir=base_dir)
    return json.dumps({
        "target": canonical,
        "version": __version__,
        "base_dir": _resolve_base_dir(base_dir),
        "content": body,
    }, indent=2, ensure_ascii=False)


def cmd(args) -> int:
    """argparse entry point."""
    try:
        if getattr(args, "format", "markdown") == "json":
            print(render_json(args.target, base_dir=getattr(args, "base_dir", "")))
        else:
            print(render(args.target, base_dir=getattr(args, "base_dir", "")))
        return 0
    except ValueError as e:
        print(f"❌ {e}", flush=True)
        print(f"   valid targets: {', '.join(VALID_TARGETS)}")
        return 2
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 3
