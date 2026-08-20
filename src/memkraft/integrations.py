"""Read-only integration discovery and live smoke checks.

The report is intentionally host-neutral. It distinguishes a package that is
installed from an integration that can actually answer a health request.
"""
from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from importlib import metadata
from typing import Any, Dict, List, Optional

from . import __version__
from .agent_bridge import AgentBridge
from .install_integrity import installation_report
from . import MemKraft


def _entry_point(group: str, name: str) -> Dict[str, Any]:
    try:
        entries = metadata.entry_points()
        selected = entries.select(group=group, name=name) if hasattr(entries, "select") else [
            item for item in entries.get(group, []) if item.name == name
        ]
        return {"available": bool(selected), "group": group, "name": name}
    except Exception as error:
        return {"available": False, "group": group, "name": name, "error": str(error)}


def integration_report(base_dir: Optional[str] = None) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    try:
        checks["bridge"] = AgentBridge(MemKraft(base_dir=base_dir)).describe()
        with redirect_stdout(io.StringIO()):
            health = AgentBridge(MemKraft(base_dir=base_dir)).call({"operation": "health"})
        checks["bridge"]["health"] = {
            "ok": health.get("ok") is True,
            "operation": health.get("operation"),
        }
    except Exception as error:
        checks["bridge"] = {"available": False, "health": {"ok": False, "error": str(error)}}

    checks["hermes"] = _entry_point("hermes_agent.memory_providers", "memkraft")
    checks["mcp"] = {
        "module_available": importlib.util.find_spec("memkraft.mcp") is not None,
        "optional_package_available": importlib.util.find_spec("mcp") is not None,
    }
    checks["generic_agents"] = {
        "available": checks.get("bridge", {}).get("health", {}).get("ok") is True,
        "transport": "json-stdio",
        "command": "memkraft bridge call",
    }
    integrity = installation_report(check_updates=False)
    return {
        "ok": bool(checks.get("bridge", {}).get("health", {}).get("ok")),
        "memkraft_version": __version__,
        "checks": checks,
        "installation": {
            "consistent": integrity.get("consistent"),
            "reasons": integrity.get("reasons", []),
            "import_path": integrity.get("import_path"),
            "distribution_version": integrity.get("distribution_version"),
        },
    }


def format_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


__all__ = ["format_report", "integration_report"]

if __name__ == "__main__":
    print(format_report(integration_report()))
    raise SystemExit(0 if integration_report().get("ok") else 1)

# Keep typing imports visible to Python 3.9 without changing the wire format.
_UNUSED: List[Any] = []