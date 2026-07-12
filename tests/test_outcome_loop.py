"""Strict-TDD contract tests for the 2.15 A2 outcome-loop preview."""
from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timezone

import pytest

from memkraft import MemKraft

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)


def _concurrent_compile(base_dir, ready, start, results):
    mk = MemKraft(base_dir)
    ready.put(1); start.wait()
    results.put(mk.compile_context("task", 500, now=NOW)["usage_id"])


def _concurrent_outcome(base_dir, usage_id, outcome, ready, start, results):
    mk = MemKraft(base_dir)
    ready.put(1); start.wait()
    try:
        result = mk.report_outcome(usage_id, outcome, metadata={"idempotency_key": "concurrent-key"}, now=NOW)
        results.put(("ok", result["status"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _run_concurrently(target, arg_tuples):
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [ctx.Process(target=target, args=(*args, ready, start, results)) for args in arg_tuples]
    for process in processes: process.start()
    for _ in processes: ready.get(timeout=10)
    start.set()
    output = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert not process.is_alive(), "concurrent operation deadlocked"
        assert process.exitcode == 0
    return output


def _seed(mk):
    mk.append_event("u", "alpha", "A", source="source:a")
    mk.append_event("u", "beta", "B", source="source:b")
    mk.compile_truth(dry_run=False)


def test_valid_outcome_is_append_only_idempotent_and_reranks(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    before = mk.compile_context("task", 500, now=NOW)
    assert [x["key"] for x in before["sections"]["truth"]] == ["alpha", "beta"]

    reported = mk.report_outcome(
        before["usage_id"], "failure", reward=-99,
        metadata={"idempotency_key": "retry-1"}, now=NOW,
    )
    retry = mk.report_outcome(
        before["usage_id"], "failure", reward=-99,
        metadata={"idempotency_key": "retry-1"}, now=NOW,
    )
    assert reported["status"] == "applied"
    assert retry["status"] == "already_reported"
    assert reported["reward"] == -1.0
    assert reported["max_update"] == 0.2
    assert [x["key"] for x in mk.compile_context("task", 500, now=NOW)["sections"]["truth"]] == ["beta", "alpha"]

    rows = [json.loads(line) for line in (tmp_path / ".memkraft" / "outcomes.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["usage_id"] == before["usage_id"]
    assert rows[0]["source_items"]


def test_timeline_versions_have_distinct_utility_identities(tmp_path):
    mk = MemKraft(str(tmp_path))
    first = mk.append_event(
        "u", "status", "old", source="shared", provenance="shared-prov",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    second = mk.append_event(
        "u", "status", "new", source="shared", provenance="shared-prov",
        valid_from="2026-01-02T00:00:00+00:00",
    )
    # Isolate the timeline contract and make the first usage contain only the old version.
    mk.compile_truth = lambda dry_run=True: {"records": []}
    old_item = mk.compile_context("task", 500, now=NOW)["sections"]["timeline"][0]
    one_item_budget = mk.estimate_tokens(json.dumps(
        {"sections": {"timeline": [old_item]}, "sources": ["shared", "shared-prov"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    usage = mk.compile_context("task", one_item_budget, now=NOW)
    assert [item["id"] for item in usage["sections"]["timeline"]] == [first["id"]]

    mk.report_outcome(usage["usage_id"], "failure", now=NOW)
    reranked = mk.compile_context("task", 500, now=NOW)["sections"]["timeline"]
    assert [item["id"] for item in reranked] == [second["id"], first["id"]]


def test_concurrent_compile_records_one_usage_row(tmp_path):
    mk = MemKraft(str(tmp_path)); _seed(mk)
    usage_ids = _run_concurrently(_concurrent_compile, [(str(tmp_path),)] * 6)
    assert len(set(usage_ids)) == 1
    rows = [json.loads(line) for line in (tmp_path / ".memkraft" / "context_usages.jsonl").read_text().splitlines()]
    assert [row["usage_id"] for row in rows].count(usage_ids[0]) == 1


def test_concurrent_identical_outcome_appends_once(tmp_path):
    mk = MemKraft(str(tmp_path)); _seed(mk)
    usage = mk.compile_context("task", 500, now=NOW)["usage_id"]
    output = _run_concurrently(_concurrent_outcome, [(str(tmp_path), usage, "success")] * 6)
    assert sorted(item[1] for item in output) == ["already_reported"] * 5 + ["applied"]
    assert len((tmp_path / ".memkraft" / "outcomes.jsonl").read_text().splitlines()) == 1


def test_concurrent_conflicting_outcome_has_one_append_and_one_value_error(tmp_path):
    mk = MemKraft(str(tmp_path)); _seed(mk)
    usage = mk.compile_context("task", 500, now=NOW)["usage_id"]
    output = _run_concurrently(_concurrent_outcome, [
        (str(tmp_path), usage, "success"), (str(tmp_path), usage, "failure"),
    ])
    assert sum(item[:2] == ("ok", "applied") for item in output) == 1
    errors = [item for item in output if item[0] == "error"]
    assert errors == [("error", "ValueError", "idempotency key already used with different outcome")]
    assert len((tmp_path / ".memkraft" / "outcomes.jsonl").read_text().splitlines()) == 1


def test_unknown_invalid_and_duplicate_conflict_never_learn(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(KeyError, match="unknown usage_id"):
        mk.report_outcome("0" * 64, "success", now=NOW)
    assert not (tmp_path / ".memkraft" / "outcomes.jsonl").exists()
    with pytest.raises(ValueError, match="outcome"):
        mk.report_outcome("x", "", now=NOW)

    _seed(mk)
    usage = mk.compile_context("task", 500, now=NOW)["usage_id"]
    mk.report_outcome(usage, "success", metadata={"idempotency_key": "same"}, now=NOW)
    with pytest.raises(ValueError, match="idempotency"):
        mk.report_outcome(usage, "failure", metadata={"idempotency_key": "same"}, now=NOW)


@pytest.mark.parametrize("reward", [True, "1", float("nan"), float("inf")])
def test_reward_and_metadata_validation(tmp_path, reward):
    mk = MemKraft(str(tmp_path)); _seed(mk)
    usage = mk.compile_context("task", 500, now=NOW)["usage_id"]
    with pytest.raises((TypeError, ValueError), match="reward"):
        mk.report_outcome(usage, "success", reward=reward, now=NOW)
    with pytest.raises(TypeError, match="metadata"):
        mk.report_outcome(usage, "success", metadata=[], now=NOW)


def test_decay_replay_pin_provenance_and_budget(tmp_path):
    mk = MemKraft(str(tmp_path)); _seed(mk)
    first = mk.compile_context("task", 500, now=NOW)
    mk.report_outcome(first["usage_id"], "failure", reward=-1, now=NOW)
    utility_now = mk.context_utility(now=NOW)
    utility_later = mk.context_utility(now=datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert utility_now == mk.context_utility(now=NOW)
    assert all(abs(v["score"]) <= 0.2 for v in utility_now.values())
    assert all(abs(utility_later[k]["score"]) < abs(v["score"]) for k, v in utility_now.items())

    result = mk.compile_context("task", 80, now=NOW, pinned_sources=["source:a"])
    assert result["sections"]["truth"][0]["source"] == "source:a"
    assert result["estimated_tokens"] <= result["budget"]
    assert all(item.get("source") or item.get("provenance") for items in result["sections"].values() for item in items)


def test_reporting_after_forget_does_not_resurface_content(tmp_path):
    mk = MemKraft(str(tmp_path)); event = mk.append_event("u", "secret", "hidden", source="private")
    mk.compile_truth(dry_run=False)
    usage = mk.compile_context("task", 500, now=NOW)["usage_id"]
    mk.forget(event["id"], dry_run=False)
    mk.report_outcome(usage, "success", now=NOW)
    assert "secret" not in [x["key"] for x in mk.compile_context("task", 500, now=NOW)["sections"].get("truth", [])]


def test_outcome_gym_contract():
    from benchmarks.gym.scenarios import registered_scenarios, run_scenario
    assert "outcome_context" in registered_scenarios()
    result = run_scenario("outcome_context", sizes=[1], top_k=1)["results"][0]
    assert result["ordering_changed"] is True
    assert result["reward_clamped"] is True
    assert result["pin_protected"] is True
    assert result["mean_recall_at_k"] == result["min_recall_at_k"] == 1.0
