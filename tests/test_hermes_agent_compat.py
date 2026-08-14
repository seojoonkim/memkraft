"""Compatibility contract for Hermes Agent's external memory-provider API."""

import importlib.metadata
import json
from pathlib import Path

import pytest


def test_distribution_exposes_hermes_memory_provider_entry_point():
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, "select"):
        matches = list(entry_points.select(
            group="hermes_agent.memory_providers", name="memkraft"
        ))
    else:  # Python 3.9's legacy importlib.metadata API
        matches = [
            entry_point
            for entry_point in entry_points.get("hermes_agent.memory_providers", [])
            if entry_point.name == "memkraft"
        ]

    declarations = {
        (entry_point.group, entry_point.name, entry_point.value)
        for entry_point in matches
    }
    assert declarations == {
        (
            "hermes_agent.memory_providers",
            "memkraft",
            "memkraft.hermes_provider:register",
        )
    }
def test_provider_runs_through_hermes_v0200_manager_contract(tmp_path, monkeypatch):
    pytest.importorskip("agent.memory_provider", reason="Hermes Agent compatibility suite")
    from agent.memory_manager import MemoryManager
    from agent.memory_provider import MemoryProvider
    from memkraft.hermes_provider import MemKraftMemoryProvider
    from plugins import memory

    # Isolate the standalone-distribution contract even in Hermes source trees
    # that happen to bundle a provider with the same name. Discovery and target
    # loading still run through Hermes' real importlib.metadata path.
    empty_bundled_dir = tmp_path / "empty-bundled-memory-providers"
    empty_bundled_dir.mkdir()
    monkeypatch.setattr(memory, "_MEMORY_PLUGINS_DIR", empty_bundled_dir)
    monkeypatch.setattr(memory, "_get_user_plugins_dir", lambda: None)
    provider = memory.load_memory_provider("memkraft")

    assert isinstance(provider, MemoryProvider)
    assert isinstance(provider, MemKraftMemoryProvider)
    assert provider.name == "memkraft"
    assert provider.is_available()

    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all(
        "session-a", hermes_home=str(tmp_path), platform="test"
    )
    manager.sync_all(
        "Ada Lovelace leads the Analytical Engine project.",
        "I will remember that.",
        session_id="session-a",
        messages=[
            {"role": "user", "content": "Ada Lovelace leads the project."},
            {"role": "assistant", "content": "I will remember that."},
        ],
    )
    assert manager.flush_pending(timeout=5)

    recalled = manager.prefetch_all("Ada Lovelace", session_id="session-a")
    assert "MemKraft recall:" in recalled
    assert "Ada Lovelace" in recalled
    assert manager.get_all_tool_schemas() == []

    manager.on_session_switch("session-b", parent_session_id="session-a")
    assert provider._session_id == "session-b"
    result = json.loads(manager.handle_tool_call("not-a-tool", {}))
    assert "not-a-tool" in result["error"]
    manager.shutdown_all()


def test_provider_uses_profile_scoped_default_storage(tmp_path):
    pytest.importorskip("agent.memory_provider", reason="Hermes Agent compatibility suite")
    from memkraft.hermes_provider import MemKraftMemoryProvider

    provider = MemKraftMemoryProvider()
    provider.initialize("session", hermes_home=str(tmp_path), platform="test")

    assert provider._store is not None
    assert provider._store.base_dir == Path(tmp_path) / "memkraft"
    assert (Path(tmp_path) / "memkraft" / "entities").is_dir()