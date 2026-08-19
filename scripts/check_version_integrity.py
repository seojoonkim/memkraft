#!/usr/bin/env python3
"""Fail-closed check that source, release metadata, and Git lineage agree."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def source_version(repo: Path) -> str:
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    package = (repo / "src/memkraft/__init__.py").read_text(encoding="utf-8")
    match = re.search(r"(?m)^__version__\s*=\s*[\"']([^\"']+)", package)
    if not match:
        raise ValueError("src/memkraft/__init__.py has no __version__")
    observed = {"pyproject": project["project"]["version"], "package": match.group(1)}
    if len(set(observed.values())) != 1:
        raise ValueError(f"source versions disagree: {observed}")
    version = next(iter(observed.values()))
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid version: {version}")
    return version


def check(repo: Path) -> dict:
    version = source_version(repo)
    manifest = json.loads((repo / "release_manifest.json").read_text(encoding="utf-8"))
    release = manifest.get("release", {})
    findings = []
    expected_branch = f"release/{version}"
    if release.get("version") != version:
        findings.append({"code": "manifest_version_mismatch", "observed": release.get("version"), "expected": version})
    if release.get("active_branch") != expected_branch:
        findings.append({"code": "manifest_branch_mismatch", "observed": release.get("active_branch"), "expected": expected_branch})

    branch = git(repo, "branch", "--show-current")
    # Feature/fix branches may work on the current release line. A branch named
    # as a release, however, must agree with the version it claims to publish.
    if branch.startswith("release/") and branch != expected_branch:
        findings.append({"code": "release_branch_version_mismatch", "observed": branch, "expected": expected_branch})

    tag_commit = git(repo, "rev-parse", f"refs/tags/v{version}^{{commit}}")
    tag_source = git(repo, "show", f"{tag_commit}:pyproject.toml")
    tag_match = re.search(r"(?m)^version\s*=\s*\"([^\"]+)\"", tag_source)
    if not tag_match or tag_match.group(1) != version:
        findings.append({"code": "tag_source_version_mismatch", "observed": tag_match.group(1) if tag_match else None, "expected": version})

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"(?m)^## \[{re.escape(version)}\]", changelog):
        findings.append({"code": "missing_changelog_heading", "expected": version})
    notes = repo / "docs" / "releases" / f"{version}.md"
    if not notes.is_file():
        findings.append({"code": "missing_release_notes", "expected": str(notes)})

    return {"version": version, "branch": branch, "tag_commit": tag_commit,
            "findings": findings, "version_integrity": not findings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        report = check(args.repo.resolve())
    except Exception as exc:
        report = {"version_integrity": False, "findings": [{"code": "collection_failed", "error": str(exc)}]}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["version_integrity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["check", "source_version"]
