"""v0.8.1 — doctor CLI."""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import MemKraft
from memkraft import doctor


@pytest.fixture
def tmp_base():
    d = tempfile.mkdtemp(prefix="mk-doctor-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_doctor_healthy_after_init(tmp_base, capsys):
    mk = MemKraft(base_dir=tmp_base)
    mk.init(verbose=False)
    clean_install = {
        "consistent": True,
        "import_path": "/venv/site-packages/memkraft/__init__.py",
        "reasons": [],
    }
    with mock.patch("memkraft.doctor.installation_report", return_value=clean_install):
        report = doctor.run(base_dir=tmp_base)
    out = capsys.readouterr().out
    assert "MemKraft doctor" in out
    assert report["status"] in ("healthy", "degraded")  # degraded if env mismatch
    # core dirs must be present
    assert "all core dirs present" in out


def test_doctor_flags_missing_structure(tmp_base, capsys):
    # Don't run init → structure is missing
    report = doctor.run(base_dir=tmp_base)
    out = capsys.readouterr().out
    assert report["status"] in ("degraded", "unhealthy")
    assert "missing" in out


def test_update_network_error_makes_doctor_unhealthy(tmp_base):
    MemKraft(base_dir=tmp_base).init(verbose=False)
    with mock.patch("memkraft.doctor._check_memkraft", return_value=(doctor._OK, "ok")), \
         mock.patch("memkraft.doctor._check_updates", return_value=(doctor._ERR, "offline")):
        report = doctor.run(base_dir=tmp_base, check_updates=True)
    assert report["status"] == "unhealthy"
