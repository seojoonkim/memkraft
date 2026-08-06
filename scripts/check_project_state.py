#!/usr/bin/env python3
"""Collect read-only host observations and evaluate MemKraft's State Contract."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from memkraft import state_contract


def _run(args: Sequence[str]) -> str:
    return subprocess.run(
        list(args), check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def collect_git(repo: Path, runner: Optional[Callable[[Sequence[str]], str]] = None) -> Dict[str, Any]:
    """Collect local Git facts using read-only commands only."""
    run = runner or _run
    prefix = ["git", "-C", str(repo)]
    branch = run(prefix + ["rev-parse", "--abbrev-ref", "HEAD"])
    status = run(prefix + ["status", "--porcelain"])
    upstream = ""
    upstream_branch = ""
    pushed = False
    remote_matches = False
    if branch != "HEAD":
        try:
            upstream = run(prefix + ["rev-parse", "--abbrev-ref", "@{upstream}"])
            pushed = run(prefix + ["rev-list", "--count", "@{upstream}..HEAD"]) == "0"
            remote, upstream_branch = upstream.split("/", 1)
            if pushed:
                head = run(prefix + ["rev-parse", "HEAD"])
                remote_line = run(
                    prefix + ["ls-remote", "--heads", remote, "refs/heads/" + branch]
                )
                remote_matches = bool(remote_line) and remote_line.split()[0] == head
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        except (ValueError, IndexError):
            pass
    same_name = bool(upstream) and upstream_branch == branch
    return {
        "branch": branch,
        "tree_clean": not bool(status.strip()),
        "branch_remote_backed": same_name and pushed and remote_matches,
    }


def collect_runtime(python: str, runner: Optional[Callable[[Sequence[str]], str]] = None) -> Dict[str, Any]:
    """Probe import source/version and installed distribution metadata."""
    run = runner or _run
    probe = (
        "import importlib.metadata as m,json,memkraft;"
        "\ntry: d=m.version('memkraft')"
        "\nexcept m.PackageNotFoundError: d=None"
        "\nprint(json.dumps({'version':memkraft.__version__,"
        "'source_path':str(__import__('pathlib').Path(memkraft.__file__).resolve()),"
        "'distribution_version':d}))"
    )
    return json.loads(run([python, "-c", probe]))


def read_source_version(repo: Path) -> str:
    source = (repo / "src" / "memkraft" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise ValueError("source __version__ not found")
    return match.group(1)


def build_observations(
    *,
    git: Dict[str, Any],
    runtime: Dict[str, Any],
    source_version: str,
    public_version: str,
    snapshot_public_version: str,
    snapshot_development_version: str,
) -> Dict[str, Any]:
    return {
        "source": {"version": source_version},
        "runtime": {
            "version": runtime.get("version"),
            "source_path": runtime.get("source_path"),
        },
        "distribution": {"version": runtime.get("distribution_version")},
        "public": {"version": public_version},
        "git": dict(git),
        "snapshot": {
            "public_version": snapshot_public_version,
            "development_version": snapshot_development_version,
        },
    }


def _constraints(allow_ephemeral_source: bool):
    return [
        dict(item)
        for item in state_contract.DEFAULT_CONSTRAINTS
        if not (allow_ephemeral_source and item["id"] == "durable_source_path")
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--public-version", required=True)
    parser.add_argument("--snapshot-public-version", required=True)
    parser.add_argument("--snapshot-development-version", required=True)
    parser.add_argument("--allow-ephemeral-source", action="store_true")
    args = parser.parse_args(argv)

    observations = build_observations(
        git=collect_git(args.repo),
        runtime=collect_runtime(args.python),
        source_version=read_source_version(args.repo),
        public_version=args.public_version,
        snapshot_public_version=args.snapshot_public_version,
        snapshot_development_version=args.snapshot_development_version,
    )
    report = state_contract.evaluate(
        observations, _constraints(args.allow_ephemeral_source)
    )
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
