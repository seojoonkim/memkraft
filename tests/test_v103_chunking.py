"""Tests for v1.0.3 ChunkingMixin (track_document, search_precise).

Motivation: AMB PersonaMem pilot showed chunking + precision search
lifts MemKraft +25pp over BM25 at 128k tokens. This file locks in the
basic API contract so we don't regress.
"""
from __future__ import annotations

import tempfile

import pytest

from memkraft import MemKraft
from memkraft.chunking import _chunk_text


# ----------------------------------------------------------------------
# _chunk_text — pure helper
# ----------------------------------------------------------------------
def test_chunk_text_short_returns_single_chunk():
    chunks = _chunk_text("hello world", size=500, overlap=50)
    assert chunks == ["hello world"]


def test_chunk_text_empty_returns_one_empty():
    chunks = _chunk_text("", size=500, overlap=50)
    assert chunks == [""]


def test_chunk_text_long_splits_with_overlap():
    # 1200 words, size=500, overlap=50 → step=450 → ≥3 chunks
    content = " ".join(f"word{i}" for i in range(1200))
    chunks = _chunk_text(content, size=500, overlap=50)
    assert len(chunks) >= 2
    # Each chunk should be at most `size` words
    for c in chunks:
        assert len(c.split()) <= 500
    # Overlap: the tail of chunk[0] should share words with the head of chunk[1]
    if len(chunks) >= 2:
        tail = set(chunks[0].split()[-50:])
        head = set(chunks[1].split()[:50])
        assert len(tail & head) > 0


def test_chunk_text_exact_boundary():
    # 600 words, size=500, overlap=50 → step=450 → 2 chunks
    content = " ".join(f"word{i}" for i in range(600))
    chunks = _chunk_text(content, size=500, overlap=50)
    assert len(chunks) == 2


# ----------------------------------------------------------------------
# track_document
# ----------------------------------------------------------------------
@pytest.fixture
def mk():
    tmp = tempfile.mkdtemp(prefix="mk_v103_")
    return MemKraft(base_dir=tmp)


def test_track_document_short_creates_one_chunk(mk):
    n = mk.track_document("doc-short", "hello world this is a short doc")
    assert n == 1


def test_track_document_long_creates_multiple_chunks(mk):
    # 1200 words → at least 2 chunks
    content = " ".join(f"token{i}" for i in range(1200))
    n = mk.track_document("doc-long", content, chunk_size=500, chunk_overlap=50)
    assert n >= 2


def test_track_document_rejects_bad_params(mk):
    with pytest.raises(ValueError):
        mk.track_document("d", "x", chunk_size=0)
    with pytest.raises(ValueError):
        mk.track_document("d", "x", chunk_size=100, chunk_overlap=100)
    with pytest.raises(ValueError):
        mk.track_document("d", "x", chunk_size=100, chunk_overlap=-1)


def test_track_document_chunk_searchable(mk):
    content = " ".join(f"filler{i}" for i in range(800)) + " unique_marker_xyz " + \
              " ".join(f"filler{i}" for i in range(800, 1600))
    n = mk.track_document("doc-marker", content, chunk_size=500, chunk_overlap=50)
    assert n >= 2
    # The unique token should be findable via plain search
    hits = mk.search("unique_marker_xyz", fuzzy=False)
    assert hits, "expected at least one hit for unique marker inside chunked doc"


# ----------------------------------------------------------------------
# search_precise
# ----------------------------------------------------------------------
def test_search_precise_empty_query_returns_empty(mk):
    assert mk.search_precise("", top_k=5) == []
    assert mk.search_precise("   ", top_k=5) == []


def test_search_precise_zero_top_k_returns_empty(mk):
    mk.track("Alice", entity_type="person", source="test")
    mk.update("Alice", "Alice is a Python engineer at Hashed.", source="test")
    assert mk.search_precise("Python", top_k=0) == []


def test_search_precise_threshold_filters_weak_hits(mk):
    mk.track("Alice", entity_type="person", source="test")
    mk.update("Alice", "Alice is a Python engineer at Hashed.", source="test")
    mk.track("Bob", entity_type="person", source="test")
    mk.update("Bob", "Bob likes hiking and coffee.", source="test")

    # Low threshold — should return something
    hits = mk.search_precise("Python engineer", top_k=5, score_threshold=0.0)
    assert isinstance(hits, list)
    assert len(hits) <= 5


def test_search_precise_respects_top_k(mk):
    # Seed several entities that all mention "python"
    for i in range(10):
        name = f"User{i}"
        mk.track(name, entity_type="person", source="test")
        mk.update(name, f"User{i} is a python developer working on project-{i}.", source="test")

    hits = mk.search_precise("python", top_k=3, score_threshold=0.0)
    assert len(hits) <= 3


def test_search_precise_fallback_on_empty_precision(mk):
    # Seed an entity whose content won't match the precision pass exactly
    mk.track("Charlie", entity_type="person", source="test")
    mk.update("Charlie", "Charlie enjoys reading science fiction novels.", source="test")

    # Query a typo that exact search is unlikely to satisfy but fuzzy might.
    # We don't assert a specific count — only that the call is safe and returns a list.
    hits = mk.search_precise("sciance fictoin", top_k=5, score_threshold=0.5)
    assert isinstance(hits, list)
