"""Executable documentation contract for runtime-owned graph adapters."""
from pathlib import Path

from examples.execution_graph_adapter import EXISTING_OPS, replay_graph_example


def test_graph_example_uses_only_closed_15_operation_registry(tmp_path):
    assert len(EXISTING_OPS) == 15
    result = replay_graph_example(tmp_path / "first")
    assert set(result["operations"]) <= EXISTING_OPS
    forbidden = {"node.declare", "edge.declare", "graph.merge", "checkpoint.save", "workflow.resume"}
    assert not (set(result["operations"]) & forbidden)


def test_fan_out_fan_in_and_independent_verifier_replay_deterministically(tmp_path):
    first = replay_graph_example(tmp_path / "a")
    second = replay_graph_example(tmp_path / "b")
    assert first["stable_trace"] == second["stable_trace"]
    assert first["fan_out"] == ["build-linux", "build-macos"]
    assert first["fan_in"] == "merge-artifacts"
    assert first["verifier"]["execution_run_id"] != first["producer_execution_run_id"]
    assert first["verifier"]["gate_id"] == "verify-merged-artifact"


def test_docs_lock_runtime_owned_topology_and_checkpoint_non_equivalence():
    text = (Path(__file__).parents[1] / "docs" / "GRAPH_ENGINEERING_ADAPTERS.md").read_text()
    for name in ("Claude Code Dynamic Workflows", "LangGraph", "Temporal"):
        assert name in text
    assert "resume/checkpoint semantics are non-equivalent" in text
    assert "no graph schema" in text.lower()
    assert "15 operations" in text
