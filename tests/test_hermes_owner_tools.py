"""Hermes provider exposes owner-ledger tools and recalls sayings verbatim."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("agent.memory_provider", reason="Hermes Agent compatibility suite")

from memkraft.hermes_provider import MemKraftMemoryProvider, OWNER_TOOL_NAMES  # noqa: E402


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(tmp_path / "owner"))
    p = MemKraftMemoryProvider()
    p.initialize("s1", hermes_home=str(tmp_path), platform="test")
    return p


def _call(p, name, **args):
    return json.loads(p.handle_tool_call(name, args))


def test_schemas_cover_every_owner_tool(provider):
    names = {s["name"] for s in provider.get_tool_schemas()}
    assert names == set(OWNER_TOOL_NAMES)
    for schema in provider.get_tool_schemas():
        assert schema["description"] and schema["parameters"]["type"] == "object"


def test_record_search_and_prefetch_verbatim(provider):
    out = _call(provider, "owner_saying_record", text="알아서 다 해 나한테 시키지마", scope="all")
    assert out["success"] and out["created"]
    hits = _call(provider, "owner_saying_search", query="형한테 명령어 시켜도 돼?")
    assert hits["results"][0]["text"] == "알아서 다 해 나한테 시키지마"
    recalled = provider.prefetch("이거 형한테 시켜도 돼?", session_id="s1")
    assert "알아서 다 해 나한테 시키지마" in recalled


def test_decision_tools_roundtrip(provider):
    skip = _call(provider, "owner_decision_enqueue", question="README 오타 수정",
                 category="docs", requester="sion", reversible=True)
    assert skip["queued"] is False and skip["action"] == "proceed"
    q = _call(provider, "owner_decision_enqueue", question="도메인 결제할까?",
              category="money", requester="sano")
    listed = _call(provider, "owner_decision_list")
    assert [d["decision_id"] for d in listed["results"]] == [q["id"]]
    assert "도메인 결제" in listed["digest"]
    done = _call(provider, "owner_decision_resolve", decision_id=q["id"], answer="보류", resolved_by="simon")
    assert done["status"] == "resolved"
    assert _call(provider, "owner_decision_list")["results"] == []


def test_bad_input_returns_error_not_exception(provider):
    out = _call(provider, "owner_saying_record", text="   ")
    assert out["success"] is False and "text" in out["error"]
    assert _call(provider, "owner_unknown")["success"] is False


def test_owner_dir_is_shared_across_profiles(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_DIR", raising=False)
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(tmp_path / "shared-owner"))
    a, b = MemKraftMemoryProvider(), MemKraftMemoryProvider()
    a.initialize("s", hermes_home=str(tmp_path / "profiles/zeon"), platform="test")
    b.initialize("s", hermes_home=str(tmp_path / "profiles/sano"), platform="test")
    _call(a, "owner_saying_record", text="결론부터 말해", scope="all")
    assert _call(b, "owner_saying_search", query="결론")["results"][0]["text"] == "결론부터 말해"
