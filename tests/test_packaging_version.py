"""Regression tests for canonical and installed package metadata."""
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.9/3.10
    import tomli as tomllib

import memkraft


def _project_version() -> str:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def test_source_versions_match_canonical_project_metadata():
    """A source checkout must not depend on metadata from the test runner."""
    assert memkraft.__version__ == _project_version()


def test_legacy_editable_shim_does_not_duplicate_metadata():
    root = Path(__file__).resolve().parents[1]
    shim = (root / "setup.py").read_text(encoding="utf-8")

    assert "setup()" in shim
    assert "version=" not in shim
    assert "package_data=" not in shim
    assert "find_packages" not in shim
