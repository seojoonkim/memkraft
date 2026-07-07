"""Tests for store_core S1 — envelope v1 append/read on JSONL (no tombstones)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from memkraft.store_core import append, read_all


class TestAppendReadRoundtrip:
    def test_append_then_read_all_returns_record(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        append(path, {"kind": "note", "text": "hello"})
        result = read_all(path)
        assert len(result.records) == 1
        assert result.records[0]["text"] == "hello"
        assert result.skipped == 0

    def test_multiple_appends_preserve_order(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        for i in range(3):
            append(path, {"seq": i})
        result = read_all(path)
        assert [r["seq"] for r in result.records] == [0, 1, 2]


class TestEnvelopeFields:
    def test_envelope_fields_auto_filled(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        returned = append(path, {"text": "payload stays"})
        rec = read_all(path).records[0]

        assert rec["schema_version"] == 1
        assert isinstance(rec["id"], str) and rec["id"]
        # created_at is UTC ISO-8601 and parseable
        parsed = datetime.datetime.fromisoformat(rec["created_at"])
        assert parsed.utcoffset() == datetime.timedelta(0)
        # payload preserved
        assert rec["text"] == "payload stays"
        # append returns the enveloped record it wrote
        assert returned["id"] == rec["id"]

    def test_caller_supplied_envelope_fields_preserved(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        append(
            path,
            {
                "id": "fixed-id",
                "created_at": "2026-01-02T03:04:05+00:00",
                "text": "x",
            },
        )
        rec = read_all(path).records[0]
        assert rec["id"] == "fixed-id"
        assert rec["created_at"] == "2026-01-02T03:04:05+00:00"

    def test_caller_supplied_schema_version_normalized_to_1(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        returned = append(path, {"schema_version": 999, "text": "x"})
        rec = read_all(path).records[0]
        # schema_version is envelope metadata: always 1 in envelope v1
        assert returned["schema_version"] == 1
        assert rec["schema_version"] == 1
        # payload untouched
        assert rec["text"] == "x"

    def test_ids_are_unique_across_appends(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        for _ in range(10):
            append(path, {})
        ids = [r["id"] for r in read_all(path).records]
        assert len(set(ids)) == 10


class TestFileCreation:
    def test_append_creates_missing_parent_dirs_and_file(self, tmp_path: Path):
        path = tmp_path / ".memkraft" / "nested" / "store.jsonl"
        assert not path.parent.exists()
        append(path, {"text": "first"})
        assert path.is_file()
        assert len(read_all(path).records) == 1

    def test_read_all_on_missing_file_returns_empty(self, tmp_path: Path):
        result = read_all(tmp_path / "absent.jsonl")
        assert result.records == []
        assert result.skipped == 0


class TestCorruptLines:
    def test_corrupt_lines_skipped_and_counted(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        append(path, {"seq": 0})
        append(path, {"seq": 1})
        # inject a truncated JSON line between valid records
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text(
            lines[0] + '{"seq": 99, "trunca' + "\n" + lines[1],
            encoding="utf-8",
        )
        result = read_all(path)
        assert [r["seq"] for r in result.records] == [0, 1]
        assert result.skipped == 1


class TestWireFormat:
    def test_append_writes_single_newline_terminated_line(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        append(path, {"text": "line one"})
        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        assert raw.count(b"\n") == 1
        # the line itself is one valid JSON object with no interior newline
        line = raw[:-1]
        assert b"\n" not in line
        assert json.loads(line)["text"] == "line one"

    def test_second_append_adds_exactly_one_line(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        append(path, {"n": 1})
        append(path, {"n": 2})
        raw = path.read_bytes()
        assert raw.count(b"\n") == 2
        assert raw.endswith(b"\n")
