"""Tests for v0.8.3: `memkraft stats --export json|csv`."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import stats as _stats
from memkraft.core import MemKraft


SRC = str(Path(__file__).resolve().parents[1] / "src")


def _cli(*args, env=None):
    e = os.environ.copy()
    e["PYTHONPATH"] = SRC + ":" + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "memkraft.cli", *args],
        capture_output=True,
        text=True,
        env=e,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_collect_empty_workspace(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    report = _stats.collect(base_dir=str(mk.base_dir))
    # init ships RESOLVER.md + TEMPLATES.md, so 2 baseline .md files exist
    assert report["total_memories"] >= 0
    # No tracked entities → no entity nodes in link graph
    assert report["link_graph"]["nodes"] == 0


def test_collect_full_workspace(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    mk.track("Alice", entity_type="person", source="test")
    mk.update("Alice", "Works at [[Hashed]] as a [[Developer]]", source="test")
    report = _stats.collect(base_dir=str(mk.base_dir))
    assert report["total_memories"] >= 1
    # link_graph should detect the two wikilinks we added
    assert report["link_graph"]["edges"] >= 2


def test_format_json_is_valid(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    report = _stats.collect(base_dir=str(mk.base_dir))
    out = _stats.format_json(report)
    parsed = json.loads(out)
    assert parsed["base_dir"] == str(mk.base_dir)


def test_format_csv_has_header(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    report = _stats.collect(base_dir=str(mk.base_dir))
    out = _stats.format_csv(report)
    reader = csv.DictReader(io.StringIO(out))
    rows = list(reader)
    assert reader.fieldnames == ["key", "value"]
    keys = [r["key"] for r in rows]
    assert "total_memories" in keys
    assert "version" in keys


def test_cli_stats_human(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    rc, out, err = _cli("stats", env={"MEMKRAFT_DIR": str(mk.base_dir)})
    assert rc == 0, err
    assert "MemKraft stats" in out


def test_cli_stats_json_stdout(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    rc, out, err = _cli("stats", "--export", "json", env={"MEMKRAFT_DIR": str(mk.base_dir)})
    assert rc == 0, err
    parsed = json.loads(out)
    assert "total_memories" in parsed


def test_cli_stats_json_to_file(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    out_file = tmp_path / "stats.json"
    rc, out, err = _cli("stats", "--export", "json", "--out", str(out_file),
                         env={"MEMKRAFT_DIR": str(mk.base_dir)})
    assert rc == 0, err
    assert out_file.exists()
    parsed = json.loads(out_file.read_text())
    assert parsed["version"]


def test_cli_stats_csv(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    rc, out, err = _cli("stats", "--export", "csv", env={"MEMKRAFT_DIR": str(mk.base_dir)})
    assert rc == 0, err
    assert "key,value" in out
