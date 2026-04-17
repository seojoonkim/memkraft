"""v0.8.1 — mk.init() dict-return contract."""
import tempfile
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import MemKraft


@pytest.fixture
def tmp_base():
    d = tempfile.mkdtemp(prefix="mk-init-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_init_returns_dict(tmp_base):
    mk = MemKraft(base_dir=tmp_base)
    result = mk.init(verbose=False)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"created", "exists", "base_dir"}
    assert result["base_dir"] == tmp_base


def test_init_creates_structure(tmp_base):
    mk = MemKraft(base_dir=tmp_base)
    result = mk.init(verbose=False)
    assert "entities/" in result["created"]
    assert "decisions/" in result["created"]
    assert "inbox/" in result["created"]
    assert "RESOLVER.md" in result["created"]
    assert (Path(tmp_base) / "entities").exists()
    assert (Path(tmp_base) / "RESOLVER.md").exists()


def test_init_idempotent(tmp_base):
    mk = MemKraft(base_dir=tmp_base)
    mk.init(verbose=False)
    second = mk.init(verbose=False)
    # 2nd call: everything should be in 'exists', nothing in 'created'
    assert second["created"] == []
    assert "entities/" in second["exists"]
    assert "RESOLVER.md" in second["exists"]


def test_init_force_recreates_resolver(tmp_base):
    mk = MemKraft(base_dir=tmp_base)
    mk.init(verbose=False)
    # mutate RESOLVER.md
    resolver = Path(tmp_base) / "RESOLVER.md"
    original = resolver.read_text()
    resolver.write_text("MUTATED")
    # force=True should rewrite it
    result = mk.init(force=True, verbose=False)
    assert "RESOLVER.md" in result["created"]
    assert resolver.read_text() == original
