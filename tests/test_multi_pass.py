"""Tests for v2.2 MultiPassMixin (search_multi)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mk(tmp_path):
    """Bare MemKraft, empty corpus."""
    return MemKraft(base_dir=str(tmp_path))


@pytest.fixture
def mk_corpus(tmp_path):
    """MemKraft with a small markdown corpus + graph + facts."""
    mk = MemKraft(base_dir=str(tmp_path))

    inbox = mk.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

    # Markdown corpus — Pass 1 will find these.
    (inbox / "sarah.md").write_text(
        "# Sarah\n"
        "**Date:** 2025-06-15\n"
        "Sarah Johnson is a software engineer who works at Google in NYC.\n"
        "She loves hiking on weekends.\n"
    )
    (inbox / "google.md").write_text(
        "# Google\n"
        "**Date:** 2025-06-10\n"
        "Google is headquartered in Mountain View but has a large NYC office.\n"
    )
    (inbox / "spanish.md").write_text(
        "# Spanish\n"
        "**Date:** 2025-07-02\n"
        "I have been learning Spanish for three months and struggling with subjunctive.\n"
    )

    # Graph layer — Pass 2 will expand from here.
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("google", "located_in", "nyc")
    mk.graph_edge("sarah", "lives_in", "brooklyn")

    # Bitemporal facts — Pass 3 will surface these.
    mk.fact_add(
        "sarah", "role", "junior_engineer",
        valid_from="2023-01-01", valid_to="2024-06-30",
        recorded_at="2023-01-15T00:00:00",
    )
    mk.fact_add(
        "sarah", "role", "senior_engineer",
        valid_from="2024-07-01",
        recorded_at="2024-07-15T00:00:00",
    )
    mk.fact_add(
        "google", "ceo", "Sundar Pichai",
        valid_from="2015-08-10",
        recorded_at="2015-08-10T00:00:00",
    )

    return mk


# ---------------------------------------------------------------------------
# 1. Empty / edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_query_returns_empty(self, mk_corpus):
        assert mk_corpus.search_multi("") == []
        assert mk_corpus.search_multi("   ") == []

    def test_non_string_query(self, mk_corpus):
        assert mk_corpus.search_multi(None) == []  # type: ignore[arg-type]
        assert mk_corpus.search_multi(123) == []  # type: ignore[arg-type]

    def test_empty_corpus_returns_empty(self, mk):
        # Bare MK with no content / graph / facts — must not raise.
        results = mk.search_multi("anything")
        assert isinstance(results, list)
        assert results == []


# ---------------------------------------------------------------------------
# 2. Pass 1 — basic retrieval (single-pass)
# ---------------------------------------------------------------------------
class TestPass1Only:
    def test_pass1_finds_markdown_hit(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah Johnson", passes=1, top_k=5)
        assert len(results) > 0
        # Pass-1-only run should never tag a result with passes 2/3.
        for r in results:
            assert "pass_scores" in r
            assert r["pass_scores"]["p2"] == 0
            assert r["pass_scores"]["p3"] == 0

    def test_pass1_simple_query_sufficient(self, mk_corpus):
        # When Pass 1 already nails it, the answer file should be at top.
        results = mk_corpus.search_multi("Spanish subjunctive", passes=1, top_k=3)
        assert any(
            (r.get("file") or "").endswith("spanish.md")
            for r in results
        ), f"expected spanish.md in {results}"


# ---------------------------------------------------------------------------
# 3. Pass 2 — graph expansion
# ---------------------------------------------------------------------------
class TestPass2GraphExpansion:
    def test_pass2_adds_graph_neighbors(self, mk_corpus):
        # Query about NYC — Pass 1 finds sarah.md/google.md, Pass 2 should
        # bring in graph neighbours like "nyc" / "brooklyn".
        with_p1 = mk_corpus.search_multi("Sarah", passes=1, top_k=10)
        with_p2 = mk_corpus.search_multi("Sarah", passes=2, top_k=10)

        p1_keys = {r.get("file") or r.get("match") for r in with_p1}
        p2_keys = {r.get("file") or r.get("match") for r in with_p2}

        # passes=2 must produce a superset (or at least one non-pass-1 hit).
        assert any(
            r.get("source_passes") and 2 in r["source_passes"]
            for r in with_p2
        ), f"no Pass 2 hits in {with_p2}"

    def test_pass2_neighbor_score_set(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah Google NYC", passes=2, top_k=10)
        p2_hits = [r for r in results if r["pass_scores"]["p2"] > 0]
        assert p2_hits, f"expected at least one Pass 2 hit, got {results}"
        for r in p2_hits:
            assert 0 <= r["pass_scores"]["p2"] <= 1


# ---------------------------------------------------------------------------
# 4. Pass 3 — bitemporal timeline
# ---------------------------------------------------------------------------
class TestPass3Temporal:
    def test_pass3_surfaces_current_fact(self, mk_corpus):
        # Knowledge-update query: "what is Sarah's role now?" The newer
        # senior_engineer fact (2024-07) should outrank junior_engineer (2023).
        results = mk_corpus.search_multi("Sarah role", passes=3, top_k=10)
        p3_hits = [r for r in results if r["pass_scores"]["p3"] > 0]
        assert p3_hits, f"expected Pass 3 hits, got {results}"

        # Find role facts and confirm senior outranks junior on p3 score.
        seniors = [
            r for r in p3_hits
            if r.get("_value") == "senior_engineer"
        ]
        juniors = [
            r for r in p3_hits
            if r.get("_value") == "junior_engineer"
        ]
        assert seniors and juniors, (
            f"missing role facts in p3 hits: {p3_hits}"
        )
        assert seniors[0]["pass_scores"]["p3"] > juniors[0]["pass_scores"]["p3"]

    def test_pass3_marks_open_facts(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah role", passes=3, top_k=10)
        open_facts = [r for r in results if r.get("_is_open")]
        assert open_facts, "expected at least one open fact"


# ---------------------------------------------------------------------------
# 5. Score blending
# ---------------------------------------------------------------------------
class TestScoreBlending:
    def test_blend_weights(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah Google", passes=3, top_k=10)
        for r in results:
            ps = r["pass_scores"]
            expected = round(0.5 * ps["p1"] + 0.3 * ps["p2"] + 0.2 * ps["p3"], 4)
            assert abs(r["score"] - expected) < 1e-3, (
                f"score blend mismatch: {r['score']} vs expected {expected} "
                f"from {ps}"
            )

    def test_results_sorted_by_score_desc(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=10)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 6. Deduplication
# ---------------------------------------------------------------------------
class TestDeduplication:
    def test_no_duplicate_files(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=20)
        files = [r.get("file") for r in results if r.get("file")]
        assert len(files) == len(set(files)), (
            f"duplicate files in results: {files}"
        )

    def test_no_duplicate_entities(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah Google", passes=3, top_k=20)
        # Pass 2 (graph) hits dedup per (entity, relation, neighbor_of):
        # the same entity may appear via different relations.
        graph_keys = [
            (
                (r.get("match") or "").lower(),
                r.get("_relation", ""),
                r.get("_neighbor_of", ""),
            )
            for r in results
            if r.get("_relation") is not None
        ]
        assert len(graph_keys) == len(set(graph_keys)), (
            f"duplicate graph hits: {graph_keys}"
        )
        # Pass 3 (temporal) hits dedup per (entity, key, value): two
        # different facts about the same entity must stay separate.
        fact_keys = [
            (
                (r.get("_entity") or "").lower(),
                r.get("_key", ""),
                r.get("_value", ""),
            )
            for r in results
            if r.get("_entity") is not None
        ]
        assert len(fact_keys) == len(set(fact_keys)), (
            f"duplicate fact hits: {fact_keys}"
        )


# ---------------------------------------------------------------------------
# 7. top_k handling
# ---------------------------------------------------------------------------
class TestTopK:
    def test_top_k_respected(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=2)
        assert len(results) <= 2

    def test_invalid_top_k_falls_back(self, mk_corpus):
        # 0 / negative / wrong type → clamped to default.
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=0)
        assert isinstance(results, list)
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=-3)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# 8. passes parameter (1 / 2 / 3)
# ---------------------------------------------------------------------------
class TestPassesParameter:
    def test_passes_1_only_pass1(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=1, top_k=10)
        for r in results:
            assert r["pass_scores"]["p2"] == 0
            assert r["pass_scores"]["p3"] == 0

    def test_passes_2_excludes_pass3(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=2, top_k=10)
        for r in results:
            assert r["pass_scores"]["p3"] == 0

    def test_passes_3_full(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=20)
        # Across the whole result set, at least one hit from p2 and p3 should appear.
        any_p2 = any(r["pass_scores"]["p2"] > 0 for r in results)
        any_p3 = any(r["pass_scores"]["p3"] > 0 for r in results)
        assert any_p2, f"no p2 hit in passes=3 run: {results}"
        assert any_p3, f"no p3 hit in passes=3 run: {results}"

    def test_passes_clamped(self, mk_corpus):
        # passes >3 is treated as 3, passes <1 as 1 — must not raise.
        a = mk_corpus.search_multi("Sarah", passes=99, top_k=5)
        b = mk_corpus.search_multi("Sarah", passes=0, top_k=5)
        assert isinstance(a, list)
        assert isinstance(b, list)


# ---------------------------------------------------------------------------
# 9. Result structure contract
# ---------------------------------------------------------------------------
class TestResultContract:
    def test_required_keys(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah", passes=3, top_k=5)
        assert results, "expected non-empty result for sanity check"
        for r in results:
            assert "score" in r
            assert "pass_scores" in r
            assert "source_passes" in r
            assert isinstance(r["pass_scores"], dict)
            assert {"p1", "p2", "p3"} <= set(r["pass_scores"].keys())

    def test_source_passes_consistent(self, mk_corpus):
        results = mk_corpus.search_multi("Sarah Google", passes=3, top_k=20)
        for r in results:
            sp = set(r.get("source_passes", []))
            ps = r["pass_scores"]
            for n in (1, 2, 3):
                if ps[f"p{n}"] > 0:
                    assert n in sp, f"source_passes missing pass {n}: {r}"
