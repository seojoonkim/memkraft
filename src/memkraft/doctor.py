"""memkraft doctor — health check for install + memory structure.

Prints a tree with 🟢/🟡/🔴 icons and suggested next actions.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from . import __version__
from .core import MemKraft

_OK = "🟢"
_WARN = "🟡"
_ERR = "🔴"
_TIP = "💡"


def _py_version() -> str:
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _check_python() -> Tuple[str, str]:
    v = sys.version_info
    if v >= (3, 9):
        return _OK, f"Python {_py_version()}"
    return _ERR, f"Python {_py_version()} (requires >= 3.9)"


def _check_memkraft() -> Tuple[str, str]:
    return _OK, f"MemKraft v{__version__} installed at {Path(__file__).parent}"


def _check_base_dir(mk: MemKraft) -> Tuple[str, str, bool]:
    bd = mk.base_dir
    if not bd.exists():
        return _ERR, f"base_dir missing: {bd}", False
    if not os.access(bd, os.W_OK):
        return _WARN, f"base_dir not writable: {bd}", True
    return _OK, f"base_dir: {bd}", True


def _check_structure(mk: MemKraft) -> Tuple[str, str, List[str]]:
    required = ["entities", "live-notes", "decisions", "inbox"]
    missing = [d for d in required if not (mk.base_dir / d).exists()]
    if not missing:
        return _OK, "structure: all core dirs present", []
    return _WARN, f"structure: missing {', '.join(missing)}", missing


def _count_files(mk: MemKraft) -> Dict[str, int]:
    counts = {}
    for sub in ["entities", "live-notes", "decisions", "inbox", "originals", "tasks", "meetings"]:
        p = mk.base_dir / sub
        if p.exists():
            counts[sub] = sum(1 for _ in p.glob("*.md"))
        else:
            counts[sub] = 0
    return counts


def _tier_stats(mk: MemKraft) -> Dict[str, int]:
    stats = {"core": 0, "recall": 0, "archival": 0, "unset": 0}
    entities = mk.base_dir / "entities"
    if not entities.exists():
        return stats
    for p in entities.glob("*.md"):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "tier: core" in txt.lower():
            stats["core"] += 1
        elif "tier: recall" in txt.lower():
            stats["recall"] += 1
        elif "tier: archival" in txt.lower():
            stats["archival"] += 1
        else:
            stats["unset"] += 1
    return stats


def _check_extras() -> List[Tuple[str, str]]:
    out = []
    try:
        import mcp  # noqa: F401
        out.append((_OK, "extras[mcp]: installed"))
    except ImportError:
        out.append((_WARN, "extras[mcp]: not installed (pip install 'memkraft[mcp]')"))
    try:
        import watchdog  # noqa: F401
        out.append((_OK, "extras[watch]: installed"))
    except ImportError:
        out.append((_WARN, "extras[watch]: not installed (pip install 'memkraft[watch]')"))
    return out


def _check_env() -> Tuple[str, str]:
    env = os.environ.get("MEMKRAFT_DIR")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return _OK, f"MEMKRAFT_DIR={env}"
        return _WARN, f"MEMKRAFT_DIR={env} (path does not exist)"
    return _OK, "MEMKRAFT_DIR not set (using default cwd/memory)"


def _check_updates() -> Tuple[str, str]:
    """Check PyPI for newer MemKraft version. Network call, may be slow/unreachable."""
    try:
        from .selfupdate import latest_version, installed_version, needs_update
    except ImportError:
        return _WARN, "update check unavailable (selfupdate module missing)"
    current = installed_version() or __version__
    latest = latest_version()
    if latest is None:
        return _ERR, "PyPI unreachable (offline or timeout)"
    if needs_update(current, latest):
        return _WARN, f"Update available: {current} → {latest}  (run `memkraft selfupdate`)"
    return _OK, f"Up to date: {current}"


# ── Auto-Fix ────────────────────────────────────────────────────
_FIXABLE_DIRS = ["entities", "live-notes", "decisions", "inbox", "originals",
                "tasks", "meetings", "sessions", "debug"]
_INTERNAL_DIRS = [".memkraft/snapshots", ".memkraft/channels",
                 ".memkraft/tasks", ".memkraft/agents"]


def plan_fixes(mk: MemKraft) -> List[Dict[str, str]]:
    """Return a list of fix actions WITHOUT executing them.

    Each action is ``{"action": "mkdir", "path": "...", "reason": "..."}``.
    Safety: only ``mkdir`` actions are ever emitted — no file deletions.
    """
    actions: List[Dict[str, str]] = []
    bd = mk.base_dir
    if not bd.exists():
        actions.append({
            "action": "mkdir",
            "path": str(bd),
            "reason": "base_dir missing",
        })
    for d in _FIXABLE_DIRS:
        p = bd / d
        if not p.exists():
            actions.append({
                "action": "mkdir",
                "path": str(p),
                "reason": f"missing required dir: {d}/",
            })
    for d in _INTERNAL_DIRS:
        p = bd / d
        if not p.exists():
            actions.append({
                "action": "mkdir",
                "path": str(p),
                "reason": f"missing internal dir: {d}/",
            })
    return actions


def apply_fixes(actions: List[Dict[str, str]], dry_run: bool = False) -> Dict[str, object]:
    """Execute a list of fix actions. Returns {applied: [...], skipped: [...]}."""
    applied: List[str] = []
    skipped: List[str] = []
    for a in actions:
        if a.get("action") != "mkdir":
            skipped.append(f"unsupported action: {a.get('action')}")
            continue
        path = Path(a["path"])
        if dry_run:
            applied.append(f"(dry-run) mkdir -p {path}")
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            applied.append(f"mkdir -p {path}")
        except Exception as e:
            skipped.append(f"failed to create {path}: {e}")
    return {"applied": applied, "skipped": skipped}


def run_fix(base_dir: str = "", dry_run: bool = False, yes: bool = False) -> Dict[str, object]:
    """Run diagnostic + apply fixes. Prompts unless ``yes`` or ``dry_run``.

    Returns a structured report.
    """
    mk = MemKraft(base_dir=base_dir) if base_dir else MemKraft()
    actions = plan_fixes(mk)

    print("🔧 MemKraft doctor --fix")
    print()
    if not actions:
        print(f"  {_OK} nothing to fix — workspace is healthy")
        return {"status": "nothing-to-do", "actions": [], "result": {"applied": [], "skipped": []}}

    print(f"  {_WARN} {len(actions)} fix(es) planned:")
    for a in actions:
        print(f"     • {a['action']}: {a['path']}")
        print(f"       └─ reason: {a['reason']}")
    print()

    if dry_run:
        print(f"  {_TIP} --dry-run: no changes applied")
        result = apply_fixes(actions, dry_run=True)
        for line in result["applied"]:
            print(f"     {line}")
        return {"status": "dry-run", "actions": actions, "result": result}

    if not yes:
        try:
            resp = input("  apply fixes? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = ""
        if resp not in ("y", "yes"):
            print(f"  {_WARN} aborted (pass --yes to skip this prompt)")
            return {"status": "aborted", "actions": actions, "result": {"applied": [], "skipped": []}}

    result = apply_fixes(actions, dry_run=False)
    print()
    for line in result["applied"]:
        print(f"  {_OK} {line}")
    for line in result["skipped"]:
        print(f"  {_ERR} {line}")
    print()
    status = "fixed" if not result["skipped"] else "partial"
    print(f"  {_OK if status == 'fixed' else _WARN} {status}: {len(result['applied'])} applied, {len(result['skipped'])} skipped")
    return {"status": status, "actions": actions, "result": result}


def run(base_dir: str = "", check_updates: bool = False) -> Dict[str, object]:
    """Run all checks. Returns a structured report dict."""
    mk = MemKraft(base_dir=base_dir) if base_dir else MemKraft()

    lines: List[str] = []
    status = "healthy"
    update_info: Dict[str, object] = {}

    print("🩺 MemKraft doctor")
    print()

    # install
    icon, msg = _check_python()
    print(f"  {icon} {msg}")
    if icon == _ERR:
        status = "unhealthy"

    icon, msg = _check_memkraft()
    print(f"  {icon} {msg}")

    # env
    icon, msg = _check_env()
    print(f"  {icon} {msg}")
    if icon == _WARN and status == "healthy":
        status = "degraded"

    # base_dir
    icon, msg, bd_ok = _check_base_dir(mk)
    print(f"  {icon} {msg}")
    if icon == _ERR:
        status = "unhealthy"
        print(f"     {_TIP} run `memkraft init` to create the structure")

    if bd_ok:
        # structure
        icon, msg, missing = _check_structure(mk)
        print(f"  {icon} {msg}")
        if missing:
            if status == "healthy":
                status = "degraded"
            print(f"     {_TIP} run `memkraft init` to recreate missing dirs")

        # counts
        counts = _count_files(mk)
        total = sum(counts.values())
        print(f"  {_OK} memory files: {total} total")
        for name, n in counts.items():
            if n:
                print(f"     └─ {name}/: {n}")

        # tier stats
        tiers = _tier_stats(mk)
        tier_total = sum(tiers.values())
        if tier_total:
            print(f"  {_OK} tiers: core={tiers['core']} recall={tiers['recall']} archival={tiers['archival']} unset={tiers['unset']}")

    # extras
    print()
    print("  optional extras:")
    for icon, msg in _check_extras():
        print(f"     {icon} {msg}")

    # update check (opt-in, network)
    if check_updates:
        print()
        print("  update check:")
        u_icon, u_msg = _check_updates()
        print(f"     {u_icon} {u_msg}")
        update_info = {"icon": u_icon, "message": u_msg}
        if u_icon == _WARN and status == "healthy":
            status = "degraded"

    print()
    icon = {"healthy": _OK, "degraded": _WARN, "unhealthy": _ERR}[status]
    print(f"  {icon} overall: {status}")

    report = {
        "status": status,
        "version": __version__,
        "base_dir": str(mk.base_dir),
        "python": _py_version(),
    }
    if check_updates:
        report["update_check"] = update_info
    return report


def cmd(args) -> int:
    # --fix mode takes precedence
    if getattr(args, "fix", False):
        result = run_fix(
            base_dir=getattr(args, "base_dir", ""),
            dry_run=getattr(args, "dry_run", False),
            yes=getattr(args, "yes", False),
        )
        if result["status"] in ("aborted", "partial"):
            return 1
        return 0

    report = run(
        base_dir=getattr(args, "base_dir", ""),
        check_updates=getattr(args, "check_updates", False),
    )
    if report["status"] == "unhealthy":
        return 1
    return 0
