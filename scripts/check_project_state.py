#!/usr/bin/env python3
"""Collect authoritative host observations and evaluate MemKraft's State Contract."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from memkraft import state_contract


def _run(args: Sequence[str]) -> str:
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def collect_git(repo: Path, runner: Optional[Callable[[Sequence[str]], str]] = None,
                expected_branch: str = "", candidate_sha: str = "", remote: str = "origin") -> Dict[str, Any]:
    """Collect candidate SHA, cleanliness, and exact same-name remote SHA (detached CI supported)."""
    run = runner or _run
    prefix = ["git", "-C", str(repo)]
    branch = run(prefix + ["rev-parse", "--abbrev-ref", "HEAD"])
    status = run(prefix + ["status", "--porcelain"])
    if expected_branch or candidate_sha:
        head = run(prefix + ["rev-parse", "HEAD"])
        effective_branch = expected_branch or ("" if branch == "HEAD" else branch)
        effective_candidate = candidate_sha or head
        remote_sha = ""
        try:
            line = run(prefix + ["ls-remote", "--heads", remote, "refs/heads/" + effective_branch])
            remote_sha = line.split()[0] if line else ""
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass
    else:
        effective_branch = "" if branch == "HEAD" else branch
        effective_candidate = ""
        head = ""
        remote_sha = ""
        upstream = ""
        if effective_branch:
            try:
                upstream = run(prefix + ["rev-parse", "--abbrev-ref", "@{upstream}"])
                if run(prefix + ["rev-list", "--count", "@{upstream}..HEAD"]) == "0":
                    head = run(prefix + ["rev-parse", "HEAD"])
                    line = run(prefix + ["ls-remote", "--heads", remote, "refs/heads/" + effective_branch])
                    remote_sha = line.split()[0] if line else ""
                    effective_candidate = head
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, IndexError):
                pass
        if upstream != remote + "/" + effective_branch:
            remote_sha = ""
    return {
        "branch": branch,
        "expected_branch": effective_branch,
        "head_sha": head,
        "candidate_sha": effective_candidate,
        "remote_sha": remote_sha,
        "tree_clean": not bool(status.strip()),
        "branch_remote_backed": bool(effective_branch) and bool(head) and head == effective_candidate == remote_sha,
    }


def collect_runtime(python: str, runner: Optional[Callable[[Sequence[str]], str]] = None) -> Dict[str, Any]:
    run = runner or _run
    probe = ("import importlib.metadata as m,json,memkraft,sys;"
             "\ntry: d=m.version('memkraft')"
             "\nexcept m.PackageNotFoundError: d=None"
             "\nprint(json.dumps({'version':memkraft.__version__,'source_path':str(__import__('pathlib').Path(memkraft.__file__).resolve()),'distribution_version':d,'install_prefix':str(__import__('pathlib').Path(sys.prefix).resolve())}))")
    return json.loads(run([python, "-c", probe]))


def read_source_versions(repo: Path) -> Dict[str, str]:
    package = (repo / "src/memkraft/__init__.py").read_text(encoding="utf-8")
    project = (repo / "pyproject.toml").read_text(encoding="utf-8")
    package_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', package, re.MULTILINE)
    project_match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', project)
    if not package_match or not project_match:
        raise ValueError("source version not found")
    return {"package_version": package_match.group(1), "project_version": project_match.group(1)}


def read_source_version(repo: Path) -> str:
    """Compatibility helper for local callers."""
    package = (repo / "src/memkraft/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', package, re.MULTILINE)
    if not match:
        raise ValueError("source __version__ not found")
    return match.group(1)


def collect_pypi_latest(package: str = "memkraft", opener=None) -> str:
    """Read the authoritative latest public version from PyPI."""
    open_url = opener or urllib.request.urlopen
    with open_url("https://pypi.org/pypi/%s/json" % package, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = payload.get("info", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("PyPI response has no version")
    return version


def build_observations(*, git: Dict[str, Any], source_versions: Optional[Dict[str, str]] = None,
                       source_version: str = "", public_version: str,
                       runtime: Optional[Dict[str, Any]] = None,
                       artifact: Optional[Dict[str, Any]] = None,
                       snapshot_public_version: str = "", snapshot_development_version: str = "") -> Dict[str, Any]:
    versions = source_versions or {"package_version": source_version, "project_version": source_version}
    observations = {
        "candidate": {"package_version": versions.get("package_version"), "project_version": versions.get("project_version")},
        "public": {"version": public_version},
        "git": dict(git),
    }
    if runtime is not None:  # legacy/local diagnostic observations
        observations.update({
            "source": {"version": versions.get("package_version")},
            "runtime": {"version": runtime.get("version"), "source_path": runtime.get("source_path")},
            "distribution": {"version": runtime.get("distribution_version")},
            "snapshot": {"public_version": snapshot_public_version, "development_version": snapshot_development_version},
        })
    if artifact is not None:
        observations["artifact"] = dict(artifact)
    return observations


def _constraints(allow_ephemeral_source: bool, release_mode: bool = False, artifact_mode: bool = False):
    if release_mode:
        selected = state_contract.RELEASE_ARTIFACT_CONSTRAINTS if artifact_mode else state_contract.RELEASE_CANDIDATE_CONSTRAINTS
        return [dict(item) for item in selected]
    return [dict(item) for item in state_contract.DEFAULT_CONSTRAINTS
            if not (allow_ephemeral_source and item["id"] == "durable_source_path")]


def _error_report(exc: Exception) -> Dict[str, Any]:
    return {"release_ready": False, "findings": [{"constraint_id": "authoritative_collection", "severity": "critical", "reason": "collection_failed", "observed": {"error": str(exc)}}]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--artifact", action="store_true", help="evaluate a fresh-wheel interpreter")
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--expected-branch", default="")
    parser.add_argument("--candidate-sha", default="")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--public-version", default="")
    parser.add_argument("--snapshot-public-version", default="")
    parser.add_argument("--snapshot-development-version", default="")
    parser.add_argument("--allow-ephemeral-source", action="store_true")
    args = parser.parse_args(argv)
    if args.release and args.allow_ephemeral_source:
        parser.error("--allow-ephemeral-source is forbidden in release mode")
    if args.artifact and not args.release:
        parser.error("--artifact requires --release")
    if args.release:
        required = {
            "--expected-version": args.expected_version,
            "--expected-branch": args.expected_branch,
            "--candidate-sha": args.candidate_sha,
        }
        missing = [flag for flag, value in required.items() if not value]
        if missing:
            parser.error("release mode requires " + ", ".join(missing))
        if not re.fullmatch(r"[0-9a-f]{40}", args.candidate_sha):
            parser.error("--candidate-sha must be an exact 40-character commit SHA")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", args.remote):
            parser.error("--remote must be a configured remote name")
    try:
        git = (collect_git(args.repo, expected_branch=args.expected_branch,
                           candidate_sha=args.candidate_sha, remote=args.remote)
               if args.release else collect_git(args.repo))
        versions = (read_source_versions(args.repo) if args.release else
                    {"package_version": read_source_version(args.repo),
                     "project_version": read_source_version(args.repo)})
        public = collect_pypi_latest() if args.release else args.public_version
        runtime = collect_runtime(args.python) if (args.artifact or not args.release) else None
        observations = build_observations(
            git=git, source_versions=versions, public_version=public, runtime=runtime,
            artifact=({"version": runtime.get("version"), "distribution_version": runtime.get("distribution_version"),
                       "source_path": runtime.get("source_path"), "install_prefix": runtime.get("install_prefix")}
                      if args.artifact and runtime else None),
            snapshot_public_version=args.snapshot_public_version,
            snapshot_development_version=args.snapshot_development_version,
        )
        if args.expected_version:
            observations["expected"] = {"version": args.expected_version}
        report = state_contract.evaluate(observations, _constraints(args.allow_ephemeral_source, args.release, args.artifact))
    except Exception as exc:
        report = _error_report(exc)
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
