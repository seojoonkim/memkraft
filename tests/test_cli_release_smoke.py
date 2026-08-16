"""CLI smoke regressions for release-critical behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import cli


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
