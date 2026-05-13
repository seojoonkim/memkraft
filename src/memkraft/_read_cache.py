"""Bounded LRU file-content cache with mtime+size invalidation.

Internal only. Used by search hot path to avoid re-reading the same
markdown file repeatedly within a single query and across nearby
queries. Invalidated automatically when the file's mtime or size
changes.

Cache is per-process, thread-safe via lock. Default capacity 256
entries (~16MB at typical 64KB per note). Configurable via env var
MEMKRAFT_READ_CACHE_SIZE (set to 0 to disable).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

log = logging.getLogger("memkraft._read_cache")

_DEFAULT_SIZE = 256
_ENV_VAR = "MEMKRAFT_READ_CACHE_SIZE"


def _get_capacity() -> int:
    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return _DEFAULT_SIZE
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return _DEFAULT_SIZE


class _ReadCache:
    """Thread-safe LRU cache keyed by (path, mtime_ns, size)."""

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._data: "OrderedDict[tuple, str]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    def get_or_read(self, path: Path, encoding: str = "utf-8") -> Optional[str]:
        if self._capacity == 0:
            return self._read_direct(path, encoding)
        try:
            st = path.stat()
        except (FileNotFoundError, OSError):
            return None
        key = (str(path), st.st_mtime_ns, st.st_size)
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
        # cache miss — read outside lock
        try:
            text = path.read_text(encoding=encoding, errors="replace")
        except (FileNotFoundError, OSError):
            return None
        with self._lock:
            # evict any older key for the same path (mtime/size changed)
            for k in list(self._data.keys()):
                if k[0] == key[0] and k != key:
                    self._data.pop(k, None)
                    self.invalidations += 1
            self._data[key] = text
            self._data.move_to_end(key)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
        return text

    def invalidate(self, path: Path) -> None:
        """Explicitly drop a path. Called from write paths as a fast-path.

        v2.8.1: also bumps the corpus-index generation counter so the
        next ``MemKraft.search()`` call skips its per-file ``stat()``
        fingerprint pass and goes straight to a rebuild check.  This
        is the canonical write-path hook — every site that already
        invalidates the read cache automatically invalidates the BM25
        corpus index too.
        """
        spath = str(path)
        with self._lock:
            for k in list(self._data.keys()):
                if k[0] == spath:
                    self._data.pop(k, None)
                    self.invalidations += 1
        # Imported lazily to avoid a circular import at module load.
        try:
            from . import _corpus_index
            _corpus_index.invalidate(path)
        except Exception:
            # Never let cache-coherency telemetry break a write path.
            pass

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "capacity": self._capacity,
                "size": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "invalidations": self.invalidations,
                "hit_rate": (
                    self.hits / (self.hits + self.misses)
                    if (self.hits + self.misses) > 0 else 0.0
                ),
            }

    @staticmethod
    def _read_direct(path: Path, encoding: str) -> Optional[str]:
        try:
            return path.read_text(encoding=encoding, errors="replace")
        except (FileNotFoundError, OSError):
            return None


# Singleton instance (created lazily so env var changes during tests work)
_INSTANCE: Optional[_ReadCache] = None
_INSTANCE_LOCK = threading.Lock()


def get_cache() -> _ReadCache:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = _ReadCache(_get_capacity())
    return _INSTANCE


def reset_for_tests() -> None:
    """Test helper: drop singleton so next get_cache() honors current env."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
