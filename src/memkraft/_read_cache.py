"""Bounded file-content cache with mtime+size invalidation.

Internal only. Used by search hot path to avoid re-reading the same
markdown file repeatedly within a single query and across nearby
queries. Invalidated automatically when the file's mtime or size
changes.

v2.9.1 adds an Adaptive Replacement Cache (ARC) policy as an opt-in
alternative to the default LRU.  ARC keeps two ring buffers (T1 =
recency, T2 = frequency) so frequently-accessed entities don't get
evicted by one-shot scans — better hit rate on mixed hot/cold
workloads.  Off by default to keep zero-config behaviour stable;
enable per-instance via ``MemKraft(base_dir=…, cache_policy="arc")``
or globally via ``MEMKRAFT_READ_CACHE_POLICY=arc``.

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
_POLICY_ENV_VAR = "MEMKRAFT_READ_CACHE_POLICY"  # v2.9.1: "lru" (default) or "arc"


def _get_capacity() -> int:
    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return _DEFAULT_SIZE
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return _DEFAULT_SIZE


def _get_policy() -> str:
    raw = (os.environ.get(_POLICY_ENV_VAR) or "").strip().lower()
    return raw if raw in ("lru", "arc") else "lru"


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


class _ARCReadCache:
    """Thread-safe Adaptive Replacement Cache (Megiddo & Modha, 2003).

    Maintains two cache lists and two ghost lists, total resident
    size bounded by ``capacity``:

    * **T1** (recent) — entries seen exactly once, LRU order.
    * **T2** (frequent) — entries seen ≥2 times, LRU order.
    * **B1** — ghost list: keys recently evicted from T1 (no data).
    * **B2** — ghost list: keys recently evicted from T2 (no data).

    The adaptation parameter ``p`` (0 ≤ p ≤ capacity) shifts the
    boundary between recency and frequency emphasis based on whether
    re-references hit B1 (→ favor recency, ``p↑``) or B2 (→ favor
    frequency, ``p↓``).  No tuning required; self-balancing.

    Compared to plain LRU, ARC shines on workloads with a hot set
    surrounded by occasional scans — e.g. a search hot path where a
    handful of entities get hit repeatedly while one-shot lookups
    flush through.  In those cases LRU evicts the hot set; ARC
    promotes it to T2 on the second touch and keeps it.

    Same key format and invalidation semantics as :class:`_ReadCache`
    so callers can swap them without code changes.
    """

    def __init__(self, capacity: int):
        self._capacity = capacity
        # Use OrderedDict for O(1) move_to_end + popitem(last=False).
        # Values: T1/T2 hold the actual file text; B1/B2 hold None
        # (ghost — we only care about the key for adaptation).
        self._t1: "OrderedDict[tuple, str]" = OrderedDict()
        self._t2: "OrderedDict[tuple, str]" = OrderedDict()
        self._b1: "OrderedDict[tuple, None]" = OrderedDict()
        self._b2: "OrderedDict[tuple, None]" = OrderedDict()
        self._p = 0  # adaptation target size for T1
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
        # Telemetry (P3): track ARC-specific events so we can compare
        # T1/T2 hit distribution against LRU in benchmarks.
        self.t1_hits = 0
        self.t2_hits = 0
        self.b1_rescues = 0  # cache miss but key was in B1 → frequency-shift
        self.b2_rescues = 0  # cache miss but key was in B2 → recency-shift

    # ── internal helpers ──
    def _replace(self, key: tuple) -> None:
        """Evict one entry to make room for the new ``key``."""
        # ARC replace rule: prefer evicting from T1 (recency) when its
        # size is over the adaptation target p, OR when the new key
        # hits B2 (which signals more frequency pressure).
        in_b2 = key in self._b2
        if self._t1 and ((len(self._t1) > self._p) or (in_b2 and len(self._t1) == self._p)):
            old_key, _old_val = self._t1.popitem(last=False)
            self._b1[old_key] = None
        elif self._t2:
            old_key, _old_val = self._t2.popitem(last=False)
            self._b2[old_key] = None
        # If both are empty we're under capacity — nothing to evict.

    def _trim_ghosts(self) -> None:
        """Bound ghost lists so they don't grow unbounded."""
        c = self._capacity
        while len(self._b1) > c:
            self._b1.popitem(last=False)
        while len(self._b2) > c:
            self._b2.popitem(last=False)

    # ── public API (matches _ReadCache) ──
    def get_or_read(self, path: Path, encoding: str = "utf-8") -> Optional[str]:
        if self._capacity == 0:
            return _ReadCache._read_direct(path, encoding)
        try:
            st = path.stat()
        except (FileNotFoundError, OSError):
            return None
        key = (str(path), st.st_mtime_ns, st.st_size)

        with self._lock:
            # Case I: hit in T1 → promote to T2 (seen ≥2 times).
            if key in self._t1:
                val = self._t1.pop(key)
                self._t2[key] = val
                self.hits += 1
                self.t1_hits += 1
                return val
            # Case II: hit in T2 → LRU refresh within T2.
            if key in self._t2:
                self._t2.move_to_end(key)
                self.hits += 1
                self.t2_hits += 1
                return self._t2[key]
            # Case III/IV: ghost-list hit (key was cached recently,
            # got evicted) — shift adaptation pointer.
            in_b1 = key in self._b1
            in_b2 = key in self._b2
            self.misses += 1
            if in_b1:
                # Recently evicted from T1 — favor recency: grow p.
                delta = max(1, len(self._b2) // max(1, len(self._b1)))
                self._p = min(self._capacity, self._p + delta)
                self.b1_rescues += 1
                self._replace(key)
                del self._b1[key]
            elif in_b2:
                # Recently evicted from T2 — favor frequency: shrink p.
                delta = max(1, len(self._b1) // max(1, len(self._b2)))
                self._p = max(0, self._p - delta)
                self.b2_rescues += 1
                self._replace(key)
                del self._b2[key]
            else:
                # True cold miss — may need to make room.
                total_resident = len(self._t1) + len(self._t2)
                if total_resident >= self._capacity:
                    self._replace(key)

        # Read outside lock.
        try:
            text = path.read_text(encoding=encoding, errors="replace")
        except (FileNotFoundError, OSError):
            return None

        with self._lock:
            # Evict any older key for the same path (mtime/size changed).
            for store in (self._t1, self._t2):
                for k in list(store.keys()):
                    if k[0] == key[0] and k != key:
                        store.pop(k, None)
                        self.invalidations += 1
            # B1 hit / B2 hit → the rescued entry is a known-hot key,
            # so insert directly into T2 (frequency list).
            if in_b1 or in_b2:
                self._t2[key] = text
            else:
                self._t1[key] = text
            self._trim_ghosts()
        return text

    def invalidate(self, path: Path) -> None:
        spath = str(path)
        with self._lock:
            for store in (self._t1, self._t2):
                for k in list(store.keys()):
                    if k[0] == spath:
                        store.pop(k, None)
                        self.invalidations += 1
            # Also drop ghost entries so a future read doesn't get
            # mis-classified as a B1/B2 rescue.
            for store in (self._b1, self._b2):
                for k in list(store.keys()):
                    if k[0] == spath:
                        store.pop(k, None)
        try:
            from . import _corpus_index
            _corpus_index.invalidate(path)
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._t1.clear()
            self._t2.clear()
            self._b1.clear()
            self._b2.clear()
            self._p = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "policy": "arc",
                "capacity": self._capacity,
                "size": len(self._t1) + len(self._t2),
                "t1_size": len(self._t1),
                "t2_size": len(self._t2),
                "b1_size": len(self._b1),
                "b2_size": len(self._b2),
                "p": self._p,
                "hits": self.hits,
                "misses": self.misses,
                "t1_hits": self.t1_hits,
                "t2_hits": self.t2_hits,
                "b1_rescues": self.b1_rescues,
                "b2_rescues": self.b2_rescues,
                "invalidations": self.invalidations,
                "hit_rate": (
                    self.hits / (self.hits + self.misses)
                    if (self.hits + self.misses) > 0 else 0.0
                ),
            }


# Singleton instance (created lazily so env var changes during tests work)
_INSTANCE: Optional["_ReadCache | _ARCReadCache"] = None
_INSTANCE_LOCK = threading.Lock()


def _build_cache(capacity: int, policy: str) -> "_ReadCache | _ARCReadCache":
    if policy == "arc":
        return _ARCReadCache(capacity)
    return _ReadCache(capacity)


def get_cache(policy: Optional[str] = None) -> "_ReadCache | _ARCReadCache":
    """Return the singleton read cache.

    v2.9.1: ``policy`` is an optional hint.  When the singleton hasn't
    been built yet, it picks the requested policy (``"lru"`` or
    ``"arc"``) — falling back to the ``MEMKRAFT_READ_CACHE_POLICY``
    env var, then to LRU.  After construction the singleton's policy
    is fixed for the lifetime of the process; callers wanting to
    switch must call :func:`reset_for_tests` first.  This keeps the
    public surface stable: passing a different policy on a later
    call is a no-op (intentional, so existing code paths don't
    accidentally tear down the cache mid-run).
    """
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                chosen = (policy or _get_policy() or "lru").lower()
                if chosen not in ("lru", "arc"):
                    chosen = "lru"
                _INSTANCE = _build_cache(_get_capacity(), chosen)
    return _INSTANCE


def reset_for_tests() -> None:
    """Test helper: drop singleton so next get_cache() honors current env."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
