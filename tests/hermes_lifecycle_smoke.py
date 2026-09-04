"""Installed-wheel Hermes discovery and lifecycle smoke used by CI/release gates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", required=True, type=Path)
    parser.add_argument("--discovery", required=True, choices=("directory", "entrypoint"))
    args = parser.parse_args()
    home = args.hermes_home.resolve()
    os.environ["HERMES_HOME"] = str(home)
    os.environ["MEMKRAFT_DIR"] = str(home / "memkraft")

    if args.discovery == "directory":
        from memkraft.hermes_install import install_bridge
        install_bridge(home, hermes_version="0.19.0")
    else:
        bridge = home / "plugins" / "memkraft"
        assert not bridge.exists(), "directory bridge would mask entry-point discovery"

    from agent.memory_manager import MemoryManager
    from memkraft.hermes_provider import MemKraftMemoryProvider
    from plugins import memory

    if args.discovery == "directory":
        # Hermes 0.19 is directory-only. Prove the selected provider is the
        # profile bridge rather than any bundled provider with the same name.
        expected_bridge = (home / "plugins" / "memkraft").resolve()
        assert memory.find_provider_dir("memkraft").resolve() == expected_bridge

    names = memory.list_memory_provider_names()
    assert "memkraft" in names, names
    provider = memory.load_memory_provider("memkraft")
    assert isinstance(provider, MemKraftMemoryProvider), type(provider)

    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all("compat-a", hermes_home=str(home), platform="ci")
    # The pinned Hermes compatibility matrix predates manager-side capability
    # aggregation. Verify the installed provider's additive contract here;
    # current Hermes tests cover automatic manager consumption separately.
    capabilities = provider.feature_capabilities()
    assert capabilities["adaptive_eta"] is True
    assert capabilities["remaining_time"] is True
    assert capabilities["development_experience"] is True
    integration = provider.integration_report(run_smoke=True)
    assert integration["ready"] is True, integration
    assert integration["smoke"]["round_trip"] is True
    manager.sync_all(
        "Grace Hopper leads the compiler project.",
        "I will remember that.",
        session_id="compat-a",
        messages=[],
    )
    assert manager.flush_pending(timeout=10)
    recalled = manager.prefetch_all("Grace Hopper", session_id="compat-a")
    assert "Grace Hopper" in recalled, recalled
    manager.on_session_switch("compat-b", parent_session_id="compat-a")
    assert provider._session_id == "compat-b"
    manager.shutdown_all()

    replacement = memory.load_memory_provider("memkraft")
    replacement.initialize("compat-c", hermes_home=str(home), platform="ci")
    restarted_recall = replacement.prefetch("Grace Hopper", session_id="compat-c")
    assert "Grace Hopper" in restarted_recall, restarted_recall
    print(json.dumps({"discovery": args.discovery, "provider": provider.name, "restart_recall": True}, sort_keys=True))


if __name__ == "__main__":
    main()
