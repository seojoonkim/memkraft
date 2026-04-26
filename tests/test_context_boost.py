"""v2.5 — Context Boost (compress + rerank + temporal annotation) tests.

Covers:
  * ``compress_context`` honours ``max_chars`` / dedup / temporal preference.
  * ``rerank_for_question_type`` per-type bonuses produce expected ordering.
  * ``_annotate_temporal`` adds correct tags & is idempotent.
  * Integrated ``format_context_for_llm`` end-to-end behaviour.

All tests use stand-alone result dicts (no filesystem / search needed) so
they're fast and deterministic.
"""
from __future__ import annotations

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


def _result(
    match: str,
    snippet: str,
    score: float = 0.6,
    *,
    confidence: str = "high",
    entity: str | None = None,
    key: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    recorded_at: str | None = None,
    session: str | None = None,
    implicit: bool = False,
) -> dict:
    r: dict = {"match": match, "snippet": snippet, "score": score, "confidence": confidence}
    if entity:
        r["entity"] = entity
    if key:
        r["key"] = key
    if valid_from:
        r["valid_from"] = valid_from
    if valid_until:
        r["valid_until"] = valid_until
    if recorded_at:
        r["recorded_at"] = recorded_at
    if session:
        r["session"] = session
    if implicit:
        r["_implicit_acquisition"] = True
    return r


# ===========================================================================
# 1. Context Compression
# ===========================================================================
def test_compress_respects_max_chars(mk):
    rows = [
        _result(f"plant{i}", f"snippet about plant number {i} " * 8, score=0.5 + i * 0.01)
        for i in range(20)
    ]
    out = mk.compress_context(rows, "plant", max_chars=400)
    assert isinstance(out, str)
    assert len(out) <= 400
    # Must include at least one plant fact.
    assert "plant" in out


def test_compress_empty_inputs(mk):
    assert mk.compress_context([], "anything") == ""
    assert mk.compress_context(None, "anything") == ""


def test_compress_dedup_same_entity_key(mk):
    rows = [
        _result("Tesla CEO", "Elon Musk runs Tesla", score=0.4,
                entity="tesla", key="ceo"),
        _result("Tesla CEO again", "Tesla's CEO is Elon Musk", score=0.9,
                entity="tesla", key="ceo"),
        _result("Apple CEO", "Tim Cook leads Apple", score=0.7,
                entity="apple", key="ceo"),
    ]
    out = mk.compress_context(rows, "ceo")
    # The lower-scored Tesla entry should be deduplicated out.
    assert "Elon Musk runs Tesla" not in out
    assert "Tim Cook" in out
    assert "Tesla's CEO" in out


def test_compress_query_relevance_boost(mk):
    rows = [
        _result("orchid care", "watering orchids weekly", score=0.4),
        _result("random fact", "lorem ipsum dolor sit amet", score=0.6),
    ]
    out = mk.compress_context(rows, "orchid watering", max_chars=200)
    # The orchid fact should appear first because of query overlap bonus.
    first_line = out.splitlines()[0]
    assert "orchid" in first_line.lower()


def test_compress_temporal_preference(mk):
    rows = [
        _result("plain fact", "no date here at all", score=0.5,
                entity="x", key="a"),
        _result("dated fact", "happened on 2024-03-15 confirmed", score=0.5,
                entity="y", key="b"),
    ]
    chosen = mk._compress_select(rows, "fact", max_chars=500)
    # Dated row should rank ahead of plain row at equal base score.
    assert chosen[0]["match"] == "dated fact"


def test_compress_max_lines_limit(mk):
    rows = [
        _result(f"e{i}", f"fact {i}", score=0.5 + i * 0.001,
                entity=f"e{i}", key="k")
        for i in range(10)
    ]
    chosen = mk._compress_select(rows, "fact", max_chars=10000, max_lines=3)
    assert len(chosen) == 3


def test_compress_uses_confidence_bonus(mk):
    rows = [
        _result("low one", "low conf snippet", score=0.6, confidence="low",
                entity="a", key="k"),
        _result("high one", "high conf snippet", score=0.6, confidence="high",
                entity="b", key="k"),
    ]
    chosen = mk._compress_select(rows, "snippet")
    assert chosen[0]["match"] == "high one"


# ===========================================================================
# 2. Re-ranking by question type
# ===========================================================================
def test_rerank_counting_prefers_acquisition_pattern(mk):
    rows = [
        _result("intent", "thinking of getting a new orchid", score=0.7,
                implicit=True),
        _result("confirmed", "I bought a new orchid yesterday", score=0.5),
    ]
    out = mk.rerank_for_question_type(rows, "counting")
    assert out[0]["match"] == "confirmed"
    assert out[0]["_rerank_bonus"] >= 0.30


def test_rerank_counting_korean_pattern(mk):
    rows = [
        _result("plain", "no acquisition keywords", score=0.7),
        _result("ko", "어제 책을 샀다", score=0.5),
    ]
    out = mk.rerank_for_question_type(rows, "counting")
    assert out[0]["match"] == "ko"


def test_rerank_knowledge_update_prefers_open_ended(mk):
    rows = [
        _result("old", "previous job description", score=0.6,
                valid_from="2020-01-01", valid_until="2022-01-01"),
        _result("current", "current role at Hashed", score=0.6,
                valid_from="2022-01-01"),  # open-ended
    ]
    out = mk.rerank_for_question_type(rows, "knowledge_update")
    assert out[0]["match"] == "current"


def test_rerank_temporal_reasoning_orders_by_date(mk):
    rows = [
        _result("a", "event before main", score=0.5, valid_from="2020-05-01"),
        _result("b", "event after main", score=0.5, valid_from="2024-08-15"),
        _result("c", "event in middle", score=0.5, valid_from="2022-03-10"),
    ]
    out = mk.rerank_for_question_type(rows, "temporal_reasoning")
    # All carry equal base; they all get the temporal-metadata bonus, so
    # within the same score band the secondary key (newer-first) wins.
    dates = [r.get("valid_from") for r in out]
    assert dates[0] == "2024-08-15"


def test_rerank_preference_pattern_boost(mk):
    rows = [
        _result("neutral", "user mentions coffee occasionally", score=0.7),
        _result("pref", "user prefers oat milk over almond", score=0.5),
    ]
    out = mk.rerank_for_question_type(rows, "preference")
    assert out[0]["match"] == "pref"
    assert out[0]["_rerank_bonus"] == pytest.approx(0.25)


def test_rerank_multi_session_diversity(mk):
    rows = [
        _result("a1", "fact a1", score=0.6, session="s1"),
        _result("a2", "fact a2", score=0.6, session="s1"),
        _result("b", "fact b", score=0.55, session="s2"),
        _result("c", "fact c", score=0.55, session="s3"),
    ]
    out = mk.rerank_for_question_type(rows, "multi_session")
    # Every session-tagged row should carry the diversity bonus when ≥3 sessions.
    bonuses = [r.get("_rerank_bonus") for r in out]
    assert all(b == pytest.approx(0.05) for b in bonuses)


def test_rerank_general_returns_original_order(mk):
    rows = [
        _result("first", "snippet 1", score=0.5),
        _result("second", "snippet 2", score=0.9),
    ]
    out = mk.rerank_for_question_type(rows, "general")
    # No re-sort for general — original order preserved.
    assert [r["match"] for r in out] == ["first", "second"]


def test_rerank_handles_empty_and_none(mk):
    assert mk.rerank_for_question_type(None, "counting") == []
    assert mk.rerank_for_question_type([], "counting") == []


def test_rerank_unknown_type_is_safe(mk):
    rows = [_result("a", "x", score=0.5), _result("b", "y", score=0.6)]
    out = mk.rerank_for_question_type(rows, "totally_unknown_type")
    assert len(out) == 2  # no crash


# ===========================================================================
# 3. Temporal annotation
# ===========================================================================
def test_annotate_temporal_open_ended(mk):
    r = _result("hashed", "Simon joined Hashed", score=0.7,
                valid_from="2020-03-01")
    mk._annotate_temporal(r)
    assert r["snippet"].startswith("[2020-03-01 ~ present]")


def test_annotate_temporal_closed_range(mk):
    r = _result("old role", "Worked at FooCorp", score=0.7,
                valid_from="2018-01-01", valid_until="2020-02-28")
    mk._annotate_temporal(r)
    assert "[2018-01-01 ~ 2020-02-28]" in r["snippet"]


def test_annotate_temporal_bitemporal(mk):
    r = _result("late record", "Backfilled job entry", score=0.7,
                valid_from="2015-06-01", recorded_at="2024-09-12")
    mk._annotate_temporal(r)
    assert "recorded: 2024-09-12" in r["snippet"]
    assert "valid: 2015-06-01 ~ present" in r["snippet"]


def test_annotate_temporal_idempotent(mk):
    r = _result("hashed", "Simon joined", score=0.7, valid_from="2020-03-01")
    mk._annotate_temporal(r)
    first = r["snippet"]
    mk._annotate_temporal(r)
    assert r["snippet"] == first  # no double-tagging


def test_annotate_temporal_no_metadata_noop(mk):
    r = _result("plain", "Just a fact", score=0.7)
    before = r["snippet"]
    mk._annotate_temporal(r)
    assert r["snippet"] == before


# ===========================================================================
# 4. Integrated format_context_for_llm
# ===========================================================================
def test_format_context_combines_pipeline(mk):
    rows = [
        _result("intent", "thinking of getting a fern", score=0.7,
                implicit=True, confidence="low"),
        _result("confirmed", "bought a peace lily", score=0.5,
                valid_from="2024-04-01", entity="lily", key="acquired"),
        _result("dup", "bought a peace lily again", score=0.4,
                entity="lily", key="acquired"),
    ]
    text = mk.format_context_for_llm(
        rows, query="how many plants", question_type="counting",
        max_chars=600,
    )
    assert isinstance(text, str)
    assert len(text) <= 600
    # The deduped duplicate must not appear twice.
    assert text.count("peace lily") == 1
    # The temporal tag should be present on the confirmed fact.
    assert "2024-04-01" in text


def test_format_context_empty(mk):
    assert mk.format_context_for_llm([], "q") == ""
    assert mk.format_context_for_llm(None, "q") == ""


def test_format_context_include_low_false_drops_low(mk):
    rows = [
        _result("h", "high snippet", score=0.9, confidence="high"),
        _result("l", "low snippet", score=0.2, confidence="low",
                entity="x", key="y"),
    ]
    out = mk.format_context_for_llm(rows, "q", include_low=False)
    assert "high snippet" in out
    assert "low snippet" not in out


def test_format_context_max_chars_hard_cap(mk):
    rows = [
        _result(f"m{i}", "x" * 300, score=0.5 + i * 0.001,
                entity=f"e{i}", key="k", confidence="high")
        for i in range(20)
    ]
    out = mk.format_context_for_llm(rows, "x", max_chars=350)
    assert len(out) <= 350


def test_format_context_falls_back_without_question_type(mk):
    rows = [
        _result("a", "alpha snippet", score=0.7),
        _result("b", "beta snippet", score=0.4),
    ]
    out = mk.format_context_for_llm(rows, "alpha")
    # Highest-score row should appear first.
    assert out.splitlines()[0].endswith("alpha snippet") or "alpha" in out.splitlines()[0]
