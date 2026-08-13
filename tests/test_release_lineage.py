"""Fail-closed release-lineage manifest and CI integration."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_auditor():
    path = REPO_ROOT / "scripts" / "audit_release_lineage.py"
    spec = importlib.util.spec_from_file_location("_audit_release_lineage", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def auditor():
    return _load_auditor()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src" / "memkraft").mkdir(parents=True)
    (repo / "src" / "memkraft" / "core.py").write_text("BASE = True\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_feature.py").write_text("def test_feature(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_packaging_version.py").write_text(
        'RELEASE_VERSION = "1.1.0"\nRELEASE_DATE = "2026-01-01"\n', encoding="utf-8"
    )
    (repo / "docs" / "releases").mkdir(parents=True)
    (repo / "docs" / "releases" / "1.1.0.md").write_text("Feature One\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")
    (repo / "src" / "memkraft" / "__init__.py").write_text('__version__ = "1.1.0"\n', encoding="utf-8")
    (repo / "README.md").write_text("Current version: **1.1.0**\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("## [1.1.0]\n", encoding="utf-8")
    (repo / "release_manifest.json").write_text(json.dumps({
        "release": {"version": "1.1.0", "active_branch": "release/1.1.0"}
    }) + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "tag", "v1.0.0")
    return repo, base, branch


def _manifest(base: str, commit: str, paths=None):
    return {
        "schema": 2,
        "release": {
            "version": "1.1.0",
            "state": "verified",
            "base_sha": base,
            "active_branch": "release/1.1.0",
            "remote": "origin",
            "release_paths": ["pyproject.toml", "README.md", "CHANGELOG.md", "docs/releases/1.1.0.md", "tests/test_feature.py"],
        },
        "features": [
            {
                "id": "feature.one",
                "state": "verified",
                "proposal_id": "prop.feature-one",
                "revision_id": "r1",
                "evaluation_refs": [{"kind": "pytest", "path": "tests/test_feature.py", "node": "all"}],
                "promotion_evidence": [{"kind": "release-note", "path": "docs/releases/1.1.0.md", "claim": "Feature One"}],
                "commits": [commit],
                "source_paths": paths if paths is not None else ["src/memkraft/feature_one.py"],
            }
        ],
        "excluded": [],
    }


def test_healthy_manifest_is_release_ready(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    commit = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, commit))

    assert report["release_ready"] is True
    assert report["findings"] == []


def test_stranded_implementation_commit_fails_closed(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    _git(repo, "checkout", "-qb", "stranded")
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "stranded feature")
    stranded = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", branch)

    report = auditor.audit_release_lineage(repo, _manifest(base, stranded))

    assert report["release_ready"] is False
    finding = next(f for f in report["findings"] if f["code"] == "commit_not_reachable")
    assert finding["feature_id"] == "feature.one"
    assert finding["commit"] == stranded


def test_unregistered_source_drift_fails_closed(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    declared = repo / "src" / "memkraft" / "feature_one.py"
    declared.write_text("VALUE = 1\n", encoding="utf-8")
    stray = repo / "src" / "memkraft" / "stray.py"
    stray.write_text("STRAY = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature plus stray")
    commit = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, commit))

    assert report["release_ready"] is False
    finding = next(f for f in report["findings"] if f["code"] == "unregistered_release_drift")
    assert finding["path"] == "src/memkraft/stray.py"


def test_undeclared_commit_touching_feature_source_fails_closed(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    declared = _git(repo, "rev-parse", "HEAD")
    path.write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "unregistered follow-up")
    undeclared = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, declared))

    finding = next(f for f in report["findings"] if f["code"] == "undeclared_feature_commit")
    assert finding["feature_id"] == "feature.one"
    assert finding["commit"] == undeclared
    assert finding["path"] == "src/memkraft/feature_one.py"


def test_planned_feature_cannot_claim_implementation(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    manifest["features"][0]["state"] = "planned"

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is False
    assert any(f["code"] == "planned_feature_has_implementation" for f in report["findings"])


def test_release_must_be_verified(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    manifest["release"]["state"] = "planned"
    report = auditor.audit_release_lineage(repo, manifest)
    assert any(f["code"] == "release_not_verified" for f in report["findings"])


def test_build_inputs_are_in_fail_closed_scope(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    (repo / "setup.py").write_text("from setuptools import setup; setup()\n")
    (repo / "MANIFEST.in").write_text("include README.md\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change build inputs")
    candidate = _git(repo, "rev-parse", "HEAD")
    report = auditor.audit_release_lineage(repo, _manifest(base, candidate, paths=[]))
    drift = {f.get("path") for f in report["findings"] if f["code"] == "unregistered_release_drift"}
    assert {"setup.py", "MANIFEST.in"} <= drift


def test_top_level_tools_are_in_fail_closed_scope(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    (repo / "tools").mkdir()
    (repo / "tools" / "release_helper.py").write_text("print('release')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add release helper")
    candidate = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, candidate, paths=[]))

    drift = {f.get("path") for f in report["findings"] if f["code"] == "unregistered_release_drift"}
    assert "tools/release_helper.py" in drift


def test_examples_and_go_workspace_are_in_fail_closed_scope(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    (repo / "examples").mkdir()
    (repo / "examples" / "adapter.py").write_text("VALUE = 1\n")
    (repo / "go.work").write_text("go 1.22\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add unregistered integration inputs")
    candidate = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, candidate, paths=[]))

    drift = {f.get("path") for f in report["findings"] if f["code"] == "unregistered_release_drift"}
    assert {"examples/adapter.py", "go.work"} <= drift


def test_commit_declared_for_another_feature_still_fails_feature_lineage(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    first = repo / "src" / "memkraft" / "feature_one.py"
    second = repo / "src" / "memkraft" / "feature_two.py"
    first.write_text("VALUE = 1\n")
    second.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "touch both features")
    commit = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, commit)
    manifest["features"].append({
        **manifest["features"][0],
        "id": "feature.two",
        "proposal_id": "prop.feature-two",
        "commits": [],
        "source_paths": ["src/memkraft/feature_two.py"],
    })

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(
        f["code"] == "undeclared_feature_commit"
        and f.get("feature_id") == "feature.two"
        and f.get("commit") == commit
        for f in report["findings"]
    )


def test_commit_touching_two_features_may_be_declared_by_both(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    first = repo / "src" / "memkraft" / "feature_one.py"
    second = repo / "src" / "memkraft" / "feature_two.py"
    first.write_text("VALUE = 1\n")
    second.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "shared integration commit")
    commit = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, commit)
    manifest["features"].append({
        **manifest["features"][0],
        "id": "feature.two",
        "proposal_id": "prop.feature-two",
        "commits": [commit],
        "source_paths": ["src/memkraft/feature_two.py"],
    })

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is True, report["findings"]


def test_merge_side_branch_feature_touch_is_audited(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "declared feature")
    declared = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "side")
    path.write_text("VALUE = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "side feature touch")
    side_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", branch)
    path.write_text("VALUE = 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "main update")
    _git(repo, "merge", "--no-ff", "-s", "ours", "side", "-m", "merge side while retaining main tree")
    merge_commit = _git(repo, "rev-parse", "HEAD")

    report = auditor.audit_release_lineage(repo, _manifest(base, declared))

    assert any(
        f["code"] == "undeclared_feature_commit"
        and f.get("feature_id") == "feature.one"
        and f.get("commit") == side_commit
        for f in report["findings"]
    )
    assert not any(
        f["code"] == "undeclared_feature_commit"
        and f.get("commit") == merge_commit
        for f in report["findings"]
    )


def _content_bearing_merge(repo, branch):
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "declared feature")
    declared = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "content-side")
    path.write_text("VALUE = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "side content")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", branch)
    _git(repo, "merge", "--no-ff", "content-side", "-m", "content-bearing merge")
    merge = _git(repo, "rev-parse", "HEAD")
    return declared, side, merge


def test_undeclared_content_bearing_merge_is_audited(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    declared, side, merge = _content_bearing_merge(repo, branch)
    manifest = _manifest(base, merge)
    manifest["features"][0]["commits"] = [declared, side]

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(
        f["code"] == "undeclared_feature_commit" and f.get("commit") == merge
        for f in report["findings"]
    )


def test_declared_content_bearing_merge_counts_as_feature_touch(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    declared, side, merge = _content_bearing_merge(repo, branch)
    manifest = _manifest(base, merge)
    manifest["features"][0]["commits"] = [declared, side, merge]

    report = auditor.audit_release_lineage(repo, manifest)

    assert not any(
        f["code"] in {"undeclared_feature_commit", "declared_feature_commit_without_touch"}
        and f.get("commit") == merge
        for f in report["findings"]
    )


def test_declared_topology_only_merge_is_rejected_as_without_touch(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "declared feature")
    declared = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "topology-side")
    path.write_text("VALUE = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "side feature touch")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", branch)
    _git(repo, "merge", "--no-ff", "-s", "ours", "topology-side", "-m", "topology only")
    merge = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, merge)
    manifest["features"][0]["commits"] = [declared, side, merge]

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(
        f["code"] == "declared_feature_commit_without_touch" and f.get("commit") == merge
        for f in report["findings"]
    )


def test_release_owner_cannot_claim_source_paths(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    manifest["release"]["release_paths"].append("src/**")

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(f["code"] == "invalid_release_path_scope" for f in report["findings"])


def test_declared_feature_commit_must_touch_the_feature(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    feature_path = repo / "src" / "memkraft" / "feature_one.py"
    feature_path.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    feature_commit = _git(repo, "rev-parse", "HEAD")
    unrelated = repo / "README.md"
    unrelated.write_text("unrelated\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "unrelated")
    unrelated_commit = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, feature_commit)
    manifest["features"][0]["commits"].append(unrelated_commit)

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(
        f["code"] == "declared_feature_commit_without_touch"
        and f.get("commit") == unrelated_commit
        for f in report["findings"]
    )


def test_invalid_commit_container_is_not_reused_by_reverse_audit(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    manifest = _manifest(base, base)
    manifest["features"][0]["commits"] = "not-a-list"

    report = auditor.audit_release_lineage(repo, manifest)

    assert any(f["code"] == "invalid_commits" for f in report["findings"])
    assert any(f["code"] == "undeclared_feature_commit" for f in report["findings"])


def test_manifest_requires_memkraft_lineage_fields(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    del manifest["features"][0]["evaluation_refs"]

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is False
    assert any(f["code"] == "invalid_evaluation_refs" for f in report["findings"])


def _commit_release_transition(repo: Path, base: str, *, omit="", extra_source=False, init_suffix=""):
    manifest = _manifest(base, base, paths=[])
    manifest["features"] = []
    manifest["release"]["version"] = "1.2.0"
    manifest["release"]["active_branch"] = "release/1.2.0"
    manifest["release"]["release_paths"].extend([
        "release_manifest.json", "docs/releases/1.2.0.md", "tests/test_packaging_version.py",
    ])
    updates = {
        "release_manifest.json": json.dumps(manifest, indent=2) + "\n",
        "pyproject.toml": '[project]\nversion = "1.2.0"\n',
        "src/memkraft/__init__.py": '__version__ = "1.2.0"\n' + init_suffix,
        "README.md": "Current version: **1.2.0**\n",
        "CHANGELOG.md": "## [1.2.0]\n## [1.1.0]\n",
        "docs/releases/1.2.0.md": "## MemKraft 1.2.0\n",
        "tests/test_packaging_version.py": (
            'RELEASE_VERSION = "1.2.0"\nRELEASE_DATE = "2026-02-02"\n'
        ),
    }
    for path, content in updates.items():
        if path != omit:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if extra_source:
        (repo / "src" / "memkraft" / "stray.py").write_text("STRAY = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "atomic release transition")
    return manifest, _git(repo, "rev-parse", "HEAD")


def test_atomic_version_only_release_transition_is_allowed(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest, candidate = _commit_release_transition(repo, base)

    report = auditor.audit_release_lineage(repo, manifest, candidate_sha=candidate)

    assert report["release_ready"] is True, report["findings"]


@pytest.mark.parametrize("omit", [
    "release_manifest.json", "pyproject.toml", "README.md", "CHANGELOG.md",
    "docs/releases/1.2.0.md", "tests/test_packaging_version.py",
])
def test_incomplete_release_transition_fails_closed(auditor, tmp_path, omit):
    repo, base, branch = _repo(tmp_path)
    manifest, candidate = _commit_release_transition(repo, base, omit=omit)

    report = auditor.audit_release_lineage(repo, manifest, candidate_sha=candidate)

    assert any(f["code"] == "invalid_release_version_transition" for f in report["findings"])
    assert any(
        f["code"] == "unregistered_release_drift" and f.get("path") == "src/memkraft/__init__.py"
        for f in report["findings"]
    )


@pytest.mark.parametrize("mutation", ["extra_source", "init_content"])
def test_release_transition_rejects_any_other_source_change(auditor, tmp_path, mutation):
    repo, base, branch = _repo(tmp_path)
    manifest, candidate = _commit_release_transition(
        repo, base, extra_source=mutation == "extra_source",
        init_suffix="OTHER = True\n" if mutation == "init_content" else "",
    )

    report = auditor.audit_release_lineage(repo, manifest, candidate_sha=candidate)

    assert any(f["code"] == "invalid_release_version_transition" for f in report["findings"])


def test_repository_manifest_passes_and_covers_all_changed_modules(auditor):
    candidate_sha = os.environ["CANDIDATE_SHA"]
    manifest = json.loads(_git(REPO_ROOT, "show", candidate_sha + ":release_manifest.json"))
    report = auditor.audit_release_lineage(REPO_ROOT, manifest, candidate_sha=candidate_sha)
    assert report["candidate_sha"] == _git(REPO_ROOT, "rev-parse", candidate_sha + "^{commit}")
    assert report["release_ready"] is True, report["findings"]


def test_repository_manifest_requires_explicit_candidate_sha(auditor, monkeypatch):
    monkeypatch.delenv("CANDIDATE_SHA", raising=False)
    with pytest.raises(KeyError, match="CANDIDATE_SHA"):
        test_repository_manifest_passes_and_covers_all_changed_modules(auditor)


def test_ci_uses_full_history_and_runs_lineage_audit():
    workflow = (REPO_ROOT / ".github" / "workflows" / "gym-gate.yml").read_text(encoding="utf-8")
    checkout_count = workflow.count("uses: actions/checkout@v4")
    assert checkout_count > 0
    assert workflow.count("fetch-depth: 0") == checkout_count
    assert "python scripts/audit_release_lineage.py --repo . --manifest release_manifest.json" in workflow
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "pull_request:\n    paths:" not in workflow
    push_block = workflow.split("  push:", 1)[1].split("\njobs:", 1)[0]
    assert "branches: [main]" in push_block
    assert "paths:" not in push_block


def test_nonexistent_evaluation_ref_fails_closed(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    manifest = _manifest(base, _git(repo, "rev-parse", "HEAD"))
    manifest["features"][0]["evaluation_refs"][0]["path"] = "tests/nope.py"
    report = auditor.audit_release_lineage(repo, manifest)
    assert any(f["code"] == "evaluation_ref_missing" for f in report["findings"])


def test_uncommitted_file_cannot_satisfy_candidate_tree(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    candidate = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, candidate)
    manifest["features"][0]["evaluation_refs"][0]["path"] = "tests/uncommitted.py"
    (repo / "tests" / "uncommitted.py").write_text("def test_only_worktree(): pass\n")

    report = auditor.audit_release_lineage(repo, manifest, candidate_sha=candidate)

    assert any(f["code"] == "evaluation_ref_missing" for f in report["findings"])


def test_declared_removed_path_must_be_absent_from_candidate(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    path = repo / "src" / "memkraft" / "feature_one.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    candidate = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(base, candidate)
    manifest["release"]["release_paths"].remove("tests/test_feature.py")
    manifest["release"]["removed_paths"] = ["tests/test_feature.py"]
    report = auditor.audit_release_lineage(repo, manifest, candidate_sha=candidate)
    assert any(f["code"] == "removed_path_still_present" for f in report["findings"])


def test_mutable_target_ref_is_rejected_by_closed_schema(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    manifest["release"]["target_ref"] = "HEAD"
    report = auditor.audit_release_lineage(repo, manifest)
    assert any(f["code"] == "unknown_release_field" for f in report["findings"])


def test_base_must_be_candidate_ancestor(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    _git(repo, "checkout", "-qb", "other")
    (repo / "other").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "other")
    other = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", branch)
    report = auditor.audit_release_lineage(repo, _manifest(other, base, paths=[]))
    assert any(f["code"] == "base_not_ancestor" for f in report["findings"])


def test_release_workflow_is_oidc_only_and_artifact_chained():
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "pull_request_target" not in workflow
    assert "workflow_dispatch" not in workflow
    assert 'tags:\n      - "v*"' in workflow
    assert "git rev-parse 'HEAD^{commit}'" in workflow
    assert "candidate_sha=$GITHUB_SHA" not in workflow
    assert "release/3.4.0" not in workflow
    assert "active_branch=" in workflow
    assert "actions/setup-go@v5" in workflow
    assert "go test ./..." in workflow
    assert "benchmark_execution_gates.py" in workflow
    assert 'result["g11"]["passed"] is True' in workflow
    assert 'result["g12"]["passed"] is True' in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert workflow.count("actions/download-artifact@v4") >= 3
    assert "PYPI_TOKEN" not in workflow and "secrets." not in workflow
    checklist = (REPO_ROOT / "docs/RELEASE_CHECKLIST.md").read_text()
    assert "python3 -m twine upload" not in checklist
    assert "gh release create" not in checklist
    assert '["3.9", "3.12"]' in (REPO_ROOT / ".github/workflows/gym-gate.yml").read_text()
