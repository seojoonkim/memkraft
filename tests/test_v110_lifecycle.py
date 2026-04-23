"""
MemKraft v1.1.0 — Lifecycle API tests
Tests: flush / compact / digest / health
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mk(tmp_path):
    """Fresh MemKraft instance in a temp directory."""
    return MemKraft(base_dir=str(tmp_path))


@pytest.fixture()
def sample_md(tmp_path):
    """Write a sample MEMORY.md and return its path."""
    content = """# My Memory

## 진행 완료

- [2026-01-01] 프로젝트 A 배포 완료
- VibeKai v1.2 출시

## 교훈

- 배포 전에 반드시 테스트 실행할 것
- sub-agent 타임아웃 체크 필수

## 프로젝트

| 이름 | 상태 | URL |
|------|------|-----|
| VibeKai | active | https://vibekai.vercel.app |
| AgentLinter | beta | https://agentlinter.vercel.app |
"""
    p = tmp_path / "MEMORY.md"
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# flush tests
# ---------------------------------------------------------------------------


def test_flush_basic(mk, sample_md):
    """flush() imports events and entities without error."""
    result = mk.flush(sample_md, strategy="auto")
    assert isinstance(result, dict)
    assert "imported" in result
    assert result["imported"] > 0


def test_flush_returns_correct_keys(mk, sample_md):
    """flush() result has all expected keys."""
    result = mk.flush(sample_md)
    for key in ("imported", "entities", "events", "facts"):
        assert key in result, f"Missing key: {key}"


def test_flush_events_strategy(mk, tmp_path):
    """flush(strategy='events') imports list items as events."""
    md = tmp_path / "events.md"
    md.write_text("- First event\n- Second event\n- Third event\n", encoding="utf-8")
    result = mk.flush(str(md), strategy="events")
    assert result["events"] == 3
    assert result["imported"] == 3


def test_flush_facts_strategy(mk, tmp_path):
    """flush(strategy='facts') imports table rows as entities."""
    md = tmp_path / "facts.md"
    md.write_text(
        "| Name | Role |\n|------|------|\n| Alice | CEO |\n| Bob | CTO |\n",
        encoding="utf-8",
    )
    result = mk.flush(str(md), strategy="facts")
    assert result["entities"] >= 2


def test_flush_nonexistent_file_raises(mk):
    """flush() raises FileNotFoundError for missing files."""
    with pytest.raises(FileNotFoundError):
        mk.flush("/tmp/nonexistent_memkraft_test_xyz.md")


def test_flush_invalid_strategy_raises(mk, tmp_path):
    """flush() raises ValueError for unknown strategy."""
    md = tmp_path / "x.md"
    md.write_text("- item\n", encoding="utf-8")
    with pytest.raises(ValueError):
        mk.flush(str(md), strategy="unknown_strategy")


# ---------------------------------------------------------------------------
# compact tests
# ---------------------------------------------------------------------------


def test_compact_dry_run_no_changes(mk):
    """compact(dry_run=True) reports moves but makes no actual changes."""
    # Create a recall entity
    mk.track("old-project", entity_type="project", source="test")
    mk.tier_set("old-project", tier="recall")

    entities_dir = Path(mk.base_dir) / "entities"
    files_before = set(entities_dir.glob("*.md"))

    result = mk.compact(dry_run=True)

    assert result["dry_run"] is True
    # Files unchanged
    files_after = set(entities_dir.glob("*.md"))
    assert files_before == files_after


def test_compact_returns_correct_keys(mk):
    """compact() result has all expected keys."""
    result = mk.compact()
    for key in ("moved", "remaining_entities", "freed_chars", "dry_run"):
        assert key in result, f"Missing key: {key}"


def test_compact_empty_memory(mk):
    """compact() on empty memory returns zeros."""
    result = mk.compact()
    assert result["moved"] == 0
    assert result["freed_chars"] == 0


def test_compact_moves_old_recall_when_over_limit(mk, tmp_path):
    """compact() archives old recall entity when size > max_chars."""
    # Create a recall entity and artificially age its file
    mk.track("aged-entity", entity_type="test", source="test")
    mk.tier_set("aged-entity", tier="recall")

    entities_dir = Path(mk.base_dir) / "live-notes"
    entity_file = entities_dir / "aged-entity.md"

    # Set mtime to 91 days ago
    ninety_one_days_ago = time.time() - (91 * 86400)
    os.utime(entity_file, (ninety_one_days_ago, ninety_one_days_ago))

    result = mk.compact()
    assert result["moved"] >= 1


# ---------------------------------------------------------------------------
# digest tests
# ---------------------------------------------------------------------------


def test_digest_basic(mk, tmp_path):
    """digest() creates output file."""
    output = str(tmp_path / "output_MEMORY.md")
    result = mk.digest(output_path=output)
    assert Path(output).exists()
    assert result["chars"] > 0


def test_digest_always_under_limit(mk, tmp_path):
    """digest() output is always ≤ max_chars."""
    # Populate with lots of entities
    for i in range(30):
        mk.track(f"entity-{i}", entity_type="test", source="test")
        mk.update(f"entity-{i}", "A" * 500, source="test")

    output = str(tmp_path / "MEMORY.md")
    max_chars = 5000
    result = mk.digest(output_path=output, max_chars=max_chars)

    assert result["chars"] <= max_chars, (
        f"Output {result['chars']} chars exceeds limit {max_chars}"
    )


def test_digest_returns_correct_keys(mk, tmp_path):
    """digest() result has all expected keys."""
    output = str(tmp_path / "out.md")
    result = mk.digest(output_path=output)
    for key in ("chars", "entities", "sections", "truncated"):
        assert key in result, f"Missing key: {key}"


def test_digest_content_has_header(mk, tmp_path):
    """digest() output contains the auto-generated header."""
    output = str(tmp_path / "MEMORY.md")
    mk.digest(output_path=output)
    content = Path(output).read_text(encoding="utf-8")
    assert "Auto-generated by MemKraft" in content


def test_digest_includes_core_entities(mk, tmp_path):
    """digest() includes core-tier entities in output."""
    mk.track("core-thing", entity_type="project", source="test")
    mk.tier_set("core-thing", tier="core")

    output = str(tmp_path / "MEMORY.md")
    mk.digest(output_path=output)
    content = Path(output).read_text(encoding="utf-8")
    assert "core-thing" in content


# ---------------------------------------------------------------------------
# health tests
# ---------------------------------------------------------------------------


def test_health_returns_correct_keys(mk):
    """health() result has all expected keys."""
    result = mk.health()
    for key in ("total_chars", "tier_distribution", "entity_count", "recommendations", "status"):
        assert key in result, f"Missing key: {key}"


def test_health_healthy_on_empty(mk):
    """health() returns 'healthy' on empty memory."""
    result = mk.health()
    assert result["status"] == "healthy"
    assert result["entity_count"] == 0


def test_health_tier_distribution_keys(mk):
    """health() tier_distribution contains core/recall/archival."""
    result = mk.health()
    dist = result["tier_distribution"]
    assert "core" in dist
    assert "recall" in dist
    assert "archival" in dist


def test_health_warning_on_many_recall(mk):
    """health() returns 'warning' when recall entity count > 500."""
    # Simulate high recall count by mocking _count_tier
    original = mk._count_tier

    def mock_count_tier(tier):
        if tier == "recall":
            return 501
        return 0

    mk._count_tier = mock_count_tier
    result = mk.health()
    mk._count_tier = original

    assert result["status"] in ("warning", "critical")
    assert any("recall" in r.lower() or "501" in r for r in result["recommendations"])


def test_health_critical_on_large_memory(mk):
    """health() returns 'critical' when memory > 100KB."""
    original = mk._estimate_memory_size

    def mock_size():
        return 150_000  # 150KB

    mk._estimate_memory_size = mock_size
    result = mk.health()
    mk._estimate_memory_size = original

    assert result["status"] == "critical"
    assert any("compact" in r.lower() for r in result["recommendations"])
