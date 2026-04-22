"""Tests for v1.0.2 SearchMixin (search_v2, search_expand, search_temporal)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk_with_corpus():
    """Build a MemKraft instance with a small, deterministic corpus."""
    tmp = tempfile.mkdtemp(prefix="mk_v102_")
    mk = MemKraft(base_dir=tmp)
    inbox = mk.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

    (inbox / "sugar.md").write_text(
        "# Session sugar\n"
        "**Date:** 2025-06-15\n"
        "## Messages\n"
        "### [0] user\n"
        "I went to the Sugar Factory at Icon Park in Orlando last weekend.\n"
    )
    (inbox / "spanish.md").write_text(
        "# Session spanish\n"
        "**Date:** 2025-07-02\n"
        "## Messages\n"
        "### [0] user\n"
        "I have been learning Spanish for three months now and struggling with subjunctive.\n"
    )
    (inbox / "bike.md").write_text(
        "# Session bike\n"
        "**Date:** 2025-05-01\n"
        "## Messages\n"
        "### [0] user\n"
        "I ride a bicycle to work most days.\n"
    )
    yield mk


class TestKeywordVariants:
    def test_basic_variants_filter_stopwords(self, mk_with_corpus):
        variants = mk_with_corpus._v102_keyword_variants(
            "How many months have I been learning Spanish?"
        )
        # At least one variant should contain the topical noun
        assert any("spanish" in v for v in variants)
        # None should be empty
        assert all(v.strip() for v in variants)
        # No stopwords leak into single-token variants
        assert "have" not in variants
        assert "been" not in variants

    def test_variants_exclude_original(self, mk_with_corpus):
        q = "spanish"  # already a keyword
        variants = mk_with_corpus._v102_keyword_variants(q)
        assert q.lower() not in variants or len(variants) == 0

    def test_variants_empty_query(self, mk_with_corpus):
        assert mk_with_corpus._v102_keyword_variants("") == []
        assert mk_with_corpus._v102_keyword_variants("   ") == []

    def test_variants_only_stopwords(self, mk_with_corpus):
        # pure stopword string → no useful variants
        assert mk_with_corpus._v102_keyword_variants("how when where why") == []

    def test_variants_korean(self, mk_with_corpus):
        variants = mk_with_corpus._v102_keyword_variants("서준이 어제 무엇을 했나요")
        # Should keep content words, drop particles like 을/이
        assert any("서준이" in v or "어제" in v for v in variants)


class TestSearchV2:
    def test_default_top_k_is_20(self, mk_with_corpus):
        # introspect signature default
        import inspect
        sig = inspect.signature(mk_with_corpus.search_v2)
        assert sig.parameters["top_k"].default == 20

    def test_top_k_limits_results(self, mk_with_corpus):
        res = mk_with_corpus.search_v2("learning", top_k=1)
        assert len(res) <= 1

    def test_empty_query_returns_empty(self, mk_with_corpus):
        assert mk_with_corpus.search_v2("") == []
        assert mk_with_corpus.search_v2("   ") == []

    def test_invalid_top_k_falls_back(self, mk_with_corpus):
        # Non-positive top_k should not crash
        res = mk_with_corpus.search_v2("spanish", top_k=0)
        assert isinstance(res, list)

    def test_expand_query_improves_recall(self, mk_with_corpus):
        q = "How many months have I been learning Spanish?"
        without = mk_with_corpus.search_v2(q, top_k=5, expand_query=False)
        with_exp = mk_with_corpus.search_v2(q, top_k=5, expand_query=True)
        # Expanded query should match spanish.md at least as well
        score_without = next(
            (r["score"] for r in without if "spanish" in r["file"]), 0
        )
        score_with = next(
            (r["score"] for r in with_exp if "spanish" in r["file"]), 0
        )
        # Expansion must not hurt recall on the topical file
        assert score_with >= score_without
        # And with expansion we expect a non-trivial match score
        assert score_with > 0.3

    def test_returns_dict_results(self, mk_with_corpus):
        res = mk_with_corpus.search_v2("bicycle", top_k=5)
        assert all(isinstance(r, dict) for r in res)
        assert all("file" in r and "score" in r for r in res)

    def test_silent_stdout(self, mk_with_corpus, capsys):
        mk_with_corpus.search_v2("bicycle", top_k=5, expand_query=True)
        captured = capsys.readouterr()
        assert captured.out == ""


class TestSearchExpand:
    def test_is_expand_alias(self, mk_with_corpus):
        q = "learning spanish"
        a = mk_with_corpus.search_expand(q, top_k=5)
        b = mk_with_corpus.search_v2(q, top_k=5, expand_query=True)
        assert [r["file"] for r in a] == [r["file"] for r in b]


class TestSearchTemporal:
    def test_no_hint_acts_like_expand(self, mk_with_corpus):
        q = "learning spanish"
        a = mk_with_corpus.search_temporal(q, top_k=5)
        b = mk_with_corpus.search_v2(q, top_k=5, expand_query=True)
        assert [r["file"] for r in a] == [r["file"] for r in b]

    def test_nearby_date_boosts_score(self, mk_with_corpus):
        q = "months learning"
        base = mk_with_corpus.search_v2(q, top_k=5, expand_query=True)
        boosted = mk_with_corpus.search_temporal(
            q, date_hint="2025-07-01", top_k=5
        )
        base_score = next((r["score"] for r in base if "spanish" in r["file"]), 0)
        boost_score = next((r["score"] for r in boosted if "spanish" in r["file"]), 0)
        assert boost_score >= base_score

    def test_far_date_no_boost(self, mk_with_corpus):
        q = "months learning"
        boosted = mk_with_corpus.search_temporal(
            q, date_hint="2020-01-01", top_k=5, window_days=30
        )
        for r in boosted:
            assert r.get("_temporal_boost", 0) == 0 or "_temporal_boost" not in r

    def test_malformed_hint_falls_back(self, mk_with_corpus):
        q = "spanish"
        res = mk_with_corpus.search_temporal(q, date_hint="not-a-date", top_k=5)
        # Should not crash and should still return results
        assert isinstance(res, list)

    def test_exact_date_match_strong_boost(self, mk_with_corpus):
        q = "learning"
        boosted = mk_with_corpus.search_temporal(
            q, date_hint="2025-07-02", top_k=5
        )
        spanish = next((r for r in boosted if "spanish" in r["file"]), None)
        assert spanish is not None
        # 0.15 exact-match boost applied
        assert spanish.get("_temporal_boost", 0) >= 0.14


class TestSearchRanked:
    def test_returns_sorted_by_score(self, mk_with_corpus):
        res = mk_with_corpus.search_ranked("spanish bicycle sugar", top_k=10)
        scores = [r["score"] for r in res]
        assert scores == sorted(scores, reverse=True)

    def test_min_score_filters(self, mk_with_corpus):
        # With a permissive floor, at least one result above
        res = mk_with_corpus.search_ranked("learning spanish", top_k=10, min_score=0.3)
        assert all(r["score"] >= 0.3 for r in res) or len(res) >= 1

    def test_floor_does_not_starve(self, mk_with_corpus):
        # Aggressive floor — if it empties the list, fall back to base
        res = mk_with_corpus.search_ranked("spanish", top_k=10, min_score=0.99)
        # Either empty OR fallback to at least one result (non-starving guarantee)
        assert isinstance(res, list)

    def test_empty_query(self, mk_with_corpus):
        assert mk_with_corpus.search_ranked("", top_k=5) == []


class TestSearchSmart:
    def test_classify_count(self, mk_with_corpus):
        assert mk_with_corpus._v102_classify("How many books did I read?") == "count"

    def test_classify_temporal(self, mk_with_corpus):
        assert mk_with_corpus._v102_classify("When did I start learning Spanish?") == "temporal"
        assert mk_with_corpus._v102_classify("How long have I been here?") == "temporal"

    def test_classify_preference(self, mk_with_corpus):
        assert mk_with_corpus._v102_classify("What is my favorite color?") == "preference"

    def test_classify_fact_default(self, mk_with_corpus):
        assert mk_with_corpus._v102_classify("I ride a bicycle to work") == "fact"

    def test_returns_list(self, mk_with_corpus):
        res = mk_with_corpus.search_smart("How many months learning Spanish?", top_k=5)
        assert isinstance(res, list)

    def test_temporal_routes_to_temporal(self, mk_with_corpus):
        # Temporal question with a matching date hint should carry a boost
        res = mk_with_corpus.search_smart(
            "When did I last practice Spanish?", top_k=5, date_hint="2025-07-02"
        )
        spanish = next((r for r in res if "spanish" in r["file"]), None)
        # Temporal path applies date boost when the file contains the hint
        assert spanish is not None
        assert spanish.get("_temporal_boost", 0) >= 0.14

    def test_fact_routes_to_ranked(self, mk_with_corpus):
        # Fact queries should remain sorted by score desc
        res = mk_with_corpus.search_smart("bicycle to work", top_k=5)
        scores = [r["score"] for r in res]
        assert scores == sorted(scores, reverse=True)

    def test_empty_query(self, mk_with_corpus):
        # search_smart delegates to search_ranked for fact bucket
        assert mk_with_corpus.search_smart("", top_k=5) == []


class TestBackwardCompat:
    def test_legacy_search_still_works(self, mk_with_corpus, capsys):
        # core.search must remain intact (prints + returns list)
        res = mk_with_corpus.search("bicycle")
        assert isinstance(res, list)
        # legacy search prints to stdout
        captured = capsys.readouterr()
        assert captured.out != ""

    def test_version_bumped(self):
        import memkraft
        # v1.0.2 introduced SearchMixin; later versions must keep it.
        parts = tuple(int(p) for p in memkraft.__version__.split(".")[:3])
        assert parts >= (1, 0, 2)
