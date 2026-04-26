"""
MemKraft v2.3 — Memory Consolidation tests
Tests: consolidate() — Stage 1-4 + dry_run + edge cases.
"""
from __future__ import annotations

import datetime
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


def _facts_path(mk_instance: MemKraft, entity: str) -> Path:
    return Path(mk_instance.base_dir) / "facts" / f"{mk_instance._slugify(entity)}.md"


def _read_lines(p: Path) -> list[str]:
    return [
        l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("- ")
    ]


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_consolidate_api_exists(mk):
    assert callable(getattr(mk, "consolidate", None))


def test_consolidate_returns_dict_keys(mk):
    result = mk.consolidate(dry_run=True)
    for key in (
        "duplicates_merged",
        "stale_closed",
        "orphans_removed",
        "observations_generated",
        "tokens_saved_estimate",
        "details",
        "dry_run",
        "strategy",
    ):
        assert key in result, f"missing key: {key}"


def test_consolidate_invalid_strategy_raises(mk):
    with pytest.raises(ValueError):
        mk.consolidate(strategy="weird")


def test_consolidate_empty_memory_no_crash(mk):
    """consolidate() on totally fresh memory must not raise."""
    result = mk.consolidate()
    assert result["duplicates_merged"] == 0
    assert result["stale_closed"] == 0
    assert result["orphans_removed"] == 0
    assert result["observations_generated"] == 0


# ---------------------------------------------------------------------------
# Stage 1 — Duplicate Fact Merge
# ---------------------------------------------------------------------------


def test_duplicate_merge_basic(mk):
    """3 identical facts → merged into 1."""
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)

    p = _facts_path(mk, "Simon")
    assert len(_read_lines(p)) == 3

    result = mk.consolidate()
    assert result["duplicates_merged"] == 2
    assert len(_read_lines(p)) == 1


def test_duplicate_merge_preserves_distinct_values(mk):
    """Different values for same key must NOT be merged."""
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CTO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CTO", auto_close_stale=False)

    result = mk.consolidate()
    assert result["duplicates_merged"] == 1

    p = _facts_path(mk, "Simon")
    lines = _read_lines(p)
    assert len(lines) == 2
    values = {l.split("role:")[1].split("<!--")[0].strip() for l in lines}
    assert values == {"CEO", "CTO"}


def test_duplicate_merge_keeps_most_recent(mk):
    """Among duplicates, the newest recorded_at must be kept."""
    mk.fact_add(
        "Simon",
        "city",
        "Seoul",
        recorded_at="2020-01-01T00:00",
        auto_close_stale=False,
    )
    mk.fact_add(
        "Simon",
        "city",
        "Seoul",
        recorded_at="2026-04-26T12:00",
        auto_close_stale=False,
    )
    mk.fact_add(
        "Simon",
        "city",
        "Seoul",
        recorded_at="2024-06-15T08:30",
        auto_close_stale=False,
    )
    mk.consolidate()
    p = _facts_path(mk, "Simon")
    lines = _read_lines(p)
    assert len(lines) == 1
    assert "2026-04-26" in lines[0]


def test_duplicate_merge_dry_run_no_changes(mk):
    """dry_run=True must not modify any file."""
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)

    p = _facts_path(mk, "Simon")
    before = p.read_text(encoding="utf-8")
    result = mk.consolidate(dry_run=True)
    after = p.read_text(encoding="utf-8")

    assert result["duplicates_merged"] == 1
    assert before == after  # nothing written
    assert result["dry_run"] is True


def test_duplicate_merge_across_multiple_entities(mk):
    mk.fact_add("Alice", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Alice", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Bob", "role", "CTO", auto_close_stale=False)
    mk.fact_add("Bob", "role", "CTO", auto_close_stale=False)
    mk.fact_add("Bob", "role", "CTO", auto_close_stale=False)

    result = mk.consolidate()
    # 1 dup for Alice + 2 dups for Bob = 3 merges
    assert result["duplicates_merged"] == 3


def test_no_duplicates_to_merge(mk):
    """Already-clean memory: nothing to merge."""
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "city", "Seoul", auto_close_stale=False)
    result = mk.consolidate()
    assert result["duplicates_merged"] == 0


# ---------------------------------------------------------------------------
# Stage 2 — Stale Fact Close
# ---------------------------------------------------------------------------


def test_stale_close_old_open_fact(mk):
    """Open-ended fact with valid_from > 1y ago → closed."""
    old = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    mk.fact_add("Simon", "role", "CEO", valid_from=old, auto_close_stale=False)

    result = mk.consolidate()
    assert result["stale_closed"] == 1

    p = _facts_path(mk, "Simon")
    text = p.read_text(encoding="utf-8")
    # Should now be closed (..date]) instead of open (..)
    assert "..)" not in text or "..]" in text  # closed marker present
    assert datetime.date.today().isoformat() in text


def test_stale_close_recent_fact_untouched(mk):
    """Open-ended fact with recent valid_from → NOT closed."""
    recent = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    mk.fact_add("Simon", "role", "CEO", valid_from=recent, auto_close_stale=False)
    result = mk.consolidate()
    assert result["stale_closed"] == 0


def test_stale_close_aggressive_threshold(mk):
    """aggressive: facts older than 180d are closed."""
    mid = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    mk.fact_add("Simon", "role", "CEO", valid_from=mid, auto_close_stale=False)

    # auto strategy: 365d threshold → not closed
    result_auto = mk.consolidate(strategy="auto", dry_run=True)
    assert result_auto["stale_closed"] == 0

    # aggressive: 180d threshold → closed
    result_aggr = mk.consolidate(strategy="aggressive", dry_run=True)
    assert result_aggr["stale_closed"] == 1


def test_stale_close_already_closed_fact_untouched(mk):
    """Already-closed facts (valid_to set) must not be re-touched."""
    old = (datetime.date.today() - datetime.timedelta(days=500)).isoformat()
    end = (datetime.date.today() - datetime.timedelta(days=100)).isoformat()
    mk.fact_add(
        "Simon", "role", "CEO", valid_from=old, valid_to=end, auto_close_stale=False
    )
    result = mk.consolidate()
    assert result["stale_closed"] == 0


def test_stale_close_dry_run_no_changes(mk):
    """Stale stage respects dry_run."""
    old = (datetime.date.today() - datetime.timedelta(days=500)).isoformat()
    mk.fact_add("Simon", "role", "CEO", valid_from=old, auto_close_stale=False)

    p = _facts_path(mk, "Simon")
    before = p.read_text(encoding="utf-8")
    result = mk.consolidate(dry_run=True)
    after = p.read_text(encoding="utf-8")

    assert result["stale_closed"] == 1
    assert before == after


def test_stale_close_no_valid_from_untouched(mk):
    """Facts with no valid_from must not be auto-closed."""
    mk.fact_add("Simon", "evergreen_truth", "loves cats", auto_close_stale=False)
    result = mk.consolidate()
    assert result["stale_closed"] == 0


# ---------------------------------------------------------------------------
# Stage 3 — Orphan Cleanup
# ---------------------------------------------------------------------------


def test_orphan_node_with_no_edges_removed(mk):
    """Graph node with no edges and no backing file → removed."""
    mk.graph_node("orphan_node_123", node_type="entity")

    # Confirm precondition.
    stats_before = mk.graph_stats()
    assert stats_before["nodes"] >= 1

    result = mk.consolidate()
    assert result["orphans_removed"] >= 1

    stats_after = mk.graph_stats()
    assert stats_after["nodes"] < stats_before["nodes"]


def test_orphan_node_with_edges_kept(mk):
    """Connected nodes are NEVER removed."""
    mk.graph_edge("alice", "knows", "bob")
    result = mk.consolidate()
    assert result["orphans_removed"] == 0
    stats = mk.graph_stats()
    assert stats["nodes"] >= 2


def test_orphan_with_backing_entity_file_kept(mk):
    """Even if no edges, a node with an entity .md file is preserved."""
    # Create a fact file → entity dir (or facts dir) exists.
    mk.fact_add("ProtectedEntity", "role", "Founder", auto_close_stale=False)
    # Add a graph node with the same slug — no edges.
    slug = mk._slugify("ProtectedEntity")
    mk.graph_node(slug, node_type="entity")

    nodes_before = mk.graph_stats()["nodes"]
    result = mk.consolidate()
    nodes_after = mk.graph_stats()["nodes"]

    # The protected node must remain.
    assert nodes_before == nodes_after or result["orphans_removed"] == 0


def test_orphan_dry_run_no_changes(mk):
    mk.graph_node("ghost_node", node_type="entity")
    nodes_before = mk.graph_stats()["nodes"]
    result = mk.consolidate(dry_run=True)
    nodes_after = mk.graph_stats()["nodes"]
    assert result["orphans_removed"] >= 1
    assert nodes_before == nodes_after  # no real deletion


# ---------------------------------------------------------------------------
# Stage 4 — Observation Generation
# ---------------------------------------------------------------------------


def test_observation_generated_for_entity(mk):
    mk.fact_add("Simon", "role", "CEO of Hashed", auto_close_stale=False)
    mk.fact_add("Simon", "lives_in", "Seoul", auto_close_stale=False)
    mk.fact_add("Simon", "likes", "Bitcoin", auto_close_stale=False)

    result = mk.consolidate()
    assert result["observations_generated"] >= 1

    obs_file = Path(mk.base_dir) / "observations" / f"{mk._slugify('Simon')}.txt"
    assert obs_file.exists()
    content = obs_file.read_text(encoding="utf-8")
    assert "Simon" in content
    assert "role=CEO of Hashed" in content
    assert "lives_in=Seoul" in content


def test_observation_skips_expired_facts(mk):
    """Closed facts whose valid_to is in the past are NOT in observation."""
    past = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    mk.fact_add(
        "Bob",
        "role",
        "Old CTO",
        valid_from="2020-01-01",
        valid_to=past,
        auto_close_stale=False,
    )
    mk.fact_add("Bob", "role", "Current CEO", auto_close_stale=False)

    mk.consolidate()
    obs_file = Path(mk.base_dir) / "observations" / f"{mk._slugify('Bob')}.txt"
    content = obs_file.read_text(encoding="utf-8")
    assert "Old CTO" not in content
    assert "Current CEO" in content


def test_observation_dry_run_no_files(mk):
    mk.fact_add("Carol", "role", "Founder", auto_close_stale=False)
    result = mk.consolidate(dry_run=True)
    assert result["observations_generated"] >= 1
    obs_dir = Path(mk.base_dir) / "observations"
    # observations/ dir may not even exist on dry_run
    if obs_dir.exists():
        files = list(obs_dir.glob("*.txt"))
        assert files == []


def test_observation_dedupes_keys(mk):
    """If same key has multiple values, only most-recent appears."""
    mk.fact_add(
        "Dave",
        "role",
        "Old Role",
        recorded_at="2020-01-01T00:00",
        auto_close_stale=False,
    )
    mk.fact_add(
        "Dave",
        "role",
        "New Role",
        recorded_at="2026-04-01T00:00",
        auto_close_stale=False,
    )
    mk.consolidate()
    obs_file = Path(mk.base_dir) / "observations" / f"{mk._slugify('Dave')}.txt"
    content = obs_file.read_text(encoding="utf-8")
    # Only one role line should be present.
    assert content.count("role=") == 1
    assert "New Role" in content


# ---------------------------------------------------------------------------
# Strategy & integration
# ---------------------------------------------------------------------------


def test_strategy_auto_default(mk):
    result = mk.consolidate()
    assert result["strategy"] == "auto"


def test_strategy_aggressive(mk):
    result = mk.consolidate(strategy="aggressive")
    assert result["strategy"] == "aggressive"


def test_full_pipeline_integration(mk):
    """End-to-end: duplicates + stale + orphan + observations."""
    # Duplicates
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)

    # Stale
    old = (datetime.date.today() - datetime.timedelta(days=500)).isoformat()
    mk.fact_add("Simon", "city", "Seoul", valid_from=old, auto_close_stale=False)

    # Orphan
    mk.graph_node("absolutely_isolated_node_xyz", node_type="entity")

    result = mk.consolidate()
    assert result["duplicates_merged"] >= 1
    assert result["stale_closed"] >= 1
    assert result["orphans_removed"] >= 1
    assert result["observations_generated"] >= 1
    assert result["tokens_saved_estimate"] >= 0
    assert isinstance(result["details"], list)
    assert len(result["details"]) > 0


def test_health_recommends_consolidate_on_duplicates(mk):
    """health() should suggest consolidate() when many duplicates exist."""
    for _ in range(15):
        mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    h = mk.health()
    text = " ".join(h["recommendations"])
    assert "consolidate" in text.lower()


def test_idempotent_consolidate(mk):
    """Running consolidate() twice on already-clean state is a no-op."""
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)
    mk.fact_add("Simon", "role", "CEO", auto_close_stale=False)

    first = mk.consolidate()
    second = mk.consolidate()
    assert first["duplicates_merged"] >= 1
    assert second["duplicates_merged"] == 0
    assert second["stale_closed"] == 0
