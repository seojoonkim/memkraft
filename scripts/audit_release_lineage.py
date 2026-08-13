#!/usr/bin/env python3
"""Fail-closed audit of the immutable baseline and candidate release lineage."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
STATES = frozenset({"planned", "implemented", "verified", "released"})
SHIPPED = frozenset({"implemented", "verified", "released"})
TOP_KEYS = frozenset({"schema", "release", "features", "excluded"})
RELEASE_KEYS = frozenset({
    "version", "state", "base_sha", "active_branch", "remote", "release_paths", "removed_paths",
})
FEATURE_KEYS = frozenset({"id", "state", "proposal_id", "revision_id", "evaluation_refs", "promotion_evidence", "commits", "source_paths"})
EXCLUDED_KEYS = frozenset({"id", "state", "reason"})
EVAL_KEYS = frozenset({"kind", "path", "node"})
PROMOTION_KEYS = frozenset({"kind", "path", "claim"})
PACKAGE_VERSION_PATH = "src/memkraft/__init__.py"
RELEASE_TRANSITION_PATHS = frozenset({
    "release_manifest.json",
    "pyproject.toml",
    PACKAGE_VERSION_PATH,
    "README.md",
    "CHANGELOG.md",
    "tests/test_packaging_version.py",
})


def _git(repo: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, timeout=30)


def _finding(code: str, **details: Any) -> Dict[str, Any]:
    finding = {"code": code, **details}
    raw = json.dumps(finding, sort_keys=True, separators=(",", ":"))
    finding["id"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return finding


def _extra_keys(value: Any, allowed: Set[str]) -> List[str]:
    return sorted(set(value) - allowed) if isinstance(value, dict) else []


def _is_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    plain = value[:-3] if value.endswith("/**") else value
    return "*" not in plain and ".." not in Path(plain).parts


def _scoped(path: str) -> bool:
    return bool(path)


def _tree_text(repo: Path, candidate: str, path: str) -> str:
    if not candidate or not _is_path(path):
        raise ValueError("invalid candidate tree lookup")
    result = _git(repo, ["show", candidate + ":" + path], check=False)
    if result.returncode != 0:
        raise FileNotFoundError(path)
    return result.stdout


def _tree_has(repo: Path, candidate: str, path: str) -> bool:
    return bool(candidate) and _git(repo, ["cat-file", "-e", candidate + ":" + path], check=False).returncode == 0


def _version_facts(repo: Path, candidate: str) -> Dict[str, str]:
    pyproject = _tree_text(repo, candidate, "pyproject.toml")
    package = _tree_text(repo, candidate, "src/memkraft/__init__.py")
    readme = _tree_text(repo, candidate, "README.md")
    changelog = _tree_text(repo, candidate, "CHANGELOG.md")
    facts = {}
    patterns = {
        "pyproject": (pyproject, r'(?m)^version\s*=\s*"([^"]+)"'),
        "package": (package, r'(?m)^__version__\s*=\s*"([^"]+)"'),
        "readme": (readme, r'(?m)^Current version:\s*\*\*([^*]+)\*\*'),
        "changelog": (changelog, r'(?m)^## \[([^]]+)\]'),
    }
    for name, (text, pattern) in patterns.items():
        match = re.search(pattern, text)
        if not match:
            raise ValueError("version not found in " + name)
        facts[name] = match.group(1)
    return facts


def _json_at(repo: Path, commit: str, path: str) -> Dict[str, Any]:
    value = json.loads(_tree_text(repo, commit, path))
    if not isinstance(value, dict):
        raise ValueError(path + " is not a JSON object")
    return value


def _constant(text: str, name: str) -> str:
    match = re.search(r'(?m)^%s\s*=\s*"([^"]+)"' % re.escape(name), text)
    if not match:
        raise ValueError(name + " not found")
    return match.group(1)


def _valid_version_transition(repo: Path, commit: str,
                              declared_feature_paths: Set[str]) -> str:
    """Validate an atomic transition, optionally squashed with declared features.

    ``declared_feature_paths`` contains only source paths owned by shipped
    features that declare this exact transition commit.
    """
    parents = _git(repo, ["rev-list", "--parents", "-n", "1", commit]).stdout.split()
    if len(parents) != 2:
        return "transition commit must have exactly one parent"
    parent = parents[1]
    changed = set(_git(
        repo, ["diff", "--name-only", "--diff-filter=ACMRD", "--no-renames", parent, commit]
    ).stdout.splitlines())
    new_manifest = _json_at(repo, commit, "release_manifest.json").get("release", {})
    old_manifest = _json_at(repo, parent, "release_manifest.json").get("release", {})
    old_version = old_manifest.get("version")
    new_version = new_manifest.get("version")
    required = set(RELEASE_TRANSITION_PATHS)
    if not isinstance(new_version, str) or not new_version or new_version == old_version:
        return "release manifest version did not change"
    note_path = "docs/releases/%s.md" % new_version
    required.add(note_path)
    missing = sorted(required - changed)
    if missing:
        return "transition is missing atomic paths: " + ", ".join(missing)
    other_source = sorted(path for path in changed if path.startswith("src/") and path != PACKAGE_VERSION_PATH)
    undeclared_source = sorted(set(other_source) - declared_feature_paths)
    if undeclared_source:
        return "transition contains undeclared or unowned source changes: " + ", ".join(undeclared_source)
    if old_manifest.get("active_branch") == new_manifest.get("active_branch"):
        return "release manifest active_branch did not change"
    if new_manifest.get("active_branch") != "release/" + new_version:
        return "active_branch does not match the new version"

    old_package = _tree_text(repo, parent, PACKAGE_VERSION_PATH)
    new_package = _tree_text(repo, commit, PACKAGE_VERSION_PATH)
    pattern = r'(?m)^__version__\s*=\s*"[^"]+"'
    if len(re.findall(pattern, old_package)) != 1 or len(re.findall(pattern, new_package)) != 1:
        return "package version assignment is ambiguous"
    normalized = "__version__ = VERSION"
    if (re.sub(pattern, normalized, old_package) != re.sub(pattern, normalized, new_package)
            and PACKAGE_VERSION_PATH not in declared_feature_paths):
        return "package initializer contains non-version drift"

    old_facts = _version_facts(repo, parent)
    new_facts = _version_facts(repo, commit)
    if set(old_facts.values()) != {old_version} or set(new_facts.values()) != {new_version}:
        return "canonical version surfaces do not transition together"
    old_tests = _tree_text(repo, parent, "tests/test_packaging_version.py")
    new_tests = _tree_text(repo, commit, "tests/test_packaging_version.py")
    if _constant(new_tests, "RELEASE_VERSION") != new_version:
        return "packaging RELEASE_VERSION does not match"
    for name in ("RELEASE_VERSION", "RELEASE_DATE"):
        if _constant(old_tests, name) == _constant(new_tests, name):
            return "packaging %s did not change" % name
    if not _tree_has(repo, commit, note_path):
        return "new release note is missing"
    if not re.search(r"(?m)^## MemKraft %s\s*$" % re.escape(new_version),
                     _tree_text(repo, commit, note_path)):
        return "new release note heading does not match"
    return ""


def audit_release_lineage(repo: Path, manifest: Dict[str, Any], *, candidate_sha: str = "HEAD",
                          candidate_branch: str = "", require_remote: bool = False) -> Dict[str, Any]:
    repo = repo.resolve()
    findings: List[Dict[str, Any]] = []
    if not isinstance(manifest, dict):
        manifest = {}
        findings.append(_finding("invalid_manifest_root"))
    for key in _extra_keys(manifest, set(TOP_KEYS)):
        findings.append(_finding("unknown_manifest_field", field=key))
    if manifest.get("schema") != 2:
        findings.append(_finding("unsupported_schema", observed=manifest.get("schema")))

    release = manifest.get("release")
    features = manifest.get("features")
    excluded = manifest.get("excluded")
    if not isinstance(release, dict):
        findings.append(_finding("missing_release")); release = {}
    if not isinstance(features, list):
        findings.append(_finding("missing_features")); features = []
    if not isinstance(excluded, list):
        findings.append(_finding("missing_excluded")); excluded = []
    for key in _extra_keys(release, set(RELEASE_KEYS)):
        findings.append(_finding("unknown_release_field", field=key))

    for field in ("version", "active_branch", "remote"):
        if not isinstance(release.get(field), str) or not release[field]:
            findings.append(_finding("missing_release_field", field=field))
    base = release.get("base_sha")
    if not isinstance(base, str) or not SHA_RE.fullmatch(base):
        findings.append(_finding("invalid_base_sha", observed=base))
    if release.get("state") != "verified":
        findings.append(_finding("release_not_verified", state=release.get("state")))
    release_paths = release.get("release_paths")
    if not isinstance(release_paths, list) or not all(_is_path(x) for x in release_paths):
        findings.append(_finding("invalid_release_paths")); release_paths = []
    invalid_release_scope = [
        path for path in release_paths
        if path == "src" or path.startswith("src/")
    ]
    for path in invalid_release_scope:
        findings.append(_finding("invalid_release_path_scope", path=path))
    release_paths = [path for path in release_paths if path not in invalid_release_scope]
    removed_paths = release.get("removed_paths", [])
    if not isinstance(removed_paths, list) or not all(_is_path(x) for x in removed_paths):
        findings.append(_finding("invalid_removed_paths")); removed_paths = []
    if set(release_paths) & set(removed_paths):
        findings.append(_finding("path_both_present_and_removed"))

    try:
        candidate = _git(repo, ["rev-parse", "--verify", candidate_sha + "^{commit}"]).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        candidate = ""
        findings.append(_finding("candidate_not_found", candidate=candidate_sha))
    if candidate_branch and candidate_branch != release.get("active_branch"):
        findings.append(_finding("active_branch_mismatch", expected=release.get("active_branch"), observed=candidate_branch))
    if candidate and base and SHA_RE.fullmatch(str(base)):
        if _git(repo, ["merge-base", "--is-ancestor", base, candidate], check=False).returncode != 0:
            findings.append(_finding("base_not_ancestor", base_sha=base, candidate_sha=candidate))
    if require_remote and candidate:
        remote = release.get("remote", "")
        branch = release.get("active_branch", "")
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
            findings.append(_finding("invalid_remote", remote=remote))
            remote = ""
        result = _git(repo, ["ls-remote", "--heads", remote, "refs/heads/" + branch], check=False) if remote else subprocess.CompletedProcess([], 1, "", "")
        remote_sha = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""
        if remote_sha != candidate:
            findings.append(_finding("remote_candidate_mismatch", candidate_sha=candidate, remote_sha=remote_sha, branch=branch))

    owners: Dict[str, str] = {}
    shipped_feature_commits: Dict[str, Set[str]] = {}
    ids = set()
    def own(path: str, owner: str) -> None:
        if path in owners and owners[path] != owner:
            findings.append(_finding("duplicate_path_owner", path=path, owners=sorted([owners[path], owner])))
        else:
            owners[path] = owner

    for path in release_paths:
        own(path, "release")
    for path in removed_paths:
        own(path, "removed")

    def owner_for(path: str) -> str:
        exact = owners.get(path, "")
        prefixes = sorted((key[:-2] for key in owners if key.endswith("/**")), key=len, reverse=True)
        matches = [prefix for prefix in prefixes if path.startswith(prefix)]
        return exact or (owners[matches[0] + "**"] if matches else "")
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            findings.append(_finding("invalid_feature", index=index)); continue
        feature_id = feature.get("id")
        for key in _extra_keys(feature, set(FEATURE_KEYS)):
            findings.append(_finding("unknown_feature_field", feature_id=feature_id, field=key))
        if not isinstance(feature_id, str) or not feature_id:
            findings.append(_finding("missing_feature_id", index=index)); feature_id = "feature#%d" % index
        if feature_id in ids:
            findings.append(_finding("duplicate_feature_id", feature_id=feature_id))
        ids.add(feature_id)
        state = feature.get("state")
        if state not in STATES:
            findings.append(_finding("invalid_feature_state", feature_id=feature_id, state=state))
        for field in ("proposal_id", "revision_id"):
            if not isinstance(feature.get(field), str) or not feature[field]:
                findings.append(_finding("missing_lineage_field", feature_id=feature_id, field=field))
        commits = feature.get("commits")
        paths = feature.get("source_paths")
        refs = feature.get("evaluation_refs")
        promotion = feature.get("promotion_evidence")
        if not isinstance(commits, list) or not all(isinstance(x, str) and SHA_RE.fullmatch(x) for x in commits):
            findings.append(_finding("invalid_commits", feature_id=feature_id)); commits = []
        if not isinstance(paths, list) or not all(_is_path(x) and x.startswith("src/") for x in paths):
            findings.append(_finding("invalid_source_paths", feature_id=feature_id)); paths = []
        if state == "planned" and (commits or paths):
            findings.append(_finding("planned_feature_has_implementation", feature_id=feature_id))
        if state in SHIPPED and (not commits or not paths):
            findings.append(_finding("implemented_feature_missing_artifacts", feature_id=feature_id))
        if state in SHIPPED:
            shipped_feature_commits[feature_id] = set(commits)
        for path in paths:
            own(path, feature_id)
        if not isinstance(refs, list) or not refs:
            findings.append(_finding("invalid_evaluation_refs", feature_id=feature_id)); refs = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) - EVAL_KEYS or ref.get("kind") not in ("pytest", "go-test") or not _is_path(ref.get("path")):
                findings.append(_finding("invalid_evaluation_ref", feature_id=feature_id)); continue
            if not _tree_has(repo, candidate, ref["path"]):
                findings.append(_finding("evaluation_ref_missing", feature_id=feature_id, path=ref["path"]))
        if not isinstance(promotion, list) or not promotion:
            findings.append(_finding("invalid_promotion_evidence", feature_id=feature_id)); promotion = []
        for evidence in promotion:
            valid = (isinstance(evidence, dict) and not (set(evidence) - PROMOTION_KEYS)
                     and evidence.get("kind") in ("release-note", "changelog")
                     and _is_path(evidence.get("path")) and isinstance(evidence.get("claim"), str)
                     and bool(evidence.get("claim")))
            if not valid:
                findings.append(_finding("invalid_promotion_evidence", feature_id=feature_id)); continue
            try:
                evidence_text = _tree_text(repo, candidate, evidence["path"])
            except (OSError, ValueError):
                evidence_text = ""
            if evidence["claim"] not in evidence_text:
                findings.append(_finding("promotion_evidence_unsubstantiated", feature_id=feature_id, path=evidence["path"]))
        for commit in commits:
            if _git(repo, ["cat-file", "-e", commit + "^{commit}"], check=False).returncode != 0:
                findings.append(_finding("commit_not_found", feature_id=feature_id, commit=commit)); continue
            if base and SHA_RE.fullmatch(str(base)) and _git(repo, ["merge-base", "--is-ancestor", base, commit], check=False).returncode != 0:
                findings.append(_finding("commit_not_after_base", feature_id=feature_id, commit=commit))
            if candidate and _git(repo, ["merge-base", "--is-ancestor", commit, candidate], check=False).returncode != 0:
                findings.append(_finding("commit_not_reachable", feature_id=feature_id, commit=commit))

    excluded_ids = set()
    for item in excluded:
        if not isinstance(item, dict) or set(item) != EXCLUDED_KEYS or item.get("state") != "planned" or not all(isinstance(item.get(k), str) and item[k] for k in ("id", "reason")):
            findings.append(_finding("invalid_excluded_entry")); continue
        if item["id"] in excluded_ids or item["id"] in ids:
            findings.append(_finding("duplicate_excluded_id", excluded_id=item["id"]))
        excluded_ids.add(item["id"])

    if candidate and base and SHA_RE.fullmatch(str(base)):
        changed = _git(repo, ["diff", "--name-only", "--diff-filter=ACMRD", base + ".." + candidate]).stdout.splitlines()
        version_transition_commits = _git(
            repo,
            ["log", "--full-history", "--format=%H", base + ".." + candidate, "--", PACKAGE_VERSION_PATH],
        ).stdout.splitlines()
        valid_version_commits = set()
        for commit in version_transition_commits:
            parent = _git(repo, ["rev-parse", commit + "^1"]).stdout.strip()
            try:
                package_changed = (
                    _version_facts(repo, parent)["package"]
                    != _version_facts(repo, commit)["package"]
                )
            except (OSError, ValueError, TypeError):
                package_changed = True
            if not package_changed:
                continue
            declared_feature_paths = {
                path for path in changed
                if path.startswith("src/")
                and owner_for(path) in shipped_feature_commits
                and commit in shipped_feature_commits[owner_for(path)]
            }
            try:
                reason = _valid_version_transition(repo, commit, declared_feature_paths)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                reason = str(exc)
            if reason:
                findings.append(_finding(
                    "invalid_release_version_transition", commit=commit,
                    path=PACKAGE_VERSION_PATH, reason=reason,
                ))
            else:
                valid_version_commits.add(commit)
        # This exception is commit-scoped. A valid transition must not mask a
        # later non-version package-initializer change.
        package_touching_commits = set(_git(
            repo,
            ["log", "--full-history", "--no-merges", "--format=%H",
             base + ".." + candidate, "--", PACKAGE_VERSION_PATH],
        ).stdout.splitlines())
        merge_commits = _git(
            repo, ["rev-list", "--merges", base + ".." + candidate]
        ).stdout.splitlines()
        for merge_commit in merge_commits:
            merge_touch = _git(
                repo,
                ["diff", "--name-only", "--diff-filter=ACMRD", "--no-renames",
                 merge_commit + "^1", merge_commit, "--", PACKAGE_VERSION_PATH],
            ).stdout.splitlines()
            if merge_touch:
                package_touching_commits.add(merge_commit)
        package_has_only_valid_version_drift = (
            bool(package_touching_commits)
            and package_touching_commits <= valid_version_commits
        )
        for path in sorted(p for p in changed if _scoped(p)):
            if not owner_for(path) and not (
                path == PACKAGE_VERSION_PATH and package_has_only_valid_version_drift
            ):
                findings.append(_finding("unregistered_release_drift", path=path))
        for feature in features:
            if not isinstance(feature, dict) or feature.get("state") not in SHIPPED:
                continue
            feature_id = feature.get("id")
            raw_commits = feature.get("commits")
            declared_commits = (
                set(raw_commits)
                if isinstance(raw_commits, list)
                and all(isinstance(item, str) and SHA_RE.fullmatch(item) for item in raw_commits)
                else set()
            )
            paths = feature.get("source_paths") or []
            if not isinstance(feature_id, str) or not all(isinstance(path, str) for path in paths):
                continue
            touched_by_declared = set()
            for path in paths:
                touching = set(_git(
                    repo,
                    ["log", "--full-history", "--no-merges", "--format=%H", base + ".." + candidate, "--", path],
                ).stdout.splitlines())
                if path == PACKAGE_VERSION_PATH:
                    # A pure release transition is not feature work. A combined
                    # squash transition remains a real feature touch only when
                    # this exact feature declares this exact commit.
                    touching -= valid_version_commits - declared_commits
                for merge_commit in merge_commits:
                    merge_touch = _git(
                        repo,
                        ["diff", "--name-only", "--diff-filter=ACMRD", "--no-renames",
                         merge_commit + "^1", merge_commit, "--", path],
                    ).stdout.splitlines()
                    if merge_touch:
                        touching.add(merge_commit)
                touched_by_declared.update(touching & declared_commits)
                for commit in sorted(touching):
                    if commit not in declared_commits:
                        findings.append(_finding(
                            "undeclared_feature_commit",
                            feature_id=feature_id,
                            commit=commit,
                            path=path,
                        ))
            for commit in sorted(declared_commits - touched_by_declared):
                findings.append(_finding(
                    "declared_feature_commit_without_touch",
                    feature_id=feature_id,
                    commit=commit,
                ))
    for path, owner in sorted(owners.items()):
        check_path = path[:-3] if path.endswith("/**") else path
        present = _tree_has(repo, candidate, check_path)
        if owner == "removed" and present:
            findings.append(_finding("removed_path_still_present", path=path))
        elif owner != "removed" and not present:
            findings.append(_finding("declared_path_missing", path=path))
    try:
        expected = release.get("version")
        for source, observed in _version_facts(repo, candidate).items():
            if observed != expected:
                findings.append(_finding("version_mismatch", source=source, expected=expected, observed=observed))
        note_path = "docs/releases/%s.md" % expected
        if not _tree_has(repo, candidate, note_path):
            findings.append(_finding("release_note_missing", path=note_path))
    except (OSError, ValueError, TypeError) as exc:
        findings.append(_finding("version_collection_failed", error=str(exc)))

    findings.sort(key=lambda x: (x["code"], x.get("feature_id", ""), x.get("path", ""), x["id"]))
    report = {"release_ready": not findings, "release": release, "candidate_sha": candidate,
              "feature_count": len(features), "findings": findings}
    report["digest"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def _failure(error: Exception) -> int:
    print(json.dumps({"release_ready": False, "findings": [_finding("audit_error", error=str(error))]}, sort_keys=True))
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate-sha", default="HEAD")
    parser.add_argument("--candidate-branch", default="")
    parser.add_argument("--require-remote", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = args.manifest if args.manifest.is_absolute() else args.repo / args.manifest
        manifest = json.loads(path.read_text(encoding="utf-8"))
        report = audit_release_lineage(args.repo, manifest, candidate_sha=args.candidate_sha,
                                       candidate_branch=args.candidate_branch, require_remote=args.require_remote)
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0 if report["release_ready"] else 1
    except Exception as exc:
        return _failure(exc)


if __name__ == "__main__":
    raise SystemExit(main())
