"""Fail-closed release-lineage manifest and CI integration."""
from __future__ import annotations

import importlib.util
import json
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
    (repo / "docs" / "releases").mkdir(parents=True)
    (repo / "docs" / "releases" / "1.1.0.md").write_text("Feature One\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")
    (repo / "src" / "memkraft" / "__init__.py").write_text('__version__ = "1.1.0"\n', encoding="utf-8")
    (repo / "README.md").write_text("Current version: **1.1.0**\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("## [1.1.0]\n", encoding="utf-8")
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


def test_manifest_requires_memkraft_lineage_fields(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    del manifest["features"][0]["evaluation_refs"]

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is False
    assert any(f["code"] == "invalid_evaluation_refs" for f in report["findings"])


def test_repository_manifest_passes_and_covers_all_changed_modules(auditor):
    manifest = json.loads((REPO_ROOT / "release_manifest.json").read_text(encoding="utf-8"))
    report = auditor.audit_release_lineage(REPO_ROOT, manifest)
    assert report["release_ready"] is True, report["findings"]


def test_ci_uses_full_history_and_runs_lineage_audit():
    workflow = (REPO_ROOT / ".github" / "workflows" / "gym-gate.yml").read_text(encoding="utf-8")
    checkout_count = workflow.count("uses: actions/checkout@v4")
    assert checkout_count > 0
    assert workflow.count("fetch-depth: 0") == checkout_count
    assert "python scripts/audit_release_lineage.py --repo . --manifest release_manifest.json" in workflow
    assert "pull_request:\n    paths:" not in workflow
    for path in ('"src/**"', '"tests/**"', '"scripts/**"', '"benchmarks/**"', '"docs/**"', '".github/**"', '"setup.py"', '"MANIFEST.in"'):
        assert path in workflow


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
