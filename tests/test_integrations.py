from __future__ import annotations

from memkraft.integrations import integration_report


def test_integration_report_proves_live_bridge_health(tmp_path):
    report = integration_report(str(tmp_path))

    assert report["ok"] is True
    assert report["checks"]["bridge"]["health"]["ok"] is True
    assert report["checks"]["generic_agents"]["transport"] == "json-stdio"
    assert report["checks"]["mcp"]["module_available"] is True


def test_integration_report_distinguishes_hermes_entry_point(tmp_path):
    report = integration_report(str(tmp_path))

    hermes = report["checks"]["hermes"]
    assert hermes["group"] == "hermes_agent.memory_providers"
    assert hermes["name"] == "memkraft"
    assert isinstance(hermes["available"], bool)
