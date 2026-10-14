"""Owner ledger is shared through MEMKRAFT_OWNER_DIR, not only inside the Hermes provider (4.3.1)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from memkraft import MemKraft

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_plain_store_uses_shared_owner_dir_from_env(tmp_path, monkeypatch):
    shared = tmp_path / "shared-owner"
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(shared))
    writer = MemKraft(base_dir=str(tmp_path / "profile-a"))
    reader = MemKraft(base_dir=str(tmp_path / "profile-b"))
    writer.saying_record("앞으로 작업 보고 끝에 남은 백로그를 보여줘", scope="all")
    assert writer.owner_dir == shared
    assert (shared / "sayings.jsonl").exists()
    assert not (tmp_path / "profile-a" / "owner").exists()
    hits = reader.saying_search("작업 보고 백로그")
    assert hits and hits[0]["text"] == "앞으로 작업 보고 끝에 남은 백로그를 보여줘"


def test_explicit_owner_dir_still_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(tmp_path / "env-owner"))
    store = MemKraft(base_dir=str(tmp_path / "mem"))
    store.owner_dir = tmp_path / "explicit"
    assert store.owner_dir == tmp_path / "explicit"


def test_without_env_owner_dir_stays_per_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_OWNER_DIR", raising=False)
    store = MemKraft(base_dir=str(tmp_path / "mem"))
    assert store.owner_dir == tmp_path / "mem" / "owner"


def _cli(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=SRC, MEMKRAFT_DIR=str(tmp_path / "mem"),
               MEMKRAFT_OWNER_DIR=str(tmp_path / "owner"))
    return subprocess.run([sys.executable, "-m", "memkraft.cli", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_owner_cli_record_search_where_decisions(tmp_path):
    rec = _cli(tmp_path, "owner", "record", "결론부터 말해", "--scope", "all", "--tag", "style")
    assert rec.returncode == 0, rec.stderr
    assert json.loads(rec.stdout.strip().splitlines()[-1])["created"] is True
    found = _cli(tmp_path, "owner", "search", "결론")
    assert found.returncode == 0 and "결론부터 말해" in found.stdout
    where = _cli(tmp_path, "owner", "where")
    assert where.stdout.strip().endswith("owner")
    dec = _cli(tmp_path, "owner", "decisions")
    assert dec.returncode == 0 and "no open decisions" in dec.stdout
