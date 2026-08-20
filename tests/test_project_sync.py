import json
import subprocess
from pathlib import Path

import pytest

from memkraft import project_sync


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")


def test_discover_projects_skips_dependency_dirs(tmp_path):
    repo = tmp_path / "workspace" / "app"
    _init_repo(repo)
    nested = tmp_path / "workspace" / "node_modules" / "dep"
    _init_repo(nested)

    found = project_sync.discover_projects([str(tmp_path / "workspace")], max_depth=3)

    assert repo in found
    assert nested not in found


def test_inspect_synced_project_records_event(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = tmp_path / "memory"

    report = project_sync.inspect_project(str(repo))
    event = project_sync.record_event(report, base_dir=str(base), event="inspect")

    assert report["state"] == "blocked"  # no upstream is configured
    assert "missing_upstream" in report["reasons"]
    assert event["event"] == "inspect"
    event_file = base / ".memkraft" / "project-sync" / "events.jsonl"
    assert event_file.exists()
    line = event_file.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line)["path"] == str(repo.resolve())


def test_sync_dirty_checkout_is_blocked(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("local change\n", encoding="utf-8")

    result = project_sync.sync_project(str(repo), base_dir=str(tmp_path / "memory"), apply=True, fetch=False)

    assert result["applied"] is False
    assert result["blocked"] is True
    assert "dirty_worktree" in result["before"]["reasons"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "local change\n"


def test_sync_clean_behind_fast_forwards_only_with_apply(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE)
    origin = tmp_path / "origin"
    subprocess.run(["git", "clone", str(remote), str(origin)], check=True, stdout=subprocess.PIPE)
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "Test User")
    (origin / "README.md").write_text("v1\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "v1")
    _git(origin, "push", "-u", "origin", "HEAD:main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], check=True, stdout=subprocess.PIPE)
    _git(clone, "checkout", "main")

    (origin / "README.md").write_text("v2\n", encoding="utf-8")
    _git(origin, "commit", "-am", "v2")
    _git(origin, "push")

    dry = project_sync.sync_project(str(clone), base_dir=str(tmp_path / "memory"), apply=False, fetch=True)
    assert dry["applied"] is False
    assert dry["before"]["state"] == "behind_clean"
    assert (clone / "README.md").read_text(encoding="utf-8") == "v1\n"

    applied = project_sync.sync_project(str(clone), base_dir=str(tmp_path / "memory"), apply=True, fetch=True)
    assert applied["applied"] is True
    assert applied["after"]["state"] == "synced"
    assert (clone / "README.md").read_text(encoding="utf-8") == "v2\n"
