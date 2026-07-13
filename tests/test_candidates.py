"""Tests for candidate memory APIs — MemKraft 2.13 S10."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import json
import pytest

from memkraft import MemKraft
from memkraft.store_core import mark_tombstone, read_all


def test_remember_candidate_appends_enveloped_record_and_returns_candidate_id(tmp_path: Path):
    mk = MemKraft(str(tmp_path))

    result = mk.remember_candidate("Simon prefers concise updates", session_id="s1")

    assert result["candidate_id"]
    records = read_all(tmp_path / ".memkraft" / "candidates.jsonl").records
    assert len(records) == 1
    record = records[0]
    assert record["id"] == result["candidate_id"]
    assert record["candidate_id"] == result["candidate_id"]
    assert record["text"] == "Simon prefers concise updates"
    assert record["session_id"] == "s1"
    assert record["schema_version"] == 1


def test_remember_candidate_fills_expires_at_and_unknown_provenance(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    before = datetime.now(timezone.utc) + timedelta(hours=23, minutes=59)

    result = mk.remember_candidate("Simon uses MemKraft", session_id="s1")

    after = datetime.now(timezone.utc) + timedelta(hours=24, minutes=1)
    record = read_all(tmp_path / ".memkraft" / "candidates.jsonl").records[0]
    expires_at = datetime.fromisoformat(record["expires_at"])
    assert before <= expires_at <= after
    assert record["provenance_id"] == "unknown"
    assert result["provenance_id"] == "unknown"


def test_list_candidates_filters_by_session_and_excludes_expired_by_default(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    live = mk.remember_candidate("live candidate", session_id="s1")
    mk.remember_candidate("expired candidate", session_id="s1", expires_at=expired)
    mk.remember_candidate("other session", session_id="s2")

    records = mk.list_candidates(session_id="s1")
    assert [r["candidate_id"] for r in records] == [live["candidate_id"]]

    all_s1 = mk.list_candidates(session_id="s1", include_expired=True)
    assert [r["text"] for r in all_s1] == ["live candidate", "expired candidate"]


def test_list_candidates_hides_tombstoned_candidates(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    keep = mk.remember_candidate("keep", session_id="s1")
    drop = mk.remember_candidate("drop", session_id="s1")

    mark_tombstone(tmp_path / ".memkraft" / "candidates.jsonl", drop["candidate_id"])

    assert [r["candidate_id"] for r in mk.list_candidates(session_id="s1")] == [
        keep["candidate_id"]
    ]


def test_s10_candidates_start_with_claims_when_extractor_matches(tmp_path: Path):
    mk = MemKraft(str(tmp_path))

    mk.remember_candidate("Simon prefers concise updates", session_id="s1", entity_hint="Simon")

    record = mk.list_candidates(session_id="s1")[0]
    assert record["claims"]
    assert record["review_state"] == "READY_FOR_RESOLVER"


def test_policy_filters_only_matching_structured_candidate_claims(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    denied = mk.remember_candidate("Simon prefers concise updates", session_id="s1")
    other_key = mk.remember_candidate("Simon uses MemKraft", session_id="s1")
    free_text = mk.remember_candidate("Simon confidential review note", session_id="s1")
    mk.do_not_remember(subject="Simon", key="prefers", dry_run=False)
    visible = {row["candidate_id"] for row in mk.list_candidates(session_id="s1")}
    assert denied["candidate_id"] not in visible
    assert other_key["candidate_id"] in visible
    assert free_text["candidate_id"] in visible


def test_forget_candidates_dry_run_apply_and_idempotent_audit(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    receipt = mk.remember_candidate("free text review note", session_id="s1")
    path = tmp_path / ".memkraft" / "candidates.jsonl"
    before = path.read_bytes()
    plan = mk.forget_candidates(candidate_id=receipt["candidate_id"])
    assert plan["matched"] == 1 and plan["status"] == "planned"
    assert plan["candidate_ids"] == [receipt["candidate_id"]]
    assert path.read_bytes() == before
    applied = mk.forget_candidates(candidate_id=receipt["candidate_id"], dry_run=False)
    retry = mk.forget_candidates(candidate_id=receipt["candidate_id"], dry_run=False)
    assert applied["status"] == "applied"
    assert retry["status"] == "already_forgotten"
    assert mk.list_candidates(session_id="s1") == []
    assert len(mk.audit_log(action="forget_candidates")) == 1
    json.dumps(applied)


def test_forget_candidates_session_selector_explicitly_removes_free_text(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    a = mk.remember_candidate("free text one", session_id="s1")
    b = mk.remember_candidate("free text two", session_id="s1")
    mk.remember_candidate("keep", session_id="s2")
    result = mk.forget_candidates(session_id="s1", dry_run=False)
    assert result["candidate_ids"] == [a["candidate_id"], b["candidate_id"]]
    assert result["matched"] == 2
    assert [row["text"] for row in mk.list_candidates()] == ["keep"]


def test_forget_candidates_requires_exactly_one_selector(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError):
        mk.forget_candidates()
    with pytest.raises(ValueError):
        mk.forget_candidates(candidate_id="a", session_id="s")


def test_compact_memory_preserves_visible_reads_and_dry_run_writes_nothing(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    event = mk.append_event("u", "city", "Seoul", source="test")
    mk.append_event("u", "lang", "ko", source="test")
    candidate = mk.remember_candidate("free text", session_id="s")
    mk.remember_candidate("keep candidate", session_id="s")
    mk.sleep(dry_run=False)
    mk.forget(event["id"], dry_run=False)
    mk.forget_candidates(candidate_id=candidate["candidate_id"], dry_run=False)
    paths = [tmp_path / ".memkraft" / name for name in ("events.jsonl", "candidates.jsonl")]
    bytes_before = [path.read_bytes() for path in paths]
    visible_before = (mk.export_memory(), mk.timeline(), mk.current_truth("u"), mk.list_candidates())
    plan = mk.compact_memory()
    assert plan["status"] == "planned"
    assert [path.read_bytes() for path in paths] == bytes_before
    assert all(store["removed_tombstoned"] == 1 and store["removed_markers"] == 1
               for store in plan["stores"].values())
    result = mk.compact_memory(dry_run=False)
    assert result["status"] == "applied"
    assert all(store["removed_tombstoned"] == 1 and store["removed_markers"] == 1
               for store in result["stores"].values())
    assert (mk.export_memory(), mk.timeline(), mk.current_truth("u"), mk.list_candidates()) == visible_before


def test_compact_memory_missing_sidecars_is_noop_without_creating_files(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    result = mk.compact_memory(dry_run=False)
    assert result["status"] == "applied"
    assert all(store == {"kept": 0, "removed_tombstoned": 0,
                         "removed_markers": 0, "removed_corrupt": 0}
               for store in result["stores"].values())
    assert not (tmp_path / ".memkraft").exists()


def test_compact_memory_dry_run_matches_apply_with_invalid_utf8_and_writes_nothing(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    dropped = mk.remember_candidate("drop candidate", session_id="s")
    mk.remember_candidate("keep candidate", session_id="s")
    mk.forget_candidates(candidate_id=dropped["candidate_id"], dry_run=False)
    path = tmp_path / ".memkraft" / "candidates.jsonl"
    with path.open("ab") as stream:
        stream.write(b"\xff invalid utf-8 json\n")
    before = path.read_bytes()

    dry = mk.compact_memory()

    assert path.read_bytes() == before
    assert dry["stores"]["candidates.jsonl"] == {
        "kept": 1,
        "removed_tombstoned": 1,
        "removed_markers": 1,
        "removed_corrupt": 1,
    }
    applied = mk.compact_memory(dry_run=False)
    assert dry["stores"] == applied["stores"]


def test_compact_memory_racing_candidate_append_preserves_live_records(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    dropped = mk.remember_candidate("drop", session_id="s")
    mk.forget_candidates(candidate_id=dropped["candidate_id"], dry_run=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        compact_future = pool.submit(mk.compact_memory, False)
        append_future = pool.submit(mk.remember_candidate, "racing live", session_id="s")
        assert compact_future.result()["status"] == "applied"
        appended = append_future.result()

    assert appended["candidate_id"] in {
        row["candidate_id"] for row in mk.list_candidates(session_id="s")
    }
