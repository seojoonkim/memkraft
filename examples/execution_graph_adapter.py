"""Runtime-owned fan-out/fan-in mapping to MKEP/0; example, not public API."""
from __future__ import annotations

from typing import Any, Dict

EXISTING_OPS = frozenset({
    "assess.record", "assess.run", "describe", "gate.declare", "gate.transition",
    "goal.declare", "goal.transition", "handoff.declare", "handoff.export",
    "handoff.import", "handoff.transition", "lease.acquire", "lease.release",
    "receipt.record", "state.read",
})


def replay_graph_example(_base_dir: Any) -> Dict[str, Any]:
    """Return a deterministic adapter trace; topology and merge stay runtime-owned."""
    operations = [
        "describe", "goal.declare",
        "gate.declare", "gate.declare", "gate.declare", "gate.declare",
        "lease.acquire", "receipt.record", "gate.transition", "lease.release",
        "lease.acquire", "receipt.record", "gate.transition", "lease.release",
        "receipt.record", "gate.transition",
        "receipt.record", "gate.transition", "assess.run", "goal.transition",
    ]
    stable_trace = [
        {"runtime_node": "build-linux", "gate_id": "build-linux", "execution_run_id": "aaaaaaaa00000001"},
        {"runtime_node": "build-macos", "gate_id": "build-macos", "execution_run_id": "bbbbbbbb00000002"},
        {"runtime_node": "merge-artifacts", "gate_id": "merge-artifacts", "execution_run_id": "cccccccc00000003",
         "parent_set_in_provenance": ["aaaaaaaa00000001", "bbbbbbbb00000002"]},
        {"runtime_node": "independent-verifier", "gate_id": "verify-merged-artifact",
         "execution_run_id": "dddddddd00000004", "artifact_digest": "0" * 64},
    ]
    return {
        "operations": operations,
        "stable_trace": stable_trace,
        "fan_out": ["build-linux", "build-macos"],
        "fan_in": "merge-artifacts",
        "producer_execution_run_id": "cccccccc00000003",
        "verifier": stable_trace[-1],
    }
