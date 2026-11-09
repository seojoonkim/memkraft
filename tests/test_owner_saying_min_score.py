"""Auto-injected owner sayings need a minimum score; explicit search does not.

A saying that shares one common word with a long message used to be injected
anyway (95% of 3,140 real turns carried one).  Prefetch now skips hits scoring
below MEMKRAFT_HERMES_SAYING_MIN_SCORE (default 0.07); saying_search keeps
min_score=0.0 so owner_saying_search still sees every overlap.
"""
import pytest

pytest.importorskip("agent.memory_provider")

from memkraft import hermes_provider as hp
from memkraft.hermes_provider import MemKraftMemoryProvider

WEAK = "디자인 리뷰 끝나면 슬랙 채널에 요약 남기고 다음 회의 일정도 같이 잡아줘 그리고 편집 끝나면 알려줘"
LONG_QUERY = ("이번 분기 마케팅 캠페인 초안 세 개를 비교해서 장단점 표로 정리하고 "
              "예산 배분 제안까지 붙여서 내일 오전까지 공유해줘 디자인")


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    for k in ("MEMKRAFT_DIR", "MEMKRAFT_OWNER_DIR", "MEMKRAFT_OWNER_SCOPE",
              "MEMKRAFT_HERMES_SAYING_MIN_SCORE"):
        monkeypatch.delenv(k, raising=False)


def _provider(root, profile="zeon"):
    home = root / "profiles" / profile
    home.mkdir(parents=True, exist_ok=True)
    p = MemKraftMemoryProvider()
    p.initialize("s-" + profile, hermes_home=str(home))
    return p


def test_saying_search_min_score_drops_weak_hits(tmp_path):
    store = _provider(tmp_path)._store
    store.saying_record(WEAK, scope="zeon")
    store.saying_record("디자인 시안은 항상 모바일부터", scope="zeon")
    weak = [h for h in store.saying_search(LONG_QUERY) if "슬랙" in h["text"]]
    assert weak and weak[0]["score"] < 0.07
    kept = store.saying_search(LONG_QUERY, min_score=0.07)
    assert all(h["score"] >= 0.07 for h in kept)
    assert not any("슬랙" in h["text"] for h in kept)


def test_saying_search_default_keeps_every_overlap(tmp_path):
    store = _provider(tmp_path)._store
    store.saying_record(WEAK, scope="zeon")
    assert any("슬랙" in h["text"] for h in store.saying_search(LONG_QUERY))


def test_prefetch_skips_low_score_saying(tmp_path):
    p = _provider(tmp_path)
    p._store.saying_record(WEAK, scope="zeon")
    assert "Owner said" not in p.prefetch(LONG_QUERY, session_id="s")


def test_prefetch_keeps_strong_saying(tmp_path):
    p = _provider(tmp_path)
    p._store.saying_record("배포 보고에는 항상 롤백 방법을 적어", scope="zeon")
    assert "롤백 방법" in p.prefetch("배포 보고 정리", session_id="s")


def test_env_zero_restores_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_SAYING_MIN_SCORE", "0")
    p = _provider(tmp_path)
    p._store.saying_record(WEAK, scope="zeon")
    assert "슬랙" in p.prefetch(LONG_QUERY, session_id="s")


@pytest.mark.parametrize("raw,expected", [("", 0.07), ("0.2", 0.2), ("-1", 0.0), ("x", 0.07)])
def test_saying_min_score_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("MEMKRAFT_HERMES_SAYING_MIN_SCORE", raw)
    assert hp._saying_min_score() == expected
