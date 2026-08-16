from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from memkraft import doctor, install_integrity, selfupdate


def _probe(**overrides):
    data = {
        "package_version": "3.5.1",
        "import_path": "/venv/lib/python/site-packages/memkraft/__init__.py",
        "distribution_version": "3.5.1",
        "distribution_location": "/venv/lib/python/site-packages",
        "distribution_count": 1,
        "editable_redirects": [],
        "public_version": None,
        "public_check_requested": False,
        "errors": [],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "probe,reason",
    [
        (_probe(package_version="3.1.0", distribution_version="2.9.0"), "version_mismatch"),
        (_probe(distribution_version=None, distribution_count=0), "missing_distribution"),
        (_probe(distribution_count=2), "duplicate_distributions"),
        (_probe(import_path="/elsewhere/memkraft/__init__.py"), "import_outside_distribution"),
        (_probe(import_path="/private/tmp/memkraft/src/memkraft/__init__.py"), "ephemeral_import"),
        (_probe(editable_redirects=[{"artifact": "/site/__editable__.memkraft-3.5.0.pth", "target": "/private/tmp/memkraft/src"}]), "editable_redirect"),
        (_probe(public_version="3.5.2", public_check_requested=True), "public_version_drift"),
        (_probe(errors=["metadata: broken"]), "collection_failure"),
    ],
)
def test_installation_report_fails_closed_for_each_split_state(probe, reason):
    report = install_integrity.evaluate_probe(probe)
    assert report["consistent"] is False
    assert reason in report["reasons"]


def test_clean_wheel_is_consistent():
    report = install_integrity.evaluate_probe(_probe())
    assert report["consistent"] is True
    assert report["reasons"] == []


def test_offline_public_check_is_fail_closed():
    report = install_integrity.evaluate_probe(
        _probe(public_check_requested=True, errors=["pypi: offline"])
    )
    assert not report["consistent"]
    assert "collection_failure" in report["reasons"]


def test_probe_finds_editable_memkraft_redirect_in_site_dir(tmp_path, monkeypatch):
    artifact = tmp_path / "__editable__.memkraft-3.5.0.pth"
    artifact.write_text("/private/tmp/memkraft-main-closure/src\n", encoding="utf-8")
    monkeypatch.setattr(install_integrity.site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(install_integrity.site, "getusersitepackages", lambda: str(tmp_path / "user"))
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    monkeypatch.setattr(install_integrity.metadata, "distributions", lambda **kwargs: [])
    probe = install_integrity.collect_probe(package_version="3.5.0", import_path="/private/tmp/memkraft-main-closure/src/memkraft/__init__.py")
    assert probe["editable_redirects"] == [
        {"artifact": str(artifact), "kind": "pep660-pth", "target": "/private/tmp/memkraft-main-closure/src"}
    ]


def test_site_dirs_only_include_active_sys_path_entries(tmp_path, monkeypatch):
    active = tmp_path / "site-packages"
    inactive = tmp_path / "inactive" / "site-packages"
    active.mkdir(parents=True)
    inactive.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(active), str(tmp_path / "src")])
    monkeypatch.setattr(install_integrity.site, "getsitepackages", lambda: [str(active), str(inactive)])
    assert install_integrity._site_dirs() == [active.resolve()]


def test_editable_scan_rejects_symlink_before_reading(tmp_path, monkeypatch):
    target = tmp_path / "payload"
    target.write_text("/secret/source\n", encoding="utf-8")
    (tmp_path / "__editable__.memkraft-3.5.1.pth").symlink_to(target)
    monkeypatch.setattr(install_integrity, "_site_dirs", lambda: [tmp_path])
    errors = []
    assert install_integrity._editable_redirects(errors) == []
    assert any("non-regular" in error for error in errors)


def test_pep660_import_hook_detected_without_execution(tmp_path, monkeypatch):
    artifact = tmp_path / "__editable__.memkraft-3.5.1.pth"
    artifact.write_text("import __editable___memkraft_finder; __editable___memkraft_finder.install()\n", encoding="utf-8")
    monkeypatch.setattr(install_integrity, "_site_dirs", lambda: [tmp_path])
    errors = []
    assert install_integrity._editable_redirects(errors) == [
        {"artifact": str(artifact), "kind": "pep660-import-hook", "target": ""}
    ]


def test_duplicate_distribution_objects_same_metadata_are_deduplicated(tmp_path, monkeypatch):
    dist_info = tmp_path / "memkraft-3.5.1.dist-info"
    dist_info.mkdir()

    class Dist:
        _path = dist_info
        metadata = {"Name": "memkraft"}
        version = "3.5.1"

        def locate_file(self, value):
            return tmp_path

    monkeypatch.setattr(install_integrity, "_site_dirs", lambda: [tmp_path])
    monkeypatch.setattr(install_integrity.metadata, "distributions", lambda **kwargs: [Dist(), Dist()])
    probe = install_integrity.collect_probe(
        package_version="3.5.1", import_path=str(tmp_path / "memkraft/__init__.py")
    )
    assert probe["distribution_count"] == 1
    assert probe["distribution_provenance"] == [str(dist_info.resolve())]


def test_doctor_install_json_returns_nonzero_for_drift(capsys):
    split = install_integrity.evaluate_probe(_probe(distribution_count=2))
    args = argparse.Namespace(install=True, json=True, check_updates=False, fix=False)
    with mock.patch("memkraft.doctor.installation_report", return_value=split):
        assert doctor.cmd(args) == 1
    assert json.loads(capsys.readouterr().out)["consistent"] is False


def test_selfupdate_split_install_never_says_already_up_to_date(capsys):
    split = install_integrity.evaluate_probe(_probe(distribution_count=2))
    with mock.patch("memkraft.selfupdate.installation_report", return_value=split), mock.patch(
        "memkraft.selfupdate.latest_version", return_value="3.5.1"
    ):
        assert selfupdate.selfupdate() == 1
    assert "Already up to date" not in capsys.readouterr().out


def test_convergence_dry_run_does_not_mutate(capsys):
    split = install_integrity.evaluate_probe(_probe(distribution_count=2))
    with mock.patch("memkraft.selfupdate.installation_report", return_value=split), mock.patch(
        "memkraft.selfupdate.latest_version", return_value="3.5.1"
    ), mock.patch("memkraft.selfupdate.subprocess.run") as run:
        assert selfupdate.selfupdate(dry_run=True, converge=True, yes=True) == 0
        run.assert_not_called()
    assert "memkraft==3.5.1" in capsys.readouterr().out


def test_convergence_verifies_post_repair_and_fails_if_still_split():
    split = install_integrity.evaluate_probe(_probe(distribution_count=2))
    install_result = mock.Mock(returncode=0)
    verify_result = mock.Mock(returncode=1, stdout=json.dumps(split), stderr="")
    artifact = {"filename": "memkraft-3.5.1-py3-none-any.whl", "url": "https://files.pythonhosted.org/x.whl", "sha256": "a" * 64}

    @contextmanager
    def downloaded(_artifact):
        yield "/tmp/verified.whl"

    with mock.patch("memkraft.selfupdate.installation_report", return_value=split), mock.patch(
        "memkraft.selfupdate.latest_version", return_value="3.5.1"
    ), mock.patch("memkraft.selfupdate.release_artifact", return_value=artifact), mock.patch(
        "memkraft.selfupdate.download_verified_wheel", side_effect=downloaded
    ), mock.patch("memkraft.selfupdate.subprocess.run", side_effect=[install_result, verify_result]) as run:
        assert selfupdate.selfupdate(converge=True, yes=True) == 1
    command = run.call_args_list[0].args[0]
    assert command[:4] == [selfupdate.sys.executable, "-m", "pip", "install"]
    assert "--force-reinstall" in command and "--no-deps" in command
    assert command[-1] == "/tmp/verified.whl"


def test_plain_update_refuses_split_even_when_newer_release_exists():
    split = install_integrity.evaluate_probe(_probe(package_version="3.5.0", distribution_version="3.5.0", distribution_count=2))
    with mock.patch("memkraft.selfupdate.installation_report", return_value=split), mock.patch(
        "memkraft.selfupdate.latest_version", return_value="3.5.1"
    ), mock.patch("memkraft.selfupdate.subprocess.run") as run:
        assert selfupdate.selfupdate() == 1
    run.assert_not_called()


def test_quarantine_moves_only_owned_active_editable_and_restores(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    artifact = site_dir / "__editable__.memkraft-3.5.0.pth"
    artifact.write_text("/private/tmp/memkraft/src\n", encoding="utf-8")
    report = _probe(editable_redirects=[{
        "artifact": str(artifact), "kind": "pep660-pth", "target": "/private/tmp/memkraft/src",
    }])
    monkeypatch.setattr(selfupdate, "_site_dirs", lambda: [site_dir])

    backup_dir, moved = selfupdate._quarantine_editable_artifacts(report)
    assert not artifact.exists()
    assert moved[0][1].is_file()
    selfupdate._restore_quarantine(backup_dir, moved)
    assert artifact.read_text(encoding="utf-8") == "/private/tmp/memkraft/src\n"
    assert not Path(backup_dir).exists()


def test_quarantine_rejects_artifact_outside_active_site(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    artifact = tmp_path / "__editable__.memkraft-3.5.0.pth"
    artifact.write_text("/private/tmp/memkraft/src\n", encoding="utf-8")
    monkeypatch.setattr(selfupdate, "_site_dirs", lambda: [site_dir])
    with pytest.raises(selfupdate.ArtifactError, match="outside an active site"):
        selfupdate._quarantine_editable_artifacts(_probe(editable_redirects=[{
            "artifact": str(artifact), "kind": "pep660-pth", "target": "",
        }]))
    assert artifact.exists()


def test_convergence_restores_quarantined_editable_when_verification_fails(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    editable = site_dir / "__editable__.memkraft-3.5.0.pth"
    editable.write_text("/private/tmp/memkraft/src\n", encoding="utf-8")
    split = install_integrity.evaluate_probe(_probe(editable_redirects=[{
        "artifact": str(editable), "kind": "pep660-pth", "target": "/private/tmp/memkraft/src",
    }]))
    artifact = {
        "filename": "memkraft-3.5.1-py3-none-any.whl",
        "url": "https://files.pythonhosted.org/x.whl",
        "sha256": "a" * 64,
    }

    @contextmanager
    def downloaded(_artifact):
        yield "/tmp/verified.whl"

    monkeypatch.setattr(selfupdate, "_site_dirs", lambda: [site_dir])
    with mock.patch("memkraft.selfupdate.installation_report", return_value=split), mock.patch(
        "memkraft.selfupdate.latest_version", return_value="3.5.1"
    ), mock.patch("memkraft.selfupdate.release_artifact", return_value=artifact), mock.patch(
        "memkraft.selfupdate.download_verified_wheel", side_effect=downloaded
    ), mock.patch("memkraft.selfupdate.subprocess.run", side_effect=[
        mock.Mock(returncode=0), mock.Mock(returncode=1, stdout="still split", stderr="")
    ]):
        assert selfupdate.selfupdate(converge=True, yes=True) == 1
    assert editable.read_text(encoding="utf-8") == "/private/tmp/memkraft/src\n"
