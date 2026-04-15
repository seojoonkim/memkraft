#!/usr/bin/env python3
"""Tests for MemKraft v0.3.0 features:
1. Query-to-Memory Feedback Loop
2. Confidence Level on Facts
3. Memory Health Assertions
4. Applicability Conditions
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from memkraft.core import MemKraft


@pytest.fixture
def mk(tmp_path):
    """Create a MemKraft instance with a temporary directory."""
    mc = MemKraft(base_dir=str(tmp_path / "memory"))
    mc.init()
    return mc


@pytest.fixture
def mk_with_entity(mk):
    """MemKraft instance with a pre-created entity for testing."""
    mk.track("Alice Smith", entity_type="person", source="test")
    return mk


@pytest.fixture
def mk_with_live_note(mk):
    """MemKraft instance with a pre-created live note."""
    mk.track("Bob Jones", entity_type="person", source="test")
    return mk


# ═══════════════════════════════════════════════════════════════
# Feature 1: Query-to-Memory Feedback Loop
# ═══════════════════════════════════════════════════════════════

class TestFeedbackLoop:
    """Tests for Query-to-Memory Feedback Loop (file_back)."""

    def test_agentic_search_file_back_creates_timeline_entry(self, mk_with_entity):
        """file_back=True should append search results to entity timelines."""
        mc = mk_with_entity
        mc.update("Alice Smith", info="Works at Hashed", source="test")
        
        # Run agentic search with file_back
        mc.agentic_search("Alice", file_back=True)
        
        # Check that the live note was updated with a "Filed back" entry
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "[Filed back]" in content

    def test_agentic_search_no_file_back_by_default(self, mk_with_entity):
        """Default agentic_search should NOT file back."""
        mc = mk_with_entity
        mc.update("Alice Smith", info="Works at Hashed", source="test")
        
        mc.agentic_search("Alice")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "[Filed back]" not in content

    def test_agentic_search_file_back_includes_query(self, mk_with_entity):
        """Filed-back entries should include the original query."""
        mc = mk_with_entity
        mc.update("Alice Smith", info="Invested in crypto", source="test")
        
        mc.agentic_search("crypto investment", file_back=True)
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "crypto investment" in content

    def test_agentic_search_file_back_with_confidence(self, mk_with_entity):
        """Filed-back entries should include confidence level."""
        mc = mk_with_entity
        mc.update("Alice Smith", info="CEO of Hashed", source="test")
        
        mc.agentic_search("Alice", file_back=True)
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "Confidence: experimental" in content

    def test_brief_file_back(self, mk_with_entity):
        """brief with file_back should append to timeline."""
        mc = mk_with_entity
        mc.update("Alice Smith", info="Joined in 2025", source="test")
        
        mc.brief("Alice Smith", file_back=True)
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "[Filed back] Brief generated" in content

    def test_brief_no_file_back_by_default(self, mk_with_entity):
        """Default brief should NOT file back."""
        mc = mk_with_entity
        
        mc.brief("Alice Smith")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "[Filed back] Brief generated" not in content

    def test_file_back_limits_to_top_5(self, mk):
        """_file_back_results should only file back top 5 results."""
        mc = mk
        # Create many entities
        for i in range(10):
            mc.track(f"Entity {i}", source="test")
            mc.update(f"Entity {i}", info=f"Info about topic X entity {i}", source="test")
        
        # Search and file back
        mc.agentic_search("topic X", file_back=True)
        
        # Count filed-back entries across all entities
        filed_count = 0
        for md in mc.live_notes_dir.glob("*.md"):
            content = md.read_text(encoding="utf-8")
            filed_count += content.count("[Filed back]")
        
        assert filed_count <= 5


# ═══════════════════════════════════════════════════════════════
# Feature 2: Confidence Level on Facts
# ═══════════════════════════════════════════════════════════════

class TestConfidenceLevel:
    """Tests for Confidence Level on Facts."""

    def test_extract_default_confidence(self, mk_with_entity):
        """Default confidence should be 'experimental'."""
        mc = mk_with_entity
        results = mc.extract("Alice Smith is the CEO of Hashed", source="test")
        
        for r in results:
            if r.get("type") == "fact":
                assert r.get("confidence") == "experimental"

    def test_extract_verified_confidence(self, mk_with_entity):
        """extract with confidence='verified' should tag facts as verified."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test", confidence="verified")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "Confidence: verified" in content

    def test_extract_hypothesis_confidence(self, mk_with_entity):
        """extract with confidence='hypothesis' should tag facts as hypothesis."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test", confidence="hypothesis")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "Confidence: hypothesis" in content

    def test_append_fact_includes_confidence(self, mk_with_entity):
        """_append_fact should include confidence tag in output."""
        mc = mk_with_entity
        mc._append_fact("Alice Smith", "Leads the team", source="test", confidence="verified")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "Confidence: verified" in content
        assert "Leads the team" in content

    def test_dream_warns_no_confidence(self, mk):
        """Dream Cycle should warn about facts without confidence tags."""
        mc = mk
        mc.track("Test Entity", source="test")
        # Manually write a fact without confidence
        live_path = mc.live_notes_dir / "test-entity.md"
        content = live_path.read_text(encoding="utf-8")
        content = content.replace(
            "## Key Points\n",
            "## Key Points\n- Important fact without confidence [Source: manual]\n"
        )
        live_path.write_text(content, encoding="utf-8")
        
        result = mc.dream(dry_run=True)
        assert result["issues"]["no_confidence"] > 0

    def test_confidence_bonus_verified(self, mk):
        """_compute_confidence_bonus should return higher bonus for verified facts."""
        mc = mk
        content_verified = "- Fact A [Source: test | Confidence: verified]\n- Fact B [Source: test | Confidence: verified]\n"
        content_hypothesis = "- Fact A [Source: test | Confidence: hypothesis]\n- Fact B [Source: test | Confidence: hypothesis]\n"
        
        bonus_v = mc._compute_confidence_bonus(content_verified)
        bonus_h = mc._compute_confidence_bonus(content_hypothesis)
        
        assert bonus_v > bonus_h

    def test_extract_fact_confidence_extracts(self, mk):
        """_extract_fact_confidence should parse confidence from a line."""
        mc = mk
        line = "- CEO of Hashed [Source: news | Confidence: verified]"
        assert mc._extract_fact_confidence(line) == "verified"

    def test_extract_fact_confidence_empty(self, mk):
        """_extract_fact_confidence should return empty string if no confidence."""
        mc = mk
        line = "- CEO of Hashed [Source: news]"
        assert mc._extract_fact_confidence(line) == ""

    def test_confidence_levels_constant(self, mk):
        """CONFIDENCE_LEVELS should contain the three valid levels."""
        assert "verified" in MemKraft.CONFIDENCE_LEVELS
        assert "experimental" in MemKraft.CONFIDENCE_LEVELS
        assert "hypothesis" in MemKraft.CONFIDENCE_LEVELS

    def test_confidence_weights_ordering(self, mk):
        """CONFIDENCE_WEIGHTS should order: verified > experimental > hypothesis."""
        w = MemKraft.CONFIDENCE_WEIGHTS
        assert w["verified"] > w["experimental"] > w["hypothesis"]


# ═══════════════════════════════════════════════════════════════
# Feature 3: Memory Health Assertions
# ═══════════════════════════════════════════════════════════════

class TestHealthCheck:
    """Tests for Memory Health Assertions (health_check)."""

    def test_health_check_returns_dict(self, mk):
        """health_check should return a dict with expected keys."""
        mc = mk
        result = mc.health_check()
        
        assert "pass_rate" in result
        assert "passed" in result
        assert "total" in result
        assert "health_score" in result
        assert "assertions" in result

    def test_health_check_all_pass_empty(self, mk):
        """health_check on empty memory should pass all assertions."""
        mc = mk
        result = mc.health_check()
        
        assert result["pass_rate"] == 100.0
        assert result["health_score"] == "A"

    def test_health_check_source_attribution_fail(self, mk):
        """health_check should fail when entity has no source attribution."""
        mc = mk
        # Create entity without [Source: ...] tag
        mc.entities_dir.mkdir(parents=True, exist_ok=True)
        entity_path = mc.entities_dir / "no-source.md"
        entity_path.write_text("# No Source\n\nJust a fact without source.\n", encoding="utf-8")
        
        result = mc.health_check()
        
        source_assertion = next(a for a in result["assertions"] if a["name"] == "source_attribution")
        assert not source_assertion["passed"]
        assert "no-source.md" in str(source_assertion["failures"])

    def test_health_check_inbox_freshness_fail(self, mk):
        """health_check should fail when inbox has items older than 7 days."""
        mc = mk
        mc.inbox_dir.mkdir(parents=True, exist_ok=True)
        old_file = mc.inbox_dir / "old-item.md"
        old_file.write_text("# Old item\nThis should be processed.", encoding="utf-8")
        # Set modification time to 10 days ago
        import time
        old_time = time.time() - (10 * 86400)
        os.utime(old_file, (old_time, old_time))
        
        result = mc.health_check()
        
        inbox_assertion = next(a for a in result["assertions"] if a["name"] == "inbox_freshness")
        assert not inbox_assertion["passed"]

    def test_health_check_conflicts_fail(self, mk):
        """health_check should fail when CONFLICTS.md has unresolved conflicts."""
        mc = mk
        conflicts_path = mc.base_dir / "CONFLICTS.md"
        conflicts_path.write_text("# Conflicts\n\n### Test\n- **Status:** ❌ unresolved\n", encoding="utf-8")
        
        result = mc.health_check()
        
        conflict_assertion = next(a for a in result["assertions"] if a["name"] == "no_unresolved_conflicts")
        assert not conflict_assertion["passed"]

    def test_health_check_five_assertions(self, mk):
        """health_check should run exactly 5 assertions."""
        mc = mk
        result = mc.health_check()
        assert result["total"] == 5

    def test_health_score_grading(self, mk):
        """Health score grading: A (>=80%), B (>=60%), C (>=40%), D (<40%)."""
        mc = mk
        # All pass = 100% = A
        result = mc.health_check()
        assert result["health_score"] == "A"

    def test_dream_includes_health_check(self, mk):
        """Dream Cycle should include health check results."""
        mc = mk
        result = mc.dream(dry_run=True)
        
        assert "health" in result
        assert "pass_rate" in result["health"]


# ═══════════════════════════════════════════════════════════════
# Feature 4: Applicability Conditions
# ═══════════════════════════════════════════════════════════════

class TestApplicabilityConditions:
    """Tests for Applicability Conditions."""

    def test_extract_with_applicability(self, mk_with_entity):
        """extract with applicability should add When: condition to facts."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test",
                   applicability="When: crypto bull market")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "When: crypto bull market" in content

    def test_extract_with_when_not(self, mk_with_entity):
        """extract with When NOT: should add negative condition."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test",
                   applicability="When NOT: recession")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "When NOT: recession" in content

    def test_extract_with_both_conditions(self, mk_with_entity):
        """extract with both When and When NOT should include both."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test",
                   applicability="When: bull market | When NOT: recession")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "When: bull market" in content
        assert "When NOT: recession" in content

    def test_extract_without_applicability(self, mk_with_entity):
        """Default extract should not add When: tags."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "When:" not in content

    def test_applicability_bonus_positive(self, mk):
        """_compute_applicability_bonus should be positive when context matches When:."""
        mc = mk
        content = "- Strategy works [Source: test] | When: bull market\n"
        
        bonus = mc._compute_applicability_bonus(content, "crypto bull market analysis")
        assert bonus > 0

    def test_applicability_bonus_negative(self, mk):
        """_compute_applicability_bonus should be negative when context matches When NOT:."""
        mc = mk
        content = "- Strategy works [Source: test] | When NOT: recession\n"
        
        bonus = mc._compute_applicability_bonus(content, "recession analysis")
        assert bonus < 0

    def test_applicability_bonus_zero_no_context(self, mk):
        """_compute_applicability_bonus should be 0 with empty context."""
        mc = mk
        content = "- Strategy works [Source: test] | When: bull market\n"
        
        bonus = mc._compute_applicability_bonus(content, "")
        assert bonus == 0.0

    def test_parse_applicability_when(self, mk):
        """_parse_applicability should parse When: conditions."""
        mc = mk
        result = mc._parse_applicability("When: crypto bull market | When NOT: recession")
        assert "crypto bull market" in result["when"]
        assert "recession" in result["when_not"]

    def test_parse_applicability_empty(self, mk):
        """_parse_applicability should return empty lists for no conditions."""
        mc = mk
        result = mc._parse_applicability("Just a normal fact")
        assert result["when"] == []
        assert result["when_not"] == []

    def test_applicability_in_results(self, mk_with_entity):
        """extract results should include applicability field when set."""
        mc = mk_with_entity
        results = mc.extract("Alice Smith is the CEO of Hashed", source="test",
                            applicability="When: meetings")
        
        for r in results:
            if r.get("type") == "fact":
                assert r.get("applicability") == "When: meetings"


# ═══════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    """Integration tests across features."""

    def test_confidence_and_applicability_together(self, mk_with_entity):
        """Both confidence and applicability should appear in the same fact."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test",
                   confidence="verified", applicability="When: boardroom")
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "Confidence: verified" in content
        assert "When: boardroom" in content

    def test_file_back_after_confidence_extract(self, mk_with_entity):
        """File-back should work after extracting with confidence."""
        mc = mk_with_entity
        mc.extract("Alice Smith is the CEO of Hashed", source="test", confidence="verified")
        mc.agentic_search("Alice", file_back=True)
        
        live_path = mc.live_notes_dir / "alice-smith.md"
        content = live_path.read_text(encoding="utf-8")
        assert "[Filed back]" in content
        assert "Confidence: verified" in content

    def test_health_check_after_operations(self, mk):
        """health_check should work after various operations."""
        mc = mk
        mc.track("Test Person", source="test")
        mc.update("Test Person", info="Updated info", source="test")
        mc.extract("Test Person is a developer", source="test", confidence="verified")
        
        result = mc.health_check()
        assert result["pass_rate"] > 0
        assert isinstance(result["assertions"], list)

    def test_dream_with_all_features(self, mk):
        """Dream Cycle should integrate all new features without errors."""
        mc = mk
        mc.track("Dream Test", source="test")
        mc.update("Dream Test", info="Some info", source="test")
        
        result = mc.dream(dry_run=True)
        
        assert "health" in result
        assert "no_confidence" in result["issues"]
        assert isinstance(result["total"], int)

    def test_version_030(self):
        """Version should be 0.4.2."""
        from memkraft import __version__
        assert __version__ == "0.6.1"
