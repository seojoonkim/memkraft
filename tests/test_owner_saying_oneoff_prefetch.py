"""Sayings classified "oneoff" are not auto-injected into a turn's prefetch block.

A oneoff saying was a single task or fact, not a standing rule.  Re-injecting it
on every matching turn crowded out real rules: a 7-day replay of live turns
showed 50% of auto-injected sayings (81% of their characters) were oneoff.
Explicit ``owner_saying_search`` must still find them.
"""
import pytest

pytest.importorskip("agent.memory_provider")

from memkraft.hermes_provider import MemKraftMemoryProvider


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    for k in ("MEMKRAFT_DIR", "MEMKRAFT_OWNER_DIR", "MEMKRAFT_OWNER_SCOPE"):
        monkeypatch.delenv(k, raising=False)


def _provider(root, profile="zeon"):
    home = root / "profiles" / profile
    home.mkdir(parents=True, exist_ok=True)
    p = MemKraftMemoryProvider()
    p.initialize("s-" + profile, hermes_home=str(home))
    return p


def _seed(store):
    one = store.saying_record("배포 보고 이번 건은 표 세 줄로 요약해서 보내줘 배포 보고", scope="zeon")
    rule = store.saying_record("배포 보고에는 항상 롤백 방법을 적어", scope="zeon")
    store.saying_classify(one["id"], "oneoff")
    store.saying_classify(rule["id"], "prose")
    return one, rule


def test_prefetch_skips_oneoff_sayings(tmp_path):
    p = _provider(tmp_path)
    _seed(p._store)
    block = p.prefetch("배포 보고 정리", session_id="s")
    assert "롤백 방법" in block
    assert "표 세 줄로" not in block


def test_oneoff_does_not_use_up_the_limit(tmp_path):
    p = _provider(tmp_path)
    store = p._store
    for i in range(3):
        sid = store.saying_record("배포 보고 배포 보고 단건 지시 {}".format(i), scope="zeon")["id"]
        store.saying_classify(sid, "oneoff")
    store.saying_record("배포 보고는 항상 한국어로", scope="zeon")
    store.saying_record("배포 보고에 비밀값은 절대 넣지마", scope="all")
    block = p.prefetch("배포 보고", session_id="s")
    assert "한국어로" in block and "비밀값" in block
    assert "단건 지시" not in block


def test_explicit_search_still_returns_oneoff(tmp_path):
    p = _provider(tmp_path)
    _seed(p._store)
    texts = {h["text"] for h in p._store.saying_search("배포 보고", limit=5)}
    assert any("표 세 줄로" in t for t in texts)


def test_unclassified_sayings_still_injected(tmp_path):
    p = _provider(tmp_path)
    p._store.saying_record("배포 보고는 금요일에 하지마", scope="zeon")
    block = p.prefetch("배포 보고", session_id="s")
    assert "금요일" in block


def test_exclude_levels_param_filters_before_limit(tmp_path):
    store = _provider(tmp_path)._store
    one, rule = _seed(store)
    hits = store.saying_search("배포 보고", limit=1, exclude_levels=("oneoff",))
    assert [h["id"] for h in hits] == [rule["id"]]
