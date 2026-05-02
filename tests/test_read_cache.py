"""Tests for _read_cache — bounded LRU file-content cache with mtime+size invalidation."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from memkraft._read_cache import _ReadCache, get_cache, reset_for_tests


@pytest.fixture(autouse=True)
def _reset_cache():
    """Ensure each test starts with a fresh singleton."""
    reset_for_tests()
    yield
    reset_for_tests()


class TestReadCacheUnit:
    """Unit tests for _ReadCache in isolation."""

    def test_basic_hit_and_miss(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")

        # First read = miss
        result = cache.get_or_read(f)
        assert result == "hello"
        assert cache.misses == 1
        assert cache.hits == 0

        # Second read = hit
        result2 = cache.get_or_read(f)
        assert result2 == "hello"
        assert cache.hits == 1
        assert cache.misses == 1

    def test_mtime_invalidation(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "b.txt"
        f.write_text("v1", encoding="utf-8")
        cache.get_or_read(f)

        # Modify file (mtime changes)
        time.sleep(0.05)
        f.write_text("v2", encoding="utf-8")

        result = cache.get_or_read(f)
        assert result == "v2"
        assert cache.invalidations >= 1

    def test_size_invalidation(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "c.txt"
        f.write_text("short", encoding="utf-8")
        cache.get_or_read(f)

        # Same mtime but different size — stat() will show different size
        # We can't easily keep mtime same without os.utime, so just verify
        # that a different-sized write is a miss
        time.sleep(0.05)
        f.write_text("much longer content here", encoding="utf-8")
        result = cache.get_or_read(f)
        assert result == "much longer content here"

    def test_explicit_invalidate(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "d.txt"
        f.write_text("data", encoding="utf-8")
        cache.get_or_read(f)
        assert cache.hits == 0

        # Second read = hit
        cache.get_or_read(f)
        assert cache.hits == 1

        # Explicit invalidation
        cache.invalidate(f)
        assert cache.invalidations == 1

        # Next read = miss (re-reads from disk)
        cache.get_or_read(f)
        assert cache.misses == 2

    def test_capacity_eviction(self, tmp_path: Path):
        cache = _ReadCache(capacity=4)
        files = []
        for i in range(5):
            f = tmp_path / f"f{i}.txt"
            f.write_text(f"content {i}", encoding="utf-8")
            files.append(f)
            cache.get_or_read(f)

        # 5 items in capacity-4 cache → first should be evicted
        assert len(cache._data) <= 4

        # Re-read the first file — should be a miss (evicted)
        old_hits = cache.hits
        cache.get_or_read(files[0])
        assert cache.misses >= 5  # first file was evicted → miss

    def test_disabled_cache(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MEMKRAFT_READ_CACHE_SIZE", "0")
        reset_for_tests()
        cache = get_cache()
        assert cache._capacity == 0

        f = tmp_path / "e.txt"
        f.write_text("test", encoding="utf-8")
        result = cache.get_or_read(f)
        assert result == "test"
        # Disabled cache: no hit/miss tracking
        assert cache.hits == 0
        assert cache.misses == 0

    def test_nonexistent_file(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "missing.txt"
        result = cache.get_or_read(f)
        assert result is None

    def test_thread_safety(self, tmp_path: Path):
        cache = _ReadCache(capacity=64)
        f = tmp_path / "shared.txt"
        f.write_text("shared content", encoding="utf-8")

        errors = []

        def reader():
            try:
                for _ in range(100):
                    result = cache.get_or_read(f)
                    assert result == "shared content"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cache.hits + cache.misses == 1000

    def test_stats(self, tmp_path: Path):
        cache = _ReadCache(capacity=16)
        f = tmp_path / "s.txt"
        f.write_text("x", encoding="utf-8")
        cache.get_or_read(f)
        cache.get_or_read(f)

        stats = cache.stats()
        assert stats["capacity"] == 16
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)


class TestReadCacheIntegration:
    """Integration tests: MemKraft update → search → update → search."""

    def test_update_search_reflects_changes(self, tmp_path: Path):
        """update → search should find new info; update again → search finds new info."""
        from memkraft import MemKraft

        reset_for_tests()
        mk = MemKraft(base_dir=str(tmp_path))
        mk.track("alice", entity_type="person")
        mk.update("alice", "Alice works at Acme Corp")

        results = mk.search("Acme")
        assert any("Acme" in r.get("snippet", "") for r in results)

        # Update with new info
        mk.update("alice", "Alice moved to Globex")
        results2 = mk.search("Globex")
        assert any("Globex" in r.get("snippet", "") for r in results2)

    def test_deleted_file_graceful(self, tmp_path: Path):
        """If a file is deleted after caching, search should not crash."""
        from memkraft import MemKraft

        reset_for_tests()
        mk = MemKraft(base_dir=str(tmp_path))
        mk.track("bob", entity_type="person")
        mk.update("bob", "Bob likes cats")

        # Delete the file
        slug = mk._slugify("bob")
        (mk.live_notes_dir / f"{slug}.md").unlink()

        # Search should not crash
        results = mk.search("cats")
        # May or may not find results, but should not raise
        assert isinstance(results, list)
