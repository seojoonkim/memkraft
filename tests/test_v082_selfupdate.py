"""Tests for memkraft.selfupdate (0.8.2)."""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from memkraft import selfupdate as su


@pytest.fixture(autouse=True)
def _clean_install_report():
    """Legacy update tests model a single, healthy wheel installation."""
    with mock.patch.object(su, "installation_report", side_effect=lambda: {
        "consistent": True,
        "package_version": su.installed_version(),
        "reasons": [],
    }):
        yield


# ---------- needs_update / _parse_version ----------

def test_parse_version_basic():
    assert su._parse_version("0.8.2") == (0, 8, 2)
    assert su._parse_version("1.0.0") == (1, 0, 0)


@pytest.mark.parametrize("value", ["0.8.2rc1", "1.0", "v1.2.3", "1.2.3+local"])
def test_parse_version_rejects_versions_outside_supported_public_shape(value):
    with pytest.raises(ValueError):
        su._parse_version(value)


def test_needs_update_strictly_newer():
    assert su.needs_update("0.8.1", "0.8.2") is True
    assert su.needs_update("0.8.0", "0.9.0") is True
    assert su.needs_update("0.7.9", "0.8.0") is True


def test_needs_update_equal():
    assert su.needs_update("0.8.2", "0.8.2") is False


def test_needs_update_older_remote():
    # if PyPI somehow reports older, do nothing
    assert su.needs_update("0.8.2", "0.8.1") is False


def test_needs_update_empty_inputs():
    assert su.needs_update("", "0.8.2") is False
    assert su.needs_update("0.8.2", "") is False
    assert su.needs_update("", "") is False


def test_needs_update_rejects_unsupported_version():
    with pytest.raises(ValueError):
        su.needs_update("1.0.0rc1", "1.0.0")


def _release_payload(files=None):
    wheel = {
        "filename": "memkraft-3.5.1-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "python_version": "py3",
        "url": "https://files.pythonhosted.org/x/memkraft-3.5.1-py3-none-any.whl",
        "digests": {"sha256": "a" * 64},
    }
    return {"info": {"version": "3.5.1"}, "urls": [wheel] if files is None else files}


def test_release_artifact_selects_official_universal_wheel():
    with mock.patch.object(su.urllib.request, "urlopen", return_value=_mock_urlopen(_release_payload())):
        artifact = su.release_artifact("3.5.1")
    assert artifact["filename"].endswith("-py3-none-any.whl")
    assert artifact["sha256"] == "a" * 64


@pytest.mark.parametrize("files", [
    [],
    [_release_payload()["urls"][0]] * 2,
    [{**_release_payload()["urls"][0], "url": "https://evil.example/x.whl"}],
    [{**_release_payload()["urls"][0], "digests": {"sha256": "bad"}}],
])
def test_release_artifact_rejects_missing_ambiguous_or_invalid_wheel(files):
    with mock.patch.object(su.urllib.request, "urlopen", return_value=_mock_urlopen(_release_payload(files))):
        with pytest.raises(su.ArtifactError):
            su.release_artifact("3.5.1")


# ---------- latest_version (network mocked) ----------

def _mock_urlopen(payload: dict):
    body = json.dumps(payload).encode()

    class _Resp:
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *a):
            return False
        def read(self_inner):
            return body
    return _Resp()


def test_latest_version_success():
    payload = {"info": {"version": "0.8.2"}}
    body = json.dumps(payload).encode()

    fake = mock.MagicMock()
    fake.__enter__.return_value = BytesIO(body)
    fake.__exit__.return_value = False

    with mock.patch.object(su.urllib.request, "urlopen", return_value=fake):
        assert su.latest_version() == "0.8.2"


def test_latest_version_prefers_highest_published_release_over_stale_info():
    payload = {
        "info": {"version": "3.5.0"},
        "releases": {"3.5.0": [{}], "3.5.1": [{}]},
    }
    with mock.patch.object(su.urllib.request, "urlopen", return_value=_mock_urlopen(payload)):
        assert su.latest_version() == "3.5.1"


def test_latest_version_ignores_empty_or_non_public_release_entries():
    payload = {
        "info": {"version": "3.5.0"},
        "releases": {"3.5.1": [], "3.6.0rc1": [{}], "invalid": [{}]},
    }
    with mock.patch.object(su.urllib.request, "urlopen", return_value=_mock_urlopen(payload)):
        assert su.latest_version() == "3.5.0"


def test_latest_version_offline_returns_none():
    import urllib.error
    with mock.patch.object(
        su.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("offline")
    ):
        assert su.latest_version() is None


def test_latest_version_timeout_returns_none():
    with mock.patch.object(
        su.urllib.request, "urlopen",
        side_effect=TimeoutError("slow")
    ):
        assert su.latest_version() is None


def test_latest_version_bad_json_returns_none():
    fake = mock.MagicMock()
    fake.__enter__.return_value = BytesIO(b"not json{{{")
    fake.__exit__.return_value = False
    with mock.patch.object(su.urllib.request, "urlopen", return_value=fake):
        assert su.latest_version() is None


# ---------- selfupdate end-to-end (mocked) ----------

def test_selfupdate_already_current(capsys):
    with mock.patch.object(su, "installed_version", return_value="0.8.2"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"):
        rc = su.selfupdate(dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Already up to date" in out
    assert "0.8.2" in out


def test_selfupdate_dry_run_when_newer(capsys):
    with mock.patch.object(su, "installed_version", return_value="0.8.1"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"), \
         mock.patch.object(su.subprocess, "run") as run_mock:
        rc = su.selfupdate(dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Update available" in out
    assert "dry-run" in out
    run_mock.assert_not_called()


def test_selfupdate_runs_pip_when_newer(capsys):
    artifact = {"filename": "memkraft-0.8.2-py3-none-any.whl", "url": "https://files.pythonhosted.org/x.whl", "sha256": "a" * 64}

    @contextmanager
    def downloaded(_artifact):
        yield "/tmp/verified.whl"

    with mock.patch.object(su, "installed_version", return_value="0.8.1"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"), \
         mock.patch.object(su, "release_artifact", return_value=artifact), \
         mock.patch.object(su, "download_verified_wheel", side_effect=downloaded), \
         mock.patch.object(su.subprocess, "run", return_value=mock.MagicMock(returncode=0)) as run_mock:
        rc = su.selfupdate(dry_run=False)
    assert rc == 0
    assert run_mock.call_count == 2
    cmd = run_mock.call_args_list[0].args[0]
    assert cmd == [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", "/tmp/verified.whl"]


def test_download_verified_wheel_preserves_official_wheel_filename():
    payload = b"verified wheel bytes"
    artifact = {
        "filename": "memkraft-3.5.1-py3-none-any.whl",
        "url": "https://files.pythonhosted.org/x/memkraft-3.5.1-py3-none-any.whl",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    response = mock.MagicMock()
    response.__enter__.return_value = BytesIO(payload)
    response.__exit__.return_value = False

    with mock.patch.object(su.urllib.request, "urlopen", return_value=response):
        with su.download_verified_wheel(artifact) as wheel:
            wheel_path = Path(wheel)
            directory = wheel_path.parent
            assert wheel_path.name == artifact["filename"]
            assert wheel_path.read_bytes() == payload

    assert not directory.exists()


@pytest.mark.parametrize("filename", [
    "", "../memkraft.whl", "nested/memkraft.whl", "memkraft.zip",
])
def test_download_verified_wheel_rejects_unsafe_or_non_wheel_filename(filename):
    artifact = {
        "filename": filename,
        "url": "https://files.pythonhosted.org/x/memkraft.whl",
        "sha256": "a" * 64,
    }
    with pytest.raises(su.ArtifactError, match="safe canonical filename"):
        with su.download_verified_wheel(artifact):
            pass


def test_selfupdate_pip_failure_propagates_returncode():
    with mock.patch.object(su, "installed_version", return_value="0.8.1"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"), \
         mock.patch.object(su.subprocess, "run", return_value=mock.MagicMock(returncode=1)):
        rc = su.selfupdate(dry_run=False)
    assert rc == 1


def test_selfupdate_pypi_unreachable():
    with mock.patch.object(su, "installed_version", return_value="0.8.2"), \
         mock.patch.object(su, "latest_version", return_value=None):
        rc = su.selfupdate(dry_run=False)
    assert rc == 1


def test_selfupdate_not_installed():
    with mock.patch.object(su, "installed_version", return_value=None):
        rc = su.selfupdate(dry_run=False)
    assert rc == 1


# ---------- cmd entry point (CLI shim) ----------

def test_cmd_dispatches_dry_run():
    args = mock.MagicMock(dry_run=True)
    with mock.patch.object(su, "installed_version", return_value="0.8.2"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"):
        rc = su.cmd(args)
    assert rc == 0
