"""Wished-for tests for MemKraft 3.8 Feature 1: TTL current focus.

Focus is an inert, append-only, TTL-bounded record of what the operator says
the session is about. It never gates, dispatches, approves, schedules, or
edits skills — those stay with Hermes.
"""
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from memkraft import MemKraft
from memkraft import focus as focus_module
from memkraft.focus import MAX_ACTIVE_FOCUS, MAX_FOCUS_TOKENS, FocusError


NOW = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 8, 18, 9, 0, 0)


def _later(seconds):
    return NOW + timedelta(seconds=seconds)


def _seed(mk):
    """A little canonical/timeline content so the compiler has real sections."""
    mk.append_event("release", "owner", "Sano", source="runbook.md")
    mk.append_event("staging", "cluster", "seoul-a", source="runbook.md")
    mk.compile_truth(dry_run=False)


# --------------------------------------------------------------------------
# module hygiene
# --------------------------------------------------------------------------

def test_focus_annotations_are_python39_compatible():
    tree = ast.parse(inspect.getsource(focus_module))
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


def test_focus_module_adds_no_execution_or_approval_surface():
    source = inspect.getsource(focus_module)
    for forbidden in ("approve", "dispatch", "schedule", "gate_", "skill", "subprocess"):
        assert forbidden not in source


def test_focus_mixin_exposes_only_the_three_focus_methods():
    public = {
        name for name in vars(focus_module.FocusMixin)
        if not name.startswith("_")
    }
    assert public == {"focus_record", "focus_active", "focus_release"}


# --------------------------------------------------------------------------
# recording
# --------------------------------------------------------------------------

def test_focus_record_requires_timezone_aware_now(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(FocusError) as excinfo:
        mk.focus_record("release", "cut 3.8.0", now=NAIVE)
    assert excinfo.value.code == "E_FOCUS_VALIDATION"


def test_focus_record_requires_now_to_be_a_datetime(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(FocusError):
        mk.focus_record("release", "cut 3.8.0", now="2026-08-18T09:00:00Z")


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "600", None, 86401])
def test_focus_record_rejects_out_of_bound_ttl(tmp_path, ttl):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(FocusError):
        mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=ttl)


@pytest.mark.parametrize("topic", ["", "   ", "a" * 81, 7, None, "bad topic!"])
def test_focus_record_rejects_invalid_topic(tmp_path, topic):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(FocusError):
        mk.focus_record(topic, "cut 3.8.0", now=NOW)


@pytest.mark.parametrize("statement", ["", "   ", "a" * 513, 7, None])
def test_focus_record_rejects_invalid_statement(tmp_path, statement):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(FocusError):
        mk.focus_record("release", statement, now=NOW)


def test_focus_record_writes_an_append_only_inert_record(tmp_path):
    mk = MemKraft(str(tmp_path))
    result = mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    assert result["outcome"] == "applied"
    record = result["record"]
    assert record["record_type"] == "focus"
    assert record["topic"] == "release"
    assert record["statement"] == "cut 3.8.0"
    assert record["created_at"] == "2026-08-18T09:00:00Z"
    assert record["expires_at"] == "2026-08-18T09:10:00Z"
    assert record["privacy"] == "local_private"
    assert record["authority_verified"] is False

    path = mk._focus_events_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    mk.focus_record("release", "still cutting 3.8.0", now=_later(60))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_focus_release_supersedes_without_rewriting_history(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    mk.focus_release("release", now=_later(60))
    assert mk.focus_active(now=_later(120)) == []
    assert len(mk._focus_events_path().read_text(encoding="utf-8").splitlines()) == 2


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def test_focus_active_requires_aware_now(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW)
    with pytest.raises(FocusError):
        mk.focus_active(now=NAIVE)


def test_focus_active_excludes_expired_records(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    assert [row["topic"] for row in mk.focus_active(now=_later(599))] == ["release"]
    assert mk.focus_active(now=_later(600)) == []
    assert mk.focus_active(now=NOW - timedelta(seconds=1)) == []


def test_focus_active_keeps_only_the_latest_record_per_topic(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    mk.focus_record("release", "wait for CI", now=_later(60), ttl_seconds=600)
    active = mk.focus_active(now=_later(120))
    assert [(row["topic"], row["statement"]) for row in active] == [
        ("release", "wait for CI")
    ]


def test_focus_active_is_deterministic_and_capped(tmp_path):
    mk = MemKraft(str(tmp_path))
    for index in range(MAX_ACTIVE_FOCUS + 2):
        mk.focus_record(
            "topic-%d" % index, "statement %d" % index,
            now=_later(index), ttl_seconds=600,
        )
    active = mk.focus_active(now=_later(100))
    assert len(active) == MAX_ACTIVE_FOCUS
    # newest first, then topic
    assert [row["topic"] for row in active] == ["topic-4", "topic-3", "topic-2"]
    assert active == mk.focus_active(now=_later(100))


def test_focus_active_skips_malformed_persisted_records(tmp_path):
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    path = mk._focus_events_path()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({
            "id": "x1", "schema_version": 1, "record_type": "focus",
            "topic": "bad topic!", "statement": "nope",
            "created_at": "2026-08-18T09:00:00Z",
            "expires_at": "2026-08-18T09:10:00Z",
            "privacy": "local_private", "authority_verified": False,
        }) + "\n")
        handle.write(json.dumps({
            "id": "x2", "schema_version": 1, "record_type": "focus",
            "topic": "claimed", "statement": "authority claim",
            "created_at": "2026-08-18T09:00:00Z",
            "expires_at": "2026-08-18T09:10:00Z",
            "privacy": "local_private", "authority_verified": True,
        }) + "\n")
        handle.write(json.dumps({
            "id": "x3", "schema_version": 1, "record_type": "focus",
            "topic": "inverted", "statement": "expires before it starts",
            "created_at": "2026-08-18T09:10:00Z",
            "expires_at": "2026-08-18T09:00:00Z",
            "privacy": "local_private", "authority_verified": False,
        }) + "\n")
        for record_id, statement in (("x4", " padded "), ("x5", "line\nbreak")):
            handle.write(json.dumps({
                "id": record_id, "schema_version": 1, "record_type": "focus",
                "topic": record_id, "statement": statement,
                "created_at": "2026-08-18T09:00:00Z",
                "expires_at": "2026-08-18T09:10:00Z",
                "privacy": "local_private", "authority_verified": False,
            }) + "\n")
    assert [row["topic"] for row in mk.focus_active(now=_later(60))] == ["release"]


# --------------------------------------------------------------------------
# as-of selection is monotonic in the log
# --------------------------------------------------------------------------

def test_future_focus_release_does_not_affect_an_earlier_as_of_now(tmp_path):
    """A release recorded later must not retroactively end an earlier focus."""
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    before = mk.focus_active(now=_later(60))
    mk.focus_release("release", now=_later(300))
    assert mk.focus_active(now=_later(60)) == before
    assert [row["statement"] for row in mk.focus_active(now=_later(60))] == ["cut 3.8.0"]
    # ...and the release still takes effect at and after its own timestamp.
    assert mk.focus_active(now=_later(300)) == []


def test_future_focus_update_does_not_affect_an_earlier_as_of_now(tmp_path):
    """A later restatement of a topic must not be visible before it happened."""
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    mk.focus_record("release", "cut 3.9.0", now=_later(300), ttl_seconds=600)
    assert [row["statement"] for row in mk.focus_active(now=_later(60))] == ["cut 3.8.0"]
    assert [row["statement"] for row in mk.focus_active(now=_later(300))] == ["cut 3.9.0"]


def test_focus_at_the_same_instant_resolves_to_exactly_one_entry(tmp_path):
    """Same topic, same created_at: append order decides, and only one wins."""
    mk = MemKraft(str(tmp_path))
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    mk.focus_record("release", "cut 3.9.0", now=NOW, ttl_seconds=600)
    active = mk.focus_active(now=_later(60))
    assert len(active) == 1
    assert active[0]["statement"] == "cut 3.9.0"
    assert active == mk.focus_active(now=_later(60))


# --------------------------------------------------------------------------
# compiler integration
# --------------------------------------------------------------------------

def test_compile_context_is_byte_identical_when_focus_budget_is_zero(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    baseline = mk.compile_context("answer the user", 800, now=NOW)

    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    default = mk.compile_context("answer the user", 800, now=NOW)
    explicit = mk.compile_context("answer the user", 800, now=NOW, focus_budget=0)

    for candidate in (default, explicit):
        assert candidate["usage_id"] == baseline["usage_id"]
        assert json.dumps(candidate, sort_keys=True) == json.dumps(baseline, sort_keys=True)
        assert "focus" not in candidate["sections"]


@pytest.mark.parametrize("focus_budget", [-1, True, 1.5, "10", None])
def test_compile_context_rejects_invalid_focus_budget(tmp_path, focus_budget):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="focus_budget"):
        mk.compile_context("answer the user", 800, now=NOW, focus_budget=focus_budget)


def test_compile_context_rejects_focus_budget_that_does_not_fit(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="focus_budget"):
        mk.compile_context("answer the user", 100, now=NOW, focus_budget=100)


def test_compile_context_requires_aware_now_for_focus(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="now"):
        mk.compile_context("answer the user", 800, focus_budget=200)
    with pytest.raises(ValueError, match="now"):
        mk.compile_context("answer the user", 800, now=NAIVE, focus_budget=200)


def test_compile_context_includes_active_focus_when_opted_in(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    result = mk.compile_context("answer the user", 800, now=NOW, focus_budget=200)
    assert [item["statement"] for item in result["sections"]["focus"]] == ["cut 3.8.0"]
    assert result["sections"]["focus"][0]["source"] == "current_focus"
    assert "current_focus" in result["sources"]
    assert result["estimated_tokens"] <= 800
    assert result == mk.compile_context("answer the user", 800, now=NOW, focus_budget=200)


def test_compile_context_omits_expired_focus(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    result = mk.compile_context(
        "answer the user", 800, now=_later(600), focus_budget=200)
    assert "focus" not in result["sections"]


def test_compile_context_hard_bounds_the_focus_section(tmp_path):
    mk = MemKraft(str(tmp_path))
    for index in range(MAX_ACTIVE_FOCUS + 2):
        mk.focus_record(
            "topic-%d" % index, "s" * 400, now=_later(index), ttl_seconds=600)
    result = mk.compile_context(
        "answer the user", 4000, now=_later(100), focus_budget=4000 - 1)
    focus = result["sections"]["focus"]
    assert 0 < len(focus) <= MAX_ACTIVE_FOCUS
    rendered = json.dumps(
        {"sections": {"focus": focus}, "sources": []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert mk.estimate_tokens(rendered) <= MAX_FOCUS_TOKENS
    assert result["estimated_tokens"] <= 4000


def test_compile_context_keeps_execution_section_last(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    goal_id = "hermes/focus"
    mk.goal_declare(goal_id, "Compile", "Expose gates", [], [], now=NOW)
    mk.gate_declare(
        goal_id, "gate-00", "Required check",
        {"check_kind": "command", "check_ref": "pytest check"}, now=NOW)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    result = mk.compile_context(
        "answer the user", 2000, now=NOW, goal_id=goal_id,
        execution_budget=400, focus_budget=200)
    names = list(result["sections"])
    assert "focus" in names and "execution" in names
    assert names[-1] == "execution"
    assert names.index("focus") < names.index("execution")


def test_focus_changes_the_usage_id_only_when_it_is_compiled(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    without = mk.compile_context("answer the user", 800, now=NOW, focus_budget=200)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    with_focus = mk.compile_context("answer the user", 800, now=NOW, focus_budget=200)
    assert without["usage_id"] != with_focus["usage_id"]
