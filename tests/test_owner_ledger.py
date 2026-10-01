"""Owner ledger: verbatim owner sayings + an irreversible-only decision queue."""
from __future__ import annotations

import json

import pytest

from memkraft import MemKraft
from memkraft.owner_ledger import IRREVERSIBLE_CATEGORIES, OwnerLedgerError


@pytest.fixture
def mk(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "mem"))
    store.owner_dir = tmp_path / "owner"
    return store


def test_saying_is_stored_verbatim_and_idempotent(mk):
    text = "알아서 다 해 나한테 시키지마"
    first = mk.saying_record(text, scope="all", tags=("autonomy",), source="telegram:2026-09-27")
    again = mk.saying_record("  알아서 다 해   나한테 시키지마 ", scope="all")
    assert first["created"] is True
    assert again["created"] is False
    assert again["id"] == first["id"]
    hits = mk.saying_search("명령어를 형한테 시켜도 돼?")
    assert hits and hits[0]["text"] == text


def test_saying_scope_filter_and_retire(mk):
    a = mk.saying_record("MemKraft PR은 CI 통과하면 알아서 머지해", scope="zeon")
    mk.saying_record("사노는 건강 일정을 챙겨", scope="sano")
    assert [h["id"] for h in mk.saying_search("MemKraft 머지", scope="zeon")] == [a["id"]]
    assert mk.saying_search("MemKraft 머지", scope="sano") == []
    mk.saying_retire(a["id"], "superseded")
    assert mk.saying_search("MemKraft 머지", scope="zeon") == []
    with pytest.raises(OwnerLedgerError):
        mk.saying_retire("nope", "x")


def test_reversible_request_is_not_queued(mk):
    result = mk.decision_enqueue("README 오타 고칠까?", category="docs",
                                 requester="sion", reversible=True)
    assert result == {"queued": False, "action": "proceed"}
    assert mk.decision_list() == []


def test_irreversible_request_is_queued_deduped_and_resolved(mk):
    assert "money" in IRREVERSIBLE_CATEGORIES
    q = mk.decision_enqueue("유료 플랜으로 업그레이드할까?", category="money",
                            requester="sano", options=("yes", "no"), reversible=True)
    dup = mk.decision_enqueue("유료 플랜으로  업그레이드할까? ", category="money", requester="sano")
    assert q["queued"] is True and dup["id"] == q["id"] and dup["created"] is False
    digest = mk.decision_digest()
    assert "sano" in digest and "유료 플랜" in digest
    mk.decision_resolve(q["id"], "no", resolved_by="simon")
    assert mk.decision_list() == []
    assert mk.decision_list(status="resolved")[0]["answer"] == "no"
    assert mk.decision_digest() == ""


def test_corrupt_line_is_tolerated_on_read(mk):
    mk.saying_record("결론부터 말해", scope="all")
    path = mk.owner_dir / "sayings.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert mk.saying_search("결론")[0]["text"] == "결론부터 말해"
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["kind"] == "saying"
