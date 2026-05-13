"""Tests for ``_corpus_index`` — singleton BM25 corpus index cache.

Covers:
- First call builds, second call reuses (cache hit).
- Invalidation on file modify / add / delete (mtime/size fingerprint).
- Disabled via ``MEMKRAFT_CORPUS_INDEX_DISABLE`` env var.
- ``mk.search`` results unchanged with cache on vs off (regression).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft import _corpus_index as ci
from memkraft._corpus_index import (
    CorpusIndex,
    get_corpus_index,
    reset_for_tests,
    stats,
)


# ── Test helpers ────────────────────────────────────────────────────


def _tokenize(text: str) -> list:
    """Stable tokenizer for unit tests (avoid pulling _search_tokens
    side-effects)."""
    return [w for w in text.lower().split() if len(w) > 1]


def _make_files(root: Path, contents: dict[str, str]) -> Path:
    """Create the given filename → content mapping under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for name, body in contents.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def _list_md(root: Path):
    return list(root.glob("*.md"))


@pytest.fixture(autouse=True)
def _reset_index_cache(monkeypatch):
    """Each test gets a fresh singleton + clean env."""
    monkeypatch.delenv("MEMKRAFT_CORPUS_INDEX_DISABLE", raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


# ── Unit tests on the cache itself ──────────────────────────────────


class TestCacheBuild:
    def test_first_call_builds(self, tmp_path: Path):
        _make_files(tmp_path, {"a.md": "alpha beta gamma", "b.md": "alpha delta"})
        before = stats()
        assert before["build_misses"] == 0
        assert before["build_hits"] == 0

        idx = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert isinstance(idx, CorpusIndex)
        assert idx.doc_count == 2
        assert idx.token_doc_freq.get("alpha") == 2
        assert idx.token_doc_freq.get("beta") == 1
        assert idx.fingerprint  # non-empty bytes

        after = stats()
        assert after["build_misses"] == 1
        assert after["build_hits"] == 0

    def test_second_call_is_hit(self, tmp_path: Path):
        _make_files(tmp_path, {"a.md": "alpha beta", "b.md": "alpha gamma"})
        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        # Same instance — cache hit, not rebuild
        assert idx2 is idx1
        s = stats()
        assert s["build_hits"] == 1
        assert s["build_misses"] == 1

    def test_doc_lengths_and_tf(self, tmp_path: Path):
        _make_files(
            tmp_path,
            {"x.md": "alpha alpha beta", "y.md": "gamma"},
        )
        idx = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        x = tmp_path / "x.md"
        y = tmp_path / "y.md"
        assert idx.doc_lengths[x] == 3
        assert idx.doc_lengths[y] == 1
        assert idx.doc_token_freqs[x]["alpha"] == 2
        assert idx.doc_token_freqs[x]["beta"] == 1
        assert idx.avg_doc_len == pytest.approx((3 + 1) / 2)

    def test_empty_corpus(self, tmp_path: Path):
        idx = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        # Mirrors legacy semantics: doc_count = max(len(all_files), 1)
        assert idx.doc_count == 1
        assert idx.avg_doc_len == 0.0
        assert idx.token_doc_freq == {}


class TestInvalidation:
    def test_modify_invalidates(self, tmp_path: Path):
        _make_files(tmp_path, {"a.md": "alpha", "b.md": "beta"})
        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx1.token_doc_freq.get("alpha") == 1
        assert idx1.token_doc_freq.get("delta") is None

        # Modify a.md — fingerprint must change
        time.sleep(0.05)  # ensure mtime ticks on coarse-resolution FS
        (tmp_path / "a.md").write_text("delta", encoding="utf-8")

        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx2 is not idx1, "cache should rebuild after modify"
        assert idx2.token_doc_freq.get("alpha") is None
        assert idx2.token_doc_freq.get("delta") == 1

    def test_add_file_invalidates(self, tmp_path: Path):
        _make_files(tmp_path, {"a.md": "alpha"})
        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx1.doc_count == 1

        (tmp_path / "b.md").write_text("beta", encoding="utf-8")

        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx2 is not idx1
        assert idx2.doc_count == 2
        assert idx2.token_doc_freq.get("beta") == 1

    def test_delete_file_invalidates(self, tmp_path: Path):
        _make_files(tmp_path, {"a.md": "alpha", "b.md": "beta"})
        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx1.doc_count == 2

        (tmp_path / "b.md").unlink()

        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx2 is not idx1
        assert idx2.doc_count == 1
        assert idx2.token_doc_freq.get("beta") is None

    def test_size_change_invalidates(self, tmp_path: Path):
        # Same mtime granularity-defeating case: write same length first,
        # then a different length.  Size differs → fingerprint differs.
        f = tmp_path / "a.md"
        f.write_text("aaa", encoding="utf-8")
        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        f.write_text("bb", encoding="utf-8")  # different size
        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx2 is not idx1


class TestEnvDisable:
    def test_disabled_via_env_rebuilds_every_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _make_files(tmp_path, {"a.md": "alpha beta", "b.md": "alpha gamma"})
        monkeypatch.setenv("MEMKRAFT_CORPUS_INDEX_DISABLE", "1")

        idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
        assert idx2 is not idx1, "disabled mode must always rebuild"
        # And the singleton stays empty.
        assert ci._INDEX_CACHE is None

        s = stats()
        assert s["disabled"] is True
        assert s["build_misses"] >= 2

    def test_falsy_values_keep_cache_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _make_files(tmp_path, {"a.md": "alpha"})
        for falsy in ("", "0", "false", "False"):
            reset_for_tests()
            monkeypatch.setenv("MEMKRAFT_CORPUS_INDEX_DISABLE", falsy)
            idx1 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
            idx2 = get_corpus_index(lambda: _list_md(tmp_path), _tokenize)
            assert idx2 is idx1, f"falsy value {falsy!r} should keep cache on"


# ── Integration: search() correctness unchanged ─────────────────────


class TestSearchRegression:
    """End-to-end: ``mk.search`` should return identical results
    with the cache enabled vs. forcibly disabled."""

    def _seed(self, mk: MemKraft) -> None:
        mk.track("Alice", source="test")
        mk.update("Alice", "Alice is an engineer who loves matcha", source="test")
        mk.track("Bob", source="test")
        mk.update("Bob", "Bob is a designer who loves coffee", source="test")
        mk.log_event("deploy v2 succeeded", tags="deploy")

    def _strip_volatile(self, results: list[dict]) -> list[tuple]:
        # Compare on (file, tier) — score is float-stable but we keep
        # things minimal in case any helper rounds slightly.
        return [(r.get("file"), r.get("tier"), round(r.get("score", 0), 4)) for r in results]

    def test_search_results_identical_with_and_without_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # First run: cache ON
        monkeypatch.delenv("MEMKRAFT_CORPUS_INDEX_DISABLE", raising=False)
        reset_for_tests()
        mk_on = MemKraft(str(tmp_path / "on"))
        if hasattr(mk_on, "init"):
            mk_on.init()
        self._seed(mk_on)

        queries = ["Alice", "engineer", "matcha", "deploy", "designer coffee"]
        on_results = {q: self._strip_volatile(mk_on.search(q)) for q in queries}

        # Second run on a fresh corpus: cache OFF
        reset_for_tests()
        monkeypatch.setenv("MEMKRAFT_CORPUS_INDEX_DISABLE", "1")
        mk_off = MemKraft(str(tmp_path / "off"))
        if hasattr(mk_off, "init"):
            mk_off.init()
        self._seed(mk_off)
        off_results = {q: self._strip_volatile(mk_off.search(q)) for q in queries}

        # The two corpora live in different base_dirs but the relative
        # file paths and scores must match exactly.
        for q in queries:
            assert on_results[q] == off_results[q], f"divergence on query={q!r}"

    def test_search_after_mutation_picks_up_change(
        self, tmp_path: Path
    ):
        reset_for_tests()
        mk = MemKraft(str(tmp_path))
        if hasattr(mk, "init"):
            mk.init()
        mk.track("Charlie", source="test")
        mk.update("Charlie", "Charlie likes ramen", source="test")
        before = mk.search("ramen")
        assert any("Charlie" in r.get("file", "") or "charlie" in r.get("file", "")
                   for r in before)

        mk.update("Charlie", "Charlie also likes pizza", source="test")
        # Cache must invalidate on file mtime change → "pizza" must
        # surface in subsequent searches.
        after = mk.search("pizza")
        assert any("charlie" in r.get("file", "").lower() for r in after), \
            f"expected Charlie's pizza note to surface, got: {after}"
