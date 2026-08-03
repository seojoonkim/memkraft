"""Slice-0 baseline guards for MKEP/0 (plan §2, §19.2).

These tests hold the assumptions the execution kernel is built on. They carry
no production code and must stay green for the whole 3.3.0 line; a failure here
means the design premises moved, not that a feature broke.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from memkraft import context_compiler, derived_views, store_core
from memkraft.derived_views import DerivedViewsMixin

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "usage_id.json"


def test_a1_governance_lock_wraps_lock_current_inode():
    """A1: the governance lock is built on the inode-revalidating store lock."""
    source = inspect.getsource(DerivedViewsMixin._governance_lock)
    assert "_lock_current_inode" in source
    assert derived_views._lock_current_inode is store_core._lock_current_inode


def test_a2_append_audit_suppresses_duplicate_operation_id():
    """A2: audit append is idempotent on ``operation_id``."""
    source = inspect.getsource(DerivedViewsMixin._append_audit)
    assert "operation_id" in source
    assert "return None" in source


def test_a3_usage_id_is_sha256_over_a_fixed_identity_dict():
    """A3 (release-blocking): the identity dict has exactly these six keys."""
    source = inspect.getsource(context_compiler.ContextCompilerMixin.compile_context)
    assert "hashlib.sha256" in source
    assert 'identity = {' in source
    identity_block = source.split("identity = {", 1)[1].split("}", 1)[0]
    for key in ("task", "budget", "objective", "session_id", "sections", "sources"):
        assert '"%s"' % key in identity_block, key
    assert "context_schema" not in identity_block
    assert "goal_id" not in identity_block
    assert "execution_budget" not in identity_block


def test_a4_execution_prefixed_modules_are_owned_by_mkep():
    """A4: no unrelated module has occupied the MKEP namespace.

    Slice 0 verified that the namespace was empty at the baseline.  As later
    slices add the planned modules, this lasting guard rejects only unexpected
    occupants rather than making the first legitimate module fail the suite.
    ``reasoning_execution.py`` is intentionally outside the prefix.
    """
    package = Path(store_core.__file__).parent
    existing = {p.name for p in package.glob("execution*.py")}
    planned = {
        "execution.py",
        "execution_assessment.py",
        "execution_dispatch.py",
        "execution_handoff.py",
        "execution_models.py",
        "execution_projection.py",
        "execution_protocol.py",
        "execution_state.py",
        "execution_store.py",
    }
    assert existing <= planned


def test_release_metadata_sources_agree_and_target_is_above_baseline():
    """G0: pyproject, ``__version__`` and the CHANGELOG heading agree at < 3.3.0."""
    import memkraft

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in pyproject.splitlines()
        if line.startswith("version =")
    )
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = next(
        line.split("[", 1)[1].split("]", 1)[0]
        for line in changelog.splitlines()
        if line.startswith("## [")
    )
    assert declared == memkraft.__version__ == heading
    parts = tuple(int(part) for part in declared.split("."))
    assert parts < (3, 3, 0), "target 3.3.0 must be strictly greater than HEAD"


def _pin_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pin_golden_usage_id", REPO_ROOT / "tools" / "pin_golden_usage_id.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_golden_usage_id_is_deterministic(tmp_path):
    """G10 basis: the pinned scenario reproduces the committed ``usage_id``."""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    from memkraft import MemKraft

    mk = MemKraft(base_dir=str(tmp_path))
    mk.init()
    for event in golden["scenario"]["events"]:
        mk.append_event(**event)
    result = mk.compile_context(
        golden["scenario"]["task"],
        golden["scenario"]["budget"],
        objective=golden["scenario"]["objective"],
    )
    assert result["usage_id"] == golden["usage_id"]
    assert _pin_tool().identity_sections(result["sections"]) == golden["identity_sections"]
    assert result["sources"] == golden["sources"]


def test_golden_usage_id_matches_the_pin_tool():
    """The committed golden is exactly what ``tools/pin_golden_usage_id.py`` emits."""
    recomputed = _pin_tool().compute_golden()
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert recomputed == committed


@pytest.mark.parametrize("name", ["b9453da", "451ed1b"])
def test_baseline_commit_is_an_ancestor(name):
    """The verified implementation baseline is reachable from this branch."""
    import subprocess

    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", name, "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if proc.returncode == 128:
        pytest.skip("commit not present in this checkout")
    assert proc.returncode == 0
