"""Tests for HierarchicalMixin — summary + raw dual-layer memory."""
import os
import shutil
import tempfile

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path):
    """Fresh MemKraft instance in a temp dir."""
    base = str(tmp_path / "mk")
    return MemKraft(base_dir=base)


class TestTrackHierarchical:
    def test_basic_track(self, mk):
        """track_hierarchical creates raw chunks and a summary file."""
        text = "Alice works at Google. Alice lives in Tokyo. Bob likes pizza."
        result = mk.track_hierarchical(text, entity_name="Alice", chunk_size=100)

        assert "raw" in result
        assert "summary" in result
        assert os.path.exists(result["summary"])

        summary_content = open(result["summary"]).read()
        assert "Alice" in summary_content

    def test_no_entity_name(self, mk):
        """track_hierarchical works without entity_name."""
        text = "The weather is sunny today. Cats enjoy napping."
        result = mk.track_hierarchical(text)
        assert os.path.exists(result["summary"])

    def test_summary_accumulation(self, mk):
        """Calling track_hierarchical twice for same entity accumulates."""
        mk.track_hierarchical("Alice works at Google.", entity_name="Alice")
        mk.track_hierarchical("Alice lives in Tokyo.", entity_name="Alice")

        summary_dir = os.path.join(mk.base_dir, "summaries")
        summary_path = os.path.join(summary_dir, "Alice.md")
        content = open(summary_path).read()
        # Should have content from both calls
        assert len(content) > 10


class TestExtractKeyFacts:
    def test_work_pattern(self, mk):
        text = "Sarah works at Microsoft."
        facts = mk._extract_key_facts(text, "Sarah")
        assert "Sarah" in facts
        assert "Microsoft" in facts

    def test_lives_pattern(self, mk):
        text = "John lives in London."
        facts = mk._extract_key_facts(text, "John")
        assert "John" in facts

    def test_likes_pattern(self, mk):
        text = "Emma likes cooking pasta."
        facts = mk._extract_key_facts(text, "Emma")
        assert "Emma" in facts

    def test_fallback_truncation(self, mk):
        text = "No recognizable patterns here at all."
        facts = mk._extract_key_facts(text)
        # Should fallback to text[:300]
        assert "No recognizable" in facts

    def test_entity_filter(self, mk):
        text = "Alice works at Google.\nBob works at Meta."
        facts = mk._extract_key_facts(text, "Alice")
        assert "Google" in facts
        # Bob should be filtered out
        assert "Meta" not in facts


class TestSearchHierarchical:
    def test_basic_search(self, mk):
        mk.track_hierarchical(
            "Alice works at Google. Alice likes hiking.",
            entity_name="Alice",
        )
        mk.track_hierarchical(
            "Bob works at Meta. Bob lives in Paris.",
            entity_name="Bob",
        )

        results = mk.search_hierarchical("Where does Alice work?", top_k=5)
        assert len(results) > 0
        # Alice's summary should rank higher
        assert any("Alice" in r or "Google" in r for r in results)

    def test_empty_query(self, mk):
        results = mk.search_hierarchical("", top_k=5)
        assert results == [] or len(results) == 0

    def test_no_summaries(self, mk):
        """search_hierarchical with no summaries directory falls back."""
        results = mk.search_hierarchical("anything", top_k=5)
        assert isinstance(results, list)

    def test_top_k_respected(self, mk):
        for i in range(10):
            mk.track_hierarchical(
                f"Person{i} works at Company{i}. Person{i} likes Sport{i}.",
                entity_name=f"Person{i}",
            )
        results = mk.search_hierarchical("Person5 Company5", top_k=3)
        assert len(results) <= 3
