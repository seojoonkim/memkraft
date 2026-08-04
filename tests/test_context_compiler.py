"""Wished-for tests for the additive 2.15 A1 context compiler preview."""
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timezone

import pytest

from memkraft import MemKraft
from memkraft import context_compiler


NOW = datetime(2026, 8, 4, 11, 22, 33, tzinfo=timezone.utc)
GOAL_ID = "hermes/context-compiler"


def _declare_pending_gates(mk, count=1):
    mk.goal_declare(GOAL_ID, "Compile context", "Expose blocking gates", [], [], now=NOW)
    for index in range(count):
        mk.gate_declare(
            GOAL_ID,
            "gate-%02d" % index,
            "Required check %02d" % index,
            {"check_kind": "command", "check_ref": "pytest check-%02d" % index},
            now=NOW,
        )


def _compiled_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def test_context_compiler_annotations_are_python39_compatible():
    """A1 must not use PEP 604 annotations, which Python 3.9 cannot evaluate."""
    tree = ast.parse(inspect.getsource(context_compiler))
    annotations = [
        node.annotation
        for node in ast.walk(tree)
        if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None
    ] + [
        node.returns
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None
    ]
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for annotation in annotations
        for node in ast.walk(annotation)
    )


def test_estimate_tokens_is_deterministic_and_handles_unicode(tmp_path):
    mk = MemKraft(str(tmp_path))
    assert mk.estimate_tokens("") == 0
    assert mk.estimate_tokens("abcd") == 1
    assert mk.estimate_tokens("서울") == 2
    assert mk.estimate_tokens("서울") == mk.estimate_tokens("서울")
    with pytest.raises(TypeError, match="text"):
        mk.estimate_tokens(None)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "10"])
def test_compile_context_rejects_invalid_budget(tmp_path, budget):
    with pytest.raises(ValueError, match="budget must be a positive integer"):
        MemKraft(str(tmp_path)).compile_context("answer the user", budget)


def test_empty_store_returns_useful_deterministic_empty_response(tmp_path):
    mk = MemKraft(str(tmp_path))
    result = mk.compile_context("answer the user", 50, objective="be accurate")
    assert result == mk.compile_context("answer the user", 50, objective="be accurate")
    assert result["task"] == "answer the user"
    assert result["budget"] == 50
    assert result["estimated_tokens"] == 0
    assert result["sections"] == {}
    assert result["sources"] == []
    assert len(result["usage_id"]) == 64


def test_compiler_reuses_truth_timeline_and_session_with_citations(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.append_event("user", "city", "Seoul", source="chat", provenance="message:1", valid_from="2026-01-01")
    mk.append_event("user", "city", "Busan", source="chat", provenance="message:2", valid_from="2026-02-01")
    mk.compile_truth(dry_run=False)
    mk.remember_candidate("User asks for concise city updates", session_id="s1", provenance_id="message:3")

    result = mk.compile_context("city update", 500, session_id="s1")

    assert list(result["sections"]) == ["truth", "timeline", "session"]
    assert result["sections"]["truth"][0]["value"] == "Busan"
    assert [item["value"] for item in result["sections"]["timeline"]] == ["Seoul", "Busan"]
    assert result["sections"]["session"][0]["text"] == "User asks for concise city updates"
    for items in result["sections"].values():
        assert all(item.get("source") or item.get("provenance") for item in items)
    assert result["sources"] == ["chat", "message:1", "message:2", "message:3"]
    assert result["estimated_tokens"] <= result["budget"]


def test_budget_is_hard_and_order_and_usage_id_are_stable(tmp_path):
    mk = MemKraft(str(tmp_path))
    for key in ("zeta", "alpha", "middle"):
        mk.append_event("u", key, "x" * 80, source=f"source:{key}")
    mk.compile_truth(dry_run=False)

    first = mk.compile_context("summarize", 45)
    second = mk.compile_context("summarize", 45)

    assert first == second
    assert first["estimated_tokens"] <= 45
    assert [item["key"] for item in first["sections"].get("truth", [])] == sorted(
        item["key"] for item in first["sections"].get("truth", [])
    )
    assert "procedural" not in first["sections"]
    assert "conflicts" not in first["sections"]


def test_budget_bounds_the_rendered_context_payload_including_citations(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.append_event("u", "k", "v", source="a-long-citation-name")
    mk.compile_truth(dry_run=False)

    result = mk.compile_context("summarize", 40)
    rendered = json.dumps(
        {"sections": result["sections"], "sources": result["sources"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert result["estimated_tokens"] == mk.estimate_tokens(rendered)
    assert result["estimated_tokens"] <= result["budget"]


def test_same_inputs_on_equivalent_stores_have_same_usage_id(tmp_path):
    ids = []
    for name in ("one", "two"):
        mk = MemKraft(str(tmp_path / name))
        mk.append_event("u", "k", "v", source="test")
        mk.compile_truth(dry_run=False)
        ids.append(mk.compile_context("task", 100)["usage_id"])
    assert ids[0] == ids[1]


def test_invalid_task_and_optional_fields_are_clear(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="task must not be empty"):
        mk.compile_context(" ", 10)
    with pytest.raises(TypeError, match="session_id"):
        mk.compile_context("task", 10, session_id=3)
    with pytest.raises(ValueError, match="objective must not be empty"):
        mk.compile_context("task", 10, objective=" ")


def test_usage_id_unchanged_when_goal_id_none(tmp_path):
    mk = MemKraft(str(tmp_path))
    baseline = mk.compile_context("task", 100)
    opted_out = mk.compile_context("task", 100, goal_id=None, execution_budget=25)
    assert opted_out["usage_id"] == baseline["usage_id"]


def test_usage_id_unchanged_when_execution_budget_zero(tmp_path):
    mk = MemKraft(str(tmp_path))
    _declare_pending_gates(mk)
    baseline = mk.compile_context("task", 100, now=NOW)
    opted_out = mk.compile_context(
        "task", 100, now=NOW, goal_id=GOAL_ID, execution_budget=0
    )
    assert opted_out["usage_id"] == baseline["usage_id"]


def test_compiled_output_byte_identical_when_feature_unused(tmp_path):
    mk = MemKraft(str(tmp_path))
    baseline = mk.compile_context("task", 100, objective="accurate", now=NOW)
    assert _compiled_bytes(mk.compile_context(
        "task", 100, objective="accurate", now=NOW,
        goal_id=None, execution_budget=40,
    )) == _compiled_bytes(baseline)
    assert _compiled_bytes(mk.compile_context(
        "task", 100, objective="accurate", now=NOW,
        goal_id=GOAL_ID, execution_budget=0,
    )) == _compiled_bytes(baseline)


def test_execution_requires_dual_opt_in_and_is_appended_last(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.append_event("u", "k", "v", source="chat")
    mk.compile_truth(dry_run=False)
    _declare_pending_gates(mk, count=2)

    assert "execution" not in mk.compile_context(
        "task", 200, now=NOW, execution_budget=80
    )["sections"]
    assert "execution" not in mk.compile_context(
        "task", 200, now=NOW, goal_id=GOAL_ID, execution_budget=0
    )["sections"]

    compiled = mk.compile_context(
        "task", 200, now=NOW, goal_id=GOAL_ID, execution_budget=80
    )
    assert list(compiled["sections"])[-1] == "execution"
    assert [item["gate_id"] for item in compiled["sections"]["execution"]] == [
        "gate-00", "gate-01"
    ]
    assert all(item["required"] and item["status"] == "pending"
               for item in compiled["sections"]["execution"])


def test_execution_budget_visibly_reduces_other_sections(tmp_path):
    mk = MemKraft(str(tmp_path))
    for index in range(12):
        mk.append_event("u", "key-%02d" % index, "x" * 40, source="chat")
    mk.compile_truth(dry_run=False)
    _declare_pending_gates(mk)

    baseline = mk.compile_context("task", 260, now=NOW)
    opted_in = mk.compile_context(
        "task", 260, now=NOW, goal_id=GOAL_ID, execution_budget=100
    )
    baseline_other = sum(len(items) for items in baseline["sections"].values())
    opted_in_other = sum(
        len(items) for name, items in opted_in["sections"].items()
        if name != "execution"
    )
    assert opted_in_other < baseline_other
    assert opted_in["estimated_tokens"] <= opted_in["budget"]


def test_execution_section_is_at_most_eight_gates_and_400_tokens(tmp_path):
    mk = MemKraft(str(tmp_path))
    _declare_pending_gates(mk, count=12)
    compiled = mk.compile_context(
        "task", 1000, now=NOW, goal_id=GOAL_ID, execution_budget=900
    )
    execution = compiled["sections"]["execution"]
    rendered = json.dumps(
        {"sections": {"execution": execution}, "sources": []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    assert len(execution) <= 8
    assert mk.estimate_tokens(rendered) <= 400

    tightly_budgeted = mk.compile_context(
        "task", 200, now=NOW, goal_id=GOAL_ID, execution_budget=100
    )["sections"]["execution"]
    tight_rendered = json.dumps(
        {"sections": {"execution": tightly_budgeted}, "sources": []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    assert mk.estimate_tokens(tight_rendered) <= 100
