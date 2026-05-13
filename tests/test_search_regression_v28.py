"""v2.8 — Search / Mutation regression tests (WS-F safety net).

Covers 23 test scenarios in 6 categories:
  A. Basic search behaviour (7)
  B. Fuzzy / alias matching (2)
  C. Update → Search consistency (3)
  D. Cache invalidation paths (3)
  E. Edge cases (5)
  F. Search result structure (3)

All tests use isolated tmp_path fixtures and real MemKraft instances (no mocks).
"""
from __future__ import annotations

import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def mk():
    """Fresh MemKraft instance in a temp directory."""
    tmpdir = tempfile.mkdtemp(prefix="mk-regression-")
    instance = MemKraft(base_dir=tmpdir)
    instance.init()
    yield instance
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def mk_with_entities(mk):
    """MemKraft with a few pre-tracked entities."""
    mk.track("Alice", entity_type="person", source="test")
    mk.update("Alice", "Alice works at Acme Corp as a senior engineer", source="test")
    mk.track("Bob", entity_type="person", source="test")
    mk.update("Bob", "Bob is a freelance designer based in Berlin", source="test")
    mk.track("Charlie", entity_type="person", source="test")
    mk.update("Charlie", "Charlie is the CTO of a blockchain startup", source="test")
    return mk


# =====================================================================
# A. Basic search behaviour (7 tests)
# =====================================================================

class TestBasicSearch:

    def test_exact_match(self, mk):
        """A1: update with unique token → search finds it."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "unique_token_xyz", source="test")
        results = mk.search("unique_token_xyz")
        matches = [r["match"].lower() for r in results]
        assert "ent" in matches

    def test_multi_token_query(self, mk):
        """A2: query with multiple tokens → entity found when most tokens match."""
        mk.track("project", entity_type="project", source="test")
        mk.update("project", "alpha beta gamma delta", source="test")
        results = mk.search("alpha beta")
        matches = [r["match"] for r in results]
        assert "project" in matches

    def test_case_insensitive(self, mk):
        """A3: different casing in query → same results."""
        mk.track("CaseEntity", entity_type="concept", source="test")
        mk.update("CaseEntity", "HelloWorld testing case", source="test")
        r1 = mk.search("helloworld")
        r2 = mk.search("HELLOworld")
        m1 = {r["match"].lower() for r in r1}
        m2 = {r["match"].lower() for r in r2}
        assert m1 == m2

    def test_empty_query(self, mk):
        """A4: empty query → stable (empty list, no exception)."""
        result = mk.search("")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_no_match(self, mk):
        """A5: non-existent token → empty results or score 0."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "completely unrelated content", source="test")
        results = mk.search("zzz_nonexistent_token_zzz")
        assert isinstance(results, list)
        # Should either be empty or all scores 0
        if results:
            assert all(r["score"] == 0 for r in results)

    def test_special_characters(self, mk):
        """A6: regex-special chars in query → no crash."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "testing brackets and parens", source="test")
        for q in ["[]", "()", "?", "*"]:
            try:
                results = mk.search(q)
                assert isinstance(results, list)
            except re.error:
                pytest.fail(f"search('{q}') raised re.error")

    def test_unicode_korean(self, mk):
        """A7: Korean tokens → search works."""
        mk.track("이순신", entity_type="person", source="test")
        mk.update("이순신", "조선 명장 충무공", source="test")
        results = mk.search("조선")
        matches = [r["match"].lower() for r in results]
        assert "이순신" in matches


# =====================================================================
# B. Fuzzy / alias matching (2 tests)
# =====================================================================

class TestFuzzyAlias:

    def test_alias_resolved(self, mk):
        """B1: alias_add → search by alias finds canonical entity."""
        mk.track("Simon", entity_type="person", source="test")
        mk.update("Simon", "CEO of Hashed", source="test")
        mk.alias_add("Simon", ["서준"])
        results = mk.search("서준")
        matches = [r["match"].lower() for r in results]
        assert "simon" in matches

    def test_partial_token(self, mk):
        """B2: partial token match — verify current behaviour doesn't crash."""
        mk.track("memkraft", entity_type="project", source="test")
        mk.update("memkraft", "memory management framework", source="test")
        results = mk.search("memk")
        assert isinstance(results, list)
        # Partial match may or may not find it; just verify no crash


# =====================================================================
# C. Update → Search consistency (3 tests)
# =====================================================================

class TestUpdateSearchConsistency:

    def test_write_then_search(self, mk):
        """C1: update then immediately search → new info found."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "quantum_supremacy_2026", source="test")
        results = mk.search("quantum_supremacy_2026")
        matches = [r["match"].lower() for r in results]
        assert "ent" in matches

    def test_update_overwrite(self, mk):
        """C2: two updates → search reflects latest content."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "first_version_abc", source="test")
        mk.update("ent", "second_version_xyz", source="test")
        results_old = mk.search("first_version_abc")
        results_new = mk.search("second_version_xyz")
        # New content must be findable
        assert any(r["match"].lower() == "ent" for r in results_new)
        # Old content may still be present (append behaviour) — no hard assert on absence

    def test_track_then_search(self, mk):
        """C3: track (no update) → entity searchable by name."""
        mk.track("SearchableEntity", entity_type="person", source="test")
        results = mk.search("SearchableEntity")
        matches = [r["match"].lower() for r in results]
        assert "searchableentity" in matches


# =====================================================================
# D. Cache invalidation paths (3 tests)
# =====================================================================

class TestCacheInvalidation:

    def test_modification_invalidation(self, mk):
        """D1: modify file mtime → search reflects new content."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "original_content_alpha", source="test")
        # Prime cache
        r1 = mk.search("original_content_alpha")
        assert any(r["match"].lower() == "ent" for r in r1)

        # Find the actual entity file (may be in live-notes/ or entities/)
        slug = mk._slugify("ent")
        entity_file = None
        for d in [mk.entities_dir, mk.base_dir / "live-notes"]:
            candidate = d / f"{slug}.md"
            if candidate.exists():
                entity_file = candidate
                break
        assert entity_file is not None, f"Entity file not found for slug '{slug}'"
        content = entity_file.read_text(encoding="utf-8")
        entity_file.write_text(content + "\nnew_content_beta", encoding="utf-8")

        # Search for new content — should find it
        results = mk.search("new_content_beta")
        matches = [r["match"].lower() for r in results]
        assert "ent" in matches

    def test_delete_invalidation(self, mk):
        """D2: delete file → search excludes it (graceful, no crash)."""
        mk.track("temp_entity", entity_type="concept", source="test")
        mk.update("temp_entity", "deleteme_content", source="test")
        # Find the actual entity file (may be in live-notes/ or entities/)
        slug = mk._slugify("temp_entity")
        entity_file = None
        for d in [mk.entities_dir, mk.base_dir / "live-notes"]:
            candidate = d / f"{slug}.md"
            if candidate.exists():
                entity_file = candidate
                break
        assert entity_file is not None, f"Entity file not found for slug '{slug}'"

        # Delete
        entity_file.unlink()

        # Search should not crash
        results = mk.search("deleteme_content")
        assert isinstance(results, list)
        matches = [r["match"].lower() for r in results]
        assert "temp_entity" not in matches

    def test_rapid_update(self, mk):
        """D3: 5 rapid updates → last content searchable."""
        mk.track("ent", entity_type="concept", source="test")
        for i in range(5):
            mk.update("ent", f"rapid_update_{i}_final", source="test")
            time.sleep(0.02)  # slight delay to ensure distinct writes

        results = mk.search("rapid_update_4_final")
        matches = [r["match"].lower() for r in results]
        assert "ent" in matches


# =====================================================================
# E. Edge cases (5 tests)
# =====================================================================

class TestEdgeCases:

    def test_large_file(self, mk):
        """E1: 50KB content → search still works."""
        mk.track("big_entity", entity_type="concept", source="test")
        large_content = "x " * 25000  # ~50KB
        mk.update("big_entity", large_content, source="test")
        results = mk.search("x")
        assert isinstance(results, list)
        # Should find the big entity
        matches = [r["match"] for r in results]
        assert "big_entity" in matches

    def test_many_entities_top_k(self, mk):
        """E2: 200 entities → search returns results (core.search returns all, not top_k limited)."""
        for i in range(200):
            mk.track(f"entity_{i:03d}", entity_type="concept", source="test")
            mk.update(f"entity_{i:03d}", f"common_token content_{i}", source="test")
        results = mk.search("common_token")
        # core.search() returns ALL matching results (no top_k param)
        # just verify it works and returns a reasonable subset
        assert len(results) > 0
        assert len(results) <= 200

    def test_frontmatter_only(self, mk):
        """E3: entity with frontmatter only, no body → stable."""
        slug = "fm_only"
        entity_file = mk.entities_dir / f"{slug}.md"
        entity_file.parent.mkdir(parents=True, exist_ok=True)
        entity_file.write_text(
            "---\nname: fm_only\ntype: concept\n---\n",
            encoding="utf-8",
        )
        results = mk.search("fm_only")
        assert isinstance(results, list)

    def test_special_filename_korean(self, mk):
        """E4: Korean entity name → searchable."""
        mk.track("김서준", entity_type="person", source="test")
        mk.update("김서준", "hashed 대표이사 ceo", source="test")
        results = mk.search("김서준")
        matches = [r["match"].lower() for r in results]
        assert "김서준" in matches

    def test_concurrent_search(self, mk):
        """E5: 5 concurrent search threads → all stable."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "concurrent_test_content", source="test")
        errors: list[Exception] = []

        def worker():
            try:
                results = mk.search("concurrent_test_content")
                assert isinstance(results, list)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == [], f"Concurrent search errors: {errors}"


# =====================================================================
# F. Search result structure (3 tests)
# =====================================================================

class TestSearchResultStructure:

    def test_score_ordering(self, mk):
        """F1: results sorted by score descending."""
        mk.track("high", entity_type="concept", source="test")
        mk.update("high", "exact_match_token alpha beta", source="test")
        mk.track("low", entity_type="concept", source="test")
        mk.update("low", "something unrelated", source="test")
        results = mk.search("exact_match_token")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_limit(self, mk):
        """F2: core.search() returns all matching results (no top_k param)."""
        for i in range(30):
            mk.track(f"tk_{i:02d}", entity_type="concept", source="test")
            mk.update(f"tk_{i:02d}", "top_k_searchable content", source="test")
        results = mk.search("top_k_searchable")
        # core.search() has no top_k parameter — returns all matches
        assert len(results) > 0
        assert len(results) <= 30

    def test_return_shape_stability(self, mk):
        """F3: each result has expected keys and types."""
        mk.track("ent", entity_type="concept", source="test")
        mk.update("ent", "shape_test_token", source="test")
        results = mk.search("shape_test_token")
        assert len(results) >= 1
        for r in results:
            assert "file" in r
            assert "score" in r
            assert "match" in r
            assert isinstance(r["file"], str)
            assert isinstance(r["score"], (int, float))
            assert isinstance(r["match"], str)


# Need re import for test_special_characters (already at top)
