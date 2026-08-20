"""Create an idempotent, host-neutral MemKraft integration manifest."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__


def _home_candidates() -> Dict[str, List[str]]:
    home = Path.home()
    return {
        "hermes": [str(home / ".hermes"), str(home / ".config" / "hermes")],
        "openclaw": [str(home / ".openclaw"), str(home / ".config" / "openclaw")],
    }


def detect_hosts() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for host, candidates in _home_candidates().items():
        existing = next((p for p in candidates if Path(p).exists()), None)
        result[host] = {"detected": existing is not None, "config_root": existing}
    return result


def build_manifest(base_dir: str, hosts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    root = str(Path(base_dir).expanduser().resolve())
    return {
        "protocol": "memkraft-agent-bridge/1",
        "version": __version__,
        "base_dir": root,
        "transport": "json-stdio",
        "command": ["memkraft", "bridge", "call", "--base-dir", root],
        "operations": ["remember", "recall", "feedback", "health"],
        "hosts": hosts if hosts is not None else detect_hosts(),
        "setup_policy": "manifest-only; never overwrite host credentials or unrelated config",
    }


def setup(base_dir: Optional[str] = None, output: Optional[str] = None) -> Dict[str, Any]:
    root = str(Path(base_dir or os.environ.get("MEMKRAFT_DIR", "~/memory")).expanduser().resolve())
    manifest_path = Path(output).expanduser().resolve() if output else Path(root) / "integrations" / "memkraft.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    changed = True
    if manifest_path.exists():
        try:
            changed = manifest_path.read_text(encoding="utf-8") != encoded
        except OSError:
            changed = True
    if changed:
        temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(manifest_path)
    return {"ok": True, "changed": changed, "path": str(manifest_path), "manifest": manifest}


def cmd(args) -> int:
    result = setup(getattr(args, "base_dir", "") or None, getattr(args, "output", "") or None)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


__all__ = ["build_manifest", "cmd", "detect_hosts", "setup"]
