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
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "tag", "v1.0.0")
    return repo, base, branch


def _manifest(base: str, commit: str, paths=None):
    return {
        "schema": 1,
        "release": {
            "version": "1.1.0",
            "state": "verified",
            "base_ref": "v1.0.0",
            "target_ref": "HEAD",
        },
        "features": [
            {
                "id": "feature.one",
                "state": "verified",
                "proposal_id": "prop.feature-one",
                "revision_id": "r1",
                "evaluation_ref": "pytest://tests/test_feature.py",
                "promotion_ref": "release://1.1.0/feature.one",
                "commits": [commit],
                "source_paths": paths or ["src/memkraft/feature_one.py"],
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
    finding = next(f for f in report["findings"] if f["code"] == "unregistered_source_drift")
    assert finding["path"] == "src/memkraft/stray.py"


def test_planned_feature_cannot_claim_implementation(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    manifest["features"][0]["state"] = "planned"

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is False
    assert any(f["code"] == "planned_feature_has_implementation" for f in report["findings"])


def test_manifest_requires_memkraft_lineage_fields(auditor, tmp_path):
    repo, base, branch = _repo(tmp_path)
    manifest = _manifest(base, base, paths=[])
    del manifest["features"][0]["evaluation_ref"]

    report = auditor.audit_release_lineage(repo, manifest)

    assert report["release_ready"] is False
    assert any(f["code"] == "missing_lineage_field" for f in report["findings"])


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
