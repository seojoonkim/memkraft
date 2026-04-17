"""memkraft selfupdate — self-upgrade via pip.

Checks PyPI for the latest version and runs ``pip install -U memkraft`` when a
newer release is available. Always explicit — never runs automatically.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_installed_version
from typing import Optional

PYPI_URL = "https://pypi.org/pypi/memkraft/json"
PYPI_TIMEOUT_SECONDS = 5


def latest_version(timeout: int = PYPI_TIMEOUT_SECONDS) -> Optional[str]:
    """Return the latest version string from PyPI, or ``None`` on any failure."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as r:
            data = json.load(r)
            return data.get("info", {}).get("version")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    except Exception:
        return None


def installed_version() -> Optional[str]:
    """Return the currently-installed memkraft version, or ``None`` if not installed."""
    try:
        return get_installed_version("memkraft")
    except PackageNotFoundError:
        return None


def _parse_version(v: str) -> tuple:
    """Best-effort PEP 440-ish tuple comparison; falls back to string."""
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        try:
            parts.append(int(num) if num else 0)
        except ValueError:
            parts.append(0)
    return tuple(parts)


def needs_update(current: str, latest: str) -> bool:
    """Return True iff ``latest`` is strictly newer than ``current``."""
    if not current or not latest:
        return False
    if current == latest:
        return False
    return _parse_version(latest) > _parse_version(current)


def selfupdate(dry_run: bool = False) -> int:
    """Compare installed vs PyPI; pip install -U memkraft if newer is available."""
    current = installed_version()
    if current is None:
        print("⚠️  MemKraft does not appear to be installed via pip.")
        return 1

    latest = latest_version()
    if latest is None:
        print("⚠️  Could not reach PyPI (offline or timeout).")
        return 1

    if not needs_update(current, latest):
        print(f"✅ Already up to date: {current}")
        return 0

    print(f"🔄 Update available: {current} → {latest}")
    if dry_run:
        print("(dry-run: skipping pip install)")
        return 0

    print("Running: pip install -U memkraft")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "memkraft"])
    if result.returncode == 0:
        print(f"✅ Upgraded to {latest}")
    else:
        print(f"❌ pip install failed (exit {result.returncode})")
    return result.returncode


def cmd(args) -> int:
    """CLI entry point — wired from memkraft.cli."""
    return selfupdate(dry_run=getattr(args, "dry_run", False))


def main(argv: Optional[list] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="memkraft selfupdate", description="Self-upgrade MemKraft via pip")
    p.add_argument("--dry-run", action="store_true", help="Check only, do not install")
    args = p.parse_args(argv)
    return selfupdate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
