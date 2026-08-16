"""Compatibility contract for Hermes Agent's external memory-provider API."""

import importlib.metadata
import json
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    import tomli as tomllib


def test_project_declares_hermes_memory_provider_entry_point():
    project_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    declarations = config["project"]["entry-points"]["hermes_agent.memory_providers"]
    assert declarations == {"memkraft": "memkraft.hermes_provider:register"}


def test_provider_runs_through_hermes_v0200_manager_contract(tmp_path):
    pytest.importorskip("agent.memory_provider", reason="Hermes Agent compatibility suite")
    from agent.memory_manager import MemoryManager
    from agent.memory_provider import MemoryProvider
    from memkraft.hermes_provider import MemKraftMemoryProvider

    # Hermes 0.20's directory loader does not discover distribution entry points.
    # Exercise the standalone wheel contract through importlib.metadata, then pass
    # the loaded provider through Hermes' real MemoryManager lifecycle.
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, "select"):
        matches = list(entry_points.select(
            group="hermes_agent.memory_providers", name="memkraft"
        ))
    else:
        matches = [
            entry_point
            for entry_point in entry_points.get("hermes_agent.memory_providers", [])
            if entry_point.name == "memkraft"
        ]
    assert matches

    class _RegistrationContext:
        def __init__(self):
            self.provider = None

        def register_memory_provider(self, provider):
            self.provider = provider

    context = _RegistrationContext()
    matches[0].load()(context)
    provider = context.provider

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
    )

    recalled = manager.prefetch_all("Ada Lovelace", session_id="session-a")
    assert "MemKraft recall:" in recalled
    assert "Ada Lovelace" in recalled
    assert manager.get_all_tool_schemas() == []

    manager.on_session_switch("session-b", parent_session_id="session-a")
    assert provider._session_id == "session-b"
    result = json.loads(manager.handle_tool_call("not-a-tool", {}))
    assert "not-a-tool" in result["error"]
    manager.shutdown_all()


def test_provider_uses_profile_scoped_default_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_DIR", raising=False)
    pytest.importorskip("agent.memory_provider", reason="Hermes Agent compatibility suite")
    from memkraft.hermes_provider import MemKraftMemoryProvider

    provider = MemKraftMemoryProvider()
    provider.initialize("session", hermes_home=str(tmp_path), platform="test")

    assert provider._store is not None
    assert provider._store.base_dir == Path(tmp_path) / "memkraft"
    assert (Path(tmp_path) / "memkraft" / "entities").is_dir()