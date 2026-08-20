# MKEP/0 Adapter Guide

Adapters translate a runtime boundary into the existing 15 MKEP/0 operations. They do not add domain semantics.

## Memory substrate contract (v4.0)

External agent hosts can use the transport-neutral `MemoryAdapter` facade:

```python
from memkraft import MemKraft, MemoryAdapter

adapter = MemoryAdapter(MemKraft(base_dir="/path/to/memory"))
adapter.remember(name="Project", info="...", source="hermes")
adapter.recall(query="project context", top_k=5)
adapter.feedback(
    classification="success",
    task_ref="task:example",
    outcome_ref="outcome:example",
    input_snapshot_ref="snapshot:example",
)
adapter.health()
```

Each call returns an envelope with `ok` and `operation`. Failures include a
stable error code and `retryable` flag. The adapter delegates persistence and
experience validation to MemKraft; it never evaluates, promotes, activates, or
authorizes a candidate.

### Host-neutral bridge

For subprocess-based hosts, use the closed JSON-stdio bridge. It is the
portable integration surface for Hermes, OpenClaw, MCP clients, Claude Code,
Codex, and custom agent runtimes:

```bash
pip install memkraft
memkraft integrations --json
printf '%s' '{"operation":"health"}' | memkraft bridge call --base-dir /path/to/memory
```

The bridge protocol is `memkraft-agent-bridge/1` and exposes exactly
`remember`, `recall`, `feedback`, and `health`. It emits one JSON response on
stdout; human progress output is suppressed so any host can safely parse it.

`memkraft setup --base-dir BASE` writes `BASE/integrations/memkraft.json`.
The operation is idempotent and manifest-only: it never overwrites host
credentials or unrelated configuration. Host-native registration remains an
adapter responsibility, while every host can consume the generated command.
Unknown operations and malformed requests return structured errors rather than
being dispatched dynamically. `memkraft integrations --json` separately
reports the live bridge health, Hermes entry-point discovery, MCP module
availability, and installation-path consistency. A development `PYTHONPATH`
or
stale editable source override is reported instead of silently claiming that
the installed wheel is active.


## Startup and calls

There are two intentionally separate subprocess contracts:

- `memkraft bridge call`: memory operations for any host.
- `memkraft exec call`: the closed MKEP/0 execution-state operations; it is not
a replacement for the memory bridge.

For the MKEP/0 execution transport, call `describe`; require `mkep: "0"`, required operations, limits, and guarantees. Select `base_dir` explicitly, mint opaque namespaced IDs, bound calls inside the host hook budget, re-read projections after mutations, enforce leases at an authoritative host boundary, and record receipts only from observed artifacts.

For subprocess use argv, never a shell string: `memkraft exec call --base-dir BASE --lock-timeout 1.5`; request JSON is stdin, response JSON is stdout, diagnostics are stderr. Exit 0–3 carries a response; 64/70 does not.

## Runtime mappings

- **Hermes:** one card references one `goal_id`; card status is not mirrored into core. Cron uses pure `assess.run`; dispatch requires a lease independently. Profile isolation is filesystem base selection, not protocol data.
- **OpenClaw:** an observation-only reference mapping records receipts/lineage from observation hooks. Policy-hook enablement depends on measured CLI latency. Observation hooks never enforce; authoritative hooks fail closed and use bounded calls.
- **Generic clients:** maintain runtime-local correlation and put only typed, intentionally shareable continuation data in handoff payloads.

## Security and lifecycle

Gates are advisory, authority strings are unverified, and `should_run` is not permission. Handoff digests prove self-consistency, not sender authenticity. Do not hold a lease across a boundary whose completion the adapter cannot guarantee. Expiry is the primary recovery path where shutdown hooks are unreliable.

Graph-runtime mappings and examples are in [GRAPH_ENGINEERING_ADAPTERS.md](GRAPH_ENGINEERING_ADAPTERS.md); protocol details are in [EXECUTION_PROTOCOL.md](EXECUTION_PROTOCOL.md).
