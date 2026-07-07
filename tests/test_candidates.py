"""Tests for candidate memory APIs — MemKraft 2.13 S10."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_s10_candidates_do_not_extract_claims_yet(tmp_path: Path):
    mk = MemKraft(str(tmp_path))

    mk.remember_candidate("Simon prefers concise updates", session_id="s1", entity_hint="Simon")

    record = mk.list_candidates(session_id="s1")[0]
    assert record["claims"] == []
