"""Tests for v2.6.0 features:
1. fact_add with fact_type (episodic/semantic/procedural)
2. auto_tier (recency + frequency + importance)
3. contradiction detection in consolidation
"""
import pytest
from pathlib import Path
from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


# ── 1. fact_type ──────────────────────────────────────────────

class TestFactType:
    def test_default_type_is_semantic(self, mk):
        mk.track("Alice")
        result = mk.fact_add("Alice", "role", "engineer")
        assert result["type"] == "semantic"

    def test_episodic_type(self, mk):
        mk.track("Alice")
        result = mk.fact_add("Alice", "met", "Monday", fact_type="episodic")
        assert result["type"] == "episodic"

    def test_procedural_type(self, mk):
        mk.track("Alice")
        result = mk.fact_add("Alice", "deploy", "vercel push", fact_type="procedural")
        assert result["type"] == "procedural"

    def test_invalid_type_raises(self, mk):
        mk.track("Alice")
        with pytest.raises(ValueError, match="fact_type"):
            mk.fact_add("Alice", "x", "y", fact_type="emotional")

    def test_type_preserved_in_file(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "event", "lunch", fact_type="episodic")
        facts = mk.fact_list("Alice")
        assert any(f.get("type") == "episodic" for f in facts)

    def test_type_survives_roundtrip(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "role", "CEO", fact_type="semantic")
        mk.fact_add("Alice", "event", "lunch", fact_type="episodic")
        mk.fact_add("Alice", "deploy", "vercel push", fact_type="procedural")
        facts = mk.fact_list("Alice")
        types = {f.get("type") for f in facts}
        assert types == {"semantic", "episodic", "procedural"}


# ── 2. auto_tier ──────────────────────────────────────────────

class TestAutoTier:
    def test_auto_tier_returns_list(self, mk):
        mk.track("Alice")
        result = mk.auto_tier()
        assert isinstance(result, list)

    def test_auto_tier_single_entity(self, mk):
        mk.track("Alice")
        result = mk.auto_tier("alice")
        assert len(result) == 1

    def test_auto_tier_dry_run(self, mk):
        mk.track("Alice")
        mk.tier_set("alice", tier="archival")
        result = mk.auto_tier("alice", dry_run=True)
        assert len(result) == 1
        assert result[0]["old_tier"] == "archival"

    def test_auto_tier_score_fields(self, mk):
        mk.track("Alice")
        result = mk.auto_tier("alice")
        r = result[0]
        assert "score" in r
        assert "recency" in r
        assert "frequency" in r
        assert "importance" in r
        assert 0 <= r["score"] <= 1

    def test_auto_tier_custom_weights(self, mk):
        mk.track("Alice")
        result = mk.auto_tier(
            "alice",
            recency_weight=0.8,
            frequency_weight=0.1,
            importance_weight=0.1,
        )
        assert len(result) == 1


# ── 3. contradiction detection ────────────────────────────────

class TestContradiction:
    def test_contradiction_detected(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "role", "CEO", valid_from="2024-01-01")
        mk.fact_add("Alice", "role", "CTO", valid_from="2024-01-01")
        result = mk.consolidate(dry_run=True)
        assert result.get("contradictions_detected", 0) >= 1

    def test_no_contradiction_different_keys(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "role", "CEO")
        mk.fact_add("Alice", "company", "Hashed")
        result = mk.consolidate(dry_run=True)
        assert result.get("contradictions_detected", 0) == 0

    def test_no_contradiction_non_overlapping(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "role", "CEO", valid_from="2020-01-01", valid_to="2023-12-31")
        mk.fact_add("Alice", "role", "CTO", valid_from="2024-01-01")
        result = mk.consolidate(dry_run=True)
        # Non-overlapping → no contradiction
        assert result.get("contradictions_detected", 0) == 0

    def test_contradiction_in_details(self, mk):
        mk.track("Alice")
        mk.fact_add("Alice", "status", "active", valid_from="2024-01-01")
        mk.fact_add("Alice", "status", "inactive", valid_from="2024-01-01")
        result = mk.consolidate(dry_run=True)
        assert any("contradiction" in d for d in result.get("details", []))
