from pathlib import Path

from memkraft.hermes_provider import MemKraftMemoryProvider


def test_completed_turn_is_persisted_and_recalled_on_next_turn(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    provider = MemKraftMemoryProvider()
    provider.initialize(
        "session-a",
        hermes_home=str(hermes_home),
        platform="cli",
        agent_context="primary",
        agent_identity="test",
    )

    provider.sync_turn(
        "My compatibility codename is QuartzFalcon.",
        "I will remember that.",
        session_id="session-a",
        messages=[
            {"role": "user", "content": "My compatibility codename is QuartzFalcon."},
            {"role": "assistant", "content": "I will remember that."},
        ],
    )

    recall = provider.prefetch("QuartzFalcon", session_id="session-a")

    assert "QuartzFalcon" in recall
    assert any(path.is_file() for path in (hermes_home / "memkraft").rglob("*.md"))


def test_sync_turn_uses_switched_session_when_caller_omits_session_id(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    provider = MemKraftMemoryProvider()
    provider.initialize("session-a", hermes_home=str(hermes_home))
    provider.on_session_switch("session-b", parent_session_id="session-a")

    provider.sync_turn(
        "The second compatibility codename is EmberOtter.",
        "Recorded.",
    )

    recall = provider.prefetch("EmberOtter", session_id="session-b")
    assert "EmberOtter" in recall
