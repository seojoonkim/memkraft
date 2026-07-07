"""Tests for last-interaction index — MemKraft 2.13 S13."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memkraft import MemKraft
from benchmarks.gym.gates import MAX_LAST_INTERACTION_P95_MS
from benchmarks.gym.scenarios import registered_scenarios, run_scenario


def test_record_interaction_then_last_interaction_returns_latest(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.record_interaction("simon", "2026-07-08T01:00:00+00:00", "msg-1", channel="telegram")
    mk.record_interaction("simon", "2026-07-08T02:00:00+00:00", "msg-2", channel="telegram")

    latest = mk.last_interaction("simon")

    assert latest["subject_id"] == "simon"
    assert latest["interaction_id"] == "msg-2"
    assert latest["occurred_at"] == "2026-07-08T02:00:00+00:00"
    assert latest["channel"] == "telegram"


def test_last_interaction_handles_reverse_inserted_200_events(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    base = datetime(2026, 7, 8, tzinfo=timezone.utc)
    for i in reversed(range(200)):
        mk.record_interaction("simon", (base + timedelta(minutes=i)).isoformat(), f"msg-{i:03d}")

    assert mk.last_interaction("simon")["interaction_id"] == "msg-199"


def test_last_interaction_tie_breaks_by_interaction_id(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    occurred_at = "2026-07-08T02:00:00+00:00"
    mk.record_interaction("simon", occurred_at, "msg-b")
    mk.record_interaction("simon", occurred_at, "msg-c")
    mk.record_interaction("simon", occurred_at, "msg-a")

    assert mk.last_interaction("simon")["interaction_id"] == "msg-c"


def test_last_interaction_rebuilds_from_log_when_snapshot_missing_or_corrupt(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.record_interaction("simon", "2026-07-08T01:00:00+00:00", "msg-1")
    mk.record_interaction("simon", "2026-07-08T02:00:00+00:00", "msg-2")
    snapshot = tmp_path / ".memkraft" / "last_interactions.json"
    snapshot.write_text("{broken", encoding="utf-8")

    assert mk.last_interaction("simon")["interaction_id"] == "msg-2"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["simon"]["interaction_id"] == "msg-2"


def test_last_interaction_gym_scenario_is_registered_and_fast_enough():
    assert "last_interaction" in registered_scenarios()

    payload = run_scenario("last_interaction", sizes=[10000], top_k=5)

    result = payload["results"][0]
    assert result["accuracy"] == 1.0
    assert result["p95_ms"] < MAX_LAST_INTERACTION_P95_MS
