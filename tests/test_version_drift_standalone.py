import subprocess
from pathlib import Path

from version_drift import inspect_project, record_event
from version_drift.cli import main


def git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "init")


def test_standalone_inspect_and_event_store_without_memkraft(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    report = inspect_project(str(repo))
    assert report["schema"] == "version-drift/1"
    assert "missing_upstream" in report["reasons"]

    event = record_event(report, base_dir=str(tmp_path / "state"), event="inspect")
    assert event["event"] == "inspect"
    assert (tmp_path / "state" / ".version-drift" / "events.jsonl").exists()


def test_standalone_cli_runs_without_memkraft_import(tmp_path, capsys):
    repo = tmp_path / "repo"
    init_repo(repo)
    exit_code = main(["--base-dir", str(tmp_path / "state"), "inspect", str(repo), "--json"])
    assert exit_code == 1
    assert '"state": "blocked"' in capsys.readouterr().out


def test_memkraft_cli_keeps_version_drift_bridge():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'memkraft = "memkraft.cli:main"' in pyproject
    assert 'version-drift = "version_drift.cli:main"' not in pyproject
    assert 'version-drift = ["version-drift>=0.1.0,<0.2.0"]' in pyproject
