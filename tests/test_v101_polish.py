"""Tests for MemKraft v1.0.1 polish fixes.

Covers the E2E-surfaced bugs fixed in 1.0.1:

- ``prompt_register(validate_path=True)`` raises on missing file
- ``prompt_register`` warns (but still succeeds) on missing file by default
- ``prompt_eval`` rejects empty scenarios+results
- ``prompt_eval`` warns when a result references an undeclared scenario
- ``convergence_check`` surfaces actually-found iterations in the
  ``insufficient-iters`` response instead of an empty list
"""

from __future__ import annotations

import warnings

import pytest

from memkraft import MemKraft


@pytest.fixture()
def mk(tmp_path):
    return MemKraft(str(tmp_path))


def _skill_file(tmp_path, name="real-skill.md"):
    p = tmp_path / name
    p.write_text("# real skill\n")
    return p


# ---------------------------------------------------------------------------
# prompt_register path validation (1.0.1)
# ---------------------------------------------------------------------------


def test_prompt_register_validate_path_raises_on_missing(mk):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        mk.prompt_register(
            "ghost",
            path="/nonexistent/path/skill.md",
            owner="zeon",
            validate_path=True,
        )


def test_prompt_register_warns_on_missing_path_by_default(mk):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        info = mk.prompt_register(
            "ghost",
            path="/nonexistent/path/skill.md",
            owner="zeon",
        )
    assert info["prompt_id"] == "ghost"
    missing_warnings = [
        w for w in caught if "does not exist" in str(w.message)
    ]
    assert missing_warnings, "expected a UserWarning about missing path"


def test_prompt_register_no_warning_when_path_exists(mk, tmp_path):
    real = _skill_file(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk.prompt_register("real", path=str(real), owner="zeon")
    missing_warnings = [
        w for w in caught if "does not exist" in str(w.message)
    ]
    assert not missing_warnings


def test_prompt_register_validate_path_accepts_existing(mk, tmp_path):
    real = _skill_file(tmp_path)
    info = mk.prompt_register(
        "real",
        path=str(real),
        owner="zeon",
        validate_path=True,
    )
    assert info["prompt_id"] == "real"


# ---------------------------------------------------------------------------
# prompt_eval guardrails (1.0.1)
# ---------------------------------------------------------------------------


def test_prompt_eval_rejects_empty_scenarios_and_results(mk, tmp_path):
    real = _skill_file(tmp_path)
    mk.prompt_register("empty-test", path=str(real), owner="zeon")
    with pytest.raises(ValueError, match="at least one scenario or result"):
        mk.prompt_eval("empty-test", iteration=1, scenarios=[], results=[])


def test_prompt_eval_warns_on_undeclared_scenario_name(mk, tmp_path):
    real = _skill_file(tmp_path)
    mk.prompt_register("mismatch-test", path=str(real), owner="zeon")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk.prompt_eval(
            "mismatch-test",
            iteration=1,
            scenarios=[
                {
                    "name": "a",
                    "description": "x",
                    "requirements": [{"item": "x", "critical": True}],
                }
            ],
            results=[
                {
                    "scenario": "b",  # undeclared
                    "success": True,
                    "accuracy": 90,
                    "tool_uses": 3,
                    "duration_ms": 1000,
                    "unclear_points": [],
                    "discretion": [],
                }
            ],
        )
    mismatches = [
        w for w in caught if "undeclared scenarios" in str(w.message)
    ]
    assert mismatches, "expected UserWarning about undeclared scenario name"


def test_prompt_eval_no_warning_when_names_match(mk, tmp_path):
    real = _skill_file(tmp_path)
    mk.prompt_register("match-test", path=str(real), owner="zeon")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk.prompt_eval(
            "match-test",
            iteration=1,
            scenarios=[
                {
                    "name": "a",
                    "description": "x",
                    "requirements": [{"item": "x", "critical": True}],
                }
            ],
            results=[
                {
                    "scenario": "a",
                    "success": True,
                    "accuracy": 90,
                    "tool_uses": 3,
                    "duration_ms": 1000,
                    "unclear_points": [],
                    "discretion": [],
                }
            ],
        )
    mismatches = [
        w for w in caught if "undeclared scenarios" in str(w.message)
    ]
    assert not mismatches


# ---------------------------------------------------------------------------
# convergence_check insufficient-iters surfaces found iters (1.0.1)
# ---------------------------------------------------------------------------


def test_convergence_insufficient_iters_reports_found_iterations(mk, tmp_path):
    real = _skill_file(tmp_path)
    mk.prompt_register("conv-test", path=str(real), owner="zeon")
    mk.prompt_eval(
        "conv-test",
        iteration=1,
        scenarios=[
            {
                "name": "a",
                "description": "x",
                "requirements": [{"item": "x", "critical": True}],
            }
        ],
        results=[
            {
                "scenario": "a",
                "success": True,
                "accuracy": 90,
                "tool_uses": 3,
                "duration_ms": 1000,
                "unclear_points": [],
                "discretion": [],
            }
        ],
    )
    cc = mk.convergence_check("conv-test", window=3)  # only 1 iter recorded
    assert cc["converged"] is False
    assert cc["reason"] == "insufficient-iters"
    assert cc["iterations_checked"] == [1], (
        "1.0.1 must surface the found iteration even below window size"
    )


def test_convergence_insufficient_iters_zero_runs_suggests_first_iteration(
    mk, tmp_path
):
    real = _skill_file(tmp_path)
    mk.prompt_register("conv-empty", path=str(real), owner="zeon")
    cc = mk.convergence_check("conv-empty")
    assert cc["converged"] is False
    assert cc["reason"] == "insufficient-iters"
    assert cc["iterations_checked"] == []
    assert cc["suggested_next"] == "first-iteration"


def test_convergence_insufficient_iters_with_partial_window_keeps_patch_hint(
    mk, tmp_path
):
    real = _skill_file(tmp_path)
    mk.prompt_register("conv-partial", path=str(real), owner="zeon")
    mk.prompt_eval(
        "conv-partial",
        iteration=1,
        scenarios=[
            {
                "name": "a",
                "description": "x",
                "requirements": [{"item": "x", "critical": True}],
            }
        ],
        results=[
            {
                "scenario": "a",
                "success": True,
                "accuracy": 90,
                "tool_uses": 3,
                "duration_ms": 1000,
                "unclear_points": [],
                "discretion": [],
            }
        ],
    )
    cc = mk.convergence_check("conv-partial", window=2)  # one iter, need two
    assert cc["suggested_next"] == "patch-and-iterate"
    assert cc["iterations_checked"] == [1]
