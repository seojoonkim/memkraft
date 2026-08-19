"""Regression tests for canonical and installed package metadata."""
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ImportError:  # Python 3.9/3.10
    import tomli as tomllib

import memkraft


RELEASE_VERSION = "4.0.0"
RELEASE_DATE = "2026-08-20"


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


def test_readme_current_release_matches_canonical_project_version():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    version = _project_version()
    header = readme.split("<div align=\"center\">", 1)[0]
    changelog_section = readme.split("## 📝 Changelog", 1)[1].split("\n## ", 1)[0]
    displayed_version = re.search(r"^\*\*v([^*]+)\*\*", header, re.MULTILINE)
    current_release = re.search(
        r"^### \[v([^]]+)]\([^\n]+\) \(current\)$",
        changelog_section,
        re.MULTILINE,
    )
    release_headings = re.findall(r"^### (.+)$", changelog_section, re.MULTILINE)

    assert displayed_version is not None
    assert displayed_version.group(1) == version
    assert readme.count("(current)") == 1
    assert current_release is not None
    assert release_headings
    assert current_release.group(0).removeprefix("### ") == release_headings[0]
    assert current_release.group(1) == version
    assert current_release.group(0) == (
        f"### [v{version}]"
        f"(https://github.com/seojoonkim/memkraft/releases/tag/v{version}) (current)"
    )


def test_current_release_has_changelog_and_release_notes():
    root = Path(__file__).resolve().parents[1]
    version = _project_version()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = root / "docs" / "releases" / f"{version}.md"
    latest_changelog = re.search(r"^## \[([^]]+)]", changelog, re.MULTILINE)

    assert latest_changelog is not None
    assert latest_changelog.group(1) == version
    assert release_notes.is_file()
    content_lines = [
        line
        for line in release_notes.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert content_lines
    assert content_lines[0] == f"## MemKraft {version}"


def test_release_ci_runs_for_every_pr_and_main_push():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/gym-gate.yml").read_text(encoding="utf-8")
    pull_request_trigger = workflow.split("  push:", 1)[0]
    push_trigger = workflow.split("  push:", 1)[1].split("\njobs:", 1)[0]

    # Lineage scope is repository-wide, so neither PRs nor main pushes may use
    # path filters that let metadata-only or newly added paths bypass the gate.
    assert "  pull_request:\n" in pull_request_trigger
    assert "    paths:" not in pull_request_trigger
    assert "branches: [main]" in push_trigger
    assert "paths:" not in push_trigger


def test_release_public_evidence_apis_are_documented_as_preview():
    root = Path(__file__).resolve().parents[1]
    api_contract = (root / "docs" / "V3_API.md").read_text(encoding="utf-8")

    assert "compile_evidence_context" in api_contract
    assert "aggregate_numeric_evidence" in api_contract
    assert "fail-closed" in api_contract


def test_release_workflow_uses_absolute_source_path_for_subprocesses():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    checklist = (root / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "PYTHONPATH=src" not in workflow
    assert workflow.count('PYTHONPATH="$GITHUB_WORKSPACE/src"') == 3
    assert "PYTHONPATH=src" not in checklist
    assert checklist.count('PYTHONPATH="$(pwd)/src"') == 3


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
