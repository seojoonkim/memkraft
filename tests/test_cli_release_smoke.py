"""CLI smoke regressions for release-critical behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
        [sys.executable, "-m", "memkraft.cli", "doctor"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert doctor.returncode == 0, doctor.stderr + doctor.stdout
    assert "overall: healthy" in doctor.stdout
