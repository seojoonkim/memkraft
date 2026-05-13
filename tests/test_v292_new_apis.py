"""Tests for v2.9.2 new APIs — ReasoningBank convenience, search_typed,
extract_structured."""
from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft


# ── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path))


# ── ReasoningBank convenience aliases ──────────────────────────────
def test_log_reasoning_chains_start_log_complete(mk):
    info = mk.log_reasoning(
        "deploy fanfic to vercel",
        outcome="success",
        steps=["build", "push", "verify"],
        tags="deploy,vercel",
    )
    assert info["status"] == "success"
    assert info["pattern_signature"]
    # Trajectory file exists with start + 3 steps + complete = 5 records.
    p = Path(info.get("path", "")) if info.get("path") else (
        Path(mk.base_dir) / ".memkraft" / "trajectories"
        / f"{info['task_id']}.jsonl"
    )
    assert p.exists()
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 5


def test_log_reasoning_failure_with_error_becomes_lesson(mk):
    info = mk.log_reasoning(
        "rollout new index",
        outcome="failure",
        steps=["build", "deploy"],
        error="Bloom filter false-positive rate too high",
    )
    assert info["status"] == "failure"
    assert "bloom" in info["lesson"].lower()


def test_log_reasoning_rejects_empty_task(mk):
    with pytest.raises(ValueError):
        mk.log_reasoning("", outcome="success")


def test_reasoning_stats_aggregates_correctly(mk):
    mk.log_reasoning("task-a", outcome="success", steps=["s1"])
    mk.log_reasoning("task-b", outcome="success", steps=["s1"])
    mk.log_reasoning("task-c", outcome="failure", steps=["s1"], error="boom")
    mk.log_reasoning("task-d", outcome="partial", steps=["s1"])

    stats = mk.reasoning_stats()
    assert stats["total"] == 4
    assert stats["success"] == 2
    assert stats["failure"] == 1
    assert stats["partial"] == 1
    assert stats["in_progress"] == 0
    # success_rate = 2 / (2+1+1) = 0.5
    assert stats["success_rate"] == 0.5


def test_reasoning_stats_handles_in_progress_trajectories(mk):
    mk.trajectory_start("inflight-task", title="not done yet")
    mk.log_reasoning("done-task", outcome="success", steps=["s1"])

    stats = mk.reasoning_stats()
    assert stats["total"] == 2
    assert stats["in_progress"] == 1
    assert stats["success"] == 1
    # success_rate ignores in-progress
    assert stats["success_rate"] == 1.0


def test_reasoning_stats_top_failure_patterns_sorted(mk):
    # Same failure pattern twice → count=2
    mk.log_reasoning(
        "task-1",
        outcome="failure",
        steps=["s1"],
        error="timeout on api call",
        tags="api,timeout",
    )
    mk.log_reasoning(
        "task-2",
        outcome="failure",
        steps=["s1"],
        error="timeout on api call",
        tags="api,timeout",
    )
    stats = mk.reasoning_stats()
    failures = stats["top_failure_patterns"]
    assert len(failures) >= 1
    # First failure pattern should have count >= 2
    assert failures[0]["count"] >= 2


def test_get_similar_reasoning_is_recall_alias(mk):
    mk.log_reasoning(
        "vercel deploy succeeded",
        outcome="success",
        steps=["build", "push"],
        tags="vercel,deploy",
    )
    hits = mk.get_similar_reasoning("vercel deploy", top_k=5)
    assert len(hits) >= 1
    assert "vercel" in (hits[0]["pattern_signature"] + hits[0]["lesson"]).lower() \
        or "deploy" in (hits[0]["pattern_signature"] + hits[0]["lesson"]).lower() \
        or "vercel" in " ".join(hits[0].get("tags", [])).lower()


# ── search_typed ────────────────────────────────────────────────────
def test_search_typed_filters_by_entity_type(mk):
    mk.track("Alice", entity_type="person")
    mk.track("Hashed", entity_type="company")
    mk.update("Alice", "Alice works at Hashed as engineer")
    mk.update("Hashed", "Hashed is a venture firm specializing in crypto")

    persons = mk.search_typed("Hashed", entity_type="person", top_k=10)
    files = [h.get("file", "") for h in persons]
    assert any("alice" in f for f in files)
    assert not any("hashed" in f.lower() for f in files)

    companies = mk.search_typed("Hashed", entity_type="company", top_k=10)
    files = [h.get("file", "") for h in companies]
    assert any("hashed" in f.lower() for f in files)
    assert not any("alice" in f for f in files)


def test_search_typed_no_filter_matches_search_v2(mk):
    mk.track("Alice", entity_type="person")
    mk.update("Alice", "Alice loves crypto")
    a = mk.search_typed("crypto", top_k=5)
    b = mk.search_v2("crypto", top_k=5)
    assert [h.get("file") for h in a] == [h.get("file") for h in b]


def test_search_typed_empty_query_returns_empty(mk):
    mk.track("Alice", entity_type="person")
    assert mk.search_typed("", entity_type="person") == []
    assert mk.search_typed("   ", entity_type="person") == []


def test_search_typed_unknown_type_returns_empty(mk):
    mk.track("Alice", entity_type="person")
    mk.update("Alice", "Alice loves crypto")
    assert mk.search_typed("crypto", entity_type="alien", top_k=10) == []


def test_search_typed_fact_key_filter(mk):
    mk.track("Alice", entity_type="person")
    mk.update("Alice", "Alice works at Hashed")
    mk.fact_add("Alice", "role", "CEO", valid_from="2020-01-01")

    mk.track("Bob", entity_type="person")
    mk.update("Bob", "Bob works at Hashed too")
    # Bob has NO 'role' fact.

    hits = mk.search_typed("Hashed", fact_key="role", top_k=10)
    files = [h.get("file", "") for h in hits]
    assert any("alice" in f for f in files)
    assert not any("bob" in f for f in files)


# ── extract_structured ─────────────────────────────────────────────
def test_extract_structured_dates(mk):
    r = mk.extract_structured(
        "Meeting on 2026-05-14 and again on 2026/06/01."
    )
    assert "2026-05-14" in r["dates"]
    assert "2026/06/01" in r["dates"]


def test_extract_structured_urls_and_emails(mk):
    r = mk.extract_structured(
        "See https://vibekai.vercel.app and email alice@example.com."
    )
    assert "https://vibekai.vercel.app" in r["urls"]
    assert "alice@example.com" in r["emails"]


def test_extract_structured_money(mk):
    r = mk.extract_structured(
        "Charged $12.99 plus ₩50,000 service fee. Also €99 for premium."
    )
    raws = [m["raw"] for m in r["money"]]
    assert "$12.99" in raws
    assert any("50,000" in raw or "50000" in raw for raw in raws)
    assert "€99" in raws


def test_extract_structured_versions_not_confused_with_money(mk):
    r = mk.extract_structured(
        "Released v2.9.2 today. Cost was $12.99."
    )
    # 2.9.2 is a version, 12.99 is money — must NOT appear as version.
    assert "2.9.2" in r["versions"]
    assert "12.99" not in r["versions"]


def test_extract_structured_empty_input(mk):
    r = mk.extract_structured("")
    assert r["dates"] == []
    assert r["urls"] == []
    assert r["emails"] == []
    assert r["money"] == []
    assert r["saved"] == 0


def test_extract_structured_auto_save_creates_facts(mk):
    mk.track("Alice", entity_type="person")
    r = mk.extract_structured(
        "Alice's email is alice@example.com, hired on 2026-05-14, $50000 salary.",
        entity_hint="Alice",
        auto_save=True,
    )
    assert r["saved"] >= 3
    facts = mk.fact_list("Alice")
    keys = {f.get("key") for f in facts if isinstance(f, dict)}
    assert "email" in keys
    assert "date" in keys
    assert "money" in keys


def test_extract_structured_no_save_when_flag_off(mk):
    mk.track("Alice", entity_type="person")
    r = mk.extract_structured(
        "Alice email alice@example.com",
        entity_hint="Alice",
        auto_save=False,
    )
    assert r["saved"] == 0
    facts = mk.fact_list("Alice")
    keys = {f.get("key") for f in facts if isinstance(f, dict)}
    assert "email" not in keys
