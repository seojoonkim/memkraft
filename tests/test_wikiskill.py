"""Deterministic experience -> verified wiki -> reusable skill contract."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from memkraft import MemKraft
from memkraft.store_core import read_all, append, mark_tombstone


VERIFIED = {
    "status": "verified",
    "verified_by": "pytest tests/test_retry.py",
    "evidence": ["run:test-retry:exit-0"],
}


def rows(root, name):
    return read_all(root / ".memkraft" / (name + ".jsonl")).records


def test_append_raw_experience_preserves_text_and_metadata(tmp_path):
    mk = MemKraft(str(tmp_path))
    raw = "  Retry only transient failures.\n검증 결과  "
    record = mk.experience_append(raw, provenance_id="source-1", session_id="s1",
                                  verification=VERIFIED)
    assert record["text"] == raw
    assert record["kind"] == "raw_experience"
    assert record["provenance_id"] == "source-1"
    assert record["session_id"] == "s1"
    assert record["verification"] == VERIFIED
    assert record["schema_version"] == 1
    assert record["id"] and record["created_at"]
    assert rows(tmp_path, "experiences") == [record]
    default = mk.experience_append("unreviewed", provenance_id="source-2")
    assert default["verification"] == {"status": "unverified"}
    assert rows(tmp_path, "candidates") == []
    record["verification"]["evidence"].append("local mutation")
    assert rows(tmp_path, "experiences")[0]["verification"] == VERIFIED


@pytest.mark.parametrize("kwargs", [
    {"text": " ", "provenance_id": "p"},
    {"text": "raw", "provenance_id": ""},
    {"text": "raw", "provenance_id": "p", "verification": {"status": "verified"}},
    {"text": "raw", "provenance_id": "p", "verification": {"status": "maybe"}},
])
def test_invalid_experience_writes_nothing(tmp_path, kwargs):
    with pytest.raises(ValueError):
        MemKraft(str(tmp_path)).experience_append(**kwargs)
    assert rows(tmp_path, "experiences") == []


def test_promote_verified_experience_copies_persisted_content(tmp_path):
    mk = MemKraft(str(tmp_path))
    raw = mk.experience_append("Use bounded retry.", provenance_id="p", verification=VERIFIED)
    before = (tmp_path / ".memkraft" / "experiences.jsonl").read_bytes()
    wiki = mk.experience_promote(raw["id"], title="Bounded retry")
    assert wiki["kind"] == "wiki_knowledge"
    assert wiki["experience_id"] == raw["id"]
    assert wiki["text"] == raw["text"]
    assert wiki["title"] == "Bounded retry"
    assert wiki["provenance_id"] == "p"
    assert wiki["verification"] == VERIFIED
    assert rows(tmp_path, "wiki") == [wiki]
    assert (tmp_path / ".memkraft" / "experiences.jsonl").read_bytes() == before


@pytest.mark.parametrize("state", ["unverified", "failed"])
def test_promotion_rejects_unverified_experience(tmp_path, state):
    mk = MemKraft(str(tmp_path))
    raw = mk.experience_append("raw", provenance_id="p", verification={"status": state})
    with pytest.raises(ValueError, match="verified"):
        mk.experience_promote(raw["id"], title="title")
    assert rows(tmp_path, "wiki") == []


def test_promotion_rejects_missing_or_tombstoned_sources(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(KeyError):
        mk.experience_promote("absent", title="title")
    raw = mk.experience_append("raw", provenance_id="p", verification=VERIFIED)
    mark_tombstone(tmp_path / ".memkraft" / "experiences.jsonl", raw["id"])
    with pytest.raises(KeyError):
        mk.experience_promote(raw["id"], title="title")
    assert rows(tmp_path, "wiki") == []


def test_skill_list_verified_filters_invalidated_sources(tmp_path):
    mk = MemKraft(str(tmp_path))
    raw = mk.experience_append("Keep verified procedure.", provenance_id="p", verification=VERIFIED)
    wiki = mk.experience_promote(raw["id"], title="Procedure")
    mk.skill_compile("procedure", wiki_ids=[wiki["id"]])
    assert [s["name"] for s in mk.skill_list_verified()] == ["procedure"]
    mark_tombstone(tmp_path / ".memkraft" / "wiki.jsonl", wiki["id"])
    assert mk.skill_list_verified() == []


def test_compile_skill_uses_only_persisted_verified_wiki_entries(tmp_path):
    mk = MemKraft(str(tmp_path))
    raw = mk.experience_append("Retry transient failures.", provenance_id="p", verification=VERIFIED)
    a = mk.experience_promote(raw["id"], title="Retry")
    raw2 = mk.experience_append("Stop after three attempts.", provenance_id="q", verification=VERIFIED)
    b = mk.experience_promote(raw2["id"], title="Bound")
    skill = mk.skill_compile("bounded-retry", wiki_ids=[b["id"], a["id"], b["id"]])
    assert skill["kind"] == "reusable_skill"
    assert skill["name"] == "bounded-retry"
    assert skill["wiki_ids"] == [b["id"], a["id"]]
    assert skill["steps"] == [b["text"], a["text"]]
    assert skill["provenance_ids"] == ["q", "p"]
    assert skill["experience_ids"] == [raw2["id"], raw["id"]]
    assert skill["source_verifications"] == [VERIFIED, VERIFIED]
    assert rows(tmp_path, "skills") == [skill]
    replay = MemKraft(str(tmp_path)).skill_compile("bounded-retry", wiki_ids=[b["id"], a["id"]])
    assert {k: v for k, v in skill.items() if k not in ("id", "created_at")} == {
        k: v for k, v in replay.items() if k not in ("id", "created_at")}


@pytest.mark.parametrize("verification", [
    {"status": "unverified"}, {"status": "failed"},
    {"status": "verified"}, {"status": True},
])
def test_skill_compile_rejects_unverified_or_malformed_wiki(tmp_path, verification):
    mk = MemKraft(str(tmp_path))
    wiki = append(tmp_path / ".memkraft" / "wiki.jsonl", {
        "kind": "wiki_knowledge", "text": "unsafe", "provenance_id": "p",
        "verification": verification,
    })
    with pytest.raises(ValueError):
        mk.skill_compile("name", wiki_ids=[wiki["id"]])
    assert rows(tmp_path, "skills") == []


@pytest.mark.parametrize("wiki_ids", [[], "not-a-list", ["missing"]])
def test_skill_compile_rejects_empty_invalid_or_missing_sources(tmp_path, wiki_ids):
    with pytest.raises((ValueError, KeyError)):
        MemKraft(str(tmp_path)).skill_compile("name", wiki_ids=wiki_ids)
    assert rows(tmp_path, "skills") == []


def test_skill_compile_cannot_use_raw_experience_or_deleted_wiki(tmp_path):
    mk = MemKraft(str(tmp_path))
    raw = mk.experience_append("raw", provenance_id="p", verification=VERIFIED)
    with pytest.raises(KeyError):
        mk.skill_compile("name", wiki_ids=[raw["id"]])
    wiki = mk.experience_promote(raw["id"], title="title")
    mark_tombstone(tmp_path / ".memkraft" / "wiki.jsonl", wiki["id"])
    with pytest.raises(KeyError):
        mk.skill_compile("name", wiki_ids=[wiki["id"]])
    assert rows(tmp_path, "skills") == []


def test_concurrent_experience_appends_are_complete(tmp_path):
    mk = MemKraft(str(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: mk.experience_append(str(n), provenance_id="p"), range(40)))
    stored = rows(tmp_path, "experiences")
    assert len(stored) == 40
    assert {r["id"] for r in stored} == {r["id"] for r in results}
    assert {r["text"] for r in stored} == {str(n) for n in range(40)}
