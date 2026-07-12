"""Regression tests for canonical and installed package metadata."""
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ImportError:  # Python 3.9/3.10
    import tomli as tomllib

import memkraft


RELEASE_VERSION = "3.0.0"
RELEASE_DATE = "2026-07-12"


def _project_version() -> str:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def test_build_backend_allows_python_312_compatible_setuptools():
    """Build isolation must be able to select setuptools 68+ on Python 3.12."""
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        build_requires = tomllib.load(stream)["build-system"]["requires"]

    setuptools = next(
        Requirement(requirement)
        for requirement in build_requires
        if Requirement(requirement).name == "setuptools"
    )
    compatible_generation = Version("68")

    assert compatible_generation in setuptools.specifier, (
        f"{setuptools} excludes setuptools 68, whose pkg_resources no longer "
        "uses pkgutil.ImpImporter on Python 3.12"
    )


def test_source_versions_match_canonical_project_metadata():
    """A source checkout must not depend on metadata from the test runner."""
    assert memkraft.__version__ == _project_version()


def test_release_cut_version_and_changelog_metadata():
    root = Path(__file__).resolve().parents[1]
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    assert _project_version() == RELEASE_VERSION
    assert memkraft.__version__ == RELEASE_VERSION
    assert f"## [{RELEASE_VERSION}] — {RELEASE_DATE}" in changelog


def test_wheel_smoke_uses_canonical_project_version_without_release_literal():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/gym-gate.yml").read_text(encoding="utf-8")

    assert RELEASE_VERSION not in workflow
    assert "import tomllib" in workflow
    assert 'tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]' in workflow
    assert 'version("memkraft") == memkraft.__version__ == project_version' in workflow


def test_legacy_editable_shim_does_not_duplicate_metadata():
    root = Path(__file__).resolve().parents[1]
    shim = (root / "setup.py").read_text(encoding="utf-8")

    assert "setup()" in shim
    assert "version=" not in shim
    assert "package_data=" not in shim
    assert "find_packages" not in shim
