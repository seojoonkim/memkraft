"""Tests for `memkraft doctor --check-updates` (0.8.2)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memkraft import doctor


@pytest.fixture
def tmp_base():
    with tempfile.TemporaryDirectory() as d:
        # minimal init: create base_dir + required subdirs so structure check passes
        bd = Path(d) / "memory"
        for sub in ("entities", "live-notes", "decisions", "inbox"):
            (bd / sub).mkdir(parents=True, exist_ok=True)
        yield str(bd)


def test_doctor_default_no_update_check(tmp_base, capsys):
    """Without --check-updates, doctor should not call PyPI."""
    with mock.patch("memkraft.selfupdate.latest_version") as lv:
        report = doctor.run(base_dir=tmp_base, check_updates=False)
        lv.assert_not_called()
    assert "update_check" not in report
    out = capsys.readouterr().out
    assert "update check:" not in out


def test_doctor_check_updates_up_to_date(tmp_base, capsys):
    with mock.patch("memkraft.selfupdate.installed_version", return_value="0.8.2"), \
         mock.patch("memkraft.selfupdate.latest_version", return_value="0.8.2"):
        report = doctor.run(base_dir=tmp_base, check_updates=True)
    out = capsys.readouterr().out
    assert "update check:" in out
    assert "Up to date" in out
    assert "0.8.2" in out
    assert report["update_check"]["icon"] == "🟢"


def test_doctor_check_updates_newer_available(tmp_base, capsys):
    with mock.patch("memkraft.selfupdate.installed_version", return_value="0.8.1"), \
         mock.patch("memkraft.selfupdate.latest_version", return_value="0.8.2"):
        report = doctor.run(base_dir=tmp_base, check_updates=True)
    out = capsys.readouterr().out
    assert "Update available" in out
    assert "0.8.1" in out and "0.8.2" in out
    assert "memkraft selfupdate" in out
    # status degraded
    assert report["status"] in {"degraded", "unhealthy"}
    assert report["update_check"]["icon"] == "🟡"


def test_doctor_check_updates_offline(tmp_base, capsys):
    with mock.patch("memkraft.selfupdate.latest_version", return_value=None):
        report = doctor.run(base_dir=tmp_base, check_updates=True)
    out = capsys.readouterr().out
    assert "PyPI unreachable" in out
    assert report["update_check"]["icon"] == "🔴"


def test_doctor_cmd_passes_flag(tmp_base):
    """cmd() should forward --check-updates from argparse Namespace."""
    args = mock.MagicMock(base_dir=tmp_base, check_updates=False)
    with mock.patch("memkraft.doctor.run") as run_mock:
        run_mock.return_value = {"status": "healthy"}
        doctor.cmd(args)
        run_mock.assert_called_once_with(base_dir=tmp_base, check_updates=False)
