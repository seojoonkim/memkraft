"""Tests for MemKraft v0.9.2 M1 alpha — ``prompt_register`` + ``prompt_eval``.

The prompt-tune layer is purely additive: it composes ``track`` /
``decision_record`` / ``incident_record`` / ``tier_set`` / ``link_scan``
without touching ``core.py``. These tests pin the API contract described
in ``memory/memkraft-1.0-design-proposal-2026-04-20.md`` §4.2–§4.3.
"""

from __future__ import annotations

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mk(tmp_path):
    return MemKraft(str(tmp_path))


def _register_default(mk, prompt_id="ulw-dispatch", **kwargs):
    defaults = dict(
        path=f"skills/{prompt_id}/SKILL.md",
        owner="zeon",
        tags=["tuning", "test"],
        critical_requirements=["Must stay additive", "Must not call an LLM"],
        description="test skill",
    )
    defaults.update(kwargs)
    return mk.prompt_register(prompt_id, **defaults)


def _one_scenario(name="parallel", success=True, unclear=0, accuracy=100.0):
    return (
        [
            {
                "name": name,
                "description": f"scenario {name}",
                "requirements": [{"item": "speed", "critical": True}],
            }
        ],
        [
            {
                "scenario": name,
                "success": success,
                "accuracy": accuracy,
                "tool_uses": 3,
                "duration_ms": 1200,
                "unclear_points": [f"u{i}" for i in range(unclear)],
                "discretion": [],
            }
        ],
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_prompt_register_basic_returns_metadata(mk):
    info = _register_default(mk)
    assert info["prompt_id"] == "ulw-dispatch"
    assert info["path"].endswith("ulw-dispatch/SKILL.md")
    assert info["owner"] == "zeon"
    assert info["tier"] == "recall"
    assert "tuning" in info["tags"]


def test_prompt_register_creates_live_note_file(mk, tmp_path):
    info = _register_default(mk)
    entity_path = tmp_path / "live-notes" / "ulw-dispatch.md"
    assert entity_path.exists()
    body = entity_path.read_text(encoding="utf-8")
    assert "## Prompt Metadata" in body
    assert "`ulw-dispatch`" in body
    assert "Must stay additive" in body  # critical requirement rendered


def test_prompt_register_duplicate_raises(mk):
    _register_default(mk)
    with pytest.raises(ValueError, match="already registered"):
        _register_default(mk)


def test_prompt_register_empty_id_raises(mk):
    with pytest.raises(ValueError):
        mk.prompt_register("", path="skills/x/SKILL.md")


def test_prompt_register_empty_path_raises(mk):
    with pytest.raises(ValueError):
        mk.prompt_register("x", path="")


def test_prompt_register_normalises_slashed_id(mk, tmp_path):
    info = mk.prompt_register(
        "skills/prompt-guard",
        path="skills/prompt-guard/SKILL.md",
        owner="zeon",
    )
    # slashes must collapse to slug-safe dashes
    assert "/" not in info["slug"]
    entity_file = tmp_path / "live-notes" / f"{info['slug']}.md"
    assert entity_file.exists()


def test_prompt_register_critical_requirements_are_persisted(mk, tmp_path):
    _register_default(mk, critical_requirements=["alpha", "beta"])
    body = (tmp_path / "live-notes" / "ulw-dispatch.md").read_text(encoding="utf-8")
    assert "[critical] alpha" in body
    assert "[critical] beta" in body


def test_prompt_register_applies_recall_tier(mk, tmp_path):
    _register_default(mk)
    body = (tmp_path / "live-notes" / "ulw-dispatch.md").read_text(encoding="utf-8")
    # tiers mixin writes a ``Tier: recall`` marker into the live-note
    # frontmatter/body. Either representation is acceptable — we just
    # ensure the default wasn't left at ``core``.
    assert "recall" in body.lower()


def test_prompt_register_triggers_link_scan(mk, tmp_path):
    # The live-note body contains no wiki-links by default, but the scan
    # must run without raising — this also creates the graph index file.
    _register_default(mk)
    # Any link-graph artefact is fine; we simply want the call to succeed.
    assert (tmp_path / "live-notes" / "ulw-dispatch.md").exists()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def test_prompt_eval_basic_records_decision(mk):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    assert out["prompt_id"] == "ulw-dispatch"
    assert out["iteration"] == 1
    assert out["decision_id"].startswith("dec-")
    assert out["incident_id"] is None
    assert out["summary"]["passed"] == 1
    assert out["summary"]["total"] == 1


def test_prompt_eval_unregistered_raises(mk):
    with pytest.raises(ValueError, match="not registered"):
        mk.prompt_eval("nope", iteration=1, scenarios=[], results=[])


def test_prompt_eval_rejects_non_positive_iteration(mk):
    _register_default(mk)
    with pytest.raises(ValueError):
        mk.prompt_eval("ulw-dispatch", iteration=0, scenarios=[], results=[])
    with pytest.raises(ValueError):
        mk.prompt_eval("ulw-dispatch", iteration=-3, scenarios=[], results=[])


def test_prompt_eval_opens_incident_on_many_unclear_points(mk):
    _register_default(mk)
    scenarios, results = _one_scenario(success=False, unclear=5, accuracy=30.0)
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    assert out["incident_id"] is not None
    assert out["incident_id"].startswith("inc-")


def test_prompt_eval_no_incident_below_threshold(mk):
    _register_default(mk)
    # 2 unclear points is below the default threshold (3)
    scenarios, results = _one_scenario(success=False, unclear=2)
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    assert out["incident_id"] is None


def test_prompt_eval_summary_aggregates_multiple_scenarios(mk):
    _register_default(mk)
    scenarios = [
        {"name": "a"},
        {"name": "b"},
        {"name": "c"},
    ]
    results = [
        {"scenario": "a", "success": True, "accuracy": 100, "tool_uses": 2, "duration_ms": 500},
        {"scenario": "b", "success": False, "accuracy": 50, "tool_uses": 6, "duration_ms": 1500},
        {"scenario": "c", "success": True, "accuracy": 90, "tool_uses": 4, "duration_ms": 1000},
    ]
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    summary = out["summary"]
    assert summary["total"] == 3
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["pass_rate"] == pytest.approx(66.7, rel=0.01)
    assert summary["total_duration_ms"] == 3000
    assert summary["avg_tool_uses"] == pytest.approx(4.0, rel=0.01)


def test_prompt_eval_links_previous_iteration(mk):
    _register_default(mk)
    scenarios, results = _one_scenario()
    first = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    scenarios2, results2 = _one_scenario(name="retry")
    second = mk.prompt_eval(
        "ulw-dispatch",
        iteration=2,
        scenarios=scenarios2,
        results=results2,
        applied_patch="+ new line\n- old line",
        applied_reason="Address retry edge case",
    )
    assert second["previous_iteration_decision_id"] == first["decision_id"]


def test_prompt_eval_records_applied_patch_and_reason_in_decision(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval(
        "ulw-dispatch",
        iteration=1,
        scenarios=scenarios,
        results=results,
        applied_patch="diff --git a/x b/x",
        applied_reason="from unclear point foo",
    )
    decision_path = tmp_path / "decisions" / f"{out['decision_id']}.md"
    assert decision_path.exists()
    body = decision_path.read_text(encoding="utf-8")
    assert "from unclear point foo" in body
    assert "diff --git a/x b/x" in body


def test_prompt_eval_decision_tags_include_prompt_id(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    decision_path = tmp_path / "decisions" / f"{out['decision_id']}.md"
    body = decision_path.read_text(encoding="utf-8")
    assert "prompt:ulw-dispatch" in body
    assert "prompt-eval" in body


def test_prompt_eval_updates_live_note_timeline(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    body = (tmp_path / "live-notes" / "ulw-dispatch.md").read_text(encoding="utf-8")
    assert f"[[{out['decision_id']}]]" in body
    assert "Iter 1" in body


def test_prompt_eval_records_models_used(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval(
        "ulw-dispatch",
        iteration=1,
        scenarios=scenarios,
        results=results,
        models_used=["sonnet-4.6", "opus-4.6"],
    )
    decision_path = tmp_path / "decisions" / f"{out['decision_id']}.md"
    body = decision_path.read_text(encoding="utf-8")
    assert "sonnet-4.6" in body
    assert "opus-4.6" in body


def test_prompt_eval_bitemporal_timestamps_present(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario()
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    # decision_record already records decided_at + valid_from + recorded_at
    decision_path = tmp_path / "decisions" / f"{out['decision_id']}.md"
    body = decision_path.read_text(encoding="utf-8")
    assert "decided_at" in body
    assert "recorded_at" in body
    # recorded_at surfaced on our return value too
    assert "T" in out["recorded_at"]


def test_prompt_eval_incident_is_linked_to_decision(mk, tmp_path):
    _register_default(mk)
    scenarios, results = _one_scenario(success=False, unclear=4)
    out = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=scenarios, results=results)
    assert out["incident_id"] is not None
    decision_path = tmp_path / "decisions" / f"{out['decision_id']}.md"
    body = decision_path.read_text(encoding="utf-8")
    assert out["incident_id"] in body


def test_prompt_eval_end_to_end_integration(mk, tmp_path):
    """Full flow: register → eval 1 (pass) → eval 2 (fail+incident) → eval 3 (pass)."""
    _register_default(mk)

    s1, r1 = _one_scenario(name="baseline")
    out1 = mk.prompt_eval("ulw-dispatch", iteration=1, scenarios=s1, results=r1)
    assert out1["incident_id"] is None

    s2, r2 = _one_scenario(name="edge", success=False, unclear=5)
    out2 = mk.prompt_eval(
        "ulw-dispatch",
        iteration=2,
        scenarios=s2,
        results=r2,
        applied_patch="patch-v2",
        applied_reason="baseline left edge case uncovered",
    )
    assert out2["incident_id"] is not None
    assert out2["previous_iteration_decision_id"] == out1["decision_id"]

    s3, r3 = _one_scenario(name="edge-fix", success=True)
    out3 = mk.prompt_eval(
        "ulw-dispatch",
        iteration=3,
        scenarios=s3,
        results=r3,
        applied_patch="patch-v3",
        applied_reason="Resolve 5 unclear points from iter 2",
    )
    assert out3["incident_id"] is None
    assert out3["previous_iteration_decision_id"] == out2["decision_id"]

    # Timeline sanity: all three decisions + the incident show up in the
    # live-note body.
    body = (tmp_path / "live-notes" / "ulw-dispatch.md").read_text(encoding="utf-8")
    assert out1["decision_id"] in body
    assert out2["decision_id"] in body
    assert out3["decision_id"] in body
    assert out2["incident_id"] in body
