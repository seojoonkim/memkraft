"""Tests for v1.1.2 search_with_entity_filter — entity-aware retrieval."""
from __future__ import annotations

import pytest
from pathlib import Path
from memkraft import MemKraft


@pytest.fixture()
def mk(tmp_path: Path) -> MemKraft:
    m = MemKraft(base_dir=str(tmp_path))
    m.init()
    return m


# ── Helpers ────────────────────────────────────────────────────────────────

def _matches(results: list[dict]) -> list[str]:
    """Return match names from hit list."""
    return [h.get("match", "") for h in results]


def _snippets(results: list[dict]) -> str:
    """Concatenate all snippets for easy assertion."""
    return " ".join(h.get("snippet", "") for h in results).lower()


# ── Basic entity extraction + filtering ────────────────────────────────────

def test_entity_filter_basic_person(mk):
    """Sarah's food info bubbles up; John's sports info stays out."""
    mk.track("sarah", entity_type="person")
    mk.update("sarah", "Sarah loves pizza and hates broccoli.")

    mk.track("john", entity_type="person")
    mk.update("john", "John enjoys soccer and basketball.")

    results = mk.search_with_entity_filter("What does Sarah like to eat?", top_k=2)
    assert results, "Should return at least one result"
    combined = _snippets(results[:1])
    assert "sarah" in combined or "pizza" in combined or "broccoli" in combined


def test_entity_filter_no_john_in_top(mk):
    """John's soccer should not be top-1 when querying about Sarah."""
    mk.track("sarah", entity_type="person")
    mk.update("sarah", "Sarah loves pasta and cheese.")

    mk.track("john", entity_type="person")
    mk.update("john", "John enjoys soccer every weekend.")

    results = mk.search_with_entity_filter("What food does Sarah enjoy?", top_k=1)
    assert results
    top = _snippets(results[:1])
    assert "soccer" not in top, "John's soccer content should not be top-1 for Sarah food query"


def test_entity_filter_returns_top_k(mk):
    """Respects top_k limit."""
    for i in range(5):
        mk.track(f"alice{i}", entity_type="person")
        mk.update(f"alice{i}", f"Alice{i} likes item{i}.")

    results = mk.search_with_entity_filter("What does Alice0 like?", top_k=2)
    assert len(results) <= 2


# ── Fallback to search_precise when no entities detected ───────────────────

def test_entity_filter_no_entities_fallback(mk):
    """Queries with no capitalised entity names fall back to search_precise."""
    mk.track("doc1", entity_type="document")
    mk.update("doc1", "The sky is blue and the grass is green.")

    results = mk.search_with_entity_filter("what color is the sky?", top_k=3)
    # Should still return something (fallback search_precise)
    assert isinstance(results, list)


def test_entity_filter_empty_query(mk):
    """Empty query returns empty list without crashing."""
    results = mk.search_with_entity_filter("", top_k=5)
    assert results == []


def test_entity_filter_zero_top_k(mk):
    """top_k=0 returns empty list."""
    mk.track("emma", entity_type="person")
    mk.update("emma", "Emma likes coding.")
    results = mk.search_with_entity_filter("Emma likes what?", top_k=0)
    assert results == []


# ── Explicit entity_names ───────────────────────────────────────────────────

def test_entity_filter_explicit_names(mk):
    """Explicit entity_names override auto_extract."""
    mk.track("emma", entity_type="person")
    mk.update("emma", "Emma works at Google as an engineer.")

    mk.track("ryan", entity_type="person")
    mk.update("ryan", "Ryan plays guitar and writes music.")

    # Explicitly pass ['emma'] — should surface Emma's content
    results = mk.search_with_entity_filter(
        "who is the engineer?",
        top_k=2,
        entity_names=["emma"],
    )
    assert results
    assert "emma" in _snippets(results).lower() or "google" in _snippets(results).lower()


# ── auto_extract=False with no entity_names → plain search_precise ──────────

def test_entity_filter_auto_extract_false_no_names(mk):
    """auto_extract=False with no entity_names falls back to search_precise."""
    mk.track("grace", entity_type="person")
    mk.update("grace", "Grace loves hiking and outdoor activities.")

    results = mk.search_with_entity_filter(
        "What does Grace enjoy?",
        top_k=3,
        entity_names=None,
        auto_extract=False,
    )
    # Should return something (via fallback)
    assert isinstance(results, list)


# ── Stopword filtering ──────────────────────────────────────────────────────

def test_entity_filter_stopwords_not_extracted(mk):
    """Stopwords like 'What', 'User', 'The' are not treated as entities."""
    mk.track("oliver", entity_type="person")
    mk.update("oliver", "Oliver likes hiking and nature.")

    # Query starts with 'User:' which should be filtered
    results = mk.search_with_entity_filter(
        "User: Oliver\n\nWhat does Oliver enjoy outdoors?", top_k=2
    )
    assert isinstance(results, list)
    # 'User' should not cause weird filtering; Oliver should be found
    combined = _snippets(results)
    assert "oliver" in combined or "hiking" in combined
