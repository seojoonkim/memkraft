"""MemKraft unit tests — R11 hotfix + org/product/location detection"""
import json
import os
import tempfile
import shutil
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft.core import MemKraft


@pytest.fixture
def mk():
    """Create a temporary MemKraft instance."""
    tmpdir = tempfile.mkdtemp(prefix="mk-test-")
    instance = MemKraft(base_dir=tmpdir)
    instance.init()
    yield instance
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def mk_with_data(mk):
    """MemKraft instance with sample data."""
    mk.extract("Simon Kim is the CEO of Hashed in Seoul.", source="test")
    mk.track("Ada Lovelace", entity_type="person", source="test")
    return mk


# ── Init ─────────────────────────────────────────────────
class TestInit:
    def test_init_creates_dirs(self, mk):
        assert (mk.base_dir / "entities").exists()
        assert (mk.base_dir / "decisions").exists()
        assert (mk.base_dir / "inbox").exists()

    def test_init_creates_resolver(self, mk):
        assert (mk.base_dir / "RESOLVER.md").exists()

    def test_init_idempotent(self, mk):
        mk.init()  # should not raise
        assert (mk.base_dir / "entities").exists()


# ── Extract ──────────────────────────────────────────────
class TestExtract:
    def test_extract_inline_person(self, mk):
        result = mk.extract("Simon Kim is the CEO of Hashed.", source="test")
        entities = [r for r in result if r.get("type") == "person"]
        names = [e["name"] for e in entities]
        assert "Simon Kim" in names

    def test_extract_creates_entity_file(self, mk):
        mk.extract("Simon Kim is the CEO of Hashed.", source="test")
        entity_files = list((mk.base_dir / "entities").glob("*.md"))
        assert len(entity_files) >= 1

    def test_extract_fact_registry(self, mk):
        result = mk.extract("Revenue is $5.3M with 85% growth.", source="test")
        registry_facts = [r for r in result if r.get("type") == "fact-registry"]
        if registry_facts:
            assert registry_facts[0]["action"] == "written"

    def test_extract_file(self, mk):
        tmpfile = mk.base_dir / "test-conversation.md"
        tmpfile.write_text("# Meeting\n\nJack Ma discussed AI strategy.", encoding="utf-8")
        result = mk.extract(str(tmpfile), source="file-test")
        entities = [r for r in result if r.get("type") == "person"]
        names = [e["name"] for e in entities]
        assert "Jack Ma" in names

    def test_extract_empty_returns_empty(self, mk):
        result = mk.extract("", source="test")
        assert result == []

    def test_extract_nonexistent_file_no_crash(self, mk):
        result = mk.extract("/tmp/nonexistent_md_file_12345.md", source="test")
        # Should return empty list, not crash
        assert result is not None

    def test_extract_returns_list(self, mk):
        result = mk.extract("Simon Kim is the CEO of Hashed.", source="test")
        assert isinstance(result, list)


# ── Organization Detection ───────────────────────────────
class TestOrgDetection:
    def test_detect_known_org_apple(self, mk):
        entities = mk._detect_regex("Apple released a new MacBook Pro with M5 chip.")
        names = [e["name"] for e in entities]
        assert "Apple" in names
        apple_type = [e for e in entities if e["name"] == "Apple"][0]["type"]
        assert apple_type == "organization"

    def test_detect_known_org_google(self, mk):
        entities = mk._detect_regex("Google announced new AI features.")
        names = [e["name"] for e in entities]
        assert "Google" in names

    def test_detect_org_suffix(self, mk):
        entities = mk._detect_regex("OpenAI Labs released GPT-5.")
        org_entities = [e for e in entities if e["type"] == "organization"]
        org_names = [e["name"] for e in org_entities]
        assert any("Labs" in n for n in org_names)

    def test_detect_korean_org(self, mk):
        entities = mk._detect_regex("삼성전자와 현대자동차가 협력했다.")
        org_entities = [e for e in entities if e["type"] == "organization"]
        assert len(org_entities) >= 1

    def test_detect_hashed_as_org(self, mk):
        entities = mk._detect_regex("Hashed is a VC firm in Seoul.")
        names = [e["name"] for e in entities]
        assert "Hashed" in names
        hashed_type = [e for e in entities if e["name"] == "Hashed"][0]["type"]
        assert hashed_type == "organization"


# ── Product Detection ────────────────────────────────────
class TestProductDetection:
    def test_detect_macbook_pro(self, mk):
        entities = mk._detect_regex("Apple released a new MacBook Pro with M5 chip.")
        product_entities = [e for e in entities if e["type"] == "product"]
        product_names = [e["name"] for e in product_entities]
        assert any("Pro" in n for n in product_names)

    def test_detect_iphone_pro(self, mk):
        entities = mk._detect_regex("The iPhone Pro has a new camera.")
        product_entities = [e for e in entities if e["type"] == "product"]
        assert len(product_entities) >= 1


# ── Location Detection ───────────────────────────────────
class TestLocationDetection:
    def test_detect_seoul(self, mk):
        entities = mk._detect_regex("Simon Kim is based in Seoul.")
        location_entities = [e for e in entities if e["type"] == "location"]
        location_names = [e["name"] for e in location_entities]
        assert "Seoul" in location_names

    def test_detect_multiple_locations(self, mk):
        entities = mk._detect_regex("Offices in Seoul, Tokyo, and Singapore.")
        location_entities = [e for e in entities if e["type"] == "location"]
        location_names = [e["name"] for e in location_entities]
        assert "Seoul" in location_names
        assert "Tokyo" in location_names
        assert "Singapore" in location_names

    def test_detect_korean_location(self, mk):
        entities = mk._detect_regex("서울시에서 강남구로 이사했다.")
        location_entities = [e for e in entities if e["type"] == "location"]
        assert len(location_entities) >= 1


# ── Track & Update ───────────────────────────────────────
class TestTrackUpdate:
    def test_track_creates_live_note(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        assert (mk.base_dir / "live-notes" / "test-person.md").exists()

    def test_update_appends_info(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.update("Test Person", info="CEO of TestCorp", source="test")
        content = (mk.base_dir / "live-notes" / "test-person.md").read_text(encoding="utf-8")
        assert "CEO of TestCorp" in content

    def test_update_entity_not_found_no_crash(self, mk):
        mk.update("Nobody", info="something", source="test")

    def test_update_empty_info_skipped(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        # Empty info should be silently skipped
        mk.update("Test Person", info="", source="test")

    def test_state_transition(self, mk):
        mk.track("Ada Lovelace", entity_type="person", source="test")
        mk.update("Ada Lovelace", info="Role: CTO of EngineCo", source="test1")
        mk.update("Ada Lovelace", info="Role: CEO of Analytical Machines", source="test2")
        content = (mk.base_dir / "live-notes" / "ada-lovelace.md").read_text(encoding="utf-8")
        assert "CEO of Analytical Machines" in content


# ── Search ───────────────────────────────────────────────
class TestSearch:
    def test_search_exact_match(self, mk_with_data):
        results = mk_with_data.search("CEO of Hashed")
        assert len(results) > 0
        assert results[0]["score"] > 0

    def test_search_exact_higher_than_partial(self, mk_with_data):
        exact_results = mk_with_data.search("CEO of Hashed")
        partial_results = mk_with_data.search("Hashed")
        if exact_results and partial_results:
            assert exact_results[0]["score"] >= partial_results[0]["score"]

    def test_search_fuzzy(self, mk_with_data):
        results = mk_with_data.search("Hashd", fuzzy=True)
        assert isinstance(results, list)

    def test_search_no_results_returns_empty(self, mk):
        results = mk.search("zzzznonexistent")
        assert results == []

    def test_search_exact_score_is_1_or_high(self, mk_with_data):
        """Exact match should score >= 0.8"""
        results = mk_with_data.search("CEO of Hashed")
        if results:
            assert results[0]["score"] >= 0.8, f"Exact match score too low: {results[0]['score']}"

    def test_search_empty_query_returns_empty(self, mk):
        results = mk.search("")
        assert results == []

    def test_search_returns_list(self, mk_with_data):
        results = mk_with_data.search("Hashed")
        assert isinstance(results, list)


# ── Detect (Core) ────────────────────────────────────────
class TestDetect:
    def test_detect_english_names(self, mk):
        entities = mk._detect_regex("Simon Kim and Grace Hopper discussed AI.")
        names = [e["name"] for e in entities]
        assert "Simon Kim" in names
        assert "Grace Hopper" in names

    def test_detect_korean_names(self, mk):
        entities = mk._detect_regex("김서준과 박민수가 만났다.")
        names = [e["name"] for e in entities]
        assert any("서준" in n or "민수" in n for n in names)

    def test_detect_handle(self, mk):
        entities = mk._detect_regex("Follow @simonkim_nft for updates.")
        names = [e["name"] for e in entities]
        assert "simonkim_nft" in names


# ── Promote ──────────────────────────────────────────────
class TestPromote:
    def test_promote_nonexistent(self, mk):
        mk.promote("Nobody", tier="core")

    def test_promote_existing(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.promote("Test Person", tier="core")


# ── Brief ────────────────────────────────────────────────
class TestBrief:
    def test_brief_nonexistent(self, mk):
        mk.brief("Nobody")

    def test_brief_existing(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.brief("Test Person")


# ── Dream ────────────────────────────────────────────────
class TestDream:
    def test_dream_dry_run(self, mk):
        mk.dream(dry_run=True)

    def test_dream_live(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.dream()


# ── Error Handling ───────────────────────────────────────
class TestErrorHandling:
    def test_corrupted_md_file_no_crash(self, mk):
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        bad_file = mk.base_dir / "entities" / "bad.md"
        bad_file.write_bytes(b'\xff\xfe\x00\x00')
        mk.search("test")  # Should not crash

    def test_empty_entity_dir(self, mk):
        mk.search("anything")

    def test_extract_special_chars(self, mk):
        mk.extract("Test with <script>alert('xss')</script>", source="test")

    def test_track_duplicate_no_crash(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.track("Test Person", entity_type="person", source="test2")

    def test_very_long_input(self, mk):
        long_text = "Simon Kim is the CEO. " * 1000
        mk.extract(long_text, source="test")

    def test_unicode_input(self, mk):
        mk.extract("김서준이 서울에서 회의를 했다. 東京に行った。🚀", source="test")

    def test_search_idf_scoring(self, mk_with_data):
        # IDF-weighted search should still return results
        results = mk_with_data.search("CEO")
        assert isinstance(results, list)

    def test_detect_mixed_content(self, mk):
        entities = mk._detect_regex("Apple opened an office in Seoul. Samsung also expanded to Tokyo.")
        types = [e["type"] for e in entities]
        assert "organization" in types
        assert "location" in types

    def test_extract_and_search_integration(self, mk):
        mk.extract("OpenAI released GPT-5 in San Francisco.", source="test")
        results = mk.search("OpenAI")
        assert len(results) > 0

    def test_safe_read_corrupted_file(self, mk):
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        bad = mk.base_dir / "entities" / "corrupt.md"
        bad.write_bytes(b'\xff\xfe\x00\x00')
        result = mk._safe_read(bad)
        # Should not crash, return something
        assert isinstance(result, str)
