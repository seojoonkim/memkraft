"""memkraft.templates_pkg — project scaffolding templates for `init --template`.

Each template is a JSON manifest:
    {
      "name": "claude-code",
      "description": "...",
      "directories": ["memory/entities", ...],
      "files": [{"path": "CLAUDE.md", "content": "..."}]
    }

Templates are loaded lazily from ``templates_pkg/*.json``. They stack on
top of a standard ``memkraft init`` call so the memory/ structure is
always created first, then template extras are laid down.

Safety: existing files are never overwritten (idempotent re-run).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ApplyResult:
    created: List[str]
    skipped: List[str]  # existed already
    target: str


def templates_root() -> Path:
    return Path(__file__).parent


def available() -> List[Dict[str, str]]:
    """Return list of {name, description} for all known templates."""
    out: List[Dict[str, str]] = []
    for p in sorted(templates_root().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "name": data.get("name", p.stem),
            "description": data.get("description", ""),
            "path": str(p),
        })
    return out


def load(name: str) -> Dict:
    """Load a template manifest by name.

    Raises:
        ValueError: if template not found.
    """
    name = (name or "").strip().lower()
    if not name:
        raise ValueError("template name is required")
    p = templates_root() / f"{name}.json"
    if not p.exists():
        known = ", ".join(t["name"] for t in available())
        raise ValueError(f"unknown template: {name!r}. valid: {known}")
    return json.loads(p.read_text(encoding="utf-8"))


def apply(name: str, target_path: str) -> ApplyResult:
    """Lay down a template under ``target_path``.

    Idempotent: existing files/dirs are preserved (listed under ``skipped``).
    """
    manifest = load(name)
    target = Path(target_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    created: List[str] = []
    skipped: List[str] = []

    # directories first
    for d in manifest.get("directories", []):
        dp = target / d
        if dp.exists():
            skipped.append(f"{d}/")
        else:
            dp.mkdir(parents=True, exist_ok=True)
            created.append(f"{d}/")

    # files (never overwrite)
    for f in manifest.get("files", []):
        rel = f.get("path")
        content = f.get("content", "")
        if not rel:
            continue
        fp = target / rel
        if fp.exists():
            skipped.append(rel)
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        created.append(rel)

    return ApplyResult(created=created, skipped=skipped, target=str(target))
