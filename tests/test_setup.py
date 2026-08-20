from __future__ import annotations

import json

from memkraft.setup import setup


def test_setup_is_idempotent_and_writes_portable_manifest(tmp_path):
    first = setup(str(tmp_path / "memory"))
    second = setup(str(tmp_path / "memory"))

    assert first["ok"] is True
    assert first["changed"] is True
    assert second["changed"] is False
    assert first["path"] == second["path"]

    manifest = json.loads((tmp_path / "memory" / "integrations" / "memkraft.json").read_text())
    assert manifest["transport"] == "json-stdio"
    assert manifest["operations"] == ["remember", "recall", "feedback", "health"]
    assert "--base-dir" in manifest["command"]


def test_setup_does_not_claim_native_host_registration(tmp_path):
    result = setup(str(tmp_path / "memory"))
    assert result["manifest"]["setup_policy"].startswith("manifest-only")
    encoded = json.dumps(result).lower()
    assert "api_key" not in encoded
    assert "password" not in encoded
    assert "token" not in encoded


def test_setup_supports_explicit_output(tmp_path):
    output = tmp_path / "custom" / "integration.json"
    result = setup(str(tmp_path / "memory"), str(output))
    assert result["path"] == str(output)
    assert output.exists()
