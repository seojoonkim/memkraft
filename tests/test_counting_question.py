"""Tests for the counting-question strategy (RoutingMixin).

The counting bucket targets "How many / how much / how often / 몇" style
queries where the failure mode is **under-recall** — missing a single
item makes the answer wrong.  The strategy fans out across five logical
passes (exact, fuzzy, keyword variants, ``search_multi(passes=3)``, and
``search_expand``) and merges the unions.

Coverage:
  * Classifier — 8+ EN/KO cases route to ``counting`` and the bucket
    wins over neighbours that share keywords (``how often`` no longer
    falls into ``multi_session``).
  * Strategy — exhaustive sweep returns ≥ baseline ``search`` recall on
    a seeded corpus.
  * Public API — ``search_smart_v2`` dispatches counting queries with a
    distinguishable strategy string and respects the ``exhaustive=``
    override.
  * Edge cases — empty/whitespace queries, top_k clamping, no-match
    queries fall back gracefully without raising.
"""
from __future__ import annotations

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mk(tmp_path):
    """Empty MemKraft instance — used for classifier-only tests."""
    return MemKraft(base_dir=str(tmp_path))


@pytest.fixture
def mk_counting(tmp_path):
    """Seeded corpus where counting questions have a clear ground truth.

    Sarah travels to N distinct cities, John reads M books, and Simon
    runs P projects — so "how many cities did Sarah visit?" has an
    objectively right answer the strategy can be checked against.
    """
    m = MemKraft(base_dir=str(tmp_path))

    # --- Sarah: 5 distinct travel records -------------------------------
    m.track("Sarah", entity_type="person", source="test")
    m.update("Sarah", "Sarah visited Tokyo for the agents conference",
             source="trip-1")
    m.update("Sarah", "Sarah travelled to Berlin for a workshop",
             source="trip-2")
    m.update("Sarah", "Sarah visited Singapore last spring",
             source="trip-3")
    m.update("Sarah", "Sarah travelled to New York twice this year",
             source="trip-4")
    m.update("Sarah", "Sarah visited Lisbon over the summer",
             source="trip-5")

    # --- John: 4 distinct book reads ------------------------------------
    m.track("John", entity_type="person", source="test")
    m.update("John", "John finished reading 'The Pragmatic Programmer'",
             source="book-1")
    m.update("John", "John read 'Designing Data-Intensive Applications'",
             source="book-2")
    m.update("John", "John finished 'Clean Architecture'", source="book-3")
    m.update("John", "John just read 'Domain-Driven Design'",
             source="book-4")

    # --- Simon: 3 distinct project launches -----------------------------
    m.track("Simon", entity_type="person", source="test")
    m.update("Simon", "Simon launched VibeKai on 2025-01-15",
             source="launch-1")
    m.update("Simon", "Simon launched MemKraft on 2025-08-01",
             source="launch-2")
    m.update("Simon", "Simon launched AgentLinter on 2025-11-10",
             source="launch-3")

    return m


# ---------------------------------------------------------------------------
# 1. Classifier — counting bucket wins on the documented keyword set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "How many cities did Sarah visit?",
    "How much coffee does John drink per day?",
    "How often does Sarah travel for work?",
    "How long has Simon worked at Hashed?",
    "Sarah가 몇 개 도시를 방문했어?",
    "John이 몇 권의 책을 읽었어?",
    "Simon이 몇 번이나 발표했어?",
    "프로젝트가 얼마나 많은지 알려줘",
])
def test_counting_classifier_basic(mk, query):
    assert mk._classify_question(query) == "counting"


def test_counting_wins_over_multi_session(mk):
    """When 'how often' appears the counting bucket wins over the older
    multi_session frequency cue (counting is checked first)."""
    assert mk._classify_question("how often does she travel?") == "counting"
    # And mixed signals where compare also appears → counting still wins
    # because counting is at the head of _TYPE_ORDER and 'how often' is
    # an unambiguous quantification marker.
    assert mk._classify_question(
        "how often do Sarah and John compare notes?"
    ) == "counting"


def test_counting_does_not_swallow_unrelated_queries(mk):
    """Plain 'much/many/long' substrings must not trigger counting —
    the keywords are multi-word phrases.  Word-boundary handling for
    Latin tokens is already exercised in test_question_routing.py."""
    # 'much' alone (no 'how') — not counting.
    assert mk._classify_question("there is too much noise") != "counting"
    # 'long' alone — not counting (and doesn't match other buckets).
    assert mk._classify_question("a long story about agents") != "counting"
    # CJK without quantifier — not counting.
    assert mk._classify_question("그 일이 정말 많이 힘들었어") != "counting"


def test_counting_ko_quantifiers(mk):
    """Korean '몇' / '얼마나' (with quantifier context) route to counting."""
    assert mk._classify_question("몇 명이나 참석했어?") == "counting"
    assert mk._classify_question("미팅이 몇 번 있었지?") == "counting"
    assert mk._classify_question("얼마나 많은 도시를 방문했어?") == "counting"


# ---------------------------------------------------------------------------
# 2. Strategy method — exhaustive sweep returns wider candidate pool
# ---------------------------------------------------------------------------

def test_counting_strategy_returns_list(mk_counting):
    out = mk_counting._search_counting("How many cities did Sarah visit?",
                                       top_k=5)
    assert isinstance(out, list)


def test_counting_strategy_recall_meets_or_exceeds_baseline(mk_counting):
    """The exhaustive sweep must surface at least as many distinct files
    as a plain fuzzy ``search`` would — that's the whole point of routing
    counting queries here.
    """
    query = "How many cities did Sarah visit?"

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        baseline = mk_counting.search("Sarah cities visited", fuzzy=True)
    baseline_files = {r.get("file") for r in baseline if r.get("file")}

    counting = mk_counting._search_counting(query, top_k=20)
    counting_files = {r.get("file") for r in counting if r.get("file")}

    # Counting sweep should cover every file the baseline did.
    missing = baseline_files - counting_files
    assert not missing, (
        f"counting sweep missed files baseline found: {missing}"
    )


def test_counting_strategy_provenance_annotation(mk_counting):
    """Every hit carries ``_counting_passes`` listing the passes that
    surfaced it — useful for debugging recall holes."""
    out = mk_counting._search_counting("How many books did John read?",
                                       top_k=10)
    if not out:
        pytest.skip("no hits on seeded corpus — provenance check N/A")
    for r in out:
        assert "_counting_passes" in r, f"missing provenance on {r}"
        assert "_counting_pass_count" in r
        assert isinstance(r["_counting_passes"], list)
        # Pass labels must be from the documented set.
        for label in r["_counting_passes"]:
            assert label in {"A", "B", "C", "D", "E"}, label


def test_counting_strategy_empty_query_returns_empty(mk_counting):
    assert mk_counting._search_counting("", top_k=5) == []
    assert mk_counting._search_counting("   ", top_k=5) == []


# ---------------------------------------------------------------------------
# 3. Public API — search_smart_v2 dispatch + override
# ---------------------------------------------------------------------------

def test_smart_v2_routes_counting_queries(mk_counting):
    out = mk_counting.search_smart_v2(
        "How many cities did Sarah visit?", top_k=10,
    )
    assert out["question_type"] == "counting"
    assert "counting" in out["strategy"].lower()
    assert isinstance(out["results"], list)


def test_smart_v2_counting_top_k_respected(mk_counting):
    out = mk_counting.search_smart_v2(
        "How many books did John read?", top_k=2,
    )
    assert len(out["results"]) <= 2


def test_smart_v2_exhaustive_override_forces_counting(mk_counting):
    """``exhaustive=True`` upgrades any query to the counting strategy."""
    # A plain 'general' query that wouldn't normally route to counting:
    out = mk_counting.search_smart_v2("Sarah", top_k=5, exhaustive=True)
    assert out["question_type"] == "counting"
    assert "counting" in out["strategy"].lower()


def test_smart_v2_exhaustive_false_opts_out(mk_counting):
    """``exhaustive=False`` skips the counting bucket even when keywords
    would have routed there."""
    out = mk_counting.search_smart_v2(
        "How many cities did Sarah visit?", top_k=5, exhaustive=False,
    )
    assert out["question_type"] != "counting"


def test_smart_v2_counting_fallback_on_no_match(mk_counting):
    """An unanswerable counting query still returns gracefully."""
    out = mk_counting.search_smart_v2(
        "How many quasar foobars did Zzzqx encounter?", top_k=5,
    )
    assert out["question_type"] == "counting"
    assert isinstance(out["results"], list)
    assert out["strategy"]  # non-empty


# ---------------------------------------------------------------------------
# 4. Recall guarantee — counting strategy >= search_multi alone
# ---------------------------------------------------------------------------

def test_counting_strategy_at_least_as_wide_as_search_multi(mk_counting):
    """The counting sweep includes ``search_multi(passes=3)`` as one of
    its passes, so its result set must be a superset (by file) of what
    ``search_multi`` alone produces."""
    query = "How many books did John finish reading?"

    import contextlib
    import io

    smulti = getattr(mk_counting, "search_multi", None)
    if not callable(smulti):
        pytest.skip("search_multi not available on this build")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sm = smulti(query, top_k=20, passes=3)
    sm_files = {r.get("file") for r in sm if r.get("file")}

    counting = mk_counting._search_counting(query, top_k=20)
    counting_files = {r.get("file") for r in counting if r.get("file")}

    missing = sm_files - counting_files
    assert not missing, (
        f"counting failed to subsume search_multi: missing {missing}"
    )
