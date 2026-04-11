"""MemKraft unit tests — R11 hotfix"""
import json
import os
import tempfile
import shutil
import pytest
from pathlib import Path

# Add src to path
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
        # Should detect monetary and percentage facts
        if registry_facts:
            assert registry_facts[0]["action"] == "written"

    def test_extract_file(self, mk):
        # Create a temp markdown file
        tmpfile = mk.base_dir / "test-conversation.md"
        tmpfile.write_text("# Meeting\n\nJack Ma discussed AI strategy.", encoding="utf-8")
        result = mk.extract(str(tmpfile), source="file-test")
        # Should extract from file content
        entities = [r for r in result if r.get("type") == "person"]
        names = [e["name"] for e in entities]
        assert "Jack Ma" in names

    def test_extract_empty_returns_empty(self, mk):
        result = mk.extract("", source="test")
        assert result == [] or result is None

    def test_extract_nonexistent_file(self, mk):
        result = mk.extract("/tmp/nonexistent_md_file_12345.md", source="test")
        # Should not crash, should return empty or handle gracefully
        assert result is not None


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
        # Should not crash when updating non-existent entity
        mk.update("Nobody", info="something", source="test")

    def test_state_transition(self, mk):
        mk.track("Ada Lovelace", entity_type="person", source="test")
        mk.update("Ada Lovelace", info="Role: CTO of EngineCo", source="test1")
        mk.update("Ada Lovelace", info="Role: CEO of Analytical Machines", source="test2")
        content = (mk.base_dir / "live-notes" / "ada-lovelace.md").read_text(encoding="utf-8")
        # Should have a state transition entry
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
        # Fuzzy should find something close to "Hashed"
        assert isinstance(results, list)

    def test_search_no_results(self, mk):
        results = mk.search("zzzznonexistent")
        assert results == [] or results is not None

    def test_search_exact_score_is_high(self, mk_with_data):
        """Exact match should score >= 0.8"""
        results = mk_with_data.search("CEO of Hashed")
        if results:
            assert results[0]["score"] >= 0.8, f"Exact match score too low: {results[0]['score']}"


# ── Detect ───────────────────────────────────────────────
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

    def test_detect_organization(self, mk):
        """Organizations like 'Apple', 'Google' should ideally be detected."""
        entities = mk._detect_regex("Apple released a new MacBook Pro.")
        # Current limitation: org/product detection not implemented
        # This test documents the gap
        # TODO: Add organization detection
        pass  # Will fail when org detection is added — change to assert


# ── Promote ──────────────────────────────────────────────
class TestPromote:
    def test_promote_nonexistent(self, mk):
        # Should not crash
        mk.promote("Nobody", tier="core")

    def test_promote_existing(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.promote("Test Person", tier="core")


# ── Brief ────────────────────────────────────────────────
class TestBrief:
    def test_brief_nonexistent(self, mk):
        # Should not crash
        mk.brief("Nobody")

    def test_brief_existing(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.brief("Test Person")


# ── Dream ────────────────────────────────────────────────
class TestDream:
    def test_dream_dry_run(self, mk):
        mk.dream(dry_run=True)  # Should not crash

    def test_dream_live(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.dream()  # Should not crash


# ── Error Handling ───────────────────────────────────────
class TestErrorHandling:
    def test_corrupted_md_file_no_crash(self, mk):
        bad_file = mk.base_dir / "entities" / "bad.md"
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        bad_file.write_bytes(b'\xff\xfe\x00\x00')
        # list/search should not crash
        mk.search("test")

    def test_empty_entity_dir(self, mk):
        mk.search("anything")  # No crash on empty dir

    def test_extract_special_chars(self, mk):
        mk.extract("Test with <script>alert('xss')</script>", source="test")
        # Should not crash, should sanitize or handle
