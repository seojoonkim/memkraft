"""Tests for GraphMixin (v2.0.0)"""
import pytest
from pathlib import Path
from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


def test_graph_node_basic(mk):
    mk.graph_node("sarah", node_type="person", label="Sarah Johnson")
    stats = mk.graph_stats()
    assert stats["nodes"] == 1


def test_graph_edge_basic(mk):
    mk.graph_edge("sarah", "works_at", "google")
    stats = mk.graph_stats()
    assert stats["edges"] == 1
    assert stats["nodes"] == 2  # auto-created


def test_graph_neighbors(mk):
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("google", "located_in", "nyc")
    results = mk.graph_neighbors("sarah", hops=2)
    targets = [r["target"] for r in results]
    assert "google" in targets
    assert "nyc" in targets


def test_graph_extract(mk):
    text = "Sarah works at Google. Sarah lives in New York."
    result = mk.graph_extract(text)
    assert result["edges_added"] > 0
    stats = mk.graph_stats()
    assert stats["edges"] > 0


def test_graph_search(mk):
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("google", "located_in", "nyc")
    results = mk.graph_search("Where does Sarah work?")
    assert len(results) > 0
    assert any("sarah" in r.lower() for r in results)


def test_multihop_reasoning(mk):
    """Avi Chawla의 Mark 예시 — multi-hop"""
    mk.graph_node("mark", node_type="person")
    mk.graph_node("grade10", node_type="grade")
    mk.graph_node("march", node_type="time")
    mk.graph_node("library", node_type="place")
    mk.graph_edge("mark", "is_in", "grade10")
    mk.graph_edge("grade10", "has_exams_in", "march")
    mk.graph_edge("library", "closes_before", "march")

    results = mk.graph_neighbors("mark", hops=2)
    all_targets = [r["target"] for r in results]
    assert "grade10" in all_targets
    assert "march" in all_targets


def test_graph_stats(mk):
    mk.graph_edge("a", "rel", "b")
    mk.graph_edge("b", "rel", "c")
    stats = mk.graph_stats()
    assert stats["nodes"] == 3
    assert stats["edges"] == 2
    assert "rel" in stats["top_relations"]


def test_no_duplicate_edges(mk):
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("sarah", "works_at", "google")  # 중복
    stats = mk.graph_stats()
    assert stats["edges"] == 1
