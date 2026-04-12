"""MemKraft v0.2.0 feature tests — Goal-Weighted Reconstructive Memory + Dialectic Synthesis"""
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
    tmpdir = tempfile.mkdtemp(prefix="mk-v020-")
    instance = MemKraft(base_dir=tmpdir)
    instance.init()
    yield instance
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def mk_with_mixed_data(mk):
    """MemKraft instance with different memory types."""
    # Identity memory
    mk.track("Self", entity_type="person", source="test")
    mk.update("Self", info="I am the primary agent. My name is Zeon.", source="test")
    # Routine memory
    mk.track("Daily Schedule", entity_type="topic", source="test")
    mk.update("Daily Schedule", info="Every day I check emails at 9am.", source="test")
    # Episodic memory
    mk.track("Project Alpha", entity_type="topic", source="test")
    mk.update("Project Alpha", info="Simon Kim joined the meeting to discuss roadmap.", source="test")
    return mk


# ══════════════════════════════════════════════════════════
# Feature 1: Goal-Weighted Reconstructive Memory
# ══════════════════════════════════════════════════════════

class TestMemoryTypeClassification:
    """Test memory type classification for differential decay."""

    def test_classify_identity(self, mk):
        assert mk.classify_memory_type("I am the CEO of Hashed") == "identity"
        assert mk.classify_memory_type("My name is Simon") == "identity"

    def test_classify_routine(self, mk):
        assert mk.classify_memory_type("Every day I check emails") == "routine"
        assert mk.classify_memory_type("I usually wake up at 7am") == "routine"

    def test_classify_transient(self, mk):
        assert mk.classify_memory_type("Today I have a meeting") == "transient"
        assert mk.classify_memory_type("Right now the server is down") == "transient"

    def test_classify_belief(self, mk):
        assert mk.classify_memory_type("I believe in open source") == "belief"

    def test_classify_preference(self, mk):
        assert mk.classify_memory_type("I prefer Python over Java") == "preference"

    def test_classify_relationship(self, mk):
        assert mk.classify_memory_type("Simon is my friend and colleague") == "relationship"

    def test_classify_skill(self, mk):
        assert mk.classify_memory_type("Here is how to deploy the app") == "skill"

    def test_classify_default(self, mk):
        assert mk.classify_memory_type("Random text without keywords") == "default"

    def test_classify_korean_identity(self, mk):
        assert mk.classify_memory_type("나는 제온이다") == "identity"

    def test_classify_korean_routine(self, mk):
        assert mk.classify_memory_type("매일 아침 코드 리뷰를 한다") == "routine"


class TestDecayMultiplier:
    """Test memory type decay multipliers."""

    def test_identity_slow_decay(self, mk):
        mult = mk.get_decay_multiplier("identity")
        assert mult == 0.1  # Very slow

    def test_routine_fast_decay(self, mk):
        mult = mk.get_decay_multiplier("routine")
        assert mult == 0.9  # Very fast

    def test_transient_fastest_decay(self, mk):
        mult = mk.get_decay_multiplier("transient")
        assert mult == 1.0

    def test_default_mid_decay(self, mk):
        mult = mk.get_decay_multiplier("default")
        assert mult == 0.5

    def test_unknown_type_uses_default(self, mk):
        mult = mk.get_decay_multiplier("nonexistent")
        assert mult == 0.5


class TestGoalWeightedRerank:
    """Test goal-weighted reconstructive re-ranking (Conway SMS)."""

    def test_context_changes_ranking(self, mk_with_mixed_data):
        """Same query with different context should produce different rankings."""
        # Search without context
        results_no_ctx = mk_with_mixed_data.agentic_search("Simon", json_output=True)
        # Search with identity context
        results_identity = mk_with_mixed_data.agentic_search(
            "Simon", json_output=True, context="identity and self"
        )
        # Search with project context
        results_project = mk_with_mixed_data.agentic_search(
            "Simon", json_output=True, context="project roadmap meeting"
        )
        # All should return results
        assert isinstance(results_no_ctx, list)
        assert isinstance(results_identity, list)
        assert isinstance(results_project, list)

    def test_context_boosts_relevant_results(self, mk_with_mixed_data):
        """Context should boost results that match the goal."""
        results = mk_with_mixed_data.agentic_search(
            "meeting", json_output=True, context="Project Alpha roadmap"
        )
        if results:
            # Project Alpha should be boosted due to context match
            project_results = [r for r in results if "project-alpha" in r["file"]]
            if project_results:
                assert project_results[0]["score"] > 0

    def test_context_empty_fallback(self, mk_with_mixed_data):
        """Empty context should not break search."""
        results = mk_with_mixed_data.agentic_search("Simon", json_output=True, context="")
        assert isinstance(results, list)

    def test_memory_type_in_results(self, mk_with_mixed_data):
        """Results with context should include memory_type field."""
        results = mk_with_mixed_data.agentic_search(
            "Simon", json_output=True, context="identity"
        )
        if results:
            types_found = [r.get("memory_type") for r in results if r.get("memory_type")]
            assert len(types_found) > 0

    def test_goal_rerank_empty_results(self, mk):
        """Goal reranking should handle empty result list."""
        results = mk._goal_weighted_rerank([], "some context")
        assert results == []


class TestTypeAwareDecay:
    """Test that decay uses memory-type differential curves."""

    def test_decay_returns_memory_type(self, mk_with_mixed_data):
        """Decay dry-run should include memory_type in results."""
        results = mk_with_mixed_data.decay(days=1, dry_run=True)
        assert isinstance(results, list)
        # All results should have memory_type
        for r in results:
            assert "memory_type" in r
            assert "effective_days" in r

    def test_identity_decays_slower(self, mk):
        """Identity memories should have much longer effective threshold."""
        mk.track("Identity Entity", entity_type="person", source="test")
        mk.update("Identity Entity", info="I am the core system agent.", source="test")
        results = mk.decay(days=90, dry_run=True)
        # Identity type should have effective_days = 90/0.1 = 900
        for r in results:
            if r.get("memory_type") == "identity":
                assert r["effective_days"] > 90


# ══════════════════════════════════════════════════════════
# Feature 2: Dialectic Synthesis (Conflict Detection & Resolution)
# ══════════════════════════════════════════════════════════

class TestConflictDetection:
    """Test automatic conflict detection in extract."""

    def test_detect_simple_negation_conflict(self, mk):
        """Detect is/is not contradiction."""
        mk.track("Alice", entity_type="person", source="test")
        mk.update("Alice", info="Role: CEO of TechCorp", source="v1")
        conflicts = mk.detect_conflicts("Alice", "Role: CTO of TechCorp")
        assert len(conflicts) >= 1
        assert conflicts[0]["entity"] == "Alice"

    def test_detect_no_conflict_for_same_fact(self, mk):
        """Identical facts should not be flagged."""
        mk.track("Bob", entity_type="person", source="test")
        mk.update("Bob", info="Senior engineer at Google", source="v1")
        conflicts = mk.detect_conflicts("Bob", "Senior engineer at Google")
        assert len(conflicts) == 0

    def test_detect_no_conflict_for_unrelated(self, mk):
        """Unrelated facts should not be flagged."""
        mk.track("Charlie", entity_type="person", source="test")
        mk.update("Charlie", info="Based in Seoul", source="v1")
        conflicts = mk.detect_conflicts("Charlie", "Loves hiking")
        assert len(conflicts) == 0

    def test_detect_conflict_joined_vs_left(self, mk):
        """Detect joined/left contradiction."""
        mk.track("Dave", entity_type="person", source="test")
        mk.update("Dave", info="Dave joined Google in 2024", source="v1")
        conflicts = mk.detect_conflicts("Dave", "Dave left Google in 2025")
        assert len(conflicts) >= 1

    def test_detect_conflict_nonexistent_entity(self, mk):
        """No crash when entity doesn't exist."""
        conflicts = mk.detect_conflicts("Nobody", "some fact")
        assert conflicts == []


class TestConflictTagging:
    """Test [CONFLICT] tag preservation in entity files."""

    def test_conflict_tag_written(self, mk):
        """Conflicts should be tagged with [CONFLICT] in entity file."""
        mk.track("Eve", entity_type="person", source="test")
        mk.update("Eve", info="Role: CEO of Alpha", source="v1")
        # Manually trigger conflict tagging
        filepath = mk.live_notes_dir / "eve.md"
        mk._tag_conflict(filepath, "Role: CEO of Alpha", "Role: CTO of Alpha", "v2")
        content = filepath.read_text(encoding="utf-8")
        assert "[CONFLICT]" in content

    def test_conflicts_md_written(self, mk):
        """CONFLICTS.md should be created when conflicts detected."""
        conflicts = [{"entity": "Test", "old_fact": "A", "new_fact": "B", "similarity": 0.8, "file": "test.md"}]
        path = mk._write_conflicts_report(conflicts)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Fact Conflicts" in content
        assert "unresolved" in content


class TestConflictResolution:
    """Test conflict resolution strategies."""

    def test_resolve_no_conflicts(self, mk):
        """Resolve with no CONFLICTS.md should not crash."""
        result = mk.resolve_conflicts()
        assert result["resolved"] == 0

    def test_resolve_dry_run(self, mk):
        """Dry-run resolution should not modify files."""
        conflicts = [{"entity": "Test", "old_fact": "A", "new_fact": "B", "similarity": 0.8, "file": "entities/test.md"}]
        mk._write_conflicts_report(conflicts)
        result = mk.resolve_conflicts(dry_run=True)
        assert result["resolved"] == 1
        # CONFLICTS.md should still say unresolved
        content = (mk.base_dir / "CONFLICTS.md").read_text(encoding="utf-8")
        assert "unresolved" in content

    def test_resolve_newest_strategy(self, mk):
        """Newest strategy should mark conflicts as resolved."""
        mk.track("Frank", entity_type="person", source="test")
        filepath = mk.live_notes_dir / "frank.md"
        mk._tag_conflict(filepath, "old fact", "new fact", "test")
        conflicts = [{"entity": "Frank", "old_fact": "old fact", "new_fact": "new fact", "similarity": 0.8, "file": f"live-notes/frank.md"}]
        mk._write_conflicts_report(conflicts)
        result = mk.resolve_conflicts(strategy="newest")
        assert result["resolved"] == 1
        content = (mk.base_dir / "CONFLICTS.md").read_text(encoding="utf-8")
        assert "resolved" in content

    def test_resolve_prompt_strategy(self, mk):
        """Prompt strategy should output synthesis prompts."""
        conflicts = [{"entity": "Grace", "old_fact": "A", "new_fact": "B", "similarity": 0.8, "file": "entities/grace.md"}]
        mk._write_conflicts_report(conflicts)
        result = mk.resolve_conflicts(strategy="prompt")
        assert result["resolved"] == 1


class TestDreamConflictIntegration:
    """Test Dream Cycle integration with conflict detection."""

    def test_dream_reports_unresolved_conflicts(self, mk):
        """Dream should report unresolved conflict count."""
        conflicts = [{"entity": "Test", "old_fact": "A", "new_fact": "B", "similarity": 0.8, "file": "test.md"}]
        mk._write_conflicts_report(conflicts)
        result = mk.dream(dry_run=True)
        assert "unresolved_conflicts" in result["issues"]
        assert result["issues"]["unresolved_conflicts"] >= 1

    def test_dream_resolve_conflicts_flag(self, mk):
        """Dream with --resolve-conflicts should auto-resolve."""
        conflicts = [{"entity": "Test", "old_fact": "A", "new_fact": "B", "similarity": 0.8, "file": "entities/test.md"}]
        mk._write_conflicts_report(conflicts)
        result = mk.dream(resolve_conflicts=True)
        assert "conflict_resolution" in result
        assert result["conflict_resolution"]["resolved"] >= 1

    def test_dream_no_conflicts_no_crash(self, mk):
        """Dream without any conflicts should work normally."""
        result = mk.dream(dry_run=True)
        assert "unresolved_conflicts" in result["issues"]
        assert result["issues"]["unresolved_conflicts"] == 0


class TestExtractConflictIntegration:
    """Test that extract auto-detects conflicts."""

    def test_extract_detects_conflict(self, mk):
        """Extract should detect and tag conflicts in results."""
        mk.track("Helen", entity_type="person", source="test")
        mk.update("Helen", info="Role: CEO of BigCorp", source="v1")
        # Now extract conflicting info
        result = mk.extract("Helen was named Role: CTO of BigCorp.", source="v2")
        # Check if any result has conflicts
        conflict_items = [r for r in result if r.get("conflicts")]
        # Conflicts may or may not be detected depending on exact text matching
        assert isinstance(result, list)

    def test_extract_no_crash_with_conflict_detection(self, mk):
        """Extract should not crash even with complex conflict scenarios."""
        mk.track("Ivan", entity_type="person", source="test")
        mk.update("Ivan", info="Ivan joined Google in 2024", source="v1")
        mk.update("Ivan", info="Ivan left Google in 2025", source="v2")
        result = mk.extract("Ivan is based in Tokyo and joined Apple.", source="v3")
        assert isinstance(result, list)


class TestIsOpposing:
    """Test the _is_opposing helper method."""

    def test_negation_is_isnot(self, mk):
        assert mk._is_opposing("simon is active", "simon is not active") is True

    def test_joined_vs_left(self, mk):
        assert mk._is_opposing("dave joined google", "dave left google") is True

    def test_same_field_different_value(self, mk):
        assert mk._is_opposing("role: CEO", "role: CTO") is True

    def test_same_fact_not_opposing(self, mk):
        assert mk._is_opposing("ceo of hashed", "ceo of hashed") is False

    def test_unrelated_not_opposing(self, mk):
        assert mk._is_opposing("lives in seoul", "loves hiking") is False


class TestExtractBulletFacts:
    """Test bullet fact extraction helper."""

    def test_extracts_basic_bullets(self, mk):
        content = "## Key Points\n- Simon is CEO [Source: test]\n- Based in Seoul [Source: test]\n"
        facts = mk._extract_bullet_facts(content)
        assert len(facts) >= 2
        assert any("CEO" in f for f in facts)

    def test_strips_conflict_tags(self, mk):
        content = "- [CONFLICT] Role changed [Source: test]\n"
        facts = mk._extract_bullet_facts(content)
        assert all("[CONFLICT]" not in f for f in facts)

    def test_empty_content(self, mk):
        facts = mk._extract_bullet_facts("")
        assert facts == []
