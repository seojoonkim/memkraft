"""v2.7.0 — Search result cache tests."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.cache import _SearchCache, DEFAULT_CACHE_CAPACITY, DEFAULT_CACHE_TTL


# ---------------------------------------------------------------------
# Pure _SearchCache unit tests (no MemKraft)
# ---------------------------------------------------------------------

def test_cache_basic_get_set():
    c = _SearchCache(capacity=4, ttl=60)
    assert c.get("missing") is None
    c.set("k1", [{"file": "a"}])
    out = c.get("k1")
    assert out == [{"file": "a"}]
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1


def test_cache_returns_shallow_copy():
    """Caller mutations on the outer list must not pollute the cache."""
    c = _SearchCache(capacity=4, ttl=60)
    c.set("k1", [{"file": "a"}, {"file": "b"}])
    out1 = c.get("k1")
    out1.pop()  # mutate the returned list
    out2 = c.get("k1")
    assert len(out2) == 2  # still both entries


def test_cache_lru_eviction():
    c = _SearchCache(capacity=2, ttl=60)
    c.set("k1", [1])
    c.set("k2", [2])
    # Touch k1 (LRU end)
    c.get("k1")
    # Insert k3 -> should evict k2 (least recently used).
    c.set("k3", [3])
    assert c.get("k1") is not None
    assert c.get("k2") is None
    assert c.get("k3") is not None
    s = c.stats()
    assert s["evictions"] == 1


def test_cache_ttl_expiry():
    c = _SearchCache(capacity=4, ttl=0.1)  # 100ms
    c.set("k1", [1])
    assert c.get("k1") is not None
    time.sleep(0.15)
    # Expired -> should return None and remove.
    assert c.get("k1") is None
    s = c.stats()
    # 1 hit + 1 miss recorded.
    assert s["hits"] == 1
    assert s["misses"] == 1


def test_cache_invalidate_all():
    c = _SearchCache(capacity=4, ttl=60)
    c.set("k1", [1])
    c.set("k2", [2])
    assert len(c) == 2
    c.invalidate_all()
    assert len(c) == 0
    assert c.get("k1") is None


def test_cache_make_key_stable():
    """Same kwargs -> same key, regardless of order."""
    k1 = _SearchCache.make_key(query="hello", top_k=10, mode="smart")
    k2 = _SearchCache.make_key(top_k=10, mode="smart", query="hello")
    assert k1 == k2
    k3 = _SearchCache.make_key(query="hello", top_k=11, mode="smart")
    assert k1 != k3


def test_cache_thread_safety():
    """Concurrent set/get must not crash or corrupt counters."""
    c = _SearchCache(capacity=128, ttl=60)
    errors: list[Exception] = []

    def worker(i: int):
        try:
            for j in range(50):
                c.set(f"k{i}-{j}", [i, j])
                v = c.get(f"k{i}-{j}")
                assert v == [i, j]
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    s = c.stats()
    # 8 workers × 50 sets, 8 × 50 gets
    assert s["hits"] == 400


# ---------------------------------------------------------------------
# Integration: MemKraft + cache invalidation
# ---------------------------------------------------------------------

@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    base = tmp_path / "memcache"
    inst = MemKraft(str(base))
    if hasattr(inst, "init"):
        inst.init()
    inst.track("Alice", source="test")
    inst.update("Alice", "Alice loves coffee", source="test")
    inst.track("Bob", source="test")
    inst.update("Bob", "Bob loves tea", source="test")
    # Reset cache stats AFTER setup so tests start with a clean slate.
    inst.cache_clear()
    inst._get_search_cache().reset_stats()
    return inst


def test_cache_hit_on_repeat_query(mk):
    r1 = mk.search_v2("Alice")
    r2 = mk.search_v2("Alice")
    assert r1 == r2
    s = mk.cache_stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["size"] == 1


def test_cache_miss_after_update(mk):
    mk.search_v2("Alice")
    gen_before = mk.cache_stats()["generation"]
    mk.update("Alice", "Alice now drinks matcha", source="test")
    gen_after = mk.cache_stats()["generation"]
    assert gen_after > gen_before
    mk.search_v2("Alice")
    s = mk.cache_stats()
    # Both calls should be misses (different generation -> different key).
    assert s["misses"] >= 2


def test_cache_miss_after_track(mk):
    mk.search_v2("Alice")
    gen_before = mk.cache_stats()["generation"]
    mk.track("Charlie", source="test")
    assert mk.cache_stats()["generation"] > gen_before
    mk.search_v2("Alice")
    assert mk.cache_stats()["misses"] >= 2


def test_cache_miss_after_fact_add(mk):
    mk.search_v2("Alice")
    gen_before = mk.cache_stats()["generation"]
    mk.fact_add("Alice", "role", "engineer")
    assert mk.cache_stats()["generation"] > gen_before
    mk.search_v2("Alice")
    assert mk.cache_stats()["misses"] >= 2


def test_cache_miss_after_log_event(mk):
    mk.search_v2("Alice")
    gen_before = mk.cache_stats()["generation"]
    mk.log_event("test event", tags="alice")
    assert mk.cache_stats()["generation"] > gen_before
    mk.search_v2("Alice")
    assert mk.cache_stats()["misses"] >= 2


def test_cache_opt_out_via_cache_false(mk):
    mk.search_v2("Alice")  # miss + populate
    s1 = mk.cache_stats()
    # cache=False should NOT touch the cache at all.
    r = mk.search_v2("Alice", cache=False)
    assert isinstance(r, list)
    s2 = mk.cache_stats()
    assert s2["hits"] == s1["hits"]
    assert s2["misses"] == s1["misses"]


def test_cache_smart_search(mk):
    """search_smart should also be cached."""
    r1 = mk.search_smart("Alice")
    r2 = mk.search_smart("Alice")
    assert r1 == r2
    s = mk.cache_stats()
    assert s["hits"] >= 1


def test_cache_smart_invalidates_on_mutation(mk):
    mk.search_smart("Alice")
    gen_before = mk.cache_stats()["generation"]
    mk.update("Alice", "new info", source="test")
    assert mk.cache_stats()["generation"] > gen_before
    mk.search_smart("Alice")
    # second smart call should miss (new generation key).
    assert mk.cache_stats()["misses"] >= 2


def test_cache_clear_purges_entries(mk):
    mk.search_v2("Alice")
    mk.search_v2("Bob")
    assert mk.cache_stats()["size"] == 2
    mk.cache_clear()
    assert mk.cache_stats()["size"] == 0


def test_cache_configure_capacity(mk):
    mk.cache_configure(capacity=2)
    mk.search_v2("Alice")
    mk.search_v2("Bob")
    mk.search_v2("Charlie") if False else mk.search_v2("nonexistent_query_xyz")
    s = mk.cache_stats()
    assert s["capacity"] == 2
    assert s["size"] <= 2


def test_cache_distinct_top_k_distinct_keys(mk):
    """Different top_k should produce different cache entries."""
    mk.search_v2("Alice", top_k=5)
    mk.search_v2("Alice", top_k=20)
    s = mk.cache_stats()
    # Two distinct misses, two cache entries.
    assert s["size"] == 2
    assert s["misses"] == 2


def test_cache_distinct_fuzzy_distinct_keys(mk):
    mk.search_v2("Alice", fuzzy=False)
    mk.search_v2("Alice", fuzzy=True)
    s = mk.cache_stats()
    assert s["size"] == 2


def test_cache_hit_rate_calculation(mk):
    mk.search_v2("Alice")  # miss
    mk.search_v2("Alice")  # hit
    mk.search_v2("Alice")  # hit
    mk.search_v2("Bob")    # miss
    s = mk.cache_stats()
    # 2 hits / 4 total = 0.5
    assert abs(s["hit_rate"] - 0.5) < 1e-6


def test_cache_empty_query_not_cached(mk):
    """Empty query short-circuits before cache lookup -> no hit/miss."""
    r = mk.search_v2("")
    assert r == []
    s = mk.cache_stats()
    assert s["hits"] == 0
    assert s["misses"] == 0


def test_cache_results_independent_after_get(mk):
    """Mutating returned list must not affect cached entry."""
    r1 = mk.search_v2("Alice")
    if r1:
        r1.append({"file": "fake"})
    r2 = mk.search_v2("Alice")
    # r2 should not contain the fake entry.
    assert {"file": "fake"} not in r2


def test_cache_thread_safety_with_mk(mk):
    """Concurrent search + mutation must not crash."""
    errors: list[Exception] = []

    def reader(i: int):
        try:
            for _ in range(20):
                mk.search_v2("Alice")
        except Exception as e:
            errors.append(e)

    def writer(i: int):
        try:
            for j in range(5):
                mk.update("Alice", f"thread {i} update {j}", source="test")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# ---------------------------------------------------------------------
# Bitemporal: as_of via fact_get_at_time should not be affected
# (we cache search_v2 / search_smart only — bitemporal is a separate API
# and bumps generation on fact_add).
# ---------------------------------------------------------------------

def test_cache_fact_add_bumps_generation_for_bitemporal_safety(tmp_path):
    base = tmp_path / "bt"
    mk = MemKraft(str(base))
    if hasattr(mk, "init"):
        mk.init()
    mk.track("Sim", source="t")
    mk.fact_add("Sim", "role", "CEO", valid_from="2020-01-01")
    g1 = mk.cache_stats()["generation"]
    mk.search_v2("Sim")
    mk.fact_add("Sim", "role", "CTO", valid_from="2024-01-01")
    g2 = mk.cache_stats()["generation"]
    assert g2 > g1
    # And subsequent search should miss.
    mk.search_v2("Sim")
    assert mk.cache_stats()["misses"] >= 2
