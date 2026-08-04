# Graph Engineering Adapters

Graph Engineering is an adapter/runtime discipline: contract-bearing work nodes, dependency edges, fan-out/fan-in, explicit merge ownership, independent verification, failure isolation, and bounded convergence. MKEP/0 remains runtime-neutral and introduces **no graph schema**. It stores durable facts and verdicts through the existing **15 operations**; the runtime owns nodes, edges, reducers, loops, reachability, checkpoints, and scheduling.

**The resume/checkpoint semantics are non-equivalent** across Claude Code Dynamic Workflows, LangGraph, and Temporal. A checkpoint token from one must never be interpreted as equivalent to another runtime's replay history, durable workflow state, or continuation model. Keep runtime tokens inside an intentionally shareable typed handoff payload or provenance artifact; core stores and hashes them as opaque data and never resumes them.

## Runtime mapping

- **Claude Code Dynamic Workflows:** a workflow task maps to opaque gate ids and execution-run ids; parallel agent tasks are runtime fan-out; the workflow/controller owns joining and merge. A fresh verifier agent uses a distinct gate, receipt, and run id. Dynamic workflow continuation remains Claude-owned.
- **LangGraph:** graph nodes/edges and reducers remain in the LangGraph state graph. A node attempt maps to an execution run; checkpointer identifiers remain opaque runtime data. A reducer's output is represented only by its artifact receipt, not by a MemKraft merge record.
- **Temporal:** workflow history, activity retry, deterministic replay, signals, and Continue-As-New remain Temporal-owned. Activity output may produce a receipt; a separate activity/workflow execution may verify it. A Temporal event/history position is not an MKEP checkpoint.

## Fan-out, fan-in, independent verifier

The executable example is `examples/execution_graph_adapter.py`.

1. Runtime declares one goal and four gates: `build-linux`, `build-macos`, `merge-artifacts`, and `verify-merged-artifact` (`goal.declare`, `gate.declare`).
2. Runtime fans out Linux and macOS attempts. Each obtains a scope lease, emits an artifact receipt, passes its gate using that receipt, then releases (`lease.acquire`, `receipt.record`, `gate.transition`, `lease.release`).
3. Runtime—not core—fans in when its dependency policy says both parents completed. It owns merge conflicts and records the merged artifact by `receipt.record`; the ordered parent set stays in provenance. It then passes `merge-artifacts`.
4. An independent verifier gets a distinct `execution_run_id` and distinct gate, reads the merged artifact, records its own receipt, and passes or fails `verify-merged-artifact`. Independence is a runtime guarantee; MemKraft makes the separation auditable.
5. Runtime queries `assess.run`; if host policy permits closure, it calls `goal.transition`.

The trace replays deterministically because opaque ids and operation ids are runtime-stable. There is no `node`, `edge`, `merge`, `checkpoint`, `resume`, or `graph` operation. `binding_digest` provides equality for richer private bindings; it creates no graph query surface.

## Compatibility rule

Propose core schema only after two runtimes with different replay models demonstrate a correctness failure that cannot be represented with `execution_run_id`, `parent_execution_run_id`, `binding_digest`, receipts/provenance, and typed handoff payloads. Convenience or fewer lookups is insufficient.
