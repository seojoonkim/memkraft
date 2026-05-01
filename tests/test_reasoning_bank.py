"""Tests for ReasoningBank — MemKraft v2.7.1."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memkraft import MemKraft


# ── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    inst = MemKraft(base_dir=str(tmp_path))
    return inst


def _read_jsonl(path: Path) -> list:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ── 1. trajectory_start writes a start record ──────────────────────
def test_trajectory_start_creates_jsonl(mk):
    info = mk.trajectory_start("task-001", title="Deploy fanfic", tags="deploy,vercel")
    p = Path(info["path"])
    assert p.exists()
    records = _read_jsonl(p)
    assert len(records) == 1
    r = records[0]
    assert r["kind"] == "start"
    assert r["task_id"] == "task-001"
    assert r["title"] == "Deploy fanfic"
    assert r["tags"] == ["deploy", "vercel"]
    assert r["schema_version"] == 1
    assert info["started_at"]


# ── 2. trajectory_log appends step records ────────────────────────
def test_trajectory_log_appends_step(mk):
    mk.trajectory_start("task-002", title="Test logging")
    mk.trajectory_log("task-002", 1, thought="think A", action="act A", outcome="ok")
    mk.trajectory_log("task-002", 2, thought="think B", action="act B", outcome="ok")

    p = Path(mk._trajectory_path("task-002"))
    records = _read_jsonl(p)
    steps = [r for r in records if r["kind"] == "step"]
    assert len(steps) == 2
    assert steps[0]["step"] == 1
    assert steps[0]["thought"] == "think A"
    assert steps[1]["step"] == 2


# ── 3. trajectory_log auto-starts if no start record ───────────────
def test_trajectory_log_auto_starts(mk):
    info = mk.trajectory_log("task-003", 1, thought="hi", action="x", outcome="y")
    assert info["task_id"] == "task-003"
    p = Path(mk._trajectory_path("task-003"))
    records = _read_jsonl(p)
    kinds = [r["kind"] for r in records]
    # start MUST appear before step
    assert kinds[0] == "start"
    assert "step" in kinds


# ── 4. trajectory_complete writes pattern bucket ───────────────────
def test_trajectory_complete_writes_pattern_bucket(mk):
    mk.trajectory_start("task-004", title="Vercel deploy ready check", tags="deploy")
    mk.trajectory_log("task-004", 1, thought="t", action="a", outcome="o")
    info = mk.trajectory_complete(
        "task-004",
        status="success",
        lesson="Vercel 배포 후 ready 확인 필수",
    )
    assert info["status"] == "success"
    assert info["pattern_signature"]
    assert info["duplicate_count"] == 1

    patterns_file = Path(mk._patterns_path())
    assert patterns_file.exists()
    data = json.loads(patterns_file.read_text(encoding="utf-8"))
    assert "patterns" in data
    key = f"success::{info['pattern_signature']}"
    assert key in data["patterns"]
    bucket = data["patterns"][key]
    assert bucket["count"] == 1
    assert bucket["status"] == "success"
    assert "task-004" in bucket["task_ids"]


# ── 5. signature derivation deterministic ──────────────────────────
def test_signature_derivation_deterministic(mk):
    mk.trajectory_start("a")
    info_a = mk.trajectory_complete(
        "a", status="success", lesson="vercel deploy ready check important"
    )
    mk.trajectory_start("b")
    info_b = mk.trajectory_complete(
        "b", status="success", lesson="vercel deploy ready check important"
    )
    assert info_a["pattern_signature"] == info_b["pattern_signature"]
    assert info_a["pattern_signature"]  # not empty


# ── 6. signature falls back to sha1 when degenerate ────────────────
def test_signature_fallback_when_degenerate(mk):
    mk.trajectory_start("d1")
    info1 = mk.trajectory_complete("d1", status="success", lesson="")
    mk.trajectory_start("d2")
    info2 = mk.trajectory_complete("d2", status="success", lesson="")
    # Empty lessons → both fall back to "unsignatured-<sha1[:8]>" of "" — equal.
    assert info1["pattern_signature"].startswith("unsignatured-")
    assert info1["pattern_signature"] == info2["pattern_signature"]
    # And different lessons should produce different signatures.
    mk.trajectory_start("d3")
    info3 = mk.trajectory_complete("d3", status="success", lesson="a b c")
    assert info3["pattern_signature"] != info1["pattern_signature"]


# ── 7. reasoning_recall finds relevant lesson ──────────────────────
def test_reasoning_recall_finds_relevant_lesson(mk):
    mk.trajectory_start("rc1", title="Vercel deploy")
    mk.trajectory_complete(
        "rc1",
        status="success",
        lesson="vercel deploy ready check critical for production",
        tags="deploy,vercel",
    )
    mk.trajectory_start("rc2", title="Random unrelated")
    mk.trajectory_complete(
        "rc2", status="success", lesson="kimchi recipe documentation"
    )

    hits = mk.reasoning_recall("vercel deploy ready check", top_k=5)
    assert hits, "should find at least one match"
    assert hits[0]["task_id"] == "rc1"
    assert hits[0]["score"] > 0


# ── 8. reasoning_recall status filter ──────────────────────────────
def test_reasoning_recall_status_filter(mk):
    mk.trajectory_start("ok-1")
    mk.trajectory_complete("ok-1", status="success", lesson="vercel deploy works")
    mk.trajectory_start("fail-1")
    mk.trajectory_complete("fail-1", status="failure", lesson="vercel deploy timeout")

    failures = mk.reasoning_recall("vercel deploy", status="failure", top_k=5)
    assert all(h["status"] == "failure" for h in failures)
    assert any(h["task_id"] == "fail-1" for h in failures)
    assert all(h["task_id"] != "ok-1" for h in failures)


# ── 9. reasoning_recall min_score threshold ────────────────────────
def test_reasoning_recall_min_score_threshold(mk):
    mk.trajectory_start("low-overlap")
    mk.trajectory_complete(
        "low-overlap",
        status="success",
        lesson="completely unrelated topic kimchi recipe",
    )
    # query has at most a tiny token overlap → high threshold filters it out
    hits_loose = mk.reasoning_recall("vercel deploy ready", top_k=5, min_score=0.0)
    hits_strict = mk.reasoning_recall("vercel deploy ready", top_k=5, min_score=0.9)
    assert len(hits_strict) == 0
    # loose may or may not include it depending on score — what we assert is monotonicity.
    assert len(hits_strict) <= len(hits_loose)


# ── 10. reasoning_patterns sorted by count ─────────────────────────
def test_reasoning_patterns_sorted_by_count(mk):
    # Create 3 separate failures with the SAME lesson → same signature, count=3
    for i in range(3):
        mk.trajectory_start(f"freq-{i}")
        mk.trajectory_complete(
            f"freq-{i}",
            status="failure",
            lesson="missing vercel ready check",
        )
    # Create 1 different failure
    mk.trajectory_start("rare-1")
    mk.trajectory_complete("rare-1", status="failure", lesson="totally different bug here")

    patterns = mk.reasoning_patterns(status="failure", min_count=1, top_k=10)
    assert len(patterns) >= 2
    assert patterns[0]["count"] >= patterns[1]["count"]
    assert patterns[0]["count"] == 3


# ── 11. repeat failure bumps count and logs event ──────────────────
def test_repeat_failure_bumps_count_and_logs_event(mk):
    # Two separate failures with same lesson → count=2 → log_event triggered
    mk.trajectory_start("rf-1")
    info1 = mk.trajectory_complete(
        "rf-1", status="failure", lesson="missed vercel ready check"
    )
    assert info1["duplicate_count"] == 1

    mk.trajectory_start("rf-2")
    info2 = mk.trajectory_complete(
        "rf-2", status="failure", lesson="missed vercel ready check"
    )
    assert info2["duplicate_count"] == 2

    # log_event should have written a JSONL entry under <base>/sessions/<today>.jsonl
    sessions_dir = Path(mk.base_dir) / "sessions"
    assert sessions_dir.exists()
    files = list(sessions_dir.glob("*.jsonl"))
    assert files, "log_event must create a sessions JSONL"
    contents = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert "Repeated failure pattern" in contents
    assert "reasoning-bank" in contents


# ── 12. trajectory_get round-trip ──────────────────────────────────
def test_trajectory_get_round_trip(mk):
    mk.trajectory_start("get-1", title="Round trip test", tags="t1,t2")
    mk.trajectory_log("get-1", 1, thought="A", action="actA", outcome="oA")
    mk.trajectory_log("get-1", 2, thought="B", action="actB", outcome="oB")
    mk.trajectory_complete("get-1", status="success", lesson="all good")

    view = mk.trajectory_get("get-1")
    assert view["task_id"] == "get-1"
    assert view["title"] == "Round trip test"
    assert view["status"] == "success"
    assert view["lesson"] == "all good"
    assert view["tags"] == ["t1", "t2"]
    assert len(view["steps"]) == 2
    assert view["steps"][0]["step"] == 1
    assert view["steps"][1]["step"] == 2


# ── 13. trajectory_complete idempotent on duplicate signature ──────
def test_trajectory_complete_idempotent_on_duplicate_signature(mk):
    mk.trajectory_start("idem-1")
    info1 = mk.trajectory_complete(
        "idem-1", status="success", lesson="same exact lesson"
    )
    info2 = mk.trajectory_complete(
        "idem-1", status="success", lesson="same exact lesson"
    )
    # Same task, same status, same signature → second call must NOT bump count.
    assert info1["pattern_signature"] == info2["pattern_signature"]
    assert info2["duplicate_count"] == info1["duplicate_count"]


# ── 14. corrupt JSONL line is skipped ──────────────────────────────
def test_corrupt_jsonl_line_is_skipped(mk):
    info = mk.trajectory_start("corrupt-1", title="t")
    p = Path(info["path"])
    # Inject a corrupt line.
    with open(p, "a", encoding="utf-8") as f:
        f.write("{not json at all\n")
    mk.trajectory_log("corrupt-1", 1, thought="ok", action="x", outcome="y")
    # Reading should not crash and should ignore the corrupt line.
    view = mk.trajectory_get("corrupt-1")
    assert view["title"] == "t"
    # exactly one good step
    assert len(view["steps"]) == 1


# ── 15. storage layout under .memkraft/ ────────────────────────────
def test_storage_layout_is_under_dot_memkraft(mk):
    info = mk.trajectory_start("layout-1")
    mk.trajectory_complete("layout-1", status="success", lesson="x")
    p = Path(info["path"])
    base = Path(mk.base_dir).resolve()
    assert ".memkraft" in p.resolve().parts
    assert p.resolve().is_relative_to(base / ".memkraft" / "trajectories")
    patterns_path = Path(mk._patterns_path())
    assert patterns_path.resolve() == (base / ".memkraft" / "patterns.json").resolve()
    # No leakage into entities/ or live-notes/
    entities_dir = base / "entities"
    if entities_dir.exists():
        assert not any(entities_dir.glob("**/layout-1*"))


# ── 16. tags accepted as list or comma string ──────────────────────
def test_tags_accept_list_or_string(mk):
    mk.trajectory_start("tags-1", tags=["a", "b", "c"])
    mk.trajectory_start("tags-2", tags="a,b,c")
    v1 = mk.trajectory_get("tags-1")
    v2 = mk.trajectory_get("tags-2")
    assert v1["tags"] == ["a", "b", "c"]
    assert v2["tags"] == ["a", "b", "c"]


# ── 17. unsafe task ids get slugified ──────────────────────────────
def test_unsafe_task_ids_slugified(mk):
    info = mk.trajectory_start("../escape/me!", title="x")
    p = Path(info["path"])
    assert p.parent.name == "trajectories"
    assert "/" not in p.name
    assert ".." not in p.name


# ── 18. invalid status coerced to partial ──────────────────────────
def test_invalid_status_coerced_to_partial(mk):
    mk.trajectory_start("bad-status")
    info = mk.trajectory_complete(
        "bad-status", status="weird-thing", lesson="something"
    )
    assert info["status"] == "partial"
