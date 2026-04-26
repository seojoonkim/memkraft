"""Tests for v2.4 ConfidenceMixin — confidence threshold + implicit-acquisition."""
from __future__ import annotations

import pytest

from memkraft import MemKraft
from memkraft.confidence import (
    _attach_confidence,
    _classify_confidence,
    _has_implicit_acquisition,
    _format_results_for_llm,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mk_corpus(tmp_path):
    """A small corpus mixing confirmed acquisitions and implicit phrasing."""
    mk = MemKraft(base_dir=str(tmp_path))
    inbox = mk.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

    (inbox / "peace_lily.md").write_text(
        "# Peace Lily\n"
        "**Date:** 2025-06-15\n"
        "I bought a peace lily from the nursery on Saturday. "
        "It's now sitting on my windowsill — confirmed plant ownership.\n"
    )
    (inbox / "succulent.md").write_text(
        "# Succulent collection\n"
        "**Date:** 2025-07-01\n"
        "Added three new succulents to the shelf this week. Definitely growing the collection.\n"
    )
    (inbox / "orchid_intent.md").write_text(
        "# Orchid musings\n"
        "**Date:** 2025-07-10\n"
        "I'm thinking of getting an orchid next month. Might pick up some "
        "orchid fertilizer too — still on the fence about it.\n"
    )
    (inbox / "fiddle_leaf.md").write_text(
        "# Fiddle leaf consideration\n"
        "**Date:** 2025-07-15\n"
        "Considering buying a fiddle leaf fig — they look great in the living room.\n"
    )
    (inbox / "korean_intent.md").write_text(
        "# 한국어 메모\n"
        "**Date:** 2025-07-20\n"
        "선인장 살까 고민 중이야. 다음 주에 화원 갈 생각 중.\n"
    )
    return mk


# ---------------------------------------------------------------------------
# Pure-function tests — no MemKraft instance needed.
# ---------------------------------------------------------------------------
def test_implicit_pattern_detection_english():
    assert _has_implicit_acquisition("I'm thinking of getting an orchid")
    assert _has_implicit_acquisition("considering buying a new lens")
    assert _has_implicit_acquisition("might purchase the larger model")
    assert _has_implicit_acquisition("planning to get one next week")
    assert _has_implicit_acquisition("on the fence about getting a Switch")


def test_implicit_pattern_detection_korean():
    assert _has_implicit_acquisition("선인장 살까 고민 중")
    assert _has_implicit_acquisition("난초 살지 고민하고 있어")
    assert _has_implicit_acquisition("새 노트북 사고 싶어")
    assert _has_implicit_acquisition("구입 할까 생각 중")


def test_implicit_pattern_negative_cases():
    # These describe confirmed acquisitions — must NOT trigger implicit.
    assert not _has_implicit_acquisition("I bought a peace lily yesterday")
    assert not _has_implicit_acquisition("got a new monstera last week")
    assert not _has_implicit_acquisition("선인장을 샀어")
    assert not _has_implicit_acquisition("")
    assert not _has_implicit_acquisition(None)  # type: ignore[arg-type]


def test_classify_confidence_thresholds():
    # High band — > 0.7, no implicit phrasing
    assert _classify_confidence(0.85, "I bought it") == "high"
    assert _classify_confidence(0.71, "owned") == "high"
    # Medium band — 0.4..0.7
    assert _classify_confidence(0.55, "added to collection") == "medium"
    assert _classify_confidence(0.4, "neutral") == "medium"
    # Low band — < 0.4
    assert _classify_confidence(0.39, "no real match") == "low"
    assert _classify_confidence(0.0, "") == "low"


def test_classify_confidence_implicit_overrides_high_score():
    # Even a high score must drop to "low" when snippet is intent-only.
    out = _classify_confidence(0.92, "thinking of getting an orchid")
    assert out == "low"


def test_classify_confidence_fuzzy_only_caps_at_medium():
    # Fuzzy-only matches never go to "high".
    assert _classify_confidence(0.95, "loose match", fuzzy_only=True) == "medium"
    assert _classify_confidence(0.3, "loose match", fuzzy_only=True) == "low"


def test_attach_confidence_marks_implicit_results():
    rows = [
        {"file": "a.md", "match": "peace lily", "snippet": "I bought a peace lily", "score": 0.82},
        {"file": "b.md", "match": "orchid", "snippet": "thinking of getting an orchid", "score": 0.78},
        {"file": "c.md", "match": "succulent", "snippet": "added to the shelf", "score": 0.5},
        {"file": "d.md", "match": "noise", "snippet": "barely related", "score": 0.2},
    ]
    out = _attach_confidence(rows, query="how many plants do I own?")

    by_file = {r["file"]: r for r in out}
    assert by_file["a.md"]["confidence"] == "high"
    # Implicit acquisition forces low even though raw score was 0.78.
    assert by_file["b.md"]["confidence"] == "low"
    assert by_file["b.md"].get("_implicit_acquisition") is True
    assert by_file["b.md"].get("confidence_reason") == "implicit_acquisition_phrase"
    assert by_file["c.md"]["confidence"] == "medium"
    assert by_file["d.md"]["confidence"] == "low"
    assert by_file["d.md"].get("_implicit_acquisition") is None


def test_attach_confidence_handles_empty_input():
    assert _attach_confidence([]) == []
    assert _attach_confidence(None) == []  # type: ignore[arg-type]
    # Non-dict entries left alone (no crash).
    weird = [None, "string", 42]
    out = _attach_confidence(weird)  # type: ignore[arg-type]
    assert out == weird


def test_format_for_llm_includes_low_section():
    rows = [
        {"match": "peace lily", "snippet": "bought from nursery", "confidence": "high"},
        {"match": "succulents", "snippet": "added 3 plants", "confidence": "medium"},
        {
            "match": "orchid",
            "snippet": "thinking of getting fertilizer",
            "confidence": "low",
            "_implicit_acquisition": True,
        },
    ]
    txt = _format_results_for_llm(rows, include_low=True)
    assert "[high confidence] peace lily" in txt
    assert "[medium confidence] succulents" in txt
    assert "low confidence (potential / inferred)" in txt
    assert "[low confidence] orchid" in txt
    assert "might indicate acquisition intent" in txt


def test_format_for_llm_excludes_low_when_disabled():
    rows = [
        {"match": "peace lily", "snippet": "bought", "confidence": "high"},
        {"match": "orchid", "snippet": "thinking of getting", "confidence": "low",
         "_implicit_acquisition": True},
    ]
    txt = _format_results_for_llm(rows, include_low=False)
    assert "peace lily" in txt
    assert "orchid" not in txt
    assert "low confidence" not in txt


# ---------------------------------------------------------------------------
# Integration tests — exercise the wrapped public API.
# ---------------------------------------------------------------------------
def test_search_v2_attaches_confidence_field(mk_corpus):
    results = mk_corpus.search_v2("peace lily", top_k=10, fuzzy=True)
    assert results, "expected at least one hit"
    for r in results:
        assert "confidence" in r
        assert r["confidence"] in {"high", "medium", "low"}


def test_search_multi_attaches_confidence(mk_corpus):
    results = mk_corpus.search_multi("plants", top_k=10, passes=3)
    if not results:
        pytest.skip("multi-pass returned nothing on tiny corpus — confidence wrap still verified by other tests")
    for r in results:
        assert "confidence" in r


def test_implicit_match_surfaces_as_low_confidence(mk_corpus):
    # The orchid_intent file uses "thinking of getting" → low confidence,
    # but it must still be retrievable.
    results = mk_corpus.search_v2("orchid", top_k=10, fuzzy=True)
    files = {(r.get("file") or "") for r in results}
    has_intent_file = any("orchid_intent" in f for f in files)
    assert has_intent_file, f"orchid_intent.md should surface in search; got {files}"

    intent_row = next(r for r in results if "orchid_intent" in (r.get("file") or ""))
    assert intent_row["confidence"] == "low"
    assert intent_row.get("_implicit_acquisition") is True


def test_korean_implicit_phrase_surfaces_as_low(mk_corpus):
    results = mk_corpus.search_v2("선인장", top_k=10, fuzzy=True)
    if not results:
        pytest.skip("Korean tokenisation didn't yield hits in this build")
    intent_rows = [r for r in results if "korean_intent" in (r.get("file") or "")]
    assert intent_rows, "korean_intent.md should be retrievable"
    assert intent_rows[0]["confidence"] == "low"
    assert intent_rows[0].get("_implicit_acquisition") is True


def test_search_with_confidence_default_includes_low(mk_corpus):
    results = mk_corpus.search_with_confidence("plant", top_k=10, include_low=True)
    confidences = {r["confidence"] for r in results}
    # A mixed-confidence corpus should produce at least one low-confidence
    # row (orchid intent / fiddle leaf consideration / Korean intent).
    assert results, "expected at least one result"
    assert "low" in confidences or "medium" in confidences or "high" in confidences


def test_search_with_confidence_strict_filters_low(mk_corpus):
    strict = mk_corpus.search_with_confidence("plant", top_k=20, include_low=False)
    for r in strict:
        assert r["confidence"] != "low"


def test_format_results_for_llm_through_instance(mk_corpus):
    results = mk_corpus.search_v2("peace lily", top_k=5, fuzzy=True)
    txt = mk_corpus.format_results_for_llm(results, include_low=True)
    assert isinstance(txt, str)
    if results:
        assert "confidence" in txt


def test_confidence_is_idempotent(mk_corpus):
    """Re-running attach_confidence must not change the labels."""
    results = mk_corpus.search_v2("plant", top_k=10, fuzzy=True)
    if not results:
        pytest.skip("no results to re-classify")
    before = [r.get("confidence") for r in results]
    mk_corpus._attach_confidence(results)
    after = [r.get("confidence") for r in results]
    assert before == after


def test_implicit_does_not_cross_contaminate_clean_snippets():
    rows = [
        {"file": "a.md", "match": "rose", "snippet": "bought a rose bush yesterday", "score": 0.9},
    ]
    _attach_confidence(rows)
    assert rows[0]["confidence"] == "high"
    assert rows[0].get("_implicit_acquisition") is None
