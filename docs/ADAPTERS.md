# MKEP/0 Adapter Guide

Adapters translate a runtime boundary into the existing 15 MKEP/0 operations. They do not add domain semantics.

## Startup and calls

1. Call `describe`; require `mkep: "0"`, required operations, limits, and guarantees.
2. Select `base_dir` explicitly. Never infer a workspace from subprocess cwd.
3. Mint opaque namespaced `goal_id`, runtime-stable `execution_run_id`, and deterministic `operation_id` for each apply.
4. Bound each call inside the host hook budget. A timeout is an unknown outcome: replay the identical request/id.
5. Re-read projections after mutations. Do not cache through a write.
6. Enforce leases at an authoritative host boundary and propagate `fence_token` to every protected write.
7. Record receipts only from observed artifacts/tool results. Core validates digest format, not artifact bytes.

For subprocess use argv, never a shell string: `memkraft exec call --base-dir BASE --lock-timeout 1.5`; request JSON is stdin, response JSON is stdout, diagnostics are stderr. Exit 0–3 carries a response; 64/70 does not.

## Runtime mappings

- **Hermes:** one card references one `goal_id`; card status is not mirrored into core. Cron uses pure `assess.run`; dispatch requires a lease independently. Profile isolation is filesystem base selection, not protocol data.
- **OpenClaw:** an observation-only reference mapping records receipts/lineage from observation hooks. Policy-hook enablement depends on measured CLI latency. Observation hooks never enforce; authoritative hooks fail closed and use bounded calls.
- **Generic clients:** maintain runtime-local correlation and put only typed, intentionally shareable continuation data in handoff payloads.

## Security and lifecycle

Gates are advisory, authority strings are unverified, and `should_run` is not permission. Handoff digests prove self-consistency, not sender authenticity. Do not hold a lease across a boundary whose completion the adapter cannot guarantee. Expiry is the primary recovery path where shutdown hooks are unreliable.

Graph-runtime mappings and examples are in [GRAPH_ENGINEERING_ADAPTERS.md](GRAPH_ENGINEERING_ADAPTERS.md); protocol details are in [EXECUTION_PROTOCOL.md](EXECUTION_PROTOCOL.md).
