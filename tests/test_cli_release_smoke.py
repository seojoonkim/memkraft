"""CLI smoke regressions for release-critical behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import MemKraft, cli


def test_cli_init_without_path_uses_memkraft_dir(tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["MEMKRAFT_DIR"] = str(tmp_path / "memory-root")

    result = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", "init"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (Path(env["MEMKRAFT_DIR"]) / "entities").is_dir()
    assert (Path(env["MEMKRAFT_DIR"]) / "live-notes").is_dir()
    assert not (tmp_path / "memory").exists()

    doctor = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", "doctor", "--install", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    # This test executes from the source checkout, not an installed wheel.  The
    # install-only doctor must expose that fact instead of claiming health.
    assert doctor.returncode == 1, doctor.stderr + doctor.stdout
    report = json.loads(doctor.stdout)
    assert report["consistent"] is False


def test_doctor_json_without_install_is_rejected_by_parser():
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--json"])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "command, expected",
    [
        (["task-list"], "Tasks (active): 1"),
        (["channel-tasks", "demo"], "Tasks for channel 'demo' (all): 1"),
        (["task-cleanup", "--max-age", "0"], "1 archived, 0 deleted, 0 kept"),
    ],
)
def test_task_commands_write_utf8_output(tmp_path: Path, command, expected):
    memory = MemKraft(base_dir=tmp_path / "memory")
    memory.task_start("demo-task", "Check task output", channel_id="demo")
    if command[0] == "task-cleanup":
        memory.task_update("demo-task", "completed")

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["MEMKRAFT_DIR"] = str(memory.base_dir)
    env["PYTHONIOENCODING"] = "utf-8:strict"
    result = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", *command],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert expected in result.stdout
    if command[0] == "task-cleanup":
        assert not (memory.context_tasks_dir / "demo-task.json").exists()
        archived = memory.context_tasks_dir / "archive" / "demo-task.json"
        assert json.loads(archived.read_text())["status"] == "completed"
    else:
        assert "demo-task: Check task output" in result.stdout
