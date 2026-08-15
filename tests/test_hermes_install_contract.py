"""Hermes Agent installation and support-matrix release contract."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_hermes_plugin.py"
DOC = REPO_ROOT / "docs" / "HERMES_AGENT.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hermes-compat.yml"


def _load_installer():
    spec = importlib.util.spec_from_file_location("_install_hermes_plugin", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_019_installer_writes_scanner_visible_profile_bridge(tmp_path):
    module = _load_installer()
    report = module.install_bridge(tmp_path, hermes_version="0.19.0")

    bridge = tmp_path / "plugins" / "memkraft" / "__init__.py"
    content = bridge.read_text(encoding="utf-8")
    assert report == {"action": "installed", "hermes_home": str(tmp_path), "path": str(bridge)}
    assert "from memkraft.hermes_provider import MemKraftMemoryProvider" in content
    assert "def register(ctx):" in content
    assert "ctx.register_memory_provider(MemKraftMemoryProvider())" in content


def test_installer_is_idempotent_and_refuses_unowned_collision(tmp_path):
    module = _load_installer()
    first = module.install_bridge(tmp_path, hermes_version="0.19.0")
    second = module.install_bridge(tmp_path, hermes_version="0.19.0")
    assert first["action"] == "installed"
    assert second["action"] == "unchanged"

    bridge = tmp_path / "plugins" / "memkraft" / "__init__.py"
    bridge.write_text("UNTRUSTED = True\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        module.install_bridge(tmp_path, hermes_version="0.19.0")
    assert bridge.read_text(encoding="utf-8") == "UNTRUSTED = True\n"


def test_installer_refuses_dangling_symlink_without_touching_target(tmp_path):
    module = _load_installer()
    bridge_dir = tmp_path / "plugins" / "memkraft"
    bridge_dir.mkdir(parents=True)
    external = tmp_path.parent / "external-plugin-target.py"
    bridge = bridge_dir / "__init__.py"
    bridge.symlink_to(external)

    with pytest.raises(RuntimeError, match="symbolic link"):
        module.install_bridge(tmp_path, hermes_version="0.19.0")

    assert bridge.is_symlink()
    assert not external.exists()


def test_installer_refuses_existing_unowned_plugin_directory(tmp_path):
    module = _load_installer()
    bridge_dir = tmp_path / "plugins" / "memkraft"
    bridge_dir.mkdir(parents=True)
    foreign = bridge_dir / "foreign.py"
    foreign.write_text("FOREIGN = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="existing unowned plugin directory"):
        module.install_bridge(tmp_path, hermes_version="0.19.0")

    assert foreign.read_text(encoding="utf-8") == "FOREIGN = True\n"
    assert not (bridge_dir / "__init__.py").exists()


def test_installer_refuses_symlinked_home_and_plugins_ancestors(tmp_path):
    module = _load_installer()
    outside_home = tmp_path / "outside-home"
    outside_home.mkdir()
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(outside_home, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symbolic link|secure"):
        module.install_bridge(linked_home, hermes_version="0.19.0")
    assert not (outside_home / "plugins" / "memkraft" / "__init__.py").exists()

    real_home = tmp_path / "real-home"
    real_home.mkdir()
    outside_plugins = tmp_path / "outside-plugins"
    outside_plugins.mkdir()
    (real_home / "plugins").symlink_to(outside_plugins, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symbolic link|secure"):
        module.install_bridge(real_home, hermes_version="0.19.0")
    assert not (outside_plugins / "memkraft" / "__init__.py").exists()


def test_installer_refuses_insecure_preseeded_bridge(tmp_path):
    module = _load_installer()
    bridge_dir = tmp_path / "plugins" / "memkraft"
    bridge_dir.mkdir(parents=True)
    bridge = bridge_dir / "__init__.py"
    bridge_source = module.install_bridge.__globals__["_BRIDGE"]
    bridge.write_text(bridge_source, encoding="utf-8")
    bridge.chmod(0o666)

    with pytest.raises(RuntimeError, match="insecure|unowned"):
        module.install_bridge(tmp_path, hermes_version="0.19.0")


def test_installer_refuses_world_writable_plugins_directory(tmp_path):
    module = _load_installer()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    plugins.chmod(0o777)

    with pytest.raises(RuntimeError, match="insecure plugins directory"):
        module.install_bridge(tmp_path, hermes_version="0.19.0")
    assert not (plugins / "memkraft" / "__init__.py").exists()


def test_installer_rejects_unsupported_or_entrypoint_versions(tmp_path):
    module = _load_installer()
    with pytest.raises(ValueError, match="0.19.0"):
        module.install_bridge(tmp_path, hermes_version="0.18.0")
    with pytest.raises(ValueError, match="entry-point"):
        module.install_bridge(tmp_path, hermes_version="0.20.1")


def test_release_contract_pins_exact_matrix_and_user_steps():
    doc = DOC.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    manifest = json.loads((REPO_ROOT / "release_manifest.json").read_text(encoding="utf-8"))

    assert "Hermes Agent 0.19.0" in doc
    assert "3ef6bbd201263d354fd83ec55b3c306ded2eb72a" in doc
    assert "45af7a71fcd420b4422d2c074b1ce58b9ce0d048" in doc
    assert "Python 3.11 and 3.12" in doc
    assert "install_hermes_plugin.py" in doc
    assert "hermes config set memory.provider memkraft" in doc
    assert "not a claim of compatibility with every Hermes release" in doc
    assert "docs/HERMES_AGENT.md" in readme

    assert workflow.count('python-version: "3.11"') == 2
    assert workflow.count('python-version: "3.12"') == 2
    assert workflow.count('hermes-ref: "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"') == 2
    assert workflow.count('hermes-ref: "45af7a71fcd420b4422d2c074b1ce58b9ce0d048"') == 2
    assert "python -m build --wheel" in workflow
    assert "dist/*.whl" in workflow
    assert "tests/hermes_lifecycle_smoke.py" in workflow

    release_paths = set(manifest["release"]["release_paths"])
    assert {
        ".github/workflows/hermes-compat.yml",
        "docs/HERMES_AGENT.md",
        "scripts/install_hermes_plugin.py",
        "tests/hermes_lifecycle_smoke.py",
        "tests/test_hermes_install_contract.py",
    } <= release_paths
