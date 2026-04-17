"""Tests for v0.8.3: `memkraft doctor --fix` / --dry-run / --yes."""
from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from memkraft import doctor as _doctor
from memkraft.core import MemKraft


SRC = str(Path(__file__).resolve().parents[1] / "src")


def _cli(*args, cwd=None, env=None, stdin=""):
    e = os.environ.copy()
    e["PYTHONPATH"] = SRC + ":" + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=e,
        input=stdin,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_plan_fixes_on_empty_dir_finds_missing(tmp_path):
    base = tmp_path / "memory"
    base.mkdir()
    mk = MemKraft(base_dir=str(base))
    actions = _doctor.plan_fixes(mk)
    # Should propose creation for entities, live-notes, decisions, inbox etc.
    paths = [a["path"] for a in actions]
    assert any("entities" in p for p in paths)
    assert any("decisions" in p for p in paths)
    assert any("inbox" in p for p in paths)


def test_plan_fixes_on_healthy_workspace_empty(tmp_path):
    base = tmp_path / "memory"
    mk = MemKraft(base_dir=str(base))
    mk.init(verbose=False)
    actions = _doctor.plan_fixes(mk)
    assert actions == [], f"expected no fixes, got {actions}"


def test_doctor_fix_dry_run_creates_nothing(tmp_path):
    base = tmp_path / "memory"
    base.mkdir()
    env = {"MEMKRAFT_DIR": str(base)}
    rc, out, err = _cli("doctor", "--fix", "--dry-run", env=env)
    assert rc == 0, err
    assert "dry-run" in out.lower()
    # no subdirs should exist
    assert not (base / "entities").exists()


def test_doctor_fix_yes_creates_dirs(tmp_path):
    base = tmp_path / "memory"
    base.mkdir()
    env = {"MEMKRAFT_DIR": str(base)}
    rc, out, err = _cli("doctor", "--fix", "--yes", env=env)
    assert rc == 0, err
    assert (base / "entities").is_dir()
    assert (base / "decisions").is_dir()
    assert (base / "inbox").is_dir()


def test_doctor_fix_never_deletes_files(tmp_path):
    base = tmp_path / "memory"
    mk = MemKraft(base_dir=str(base))
    mk.init(verbose=False)
    # Add a user file
    (base / "entities" / "preserve-me.md").write_text("# keep\n")
    env = {"MEMKRAFT_DIR": str(base)}
    rc, out, err = _cli("doctor", "--fix", "--yes", env=env)
    assert rc == 0
    assert (base / "entities" / "preserve-me.md").exists()
