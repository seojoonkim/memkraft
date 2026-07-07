"""Tests for project-level pytest collection defaults."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_pytest_collects_package_tests_by_default():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pytest_ini = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert pytest_ini.get("testpaths") == ["tests"]
