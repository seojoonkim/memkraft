"""Tests for memkraft.selfupdate (0.8.2)."""
from __future__ import annotations

import json
import sys
from io import BytesIO
from unittest import mock

import pytest

from memkraft import selfupdate as su


# ---------- needs_update / _parse_version ----------

def test_parse_version_basic():
    assert su._parse_version("0.8.2") == (0, 8, 2)
    assert su._parse_version("1.0.0") == (1, 0, 0)


def test_parse_version_with_suffix():
    # PEP 440-ish; we only care about numeric ordering up to first non-digit
    assert su._parse_version("0.8.2rc1") == (0, 8, 2)


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
    with mock.patch.object(su, "installed_version", return_value="0.8.1"), \
         mock.patch.object(su, "latest_version", return_value="0.8.2"), \
         mock.patch.object(su.subprocess, "run", return_value=mock.MagicMock(returncode=0)) as run_mock:
        rc = su.selfupdate(dry_run=False)
    assert rc == 0
    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert "-m" in cmd and "pip" in cmd and "install" in cmd and "-U" in cmd and "memkraft" in cmd


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
