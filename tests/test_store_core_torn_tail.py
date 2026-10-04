"""A reader racing an in-flight append must not report the torn tail as corrupt."""
from __future__ import annotations

import json
from pathlib import Path

from memkraft.store_core import append, read_all


def test_unterminated_partial_tail_is_in_flight_not_corrupt(tmp_path: Path):
    path = tmp_path / "store.jsonl"
    first = append(path, {"kind": "a"})
    with open(path, "ab") as handle:
        handle.write(b'{"schema_version":1,"id":"half')  # writer mid-flight
    result = read_all(path)
    assert result.skipped == 0
    assert [r["id"] for r in result.records] == [first["id"]]


def test_unterminated_complete_tail_is_still_read(tmp_path: Path):
    path = tmp_path / "store.jsonl"
    append(path, {"kind": "a"})
    with open(path, "ab") as handle:
        handle.write(json.dumps({"schema_version": 1, "id": "manual"}).encode())
    result = read_all(path)
    assert result.skipped == 0
    assert result.records[-1]["id"] == "manual"


def test_unterminated_non_json_tail_is_still_corrupt(tmp_path: Path):
    path = tmp_path / "store.jsonl"
    path.write_bytes(b"x" * 40)  # replaced/garbage file, not an append
    result = read_all(path)
    assert result.skipped == 1
    assert result.records == []


def test_terminated_corrupt_line_is_still_counted(tmp_path: Path):
    path = tmp_path / "store.jsonl"
    append(path, {"kind": "a"})
    with open(path, "ab") as handle:
        handle.write(b'{"broken\n')
    append(path, {"kind": "b"})
    result = read_all(path)
    assert result.skipped == 1
    assert len(result.records) == 2


def test_append_completes_short_writes(tmp_path: Path, monkeypatch):
    import memkraft.store_core as sc

    real_write = sc.os.write

    def short_write(fd, data):
        return real_write(fd, bytes(data[:7]))

    monkeypatch.setattr(sc.os, "write", short_write)
    rec = append(tmp_path / "s.jsonl", {"payload": "x" * 50})
    monkeypatch.undo()
    result = read_all(tmp_path / "s.jsonl")
    assert result.skipped == 0
    assert result.records[0]["id"] == rec["id"]
