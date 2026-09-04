from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("agent.memory_provider")

from memkraft.hermes_provider import MemKraftMemoryProvider


def _provider(tmp_path: Path) -> MemKraftMemoryProvider:
    provider = MemKraftMemoryProvider()
    provider.initialize("session-private", hermes_home=str(tmp_path))
    return provider


def test_provider_advertises_one_default_auto_feature_bundle(tmp_path):
    provider = _provider(tmp_path)

    assert provider.feature_capabilities() == {
        "contract_version": 1,
        "recall": True,
        "retain": True,
        "adaptive_eta": True,
        "remaining_time": True,
        "phase_learning": True,
        "development_experience": True,
        "installation_integrity": True,
    }


def test_completed_turns_train_eta_without_raw_prompt_or_session_id(tmp_path):
    provider = _provider(tmp_path)
    store = provider._store
    assert store is not None

    for turn, elapsed in enumerate([60_000, 90_000, 120_000, 180_000, 240_000], 1):
        provider.on_turn_timing_start(
            turn,
            session_id="RAW-private-session",
            platform="telegram",
            subject="development",
        )
        state = provider._timing_turns[turn]
        state["started_monotonic"] -= elapsed / 1000
        provider.on_turn_progress(turn, phase="wait", iteration=turn)
        provider.on_turn_progress(turn, phase="active", iteration=turn)
        provider.on_turn_finish(turn, outcome="completed")

    estimate = provider.estimate_turn(platform="telegram", subject="development")
    assert estimate is not None
    assert estimate["sample_count"] == 5
    assert 119_900 <= estimate["p50_ms"] <= 120_100
    assert 179_900 <= estimate["p80_ms"] <= 180_100
    assert estimate["recommended_ms"] >= estimate["p80_ms"]
    assert estimate["critical_path"] == "development"

    ledger = (Path(store.base_dir) / ".memkraft" / "delay" / "events.jsonl").read_text()
    assert "RAW-private-session" not in ledger
    assert "Fix checkout" not in ledger


def test_failed_and_interrupted_turns_do_not_train_eta(tmp_path):
    provider = _provider(tmp_path)
    outcomes = ["failed", "interrupted", "partial", "completed"]
    for turn, outcome in enumerate(outcomes, 1):
        provider.on_turn_timing_start(turn, session_id="s", platform="telegram", subject="research")
        provider._timing_turns[turn]["started_monotonic"] -= turn
        provider.on_turn_finish(turn, outcome=outcome)

    assert provider.estimate_turn(platform="telegram", subject="research") is None


def test_progress_transitions_close_child_before_parent_and_abort_is_idempotent(tmp_path):
    provider = _provider(tmp_path)
    provider.on_turn_timing_start(1, session_id="s", platform="telegram", subject="operations")
    provider.on_turn_progress(1, phase="wait", iteration=1)
    provider.on_turn_progress(1, phase="rework", iteration=1)
    provider.abort_open_turns()
    provider.abort_open_turns()

    assert provider._timing_turns == {}
    provider.on_turn_timing_start(2, session_id="s", platform="telegram", subject="operations")
    provider.on_turn_finish(2, outcome="completed")
    assert provider._timing_turns == {}


def test_installation_smoke_reports_capabilities_and_round_trip(tmp_path):
    provider = _provider(tmp_path)
    provider.installation_report = lambda: {"consistent": True, "package_version": "test"}
    report = provider.integration_report(run_smoke=True)

    assert report["ready"] is True
    assert report["installation"]["consistent"] is True
    assert report["capabilities"]["adaptive_eta"] is True
    assert report["smoke"]["timing_round_trip"] is True
