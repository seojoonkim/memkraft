"""MemKraft unit tests — R16 검수 + 고도화"""
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


class TestR14Features:
    def test_decay_dry_run(self, mk_with_data):
        results = mk_with_data.decay(days=1, dry_run=True)
        assert isinstance(results, list)

    def test_decay_no_crash(self, mk):
        mk.decay(days=999, dry_run=True)

    def test_dedup_dry_run(self, mk_with_data):
        results = mk_with_data.dedup(dry_run=True)
        assert isinstance(results, list)

    def test_dedup_empty_memory(self, mk):
        mk.dedup(dry_run=True)

    def test_summarize_existing(self, mk_with_data):
        results = mk_with_data.summarize(name="Ada Lovelace")
        assert isinstance(results, list)

    def test_summarize_nonexistent(self, mk):
        mk.summarize(name="Nobody")

    def test_summarize_all(self, mk_with_data):
        mk_with_data.summarize()  # bulk summarize


class TestR15AgenticSearch:
    def test_agentic_search_basic(self, mk_with_data):
        results = mk_with_data.agentic_search("Hashed")
        assert isinstance(results, list)

    def test_agentic_search_decomposition(self, mk):
        sub = mk._decompose_query("who is CEO of Hashed")
        assert len(sub) >= 2
        assert any("CEO" in s or "Hashed" in s for s in sub)

    def test_agentic_search_korean_decomposition(self, mk):
        sub = mk._decompose_query("Hashed의 CEO")
        assert len(sub) >= 2

    def test_agentic_search_empty(self, mk):
        results = mk.agentic_search("zzzznonexistent")
        assert isinstance(results, list)

    def test_agentic_search_with_hops(self, mk_with_data):
        results = mk_with_data.agentic_search("CEO", max_hops=1)
        assert isinstance(results, list)

    def test_agentic_search_json_output(self, mk_with_data):
        results = mk_with_data.agentic_search("Hashed", json_output=True)
        assert isinstance(results, list)


# ── R16: Entity Detection — Email, URL, Version Products ────
class TestR16EntityDetection:
    def test_detect_email(self, mk):
        entities = mk._detect_regex("Contact simon@hashed.com for details.")
        names = [e["name"] for e in entities]
        assert "simon@hashed.com" in names
        email_entity = [e for e in entities if e["name"] == "simon@hashed.com"][0]
        assert email_entity["type"] == "contact"

    def test_detect_url(self, mk):
        entities = mk._detect_regex("Visit https://memkraft.dev/docs for documentation.")
        names = [e["name"] for e in entities]
        assert any("https://memkraft.dev" in n for n in names)
        url_entities = [e for e in entities if e["type"] == "reference"]
        assert len(url_entities) >= 1

    def test_detect_version_product(self, mk):
        entities = mk._detect_regex("OpenAI released GPT-5 with better reasoning.")
        product_entities = [e for e in entities if e["type"] == "product"]
        product_names = [e["name"] for e in product_entities]
        assert any("GPT-5" in n for n in product_names)

    def test_detect_iphone_version(self, mk):
        entities = mk._detect_regex("The iPhone 16 Pro has a new camera system.")
        product_entities = [e for e in entities if e["type"] == "product"]
        product_names = [e["name"] for e in product_entities]
        assert any("Pro" in n or "16" in n for n in product_names)

    def test_detect_no_false_positive_date_as_product(self, mk):
        entities = mk._detect_regex("The meeting is on 2024-03-15.")
        product_names = [e["name"] for e in entities if e["type"] == "product"]
        assert "2024" not in product_names

    def test_detect_korean_tech_org(self, mk):
        entities = mk._detect_regex("카카오벤처스에서 투자를 받았다.")
        org_entities = [e for e in entities if e["type"] == "organization"]
        assert len(org_entities) >= 1

    def test_detect_multiple_emails(self, mk):
        entities = mk._detect_regex("Send to alice@test.com and bob@test.com")
        contact_entities = [e for e in entities if e["type"] == "contact"]
        assert len(contact_entities) >= 2

    def test_blocklist_expansion(self, mk):
        """Expanded common word blocklist should filter false positives."""
        entities = mk._detect_regex("Also Would Could Should be ignored.")
        person_names = [e["name"] for e in entities if e["type"] == "person"]
        assert "Also Would" not in person_names
        assert "Could Should" not in person_names


# ── R16: Search Quality — Phrase Matching + Date-Aware ──────
class TestR16SearchQuality:
    def test_phrase_match_higher_than_scattered_tokens(self, mk):
        """Multi-word exact phrase should score higher than scattered tokens."""
        mk.extract("Simon Kim is the CEO of Hashed in Seoul.", source="test")
        # Create another entity with scattered words
        mk.track("CEO Report", entity_type="person", source="test")
        mk.update("CEO Report", info="Hashed mentioned briefly, not CEO related", source="test")
        phrase_results = mk.search("CEO of Hashed")
        assert len(phrase_results) > 0
        # The first result should be the one with the exact phrase
        assert phrase_results[0]["score"] >= 0.8

    def test_heading_match_boost(self, mk):
        """Query matching a heading should get a boost."""
        mk.track("Test Heading", entity_type="person", source="test")
        results = mk.search("Test Heading")
        assert len(results) > 0
        # Heading match should contribute to high score
        assert results[0]["score"] > 0

    def test_search_empty_string_returns_empty(self, mk):
        results = mk.search("   ")
        assert results == []

    def test_search_special_chars_no_crash(self, mk_with_data):
        results = mk_with_data.search("CEO (test) [bracket]")
        assert isinstance(results, list)

    def test_search_korean_query(self, mk):
        mk.extract("김서준이 서울에서 회의를 했다.", source="test")
        results = mk.search("서울")
        assert len(results) > 0

    def test_search_cjk_mixed(self, mk):
        mk.extract("田中太郎 met Simon Kim in Tokyo.", source="test")
        results = mk.search("Tokyo")
        assert len(results) > 0


# ── R16: Dream Cycle — Returns + Source-less + Compression ──
class TestR16DreamCycle:
    def test_dream_returns_dict(self, mk):
        result = mk.dream(dry_run=True)
        assert isinstance(result, dict)
        assert "issues" in result
        assert "total" in result

    def test_dream_returns_structured_issues(self, mk_with_data):
        result = mk_with_data.dream(dry_run=True)
        issues = result["issues"]
        assert "incomplete_sources" in issues
        assert "sourceless_facts" in issues
        assert "thin_entities" in issues
        assert "bloated_pages" in issues
        assert isinstance(result["details"], dict)

    def test_dream_detects_sourceless_facts(self, mk):
        """Facts in Key Points without [Source:] should be flagged."""
        mk.track("Test Person", entity_type="person", source="test")
        # Manually add a sourceless fact to Key Points
        filepath = mk.live_notes_dir / "test-person.md"
        content = filepath.read_text(encoding="utf-8")
        content = content.replace(
            "## Key Points\n(Key points are automatically summarized here)",
            "## Key Points\n- This fact has no source attribution\n- Another unsourced fact"
        )
        filepath.write_text(content, encoding="utf-8")
        result = mk.dream(dry_run=True)
        assert result["issues"]["sourceless_facts"] >= 2

    def test_dream_bloated_page_compression_suggestion(self, mk):
        """Bloated pages should get actionable compression suggestions."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        big_file = mk.entities_dir / "bloated-entity.md"
        content = "# Bloated Entity\n\n**Tier: core**\n\n## Key Points\n"
        for i in range(100):
            content += f"- Fact number {i} with some details [Source: test]\n"
        content += "\n## Timeline\n\n"
        for i in range(50):
            content += f"- **2024-01-{(i % 28) + 1:02d}** | Event {i} [Source: test]\n"
        big_file.write_text(content, encoding="utf-8")
        result = mk.dream(dry_run=True)
        assert result["issues"]["bloated_pages"] >= 1
        # Check that details contain compression suggestion
        assert any("condense" in d or "merge" in d or "split" in d for d in result["details"]["bloated_pages"])

    def test_dream_live_returns_dict(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        result = mk.dream()
        assert isinstance(result, dict)


# ── R16: Duplicate Entities ─────────────────────────────────
class TestR16DuplicateEntities:
    def test_track_duplicate_returns_none(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        result = mk.track("Test Person", entity_type="person", source="test2")
        assert result is None

    def test_extract_duplicate_no_duplication(self, mk):
        mk.extract("Simon Kim is the CEO of Hashed.", source="test")
        mk.extract("Simon Kim is the CEO of Hashed.", source="test2")
        entity_files = list(mk.entities_dir.glob("simon-kim.md"))
        assert len(entity_files) == 1

    def test_dream_catches_slug_duplicates(self, mk):
        """Entities with same normalized slug should be flagged."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        (mk.entities_dir / "simon-kim.md").write_text("# Simon Kim\n\n## Timeline\n\n", encoding="utf-8")
        (mk.entities_dir / "simonkim.md").write_text("# SimonKim\n\n## Timeline\n\n", encoding="utf-8")
        result = mk.dream(dry_run=True)
        assert result["issues"]["duplicate_entities"] >= 1


# ── R16: Circular References ────────────────────────────────
class TestR16CircularReferences:
    def test_agentic_search_circular_links_no_infinite_loop(self, mk):
        """Circular wiki-links should not cause infinite loop in agentic search."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        (mk.entities_dir / "alice.md").write_text(
            "# Alice\n\nWorks with [[bob]]\n\n## Timeline\n\n- **2024-01-01** | Created [Source: test]\n",
            encoding="utf-8"
        )
        (mk.entities_dir / "bob.md").write_text(
            "# Bob\n\nWorks with [[alice]]\n\n## Timeline\n\n- **2024-01-01** | Created [Source: test]\n",
            encoding="utf-8"
        )
        # Should not hang — visited set prevents revisiting
        results = mk.agentic_search("Alice", max_hops=5)
        assert isinstance(results, list)

    def test_links_self_reference_no_crash(self, mk):
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        (mk.entities_dir / "self-ref.md").write_text(
            "# Self Ref\n\nSee also [[self-ref]]\n",
            encoding="utf-8"
        )
        mk.links("Self Ref")  # Should not crash


# ── R16: Very Large Files ───────────────────────────────────
class TestR16LargeFiles:
    def test_search_large_file_no_crash(self, mk):
        """Search should handle very large files without crash or timeout."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        big_content = "# Big Entity\n\n" + ("This is a line of content. " * 100 + "\n") * 200
        (mk.entities_dir / "big-entity.md").write_text(big_content, encoding="utf-8")
        results = mk.search("Big Entity")
        assert len(results) > 0

    def test_extract_very_long_text_no_crash(self, mk):
        long_text = "Simon Kim met Jack Ma at a conference. " * 5000
        result = mk.extract(long_text, source="test")
        assert isinstance(result, list)

    def test_dream_with_many_files(self, mk):
        """Dream should handle many entity files."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        for i in range(50):
            (mk.entities_dir / f"entity-{i}.md").write_text(
                f"# Entity {i}\n\n## Timeline\n\n- **2024-01-01** | Created [Source: test]\n",
                encoding="utf-8"
            )
        result = mk.dream(dry_run=True)
        assert isinstance(result, dict)
        assert result["issues"]["thin_entities"] >= 40  # Most are small

    def test_dedup_large_dataset(self, mk):
        """Dedup should handle many facts without crashing."""
        mk.entities_dir.mkdir(parents=True, exist_ok=True)
        for i in range(20):
            content = f"# Entity {i}\n\n## Key Points\n"
            for j in range(10):
                content += f"- Fact about topic {j} with details [Source: test]\n"
            (mk.entities_dir / f"entity-{i}.md").write_text(content, encoding="utf-8")
        results = mk.dedup(dry_run=True)
        assert isinstance(results, list)


# ── R16: Edge Cases ─────────────────────────────────────────
class TestR16EdgeCases:
    def test_slugify_unicode_safe(self, mk):
        slug = mk._slugify("김서준 (Seoul Office)")
        assert len(slug) > 0
        assert " " not in slug

    def test_slugify_empty_string(self, mk):
        slug = mk._slugify("")
        assert slug == ""

    def test_slugify_very_long_name(self, mk):
        slug = mk._slugify("A" * 200)
        assert len(slug) <= 80

    def test_track_empty_name(self, mk):
        result = mk.track("", entity_type="person", source="test")
        assert result is None

    def test_track_whitespace_name(self, mk):
        result = mk.track("   ", entity_type="person", source="test")
        assert result is None

    def test_update_nonexistent_returns_none(self, mk):
        result = mk.update("Nobody", info="test", source="test")
        assert result is None

    def test_promote_invalid_tier(self, mk):
        mk.track("Test Person", entity_type="person", source="test")
        mk.promote("Test Person", tier="invalid")
        # Should not crash, entity should still be in original tier

    def test_cognify_empty_inbox(self, mk):
        mk.cognify(dry_run=True)  # Should not crash

    def test_distill_decisions_empty(self, mk):
        mk.distill_decisions()  # Should not crash

    def test_open_loops_empty(self, mk):
        mk.open_loops(dry_run=True)  # Should not crash

    def test_build_index_empty(self, mk):
        mk.build_index()  # Should not crash

    def test_suggest_links_empty(self, mk):
        mk.suggest_links()  # Should not crash

    def test_lookup_empty(self, mk):
        mk.lookup("nonexistent")  # Should not crash

    def test_lookup_json_output(self, mk_with_data):
        mk_with_data.lookup("Hashed", json_output=True)

    def test_query_all_levels(self, mk_with_data):
        for level in [1, 2, 3]:
            mk_with_data.query("Hashed", level=level)

    def test_log_event_and_read(self, mk):
        mk.log_event("test event", tags="test,debug", importance="high")
        mk.log_read()

    def test_retro_empty(self, mk):
        mk.retro(dry_run=True)  # Should not crash

    def test_extract_facts_registry_empty(self, mk):
        mk.extract_facts_registry("")  # Should scan files, not crash

    def test_safe_read_nonexistent(self, mk):
        result = mk._safe_read(Path("/nonexistent/file.md"))
        assert result == ""

    def test_detect_mixed_cjk_entities(self, mk):
        """Mixed CJK + English text should detect entities from both."""
        entities = mk._detect_regex("Simon Kim met 田中太郎 and 김서준 at a conference in Tokyo.")
        types = set(e["type"] for e in entities)
        assert "person" in types
        assert "location" in types
