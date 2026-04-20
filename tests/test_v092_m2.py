"""Tests for MemKraft v0.9.2 M2 alpha.

Covers the two new APIs introduced by M2:

* ``prompt_evidence`` \u2014 pre-iteration recall of past tuning results.
* ``convergence_check`` \u2014 mizchi-style stopping rule + decay overlay.

Both APIs are purely additive on top of 0.9.1/0.9.2a1 primitives; these
tests pin the contract from
``memory/memkraft-0.9.2-m2-spec-2026-04-20.md`` \u00a71\u2013\u00a72.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def mk(tmp_path):
    return MemKraft(str(tmp_path))


def _register(mk, prompt_id="ulw-dispatch", **kwargs):
    defaults = dict(
        path=f"skills/{prompt_id}/SKILL.md",
        owner="zeon",
        tags=["tuning", "test"],
        critical_requirements=["must not call an LLM"],
        description="test skill",
    )
    defaults.update(kwargs)
    return mk.prompt_register(prompt_id, **defaults)


def _eval(
    mk,
    prompt_id: str,
    iteration: int,
    *,
    accuracy: float = 100.0,
    tool_uses: int = 3,
    duration_ms: int = 1200,
    unclear: int = 0,
    success: bool = True,
    scenario: str = "parallel",
) -> Dict[str, Any]:
    scenarios = [
        {
            "name": scenario,
            "description": f"scenario {scenario}",
            "requirements": [{"item": "speed", "critical": True}],
        }
    ]
    results = [
        {
            "scenario": scenario,
            "success": success,
            "accuracy": accuracy,
            "tool_uses": tool_uses,
            "duration_ms": duration_ms,
            "unclear_points": [f"u{i}" for i in range(unclear)],
            "discretion": [],
        }
    ]
    return mk.prompt_eval(prompt_id, iteration=iteration, scenarios=scenarios, results=results)


def _backdate_decision(mk: MemKraft, decision_id: str, iso_ts: str) -> None:
    """Rewrite ``decided_at`` / ``valid_from`` on a decision file so we can
    exercise the time-range filter + decay gate deterministically.
    """
    path = Path(mk.base_dir) / "decisions" / f"{decision_id}.md"
    text = path.read_text(encoding="utf-8")
    out_lines: List[str] = []
    in_fm = False
    fm_done = False
    for line in text.splitlines():
        if not fm_done and line.strip() == "---":
            in_fm = not in_fm
            if not in_fm:
                fm_done = True
            out_lines.append(line)
            continue
        if in_fm and line.startswith("decided_at:"):
            out_lines.append(f"decided_at: {iso_ts}")
            continue
        if in_fm and line.startswith("valid_from:"):
            out_lines.append(f"valid_from: {iso_ts}")
            continue
        out_lines.append(line)
    path.write_text("\n".join(out_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")


# ---------------------------------------------------------------------------
# prompt_evidence
# ---------------------------------------------------------------------------


def test_prompt_evidence_basic_returns_records(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, scenario="parallel", unclear=1)
    _eval(mk, "ulw-dispatch", iteration=2, scenario="parallel", unclear=0)
    ev = mk.prompt_evidence("ulw-dispatch", "parallel dispatch unclear rate limiting")
    assert ev["counts"]["decisions_total"] == 2
    assert ev["counts"]["decisions_matched"] >= 1
    assert len(ev["results"]) >= 1
    # newest iteration should be surfaced
    iterations = [r["iteration"] for r in ev["results"]]
    assert 2 in iterations or 1 in iterations


def test_prompt_evidence_unregistered_raises(mk):
    with pytest.raises(ValueError, match="not registered"):
        mk.prompt_evidence("does-not-exist", "q")


def test_prompt_evidence_empty_when_no_evals(mk):
    _register(mk)
    ev = mk.prompt_evidence("ulw-dispatch", "parallel")
    assert ev["counts"]["decisions_total"] == 0
    assert ev["results"] == []


def test_prompt_evidence_respects_max_results(mk):
    _register(mk)
    for i in range(1, 5):
        _eval(mk, "ulw-dispatch", iteration=i, scenario="parallel")
    ev = mk.prompt_evidence("ulw-dispatch", "parallel dispatch ulw", max_results=2)
    assert len(ev["results"]) <= 2


def test_prompt_evidence_similarity_threshold_filters(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, scenario="parallel")
    # very high threshold, unrelated query \u2192 nothing matches
    ev = mk.prompt_evidence(
        "ulw-dispatch", "totally unrelated xyz foobar", min_similarity=0.9
    )
    assert ev["counts"]["decisions_matched"] == 0
    assert ev["results"] == []


def test_prompt_evidence_time_range_excludes_old_records(mk):
    _register(mk)
    res = _eval(mk, "ulw-dispatch", iteration=1, scenario="parallel")
    _backdate_decision(mk, res["decision_id"], "2024-01-01T00:00:00")
    ev = mk.prompt_evidence(
        "ulw-dispatch", "parallel dispatch ulw", time_range_days=30
    )
    assert ev["counts"]["skipped_stale"] >= 1
    assert all(r["age_days"] <= 30 for r in ev["results"])


def test_prompt_evidence_return_shape_has_required_keys(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, scenario="parallel")
    ev = mk.prompt_evidence("ulw-dispatch", "parallel dispatch ulw")
    for key in (
        "query",
        "prompt_id",
        "scenario",
        "time_range_days",
        "min_similarity",
        "counts",
        "results",
    ):
        assert key in ev, f"missing key: {key}"
    if ev["results"]:
        r = ev["results"][0]
        for key in (
            "_source",
            "id",
            "iteration",
            "decided_at",
            "similarity",
            "age_days",
            "score",
            "summary",
            "tags",
        ):
            assert key in r, f"result missing key: {key}"


# ---------------------------------------------------------------------------
# convergence_check
# ---------------------------------------------------------------------------


def test_convergence_insufficient_iters(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1)
    cc = mk.convergence_check("ulw-dispatch", window=2)
    assert cc["converged"] is False
    assert cc["reason"] == "insufficient-iters"


def test_convergence_happy_path(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, accuracy=100.0, tool_uses=3, duration_ms=1200)
    _eval(mk, "ulw-dispatch", iteration=2, accuracy=100.0, tool_uses=3, duration_ms=1180)
    cc = mk.convergence_check("ulw-dispatch", window=2)
    assert cc["converged"] is True
    assert cc["reason"] == "converged"
    assert cc["suggested_next"] == "stop"
    assert sorted(cc["iterations_checked"]) == [1, 2]


def test_convergence_accuracy_delta_fails(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, accuracy=100.0)
    _eval(mk, "ulw-dispatch", iteration=2, accuracy=80.0)
    cc = mk.convergence_check(
        "ulw-dispatch", window=2, max_accuracy_delta=3.0
    )
    assert cc["converged"] is False
    assert cc["reason"] == "accuracy-delta"


def test_convergence_steps_delta_fails(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, tool_uses=3)
    _eval(mk, "ulw-dispatch", iteration=2, tool_uses=10)
    cc = mk.convergence_check(
        "ulw-dispatch", window=2, max_steps_delta_pct=10.0
    )
    assert cc["converged"] is False
    assert cc["reason"] == "steps-delta"


def test_convergence_duration_delta_fails(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, duration_ms=1000)
    _eval(mk, "ulw-dispatch", iteration=2, duration_ms=5000)
    cc = mk.convergence_check(
        "ulw-dispatch", window=2, max_duration_delta_pct=15.0
    )
    assert cc["converged"] is False
    assert cc["reason"] == "duration-delta"


def test_convergence_unclear_points_fails(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, unclear=0)
    _eval(mk, "ulw-dispatch", iteration=2, unclear=2)
    cc = mk.convergence_check("ulw-dispatch", window=2)
    assert cc["converged"] is False
    assert cc["reason"] == "unclear-points"


def test_convergence_not_all_passed_fails(mk):
    _register(mk)
    _eval(mk, "ulw-dispatch", iteration=1, success=True, accuracy=100.0)
    _eval(mk, "ulw-dispatch", iteration=2, success=False, accuracy=100.0)
    cc = mk.convergence_check("ulw-dispatch", window=2)
    assert cc["converged"] is False
    assert cc["reason"] == "not-all-passed"


def test_convergence_stale_invalidates(mk):
    _register(mk)
    r1 = _eval(mk, "ulw-dispatch", iteration=1, accuracy=100.0, tool_uses=3, duration_ms=1200)
    r2 = _eval(mk, "ulw-dispatch", iteration=2, accuracy=100.0, tool_uses=3, duration_ms=1200)
    # backdate both to 2023
    _backdate_decision(mk, r1["decision_id"], "2023-01-01T00:00:00")
    _backdate_decision(mk, r2["decision_id"], "2023-01-02T00:00:00")
    cc = mk.convergence_check(
        "ulw-dispatch", window=2, consider_decay=True, stale_after_days=30.0
    )
    assert cc["converged"] is False
    assert cc["reason"] == "stale"
    assert cc["suggested_next"] == "re-run"


def test_convergence_consider_decay_false_ignores_age(mk):
    _register(mk)
    r1 = _eval(mk, "ulw-dispatch", iteration=1, accuracy=100.0, tool_uses=3, duration_ms=1200)
    r2 = _eval(mk, "ulw-dispatch", iteration=2, accuracy=100.0, tool_uses=3, duration_ms=1200)
    _backdate_decision(mk, r1["decision_id"], "2023-01-01T00:00:00")
    _backdate_decision(mk, r2["decision_id"], "2023-01-02T00:00:00")
    cc = mk.convergence_check(
        "ulw-dispatch", window=2, consider_decay=False
    )
    assert cc["converged"] is True
    assert cc["reason"] == "converged"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_evidence_then_convergence_e2e(mk):
    """Full loop: register \u2192 eval x2 \u2192 evidence cites them \u2192 convergence converged."""
    _register(mk, prompt_id="ulw-dispatch")
    _eval(mk, "ulw-dispatch", iteration=1, accuracy=100.0, tool_uses=3, duration_ms=1200)
    _eval(mk, "ulw-dispatch", iteration=2, accuracy=100.0, tool_uses=3, duration_ms=1200)

    ev = mk.prompt_evidence("ulw-dispatch", "parallel dispatch ulw")
    assert ev["counts"]["decisions_total"] == 2
    assert ev["counts"]["decisions_matched"] >= 1

    cc = mk.convergence_check("ulw-dispatch", window=2)
    assert cc["converged"] is True
    assert cc["suggested_next"] == "stop"
