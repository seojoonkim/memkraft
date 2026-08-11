#!/usr/bin/env python3
"""Fail-closed audit of one release baseline and its MemKraft lineage manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Sequence

_REQUIRED_LINEAGE = (
    "proposal_id",
    "revision_id",
    "evaluation_ref",
    "promotion_ref",
)
_IMPLEMENTED_STATES = frozenset({"implemented", "verified", "released"})
_VALID_STATES = frozenset({"planned", "implemented", "verified", "released"})


def _git(repo: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _finding(code: str, **details: Any) -> Dict[str, Any]:
    finding = {"code": code, **details}
    canonical = json.dumps(finding, sort_keys=True, separators=(",", ":"))
    finding["id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return finding


def _changed_source_paths(repo: Path, base_ref: str, target_ref: str) -> List[str]:
    result = _git(
        repo,
        ["diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}..{target_ref}", "--", "src/memkraft/*.py"],
    )
    return sorted(line for line in result.stdout.splitlines() if line)


def audit_release_lineage(repo: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Audit manifest schema, Git reachability, and unregistered source drift."""
    repo = repo.resolve()
    findings: List[Dict[str, Any]] = []
    release = manifest.get("release")
    features = manifest.get("features")
    if manifest.get("schema") != 1:
        findings.append(_finding("unsupported_schema", observed=manifest.get("schema")))
    if not isinstance(release, dict):
        findings.append(_finding("missing_release"))
        release = {}
    if not isinstance(features, list):
        findings.append(_finding("missing_features"))
        features = []

    base_ref = release.get("base_ref")
    target_ref = release.get("target_ref")
    release_state = release.get("state")
    if release_state not in _VALID_STATES:
        findings.append(_finding("invalid_release_state", state=release_state))
    for field, value in (("base_ref", base_ref), ("target_ref", target_ref), ("version", release.get("version"))):
        if not isinstance(value, str) or not value:
            findings.append(_finding("missing_release_field", field=field))

    declared_paths = set()
    seen_ids = set()
    for feature in features:
        if not isinstance(feature, dict):
            findings.append(_finding("invalid_feature"))
            continue
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id:
            findings.append(_finding("missing_feature_id"))
            continue
        if feature_id in seen_ids:
            findings.append(_finding("duplicate_feature_id", feature_id=feature_id))
        seen_ids.add(feature_id)
        state = feature.get("state")
        if state not in _VALID_STATES:
            findings.append(_finding("invalid_feature_state", feature_id=feature_id, state=state))
        for field in _REQUIRED_LINEAGE:
            if not isinstance(feature.get(field), str) or not feature[field]:
                findings.append(_finding("missing_lineage_field", feature_id=feature_id, field=field))
        commits = feature.get("commits")
        paths = feature.get("source_paths")
        if not isinstance(commits, list) or not all(isinstance(item, str) and item for item in commits):
            findings.append(_finding("invalid_commits", feature_id=feature_id))
            commits = []
        if not isinstance(paths, list) or not all(isinstance(item, str) and item.startswith("src/memkraft/") for item in paths):
            findings.append(_finding("invalid_source_paths", feature_id=feature_id))
            paths = []
        if state == "planned" and (commits or paths):
            findings.append(_finding("planned_feature_has_implementation", feature_id=feature_id))
        if state in _IMPLEMENTED_STATES and (not commits or not paths):
            findings.append(_finding("implemented_feature_missing_artifacts", feature_id=feature_id))
        declared_paths.update(paths)
        if isinstance(target_ref, str) and target_ref:
            for commit in commits:
                exists = _git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"], check=False)
                if exists.returncode != 0:
                    findings.append(_finding("commit_not_found", feature_id=feature_id, commit=commit))
                    continue
                reachable = _git(repo, ["merge-base", "--is-ancestor", commit, target_ref], check=False)
                if reachable.returncode != 0:
                    findings.append(_finding("commit_not_reachable", feature_id=feature_id, commit=commit))

    if isinstance(base_ref, str) and base_ref and isinstance(target_ref, str) and target_ref:
        refs_ok = all(
            _git(repo, ["rev-parse", "--verify", ref], check=False).returncode == 0
            for ref in (base_ref, target_ref)
        )
        if not refs_ok:
            findings.append(_finding("release_ref_not_found", base_ref=base_ref, target_ref=target_ref))
        else:
            for path in _changed_source_paths(repo, base_ref, target_ref):
                if path not in declared_paths:
                    findings.append(_finding("unregistered_source_drift", path=path))
            for path in sorted(declared_paths):
                if not (repo / path).is_file():
                    findings.append(_finding("declared_source_missing", path=path))

    findings.sort(key=lambda item: (item["code"], item.get("feature_id", ""), item.get("path", "")))
    report = {
        "release_ready": not findings,
        "release": release,
        "feature_count": len(features),
        "findings": findings,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest_path = args.manifest if args.manifest.is_absolute() else args.repo / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = audit_release_lineage(args.repo, manifest)
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
