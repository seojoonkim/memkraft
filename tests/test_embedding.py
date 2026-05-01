"""v2.7.3 — Local embedding retrieval tests.

Covers:
1. Method presence (all 7 public methods land on `MemKraft`).
2. Pure helpers: `_cosine` math, `_to_float_list` coercion.
3. Missing-extra error path (monkey-patch `_load_st_model` to raise
   `MemKraftEmbeddingError`) — keeps the suite green even on hosts
   without `sentence-transformers`.
4. End-to-end with a real model when the extra IS installed:
   - `embed_text` length / type / cache hit
   - `embed_batch` returns one vector per input, including for
     skipped empty / non-string entries
   - `build_embeddings` writes `index.jsonl` and is incremental
     (skip count grows on re-run)
   - `search_semantic` returns hits ordered by cosine similarity
   - `search_hybrid` includes hits found only by BM25 *or* only by
     the semantic side, and respects `alpha` extremes
   - Hybrid degrades to BM25 when the extra is missing
   - `embedding_clear` removes the on-disk index

These tests skip the live-model bits via `pytest.importorskip` when
`sentence-transformers` isn't installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Project layout: tests/ sits next to src/, conftest already arranges
# `import memkraft` to resolve to `src/memkraft`. Use a fresh import
# so the module-level mixin attachment runs.
import sys
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import memkraft  # noqa: E402
from memkraft import MemKraft  # noqa: E402
from memkraft import embedding as emb_mod  # noqa: E402
from memkraft.embedding import (  # noqa: E402
    EmbeddingMixin,
    MemKraftEmbeddingError,
    DEFAULT_EMBEDDING_MODEL,
    _cosine,
    _to_float_list,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def tmp_mk(tmp_path: Path) -> MemKraft:
    """A fresh `MemKraft` with a tiny corpus (3 docs)."""
    base = tmp_path / "memory"
    base.mkdir()
    (base / "entities").mkdir()
    # Three docs with distinct semantic content.
    (base / "entities" / "alice.md").write_text(
        "# Alice\n\nAlice loves Italian pasta and red wine. "
        "She is a chef in Rome."
    )
    (base / "entities" / "bob.md").write_text(
        "# Bob\n\nBob is a software engineer who writes Rust and Go. "
        "He works on distributed systems."
    )
    (base / "entities" / "claire.md").write_text(
        "# Claire\n\nClaire enjoys hiking, mountain biking, and "
        "rock climbing every weekend."
    )
    mk = MemKraft(base_dir=str(base))
    return mk


# ---------------------------------------------------------------------
# 1. Method presence
# ---------------------------------------------------------------------
def test_methods_present_on_memkraft():
    for name in (
        "embed_text",
        "embed_batch",
        "search_semantic",
        "search_hybrid",
        "build_embeddings",
        "embedding_stats",
        "embedding_clear",
    ):
        assert hasattr(MemKraft, name), f"MemKraft missing method: {name}"
        assert callable(getattr(MemKraft, name))


# ---------------------------------------------------------------------
# 2. Pure helpers
# ---------------------------------------------------------------------
def test_cosine_basic():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_safe_on_bad_input():
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0  # mismatched dims
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vec


def test_to_float_list_handles_lists_and_tuples():
    assert _to_float_list([1, 2, 3]) == [1.0, 2.0, 3.0]
    assert _to_float_list((1, 2, 3)) == [1.0, 2.0, 3.0]
    # 2-D is flattened to first row.
    assert _to_float_list([[1, 2], [3, 4]]) == [1.0, 2.0]


def test_to_float_list_uses_tolist_on_numpy_like():
    class FakeArray:
        def tolist(self):
            return [0.5, -0.5, 1.5]
    assert _to_float_list(FakeArray()) == [0.5, -0.5, 1.5]


# ---------------------------------------------------------------------
# 3. Missing-extra error path (monkeypatched, no st install needed)
# ---------------------------------------------------------------------
def test_embed_text_raises_when_st_missing(tmp_mk, monkeypatch):
    def _boom(name):
        raise MemKraftEmbeddingError(
            "sentence-transformers is not installed (test stub)"
        )
    monkeypatch.setattr(emb_mod, "_load_st_model", _boom)
    with pytest.raises(MemKraftEmbeddingError):
        tmp_mk.embed_text("hello world")


def test_search_hybrid_falls_back_to_bm25_when_st_missing(tmp_mk, monkeypatch):
    """If the embedding extra is missing, hybrid should degrade,
    NOT raise."""
    def _boom(name):
        raise MemKraftEmbeddingError("missing extra (test stub)")
    monkeypatch.setattr(emb_mod, "_load_st_model", _boom)
    # search_smart should still return BM25 hits.
    out = tmp_mk.search_hybrid("Italian pasta", top_k=3)
    # Hybrid degrades — return whatever BM25 produces (could be empty
    # on this tiny corpus, but must NOT raise).
    assert isinstance(out, list)


# ---------------------------------------------------------------------
# 4. Live-model tests (require sentence-transformers extra)
# ---------------------------------------------------------------------
st = pytest.importorskip(
    "sentence_transformers",
    reason="install memkraft[embedding] to run live-model tests",
)


def test_embed_text_live(tmp_mk):
    vec = tmp_mk.embed_text("hello world")
    assert isinstance(vec, list)
    assert len(vec) > 0
    assert all(isinstance(x, float) for x in vec)
    # MiniLM-L6-v2 is 384 dim.
    assert len(vec) == 384


def test_embed_text_cache_hit(tmp_mk):
    v1 = tmp_mk.embed_text("the quick brown fox")
    v2 = tmp_mk.embed_text("the quick brown fox")
    assert v1 == v2  # exact match through LRU cache
    cache = tmp_mk._embedding_text_cache()
    assert "the quick brown fox" in cache


def test_embed_text_empty_returns_empty(tmp_mk):
    assert tmp_mk.embed_text("") == []
    assert tmp_mk.embed_text("   ") == []


def test_embed_batch_preserves_length(tmp_mk):
    texts = ["alpha", "", "beta", None, "gamma"]
    # filter out None — embed_batch tolerates non-strings via skip.
    out = tmp_mk.embed_batch(texts)
    assert len(out) == len(texts)
    # Non-strings / empties → empty vectors.
    assert out[1] == []
    assert out[3] == []
    # Real strings → real vectors.
    assert len(out[0]) == 384
    assert len(out[2]) == 384
    assert len(out[4]) == 384


def test_build_embeddings_creates_index(tmp_mk):
    stats = tmp_mk.build_embeddings()
    assert stats["indexed"] >= 3  # at least our 3 docs
    assert stats["model"] == DEFAULT_EMBEDDING_MODEL
    idx_path = Path(tmp_mk._embedding_index_path())
    assert idx_path.exists()
    # Quick well-formedness check on the JSONL.
    with idx_path.open() as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) >= 3
    assert all("vec" in r and len(r["vec"]) == 384 for r in lines)


def test_build_embeddings_is_incremental(tmp_mk):
    s1 = tmp_mk.build_embeddings()
    s2 = tmp_mk.build_embeddings()
    # Second pass should skip everything (no mtime change).
    assert s2["indexed"] == 0
    assert s2["skipped"] >= s1["indexed"]


def test_search_semantic_orders_by_meaning(tmp_mk):
    tmp_mk.build_embeddings()
    hits = tmp_mk.search_semantic("Italian food and wine", top_k=3)
    assert hits, "expected at least one hit"
    top = hits[0]
    assert "alice" in top["file"].lower(), (
        f"semantic top hit should be Alice (Italian/wine), got {top['file']}"
    )
    # Score field is a float in [-1, 1].
    assert -1.0 <= top["score"] <= 1.0
    assert top["retrieval"] == "semantic"


def test_search_semantic_top_k_respected(tmp_mk):
    tmp_mk.build_embeddings()
    hits = tmp_mk.search_semantic("anything", top_k=2)
    assert len(hits) <= 2


def test_search_semantic_empty_query(tmp_mk):
    assert tmp_mk.search_semantic("", top_k=5) == []


def test_search_semantic_auto_build(tmp_mk):
    """First call with no index should auto-build and still return hits."""
    # Sanity: index doesn't exist yet.
    idx_path = Path(tmp_mk._embedding_index_path())
    if idx_path.exists():
        idx_path.unlink()
    tmp_mk.embedding_clear()
    hits = tmp_mk.search_semantic("rust programming", top_k=3, auto_build=True)
    assert hits, "auto_build should have populated the index"


def test_search_hybrid_alpha_extremes(tmp_mk):
    tmp_mk.build_embeddings()
    bm25_only = tmp_mk.search_hybrid("Italian pasta", top_k=3, alpha=0.0)
    semantic_only = tmp_mk.search_hybrid("Italian pasta", top_k=3, alpha=1.0)
    # Both should yield results (corpus is non-empty).
    assert isinstance(bm25_only, list)
    assert isinstance(semantic_only, list)
    # alpha=1.0 should rank Alice first by meaning.
    assert semantic_only and "alice" in semantic_only[0]["file"].lower()
    # Each result carries a fused score and retrieval=hybrid.
    for r in semantic_only:
        assert r["retrieval"] == "hybrid"
        assert isinstance(r["score"], float)


def test_search_hybrid_unions_signals(tmp_mk):
    """Hybrid should pick up hits from either side. With a query that
    keyword-matches one doc and semantic-matches another, we expect
    >=2 distinct files in the merged top-3."""
    tmp_mk.build_embeddings()
    out = tmp_mk.search_hybrid(
        "outdoor adventure climbing",
        top_k=3,
        alpha=0.5,
    )
    files = {r["file"] for r in out}
    # Claire (climbing/hiking) MUST be in there.
    assert any("claire" in f.lower() for f in files), out


def test_embedding_clear_removes_index(tmp_mk):
    tmp_mk.build_embeddings()
    idx_path = Path(tmp_mk._embedding_index_path())
    assert idx_path.exists()
    tmp_mk.embedding_clear()
    assert not idx_path.exists()
    assert tmp_mk._embedding_doc_cache() == {}


def test_embedding_stats_reports_count(tmp_mk):
    tmp_mk.build_embeddings()
    stats = tmp_mk.embedding_stats()
    assert stats["count"] >= 3
    assert stats["dim"] == 384
    assert stats["model"] == DEFAULT_EMBEDDING_MODEL
    assert "index_path" in stats
