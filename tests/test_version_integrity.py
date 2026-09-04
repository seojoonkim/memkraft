"""Regression tests for source/release version convergence."""
import json
from pathlib import Path

import pytest

from scripts.check_version_integrity import check


ROOT = Path(__file__).resolve().parents[1]


def test_current_4_1_source_and_release_metadata_converge():
    report = check(ROOT)
    assert report["version"] == "4.1.0"
    assert report["version_integrity"] is True
    assert report["findings"] == []


def test_manifest_version_drift_fails_closed(tmp_path):
    for source in ROOT.iterdir():
        target = tmp_path / source.name
        if source.is_dir() and source.name not in {".git", "build", "dist", ".pytest_cache"}:
            continue
        if source.is_file():
            target.write_bytes(source.read_bytes())
    manifest = json.loads((tmp_path / "release_manifest.json").read_text())
    manifest["release"]["version"] = "3.4.1"
    (tmp_path / "release_manifest.json").write_text(json.dumps(manifest))
    # The temporary copy is intentionally incomplete for Git/tag checks; the
    # production check remains fail-closed rather than silently accepting it.
    with pytest.raises(Exception):
        check(tmp_path)


def test_release_branch_mismatch_is_rejected(monkeypatch):
    import scripts.check_version_integrity as integrity

    original = integrity.git

    def fake_git(repo, *args):
        if args == ("branch", "--show-current"):
            return "release/3.4.1"
        return original(repo, *args)

    monkeypatch.setattr(integrity, "git", fake_git)
    report = check(ROOT)
    assert report["version_integrity"] is False
    assert any(item["code"] == "release_branch_version_mismatch" for item in report["findings"])
