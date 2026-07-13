"""Tests for session overlay candidate retrieval — MemKraft 2.13 S11."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memkraft import MemKraft
from benchmarks.gym.gates import evaluate_gate
from benchmarks.gym.scenarios import registered_scenarios, run_scenario


def test_session_overlay_finds_recent_candidate_in_same_session(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.remember_candidate("Simon prefers concise update bullets", session_id="s1")

    results = mk.session_overlay("s1", "concise bullets", top_k=3)

    assert len(results) == 1
    assert results[0]["text"] == "Simon prefers concise update bullets"
    assert results[0]["memory_state"] == "session_overlay"
    assert results[0]["score"] > 0


def test_session_overlay_does_not_leak_other_sessions(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.remember_candidate("private beta launch plan", session_id="a")
    mk.remember_candidate("unrelated public note", session_id="b")

    results = mk.session_overlay("b", "private beta launch", top_k=5)

    assert results == []


def test_session_overlay_excludes_expired_candidates(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    mk.remember_candidate("expired concise bullets", session_id="s1", expires_at=expired)

    assert mk.session_overlay("s1", "concise bullets", top_k=5) == []


def test_session_overlay_label_validation_raises_on_missing_label(tmp_path: Path):
    mk = MemKraft(str(tmp_path))

    with pytest.raises(ValueError, match="memory_state"):
        mk._validate_session_overlay_results([{"text": "missing label"}])


def test_session_overlay_is_separate_from_global_search_shape(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.remember_candidate("candidate-only phrase", session_id="s1")

    results = mk.session_overlay("s1", "candidate phrase", top_k=1)

    assert results
    assert all(r["memory_state"] == "session_overlay" for r in results)
    assert all("path" not in r for r in results)


def test_session_overlay_gym_scenario_is_registered_and_passes_gate():
    assert "session_overlay_recall" in registered_scenarios()

    payload = run_scenario("session_overlay_recall", sizes=[20], top_k=5)

    result = payload["results"][0]
    assert result["same_session_recall"] == 1.0
    assert result["cross_session_leaks"] == 0
    assert result["expired_exposures"] == 0
    assert result["governed_exposures"] == 0
    assert result["governed_free_text_discoveries"] == 1
    assert evaluate_gate(payload)["passed"] is True


def test_session_overlay_gym_gate_fails_without_candidate_policy_filter(monkeypatch):
    monkeypatch.setattr(MemKraft, "_denied", lambda self, subject, key: False)

    payload = run_scenario("session_overlay_recall", sizes=[20], top_k=5)

    result = payload["results"][0]
    assert result["governed_exposures"] == 1
    assert result["governed_free_text_discoveries"] == 1
    gate = evaluate_gate(payload)
    assert gate["passed"] is False
    assert any("governed_exposures" in failure for failure in gate["failures"])


def test_session_overlay_inherits_structured_policy_filter_but_keeps_free_text(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.remember_candidate("Simon prefers private mode", session_id="s1")
    mk.remember_candidate("private mode free text", session_id="s1")
    mk.do_not_remember(subject="Simon", key="prefers", dry_run=False)
    assert [hit["text"] for hit in mk.session_overlay("s1", "private mode", top_k=5)] == [
        "private mode free text"
    ]
