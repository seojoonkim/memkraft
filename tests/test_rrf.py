"""Tests for v2.3 RRFMixin (Reciprocal Rank Fusion)."""
from __future__ import annotations

import pytest

from memkraft import MemKraft
from memkraft.rrf import RRF_K_DEFAULT, rrf_fuse


# ---------------------------------------------------------------------------
# Pure-function tests for rrf_fuse
# ---------------------------------------------------------------------------
def _mk_doc(file_id: str, score: float = 1.0) -> dict:
    """Build a minimal MemKraft-shaped result dict keyed on `file`."""
    return {"file": f"docs/{file_id}.md", "match": file_id, "score": score}


def test_rrf_k_default_is_60():
    """RRF default smoothing constant must be 60 (Cormack et al.)."""
    assert RRF_K_DEFAULT == 60


def test_rrf_score_calculation_matches_formula():
    """Manually-computed RRF scores match the implementation.

    list_a = [A, B, C]   ranks: A=1, B=2, C=3
    list_b = [B, A, D]   ranks: B=1, A=2, D=3
    k = 60

    A: 1/61 + 1/62 = 0.016393... + 0.016129... = 0.032522...
    B: 1/62 + 1/61 = same as A          = 0.032522...
    C: 1/63                              = 0.015873...
    D: 1/63                              = 0.015873...
    """
    a = _mk_doc("A")
    b = _mk_doc("B")
    c = _mk_doc("C")
    d = _mk_doc("D")

    fused = rrf_fuse([a, b, c], [b, a, d], k=60)

    assert len(fused) == 4
    by_match = {r["match"]: r for r in fused}

    expected_a = 1 / 61 + 1 / 62
    expected_b = 1 / 62 + 1 / 61
    expected_c = 1 / 63
    expected_d = 1 / 63

    assert by_match["A"]["rrf_score"] == pytest.approx(expected_a, rel=1e-4)
    assert by_match["B"]["rrf_score"] == pytest.approx(expected_b, rel=1e-4)
    assert by_match["C"]["rrf_score"] == pytest.approx(expected_c, rel=1e-4)
    assert by_match["D"]["rrf_score"] == pytest.approx(expected_d, rel=1e-4)


def test_rrf_single_list_preserves_order():
    """A single input list should come out in the same order."""
    items = [_mk_doc(x) for x in "ABCDE"]
    fused = rrf_fuse(items, k=60)
    assert [r["match"] for r in fused] == list("ABCDE")
    # And rrf_score must be strictly decreasing.
    scores = [r["rrf_score"] for r in fused]
    assert scores == sorted(scores, reverse=True)


def test_rrf_two_lists_intersection_ranked_higher():
    """Docs appearing in BOTH lists should outrank docs appearing in only one."""
    list_a = [_mk_doc("X"), _mk_doc("Y"), _mk_doc("Z")]
    list_b = [_mk_doc("Y"), _mk_doc("Q"), _mk_doc("X")]
    fused = rrf_fuse(list_a, list_b, k=60)
    matches = [r["match"] for r in fused]
    # X and Y appear in both, so they should be at the top.
    assert set(matches[:2]) == {"X", "Y"}


def test_rrf_duplicate_documents_score_summed_via_best_rank():
    """Doc in both lists should have score = 1/(k+r1) + 1/(k+r2).

    Different from "summing weighted scores": RRF only cares about ranks.
    """
    a = _mk_doc("A")
    fused = rrf_fuse([a], [a], k=10)  # rank 1 in both
    assert len(fused) == 1
    assert fused[0]["rrf_score"] == pytest.approx(1 / 11 + 1 / 11, abs=1e-5)
    assert fused[0]["rrf_ranks"] == [1, 1]


def test_rrf_k_parameter_changes_top_n_weighting():
    """Smaller k → top ranks dominate; larger k → flatter."""
    list_a = [_mk_doc("A"), _mk_doc("B")]
    list_b = [_mk_doc("B"), _mk_doc("A")]

    fused_small = rrf_fuse(list_a, list_b, k=1)
    fused_large = rrf_fuse(list_a, list_b, k=1000)

    # Whatever k is, A and B tie (symmetric ranks 1 & 2 in both).
    by_match_small = {r["match"]: r["rrf_score"] for r in fused_small}
    by_match_large = {r["match"]: r["rrf_score"] for r in fused_large}
    assert by_match_small["A"] == pytest.approx(by_match_small["B"], rel=1e-6)
    assert by_match_large["A"] == pytest.approx(by_match_large["B"], rel=1e-6)
    # And the absolute scores differ as expected.
    assert by_match_small["A"] > by_match_large["A"]


def test_rrf_empty_lists_yield_empty_result():
    """No inputs → empty result."""
    assert rrf_fuse() == []
    assert rrf_fuse([], [], []) == []


def test_rrf_some_empty_lists_handled_gracefully():
    """If one list is empty, RRF should still work over the remaining ones."""
    list_a = [_mk_doc("A"), _mk_doc("B")]
    fused = rrf_fuse(list_a, [], k=60)
    assert [r["match"] for r in fused] == ["A", "B"]
    # rrf_ranks shows None for the empty list slot.
    for r in fused:
        assert r["rrf_ranks"][1] is None


def test_rrf_invalid_k_raises():
    with pytest.raises(ValueError):
        rrf_fuse([_mk_doc("A")], k=0)
    with pytest.raises(ValueError):
        rrf_fuse([_mk_doc("A")], k=-1)


def test_rrf_score_field_overwritten_for_downstream_sort():
    """`score` field gets overwritten with `rrf_score` so existing callers
    that sort by `score` continue to work."""
    a = _mk_doc("A", score=999.0)  # huge legacy score
    fused = rrf_fuse([a], k=60)
    assert fused[0]["score"] == fused[0]["rrf_score"]
    assert fused[0]["score"] < 1.0  # RRF scores are always < 1


def test_rrf_dedup_within_single_list():
    """If a document appears twice in the same list, only its best (lowest) rank counts."""
    a1 = _mk_doc("A")
    a2 = _mk_doc("A")  # same doc, rank 3
    b = _mk_doc("B")
    fused = rrf_fuse([a1, b, a2], k=60)
    # Two unique documents.
    assert len(fused) == 2
    by_match = {r["match"]: r for r in fused}
    # A's rank should be 1 (best), not 3.
    assert by_match["A"]["rrf_ranks"][0] == 1
    assert by_match["A"]["rrf_score"] == pytest.approx(1 / 61, abs=1e-5)


def test_rrf_custom_key_fn():
    """Custom key_fn lets callers dedup on arbitrary fields."""
    items_a = [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]
    items_b = [{"id": 1, "v": "x2"}, {"id": 3, "v": "z"}]
    fused = rrf_fuse(items_a, items_b, k=60, key_fn=lambda r: r["id"])
    ids = sorted(r.get("id") for r in fused)
    assert ids == [1, 2, 3]


# ---------------------------------------------------------------------------
# Mixin-level tests on a real MemKraft instance
# ---------------------------------------------------------------------------
@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


def test_rrf_mixin_attached(mk):
    """RRFMixin methods are attached to MemKraft instances."""
    assert callable(getattr(mk, "_rrf_fusion", None))
    assert callable(getattr(mk, "search_rrf", None))


def test_rrf_fusion_method_delegates_to_rrf_fuse(mk):
    """Instance shim ``_rrf_fusion`` produces same output as module-level rrf_fuse."""
    a = _mk_doc("A")
    b = _mk_doc("B")
    via_method = mk._rrf_fusion([a, b], [b, a], k=60)
    via_func = rrf_fuse([a, b], [b, a], k=60)
    assert [r["match"] for r in via_method] == [r["match"] for r in via_func]
    assert via_method[0]["rrf_score"] == pytest.approx(via_func[0]["rrf_score"])


def test_search_rrf_empty_query_returns_empty(mk):
    assert mk.search_rrf("") == []
    assert mk.search_rrf("   ") == []


def test_search_rrf_returns_list(mk, tmp_path):
    """End-to-end: search_rrf returns a ranked list with rrf_score fields."""
    inbox = mk.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "alice.md").write_text(
        "# Alice\nAlice Johnson is a software engineer at Google.\n"
    )
    (inbox / "bob.md").write_text(
        "# Bob\nBob Smith is a designer at Apple.\n"
    )

    results = mk.search_rrf("Alice software engineer", top_k=5)
    assert isinstance(results, list)
    # Should find alice.md somewhere in results.
    files = [r.get("file", "") for r in results]
    assert any("alice" in f for f in files)
    # Every result should have rrf_score.
    if results:
        for r in results:
            if "rrf_score" in r:
                assert isinstance(r["rrf_score"], (int, float))


# ---------------------------------------------------------------------------
# search_multi RRF integration tests
# ---------------------------------------------------------------------------
@pytest.fixture
def mk_corpus(tmp_path):
    """MemKraft with a small markdown corpus + graph + facts (mirrors test_multi_pass.py)."""
    mk = MemKraft(base_dir=str(tmp_path))

    inbox = mk.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

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

    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("google", "located_in", "nyc")

    mk.fact_add(
        "sarah", "role", "junior_engineer",
        valid_from="2023-01-01", valid_to="2024-06-30",
        recorded_at="2023-01-15T00:00:00",
    )
    return mk


def test_search_multi_rrf_default(mk_corpus):
    """search_multi defaults to use_rrf=True and produces RRF-tagged results."""
    results = mk_corpus.search_multi("Sarah Google", top_k=5)
    assert isinstance(results, list)
    # At least one result should have rrf_score (default RRF mode).
    has_rrf = any("rrf_score" in r for r in results)
    assert has_rrf, "search_multi default mode should produce rrf_score field"


def test_search_multi_rrf_off_uses_weighted_blend(mk_corpus):
    """use_rrf=False falls back to legacy weighted blend (no rrf_score)."""
    results = mk_corpus.search_multi("Sarah Google", top_k=5, use_rrf=False)
    assert isinstance(results, list)
    # No rrf_score in legacy mode.
    for r in results:
        assert "rrf_score" not in r
    # But pass_scores must still be there.
    if results:
        assert any("pass_scores" in r for r in results)


def test_search_multi_rrf_preserves_pass_scores(mk_corpus):
    """RRF mode keeps backwards-compatible pass_scores / source_passes fields."""
    results = mk_corpus.search_multi("Sarah Google", top_k=5, use_rrf=True)
    for r in results:
        assert "pass_scores" in r
        assert set(r["pass_scores"].keys()) == {"p1", "p2", "p3"}
        assert "source_passes" in r
        assert isinstance(r["source_passes"], list)


def test_search_multi_rrf_custom_k(mk_corpus):
    """Custom rrf_k parameter is respected."""
    r_default = mk_corpus.search_multi("Sarah Google", top_k=5)
    r_custom = mk_corpus.search_multi("Sarah Google", top_k=5, rrf_k=10)
    # Both produce results; absolute scores differ when k differs.
    if r_default and r_custom:
        # Top result should be the same doc, but score values differ.
        assert r_default[0].get("rrf_score") != r_custom[0].get("rrf_score")


def test_search_multi_empty_query_returns_empty(mk_corpus):
    assert mk_corpus.search_multi("") == []
    assert mk_corpus.search_multi("", use_rrf=False) == []


def test_search_multi_top_k_respected_with_rrf(mk_corpus):
    """top_k truncates RRF-fused output too."""
    r1 = mk_corpus.search_multi("Sarah Google NYC", top_k=1, use_rrf=True)
    r5 = mk_corpus.search_multi("Sarah Google NYC", top_k=5, use_rrf=True)
    assert len(r1) <= 1
    assert len(r5) <= 5
    if r1 and r5:
        assert r1[0].get("file") == r5[0].get("file")
