"""v2.7.0 — Search result caching.

Thread-safe LRU + TTL cache for search results, with a generation
counter that mutation methods (`update`, `track`, `fact_add`,
`log_event`, `consolidate`, `decision_record`, `dream_cycle`) bump to
trigger automatic invalidation.

Design constraints
------------------
* Zero breaking changes — existing search APIs keep their signatures.
* Opt-out per call via ``cache=False`` keyword on ``search_v2`` /
  ``search_smart``.
* No external deps — only the standard library.
* Returned values are shallow-copied so callers can safely mutate the
  outer list without polluting the cache.

Public surface (added to ``MemKraft``)
--------------------------------------
* ``mk.cache_stats()`` → dict of hit/miss/eviction counters.
* ``mk.cache_clear()`` → manual purge.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


__all__ = [
    "_SearchCache",
    "CacheInvalidationMixin",
    "DEFAULT_CACHE_CAPACITY",
    "DEFAULT_CACHE_TTL",
]


DEFAULT_CACHE_CAPACITY = 256
DEFAULT_CACHE_TTL = 300.0  # seconds (5 min)


class _SearchCache:
    """Thread-safe LRU + TTL cache.

    Parameters
    ----------
    capacity:
        Maximum number of entries before LRU eviction. Default 256.
    ttl:
        Per-entry time-to-live in seconds. Default 300.
    """

    def __init__(
        self,
        capacity: int = DEFAULT_CACHE_CAPACITY,
        ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        if not isinstance(capacity, int) or capacity <= 0:
            capacity = DEFAULT_CACHE_CAPACITY
        try:
            ttl = float(ttl)
            if ttl <= 0:
                ttl = DEFAULT_CACHE_TTL
        except (TypeError, ValueError):
            ttl = DEFAULT_CACHE_TTL

        self.capacity: int = capacity
        self.ttl: float = ttl
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------
    @staticmethod
    def make_key(**kwargs: Any) -> str:
        """Build a stable SHA-256 key from arbitrary kwargs.

        Uses ``json.dumps(..., sort_keys=True, default=str)`` so the
        same logical inputs always produce the same key regardless of
        argument order or value types.
        """
        try:
            payload = json.dumps(kwargs, sort_keys=True, default=str)
        except (TypeError, ValueError):
            # Fall back to repr-based hashing if anything is not JSON
            # serialisable. This is best-effort — repr ordering of
            # dict keys is also stable in CPython 3.7+.
            payload = repr(sorted(kwargs.items()))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def get(self, key: str) -> Optional[Any]:
        """Return cached value or ``None`` if absent / expired.

        Expired entries are removed lazily here.
        """
        if not isinstance(key, str):
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            ts, value = entry
            if (time.monotonic() - ts) > self.ttl:
                # Expired — drop and report as miss.
                self._store.pop(key, None)
                self._misses += 1
                return None
            # LRU touch.
            self._store.move_to_end(key)
            self._hits += 1
            # Return shallow copy if value is a list (search results are
            # ``list[dict]`` so callers can iterate / pop without
            # mutating the cached entry).
            if isinstance(value, list):
                return list(value)
            return value

    def set(self, key: str, value: Any) -> None:
        """Insert / update an entry, evicting LRU if over capacity."""
        if not isinstance(key, str):
            return
        with self._lock:
            now = time.monotonic()
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = (now, value)
                return
            self._store[key] = (now, value)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)
                self._evictions += 1

    def invalidate_all(self) -> None:
        """Remove every entry. Counters are NOT reset."""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """Return current counters and configuration."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "size": len(self._store),
                "capacity": self.capacity,
                "ttl_seconds": self.ttl,
                "hit_rate": round(hit_rate, 4),
            }

    def reset_stats(self) -> None:
        """Zero out hit/miss/eviction counters (does not clear entries)."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def __len__(self) -> int:  # pragma: no cover - trivial
        with self._lock:
            return len(self._store)

    def __contains__(self, key: str) -> bool:  # pragma: no cover - trivial
        if not isinstance(key, str):
            return False
        with self._lock:
            return key in self._store


# ----------------------------------------------------------------------
# Mixin: cache lifecycle + mutation invalidation
# ----------------------------------------------------------------------
# Methods that should bump the cache generation (and therefore make
# every cached entry stale on the next ``get``).
_MUTATION_METHODS = (
    "update",
    "track",
    "fact_add",
    "log_event",
    "consolidate",
    "consolidate_run",
    "decision_record",
    "incident_record",
    "dream_cycle",
)


class CacheInvalidationMixin:
    """Wires lazy ``_SearchCache`` access onto ``MemKraft`` and exposes
    public cache management helpers.

    The mixin does NOT directly wrap mutation methods at class-load
    time — that is handled in ``__init__.py`` after every other mixin
    has been merged onto ``MemKraft`` (so we can safely replace the
    final method objects).
    """

    # ---- lazy accessor -------------------------------------------------
    def _get_search_cache(self) -> _SearchCache:
        cache = getattr(self, "_search_cache_instance", None)
        if cache is None:
            cache = _SearchCache(
                capacity=DEFAULT_CACHE_CAPACITY,
                ttl=DEFAULT_CACHE_TTL,
            )
            self._search_cache_instance = cache
        return cache

    def _get_cache_generation(self) -> int:
        return getattr(self, "_cache_generation_counter", 0)

    def _bump_cache_generation(self) -> int:
        gen = self._get_cache_generation() + 1
        self._cache_generation_counter = gen
        return gen

    # ---- public API ---------------------------------------------------
    def cache_stats(self) -> dict:
        """Return search cache counters + size + hit-rate.

        Returned dict includes ``generation`` so callers can verify a
        mutation actually took effect.
        """
        cache = self._get_search_cache()
        s = cache.stats()
        s["generation"] = self._get_cache_generation()
        return s

    def cache_clear(self) -> None:
        """Manually purge every cached search result.

        Use sparingly — mutations already auto-invalidate via the
        generation counter. Mostly useful for tests and benchmarks.
        """
        cache = self._get_search_cache()
        cache.invalidate_all()

    def cache_configure(
        self,
        capacity: Optional[int] = None,
        ttl: Optional[float] = None,
    ) -> dict:
        """Reconfigure cache capacity / TTL.

        Existing entries are preserved (capacity may evict immediately
        if shrunk below current size). Returns the new ``stats()``.
        """
        cache = self._get_search_cache()
        with cache._lock:  # safe: we own the lock semantics here.
            if isinstance(capacity, int) and capacity > 0:
                cache.capacity = capacity
                while len(cache._store) > cache.capacity:
                    cache._store.popitem(last=False)
                    cache._evictions += 1
            if ttl is not None:
                try:
                    new_ttl = float(ttl)
                    if new_ttl > 0:
                        cache.ttl = new_ttl
                except (TypeError, ValueError):
                    pass
        return self.cache_stats()


def install_cache_invalidation_wrappers(mem_class: type) -> None:
    """Wrap each known mutation method on ``mem_class`` so it bumps
    the search-cache generation counter after the original returns.

    Idempotent — safe to call multiple times. Methods that don't
    exist on the class are silently skipped.
    """
    sentinel = "_memkraft_cache_invalidation_wrapped"

    for method_name in _MUTATION_METHODS:
        original = getattr(mem_class, method_name, None)
        if original is None or not callable(original):
            continue
        if getattr(original, sentinel, False):
            # Already wrapped (e.g. on re-import in tests).
            continue

        def _make_wrapper(_name: str, _orig):
            def _wrapper(self, *args, **kwargs):
                result = _orig(self, *args, **kwargs)
                try:
                    if hasattr(self, "_bump_cache_generation"):
                        self._bump_cache_generation()
                except Exception:
                    # Cache bookkeeping must never break a mutation.
                    pass
                return result

            _wrapper.__name__ = getattr(_orig, "__name__", _name)
            _wrapper.__doc__ = getattr(_orig, "__doc__", None)
            _wrapper.__qualname__ = getattr(_orig, "__qualname__", _name)
            setattr(_wrapper, sentinel, True)
            # Keep a reference to the original for debuggability.
            setattr(_wrapper, "_memkraft_cache_original", _orig)
            return _wrapper

        setattr(mem_class, method_name, _make_wrapper(method_name, original))
