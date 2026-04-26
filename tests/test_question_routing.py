"""Tests for v2.2 Question-Type Routing (RoutingMixin).

Coverage:
  * ``_classify_question`` — keyword-based bucketing across 5 LongMemEval
    question types + ``general`` fallback (KO + EN, 15+ cases).
  * ``search_smart_v2`` — strategy dispatch produces a typed dict and
    falls back gracefully on empty corpora / ambiguous queries.
  * Each strategy method returns a list (no exceptions) on a small
    seeded corpus.
  * Edge cases: empty query, whitespace-only query, single-token query.
"""
from __future__ import annotations

import os
import time

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
def mk_seeded(tmp_path):
    """MemKraft seeded with a small multi-entity, multi-date corpus."""
    m = MemKraft(base_dir=str(tmp_path))

    # --- Entity 1: Sarah — career timeline -------------------------------
    m.track("Sarah", entity_type="person", source="test")
    m.update("Sarah", "Joined Hashed as engineer on 2023-05-01", source="test")
    m.update("Sarah", "Promoted to lead in 2024-03-10", source="test")
    m.update("Sarah", "Currently working on agents project", source="test")

    # Bitemporal facts for Sarah (knowledge updates)
    m.fact_add("Sarah", "role", "engineer", valid_from="2023-05-01",
               valid_to="2024-03-10")
    m.fact_add("Sarah", "role", "lead", valid_from="2024-03-10")

    # --- Entity 2: John — preferences ------------------------------------
    m.track("John", entity_type="person", source="test")
    m.update("John", "John loves espresso and prefers dark roast", source="test")
    m.update("John", "John hates instant coffee", source="test")
    m.update("John", "John's favorite movie is Inception", source="test")

    # --- Entity 3: Simon — generic project notes -------------------------
    m.track("Simon", entity_type="person", source="test")
    m.update("Simon", "Simon launched VibeKai on 2025-01-15", source="test")
    m.update("Simon", "Simon launched MemKraft on 2025-08-01", source="test")

    # Force distinct mtimes so recency ordering is deterministic.
    base = m.base_dir
    deltas = {
        "live-notes/sarah.md": 100,   # newest (== current)
        "live-notes/john.md": 50,     # middle
        "live-notes/simon.md": 0,     # oldest
    }
    now = time.time()
    for rel, off in deltas.items():
        p = base / rel
        if p.exists():
            mt = now - (200 - off)  # sarah → most recent
            os.utime(p, (mt, mt))

    return m


# ---------------------------------------------------------------------------
# 1. Classifier accuracy — 5 types × 3 cases (KO + EN mix) + edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    # single_session ------------------------------------------------------
    ("When did Sarah join Hashed?",      "single_session"),
    ("what did simon say about agents",  "single_session"),
    ("언제 Hashed 합류했어?",              "single_session"),
    # multi_session -------------------------------------------------------
    ("Compare Sarah and John performance", "multi_session"),
    # NOTE: "how often" was reclassified to ``counting`` in the v2.3+
    # routing layer — see test_counting_question.py for coverage.
    ("Sarah와 John 비교해줘",               "multi_session"),
    # knowledge_update ----------------------------------------------------
    ("What is the current CEO?",   "knowledge_update"),
    ("지금 어디 살아?",              "knowledge_update"),
    ("latest version of MemKraft", "knowledge_update"),
    # temporal_reasoning --------------------------------------------------
    ("What happened before the launch?", "temporal_reasoning"),
    ("first meeting with investor",      "temporal_reasoning"),
    ("Hashed 합류 전에 뭐했어?",          "temporal_reasoning"),
    # preference ----------------------------------------------------------
    ("What does Simon prefer for coffee?", "preference"),
    ("Sarah's favorite food",              "preference"),
    ("서준이 좋아하는 음악",                "preference"),
])
def test_classify_question_buckets(mk, query, expected):
    assert mk._classify_question(query) == expected


def test_classify_word_boundary_safety(mk):
    """Latin keywords match on word boundaries — 'now' inside 'Knows'
    must NOT trigger ``knowledge_update``.
    """
    # 'now' is a substring of 'KnowsThisName' but should not match.
    assert mk._classify_question("Sarah KnowsThisName fact") == "general"
    # 'like' inside 'unlikely' should not trigger ``preference``.
    assert mk._classify_question("unlikely event happened") == "general"
    # But standalone 'now' / 'like' DO trigger.
    assert mk._classify_question("what about now") == "knowledge_update"
    assert mk._classify_question("do you like it") == "preference"


def test_classify_priority_ordering(mk):
    """Multi-session beats knowledge-update when both signals present."""
    # "compare current X" → multi_session (compare wins over current)
    assert mk._classify_question("compare current and previous") == "multi_session"
    # "before now" → temporal_reasoning wins over knowledge_update
    assert mk._classify_question("what was true before now") == "temporal_reasoning"


def test_classify_general_fallback(mk):
    """Queries with no matching keyword fall to general."""
    assert mk._classify_question("Hashed") == "general"
    assert mk._classify_question("Sarah") == "general"


def test_classify_empty_and_whitespace(mk):
    assert mk._classify_question("") == "general"
    assert mk._classify_question("   ") == "general"
    assert mk._classify_question(None) == "general"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. search_smart_v2 — output shape + dispatch correctness
# ---------------------------------------------------------------------------

def test_smart_v2_return_shape(mk_seeded):
    out = mk_seeded.search_smart_v2("When did Sarah join?", top_k=5)
    assert isinstance(out, dict)
    assert set(out.keys()) == {"question_type", "results", "strategy"}
    assert out["question_type"] == "single_session"
    assert isinstance(out["results"], list)
    assert isinstance(out["strategy"], str) and out["strategy"]


def test_smart_v2_empty_query(mk_seeded):
    out = mk_seeded.search_smart_v2("", top_k=5)
    assert out["question_type"] == "general"
    assert out["results"] == []
    assert "empty" in out["strategy"].lower()


def test_smart_v2_top_k_respected(mk_seeded):
    out = mk_seeded.search_smart_v2("Sarah", top_k=2)
    assert len(out["results"]) <= 2


def test_smart_v2_dispatch_per_type(mk_seeded):
    """Each of the five buckets dispatches to a distinguishable strategy."""
    cases = {
        "single_session":     "When did Sarah join Hashed?",
        "multi_session":      "Compare Sarah and John",
        "knowledge_update":   "What is Sarah's current role?",
        "temporal_reasoning": "What did Sarah do before her promotion?",
        "preference":         "What coffee does John prefer?",
    }
    seen_strategies: set[str] = set()
    for expected_type, q in cases.items():
        out = mk_seeded.search_smart_v2(q, top_k=5)
        assert out["question_type"] == expected_type, f"{q} → {out['question_type']}"
        seen_strategies.add(out["strategy"].split(" \u2192")[0])  # strip fallback suffix
    # 5 different primary strategies were used.
    assert len(seen_strategies) == 5


# ---------------------------------------------------------------------------
# 3. Strategy methods — return list, no exceptions, on seeded corpus
# ---------------------------------------------------------------------------

def test_strategy_temporal_latest_runs(mk_seeded):
    out = mk_seeded._search_temporal_latest("Sarah current role")
    assert isinstance(out, list)
    # If we got results, the most recent file (sarah.md) should be near the top.
    if out:
        top_files = [r.get("file", "") for r in out[:3]]
        # sarah.md was forced to newest mtime → should appear in top 3.
        assert any("sarah" in f.lower() for f in top_files)


def test_strategy_temporal_timeline_runs(mk_seeded):
    out = mk_seeded._search_temporal_timeline("Sarah promotion timeline")
    assert isinstance(out, list)
    # When dated entries exist, results carrying _timeline_date come first
    # in chronological order.
    dated = [r for r in out if "_timeline_date" in r]
    if len(dated) >= 2:
        dates = [r["_timeline_date"] for r in dated]
        assert dates == sorted(dates), f"timeline not chronological: {dates}"


def test_strategy_preference_runs(mk_seeded):
    out = mk_seeded._search_preference("John coffee preference")
    assert isinstance(out, list)
    # John's note contains "loves" + "prefers" + "hates" + "favorite" → boost.
    if out:
        top = out[0]
        # Preference boost should be applied to at least one top hit.
        any_boosted = any("_preference_boost" in r for r in out[:3])
        assert any_boosted, f"no preference boost in top 3: {out[:3]}"


def test_strategy_multi_session_runs(mk_seeded):
    out = mk_seeded._search_multi_session("Compare Sarah and John")
    assert isinstance(out, list)


def test_strategy_methods_handle_empty_query(mk_seeded):
    assert mk_seeded._search_temporal_latest("") == []
    assert mk_seeded._search_temporal_timeline("") == []
    assert mk_seeded._search_preference("") == []
    assert mk_seeded._search_multi_session("") == []


# ---------------------------------------------------------------------------
# 4. Fallback behaviour — strategy returns nothing → fuzzy fallback kicks in
# ---------------------------------------------------------------------------

def test_smart_v2_fallback_on_no_match(mk_seeded):
    """A bizarre query routed to a strict bucket should still degrade
    gracefully via the universal fuzzy fallback (or return [], never raise).
    """
    out = mk_seeded.search_smart_v2(
        "What did Zzzqx say about QuasarFooBar?",
        top_k=5,
    )
    assert out["question_type"] == "single_session"
    assert isinstance(out["results"], list)
    # Strategy string is non-empty either way.
    assert out["strategy"]


def test_smart_v2_general_query_uses_fuzzy_fallback(mk_seeded):
    """A query with no routing keyword should land on the general bucket."""
    out = mk_seeded.search_smart_v2("Hashed", top_k=5)
    assert out["question_type"] == "general"
    assert "fuzzy" in out["strategy"].lower()


# ---------------------------------------------------------------------------
# 5. Edge cases — invalid top_k, weird input types
# ---------------------------------------------------------------------------

def test_smart_v2_invalid_top_k(mk_seeded):
    """Non-positive top_k clamps to default rather than raising."""
    out = mk_seeded.search_smart_v2("Sarah", top_k=0)
    assert isinstance(out["results"], list)
    out2 = mk_seeded.search_smart_v2("Sarah", top_k=-5)
    assert isinstance(out2["results"], list)


def test_smart_v2_results_are_dicts(mk_seeded):
    out = mk_seeded.search_smart_v2("Sarah", top_k=5)
    for r in out["results"]:
        assert isinstance(r, dict)
        # Every result the underlying search returns has a 'file' key.
        assert "file" in r or "match" in r
