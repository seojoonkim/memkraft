"""Bulk tombstones use the same handle as their real platform lock."""
from __future__ import annotations

import subprocess
import sys

import pytest

from memkraft import MemKraft, store_core


def test_bulk_tombstones_preserve_other_records_and_original_bytes(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [
        store_core.append(path, {"text": text})
        for text in ["café", "keep", "한글"]
    ]
    before = path.read_bytes()
    markers = store_core.mark_tombstones(
        path, [rows[2]["id"], rows[0]["id"], rows[0]["id"]]
    )
    assert path.read_bytes().startswith(before)
    assert [m["tombstone_of"] for m in markers] == sorted(
        [rows[0]["id"], rows[2]["id"]]
    )
    assert store_core.read_all(path).records == [rows[1]]
    assert store_core.read_all(path, True).records == rows + markers


def test_missing_target_is_atomic_and_releases_the_lock(tmp_path):
    path = tmp_path / "events.jsonl"
    row = store_core.append(path, {"text": "keep"})
    before = path.read_bytes()
    with pytest.raises(store_core.RecordNotFoundError):
        store_core.mark_tombstones(path, [row["id"], "missing"])
    assert path.read_bytes() == before
    next_row = store_core.append(path, {"text": "next"})
    assert store_core.read_all(path).records == [row, next_row]


def test_empty_targets_do_not_create_a_store(tmp_path):
    path = tmp_path / "missing.jsonl"
    assert store_core.mark_tombstones(path, []) == []
    assert not path.exists()


def test_corrupt_lines_use_the_same_reader_rules(tmp_path):
    path = tmp_path / "events.jsonl"
    row = store_core.append(path, {"text": "forget"})
    with path.open("ab") as f:
        f.write(b"\nnot-json\n[]\n\xff\n")
    before = store_core.read_all(path, True)
    assert before.skipped == 3
    markers = store_core.mark_tombstones(path, [row["id"]])
    result = store_core.read_all(path, True)
    assert result.records == before.records + markers
    assert result.skipped == before.skipped
    assert store_core.read_all(path).records == []


def test_already_tombstoned_targets_still_validate(tmp_path):
    path = tmp_path / "events.jsonl"
    row = store_core.append(path, {"text": "forget"})
    store_core.mark_tombstone(path, row["id"])
    markers = store_core.mark_tombstones(path, [row["id"]])
    assert len(markers) == 1
    assert store_core.read_all(path).records == []


def test_another_process_can_append_after_bulk_tombstones(tmp_path):
    path = tmp_path / "events.jsonl"
    row = store_core.append(path, {"text": "forget"})
    store_core.mark_tombstones(path, [row["id"]])
    code = (
        "from memkraft.store_core import append; import sys; "
        "append(sys.argv[1], {'text':'next'})"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert [r["text"] for r in store_core.read_all(path).records] == ["next"]


@pytest.mark.parametrize("selector", ["subject", "key", "id"])
def test_public_forget_removes_only_the_selected_events(tmp_path, selector):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    first = mk.append_event("person:alice", "tea", "jasmine", source="regression")
    second = mk.append_event("person:alice", "colour", "blue", source="regression")
    other = mk.append_event("person:bob", "tea", "oolong", source="regression")
    if selector == "subject":
        target = {"subject_id": "person:alice"}
    elif selector == "key":
        target = {"subject": "person:alice", "key": "tea"}
    else:
        target = first["id"]
    result = mk.forget(target, dry_run=False)
    assert result["status"] == "applied"
    live = store_core.read_all(mk._canonical_events_path()).records
    expected = (
        {other["id"]} if selector == "subject"
        else {second["id"], other["id"]}
    )
    assert {r["id"] for r in live} == expected


def test_current_do_not_remember_policy_path_is_unchanged(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path / "memory"))
    mk.init(verbose=False)
    result = mk.do_not_remember(subject="person:alice", key="tea", dry_run=False)
    assert result["status"] == "applied"
    again = mk.do_not_remember(subject="person:alice", key="tea", dry_run=False)
    assert again["status"] == "already_applied"
