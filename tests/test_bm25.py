"""Tests for v2.3 BM25 scoring (additive 4th retrieval signal).

Covers:
* :py:meth:`MemKraft._bm25_score` formula correctness against hand
  calculations.
* :py:meth:`MemKraft._get_corpus_stats` against a deterministic corpus.
* IDF-overlap vs BM25 differentiation on TF / length disparate corpora.
* Regression — existing search() public API and result shape unchanged.
* Search quality improvement — BM25 tips ranking in scenarios designed
  to expose its TF-saturation + length-norm advantages.
* Edge cases — empty query, single token query, all-doc-match,
  empty corpus.
"""
from __future__ import annotations

import contextlib
import io
import math
import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _silent_search(mk: MemKraft, query: str, **kwargs):
    """Run mk.search while swallowing its print side-effects."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return mk.search(query, **kwargs)


def _mk(corpus: dict[str, str]) -> MemKraft:
    """Build a MemKraft instance with files inside ``inbox/``."""
    base = tempfile.mkdtemp(prefix="mk_bm25_")
    mk = MemKraft(base_dir=base)
    mk.inbox_dir.mkdir(parents=True, exist_ok=True)
    for name, body in corpus.items():
        (mk.inbox_dir / name).write_text(body)
    return mk


# ─────────────────────────────────────────────────────────────────────
# Section 1 — Formula correctness
# ─────────────────────────────────────────────────────────────────────
class TestBM25Formula:
    def test_bm25_zero_when_no_query_tokens(self):
        mk = _mk({"a.md": "hello world"})
        score = mk._bm25_score(
            query_tokens=[],
            doc_tf={"hello": 1, "world": 1},
            doc_length=2,
            avg_doc_length=2.0,
            doc_count=1,
            token_doc_freq={"hello": 1, "world": 1},
        )
        assert score == 0.0

    def test_bm25_zero_when_corpus_empty(self):
        mk = _mk({})
        score = mk._bm25_score(
            query_tokens=["spanish"],
            doc_tf={},
            doc_length=0,
            avg_doc_length=0.0,
            doc_count=0,
            token_doc_freq={},
        )
        assert score == 0.0

    def test_bm25_no_match_returns_zero(self):
        mk = _mk({"a.md": "hello"})
        score = mk._bm25_score(
            query_tokens=["spanish"],
            doc_tf={"hello": 1},
            doc_length=1,
            avg_doc_length=1.0,
            doc_count=1,
            token_doc_freq={"hello": 1},
        )
        assert score == 0.0

    def test_bm25_matches_hand_calculation_single_term(self):
        """Verify single-term BM25 against an explicit hand computation."""
        mk = _mk({"a.md": "x"})
        # Setup: N=10 docs, term appears in 2 of them, tf in this doc = 3,
        # doc length = 100, avgdl = 100  → length_norm = 1.0 (k1=1.5,b=0.75)
        N = 10
        n_qi = 2
        tf = 3
        k1 = 1.5
        b = 0.75
        doc_len = 100
        avgdl = 100.0

        score = mk._bm25_score(
            query_tokens=["spanish"],
            doc_tf={"spanish": tf},
            doc_length=doc_len,
            avg_doc_length=avgdl,
            doc_count=N,
            token_doc_freq={"spanish": n_qi},
            k1=k1,
            b=b,
        )

        # Expected: idf * tf*(k1+1) / (tf + k1*(1-b+b*doc_len/avgdl))
        expected_idf = math.log(((N - n_qi + 0.5) / (n_qi + 0.5)) + 1.0)
        length_norm = (1.0 - b) + b * (doc_len / avgdl)  # = 1.0
        expected = expected_idf * (tf * (k1 + 1.0)) / (tf + k1 * length_norm)
        assert score == pytest.approx(expected, rel=1e-9)

    def test_bm25_tf_saturation(self):
        """Higher TF gives higher score, but with saturation (not linear)."""
        mk = _mk({"a.md": "x"})
        common = dict(
            doc_length=50,
            avg_doc_length=50.0,
            doc_count=10,
            token_doc_freq={"x": 5},
            k1=1.5,
            b=0.75,
        )
        s1 = mk._bm25_score(["x"], doc_tf={"x": 1}, **common)
        s2 = mk._bm25_score(["x"], doc_tf={"x": 2}, **common)
        s10 = mk._bm25_score(["x"], doc_tf={"x": 10}, **common)
        s100 = mk._bm25_score(["x"], doc_tf={"x": 100}, **common)

        # Monotone increasing
        assert s1 < s2 < s10 < s100
        # Saturation: doubling tf 1→2 helps a lot; 10→100 helps very little.
        gain_low = s2 - s1
        gain_high = s100 - s10
        assert gain_low > gain_high, (
            f"BM25 should saturate but gain_low={gain_low}, gain_high={gain_high}"
        )

    def test_bm25_length_normalisation_short_doc_wins(self):
        """For equal TF, shorter docs should outrank longer docs."""
        mk = _mk({"a.md": "x"})
        common = dict(
            avg_doc_length=50.0,
            doc_count=10,
            token_doc_freq={"x": 5},
            k1=1.5,
            b=0.75,
        )
        short = mk._bm25_score(["x"], doc_tf={"x": 2}, doc_length=10, **common)
        long_ = mk._bm25_score(["x"], doc_tf={"x": 2}, doc_length=200, **common)
        assert short > long_

    def test_bm25_idf_rewards_rare_terms(self):
        """Rare terms (low df) should produce a much larger BM25 contribution
        than common ones for identical tf and doc length."""
        mk = _mk({"a.md": "x"})
        common = dict(
            doc_tf={"rare": 3, "common": 3},
            doc_length=100,
            avg_doc_length=100.0,
            doc_count=100,
            k1=1.5,
            b=0.75,
        )
        rare = mk._bm25_score(
            ["rare"], token_doc_freq={"rare": 1, "common": 90}, **common
        )
        cmn = mk._bm25_score(
            ["common"], token_doc_freq={"rare": 1, "common": 90}, **common
        )
        assert rare > cmn

    def test_bm25_filename_token_synthetic_tf(self):
        """Filename-only matches should still score (synthetic TF=1)."""
        mk = _mk({"a.md": "x"})
        # Body has no 'spanish' but filename does.
        s = mk._bm25_score(
            query_tokens=["spanish"],
            doc_tf={"unrelated": 5},  # body has no 'spanish'
            doc_length=5,
            avg_doc_length=5.0,
            doc_count=10,
            token_doc_freq={"spanish": 2, "unrelated": 5},
            filename_tokens={"spanish"},
        )
        assert s > 0.0


# ─────────────────────────────────────────────────────────────────────
# Section 2 — Corpus statistics
# ─────────────────────────────────────────────────────────────────────
class TestCorpusStats:
    def test_empty_corpus_stats(self):
        mk = _mk({})
        n, avg = mk._get_corpus_stats()
        assert n == 0
        assert avg == 0.0

    def test_single_doc_corpus(self):
        mk = _mk({"a.md": "one two three four"})
        n, avg = mk._get_corpus_stats()
        assert n == 1
        # _search_tokens drops len<=1 tokens but all here are len>=3.
        assert avg == 4.0

    def test_multi_doc_corpus_average(self):
        mk = _mk(
            {
                "a.md": "one two",                 # 2 tokens
                "b.md": "alpha beta gamma delta",  # 4 tokens
                "c.md": "x y z hello world bye",   # 'x','y','z' dropped (len<2)
            }
        )
        n, avg = mk._get_corpus_stats()
        assert n == 3
        assert avg == pytest.approx((2 + 4 + 3) / 3, rel=1e-9)


# ─────────────────────────────────────────────────────────────────────
# Section 3 — Regression on existing search() public surface
# ─────────────────────────────────────────────────────────────────────
class TestRegression:
    @pytest.fixture
    def mk(self):
        return _mk(
            {
                "sugar.md": (
                    "# sugar\n"
                    "I went to the Sugar Factory at Icon Park last weekend.\n"
                ),
                "spanish.md": (
                    "# spanish\n"
                    "I have been learning Spanish for three months now.\n"
                ),
                "bike.md": "# bike\nI ride a bicycle to work most days.\n",
            }
        )

    def test_search_returns_list_of_dicts(self, mk):
        res = _silent_search(mk, "spanish")
        assert isinstance(res, list)
        assert all(isinstance(r, dict) for r in res)

    def test_search_result_shape_unchanged(self, mk):
        """v2.3 must not introduce new mandatory keys nor drop old ones."""
        res = _silent_search(mk, "spanish")
        assert res, "expected at least one match"
        for r in res:
            # Existing v2.2 keys still present.
            assert set(r.keys()) >= {"file", "score", "match", "snippet"}

    def test_empty_query_returns_empty_list(self, mk):
        assert _silent_search(mk, "") == []
        assert _silent_search(mk, "   ") == []

    def test_exact_match_still_dominates(self, mk):
        """Exact phrase match must still rank ≥ token-only matches."""
        res = _silent_search(mk, "Sugar Factory")
        assert res
        assert res[0]["match"] == "sugar"
        # First hit must clear a strong-relevance threshold.
        assert res[0]["score"] >= 0.7

    def test_score_bounded_zero_to_one(self, mk):
        for q in ["spanish", "bicycle", "Sugar Factory", "Icon Park"]:
            for r in _silent_search(mk, q):
                assert 0.0 <= r["score"] <= 1.0

    def test_v22_v102_helpers_still_work(self, mk):
        """search_v2 / search_expand / search_smart all still produce results."""
        assert mk.search_v2("spanish", top_k=5)
        assert mk.search_expand("how long have I learned spanish", top_k=5)
        # search_smart classifies + dispatches; just ensure no crash + list out.
        out = mk.search_smart("when did I go to Sugar Factory", top_k=5)
        assert isinstance(out, list)


# ─────────────────────────────────────────────────────────────────────
# Section 4 — Quality / ranking improvements driven by BM25
# ─────────────────────────────────────────────────────────────────────
class TestSearchQuality:
    def test_bm25_breaks_ties_on_tf(self):
        """Two docs both contain the term, but one mentions it more often.

        BM25's TF saturation should rank the higher-TF doc above the
        lower-TF one (assuming similar length).
        """
        mk = _mk(
            {
                "low.md": (
                    "# low\nThis document mentions spanish exactly one time. "
                    "The rest is filler text about other unrelated topics like "
                    "weather and sports and food and travel."
                ),
                "high.md": (
                    "# high\nspanish spanish spanish spanish spanish. "
                    "I love spanish and I study spanish often. "
                    "spanish grammar, spanish vocabulary, spanish accents."
                ),
            }
        )
        res = _silent_search(mk, "spanish")
        # Both will exact-match → both 1.0. To inspect TF effect we need a
        # query that does NOT exact-match either body verbatim.
        res2 = _silent_search(mk, "learn spanish")
        # No body contains the exact phrase 'learn spanish' so exact_score=0;
        # token_score and BM25 decide the ranking.
        assert res2
        # Sanity: 'high.md' (heavy spanish TF) outranks 'low.md'.
        names = [r["match"] for r in res2]
        assert names.index("high") < names.index("low"), (
            f"BM25 should prefer the high-TF doc: got order {names}"
        )

    def test_bm25_prefers_short_doc_on_equal_tf(self):
        """BM25 length normalisation should rank the shorter doc higher
        when both have the same token frequency for a non-exact query."""
        mk = _mk(
            {
                "short.md": "# short\nlearning korean every day.\n",
                "long.md": (
                    "# long\nlearning korean every day. " + ("filler word " * 80)
                ),
            }
        )
        # Query that does NOT exact-match either body verbatim.
        res = _silent_search(mk, "study korean")
        names = [r["match"] for r in res]
        if "short" in names and "long" in names:
            assert names.index("short") < names.index("long"), (
                f"BM25 length norm should favour shorter doc: {names}"
            )

    def test_irrelevant_doc_not_returned(self):
        """A doc with zero query-token overlap must not appear in results."""
        mk = _mk(
            {
                "spanish.md": "# spanish\nI study spanish.\n",
                "weather.md": "# weather\nIt rained today in Seoul.\n",
            }
        )
        res = _silent_search(mk, "spanish")
        names = [r["match"] for r in res]
        assert "spanish" in names
        assert "weather" not in names


# ─────────────────────────────────────────────────────────────────────
# Section 5 — Edge cases
# ─────────────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_single_token_query_no_crash(self):
        mk = _mk({"a.md": "alpha beta gamma"})
        res = _silent_search(mk, "alpha")
        assert res
        assert res[0]["match"] == "a"

    def test_all_docs_contain_query_term(self):
        """When every doc contains the query term, BM25 IDF→0 but corpus
        should still return ranked results (driven by exact/token signals)."""
        mk = _mk(
            {
                "a.md": "spanish",
                "b.md": "spanish spanish",
                "c.md": "spanish spanish spanish",
            }
        )
        res = _silent_search(mk, "spanish")
        # All exact-match → all should be returned.
        assert len(res) == 3
        for r in res:
            assert 0.0 <= r["score"] <= 1.0

    def test_unicode_korean_query(self):
        """Korean search should still work with BM25 added."""
        mk = _mk(
            {
                "ko.md": "# ko\n저는 매일 한국어 공부를 합니다.\n",
                "en.md": "# en\nI study Korean every day.\n",
            }
        )
        res = _silent_search(mk, "한국어")
        names = [r["match"] for r in res]
        assert "ko" in names

    def test_no_matching_doc_returns_empty(self):
        mk = _mk({"a.md": "alpha", "b.md": "beta"})
        res = _silent_search(mk, "zzz_no_match_xyz")
        assert res == []
