# Adaptive ETA and Delay Ledger Preview

MemKraft 3.5 adds a Python-only, local-private, append-only timing ledger. It records evidence; it never starts, queues, schedules, orders, deploys, or otherwise executes work.

## Public API

Exactly seven methods are attached to `MemKraft`:

- `delay_run_start(run_id, kind, subject, *, now, parent_run_id=None, context_refs=(), evidence_refs=(), authority_verified=False, operation_id=None)`
- `delay_run_finish(run_id, elapsed_ms, *, now, outcome="completed", evidence_refs=(), authority_verified=False, operation_id=None)`
- `delay_estimate(kind, subject, *, now, window=100, through_seq=None)`
- `delay_record_retrospective(...)`
- `delay_record_action(...)`
- `delay_record_application(...)`
- `delay_record_verification(...)`

Run kinds form a closed hierarchy: `task` → `phase` → `attempt`. A child may start only while its direct parent is open, and a parent may finish only after every direct child finishes. Outcomes are `completed`, `failed`, `interrupted`, `incomplete`, or `aborted`; only completed runs feed estimates.

## Estimates and anomalies

Estimates are scoped to the exact kind and opaque subject token. At least five completed samples are required. The bounded window uses deterministic nearest-rank p50/p80 and integer MAD. The anomaly threshold is `p50 + 4 * MAD`; a finish is anomalous only when elapsed milliseconds strictly exceed it. `through_seq` reproduces historical estimates.

## Evidence chain

A completed anomalous run may anchor the inert sequence retrospective → action → application → verification. Every link requires the exact predecessor type. Verification verdicts are the closed domain `pass`, `fail`, or `inconclusive`. External receipt and evidence references are opaque references, not authenticated authority.

## Integrity and privacy

Records live at `.memkraft/delay/events.jsonl`; the directory and file are repaired to owner-only modes before append. Reads do not create the store. Replay validates persisted types, domains, hierarchy, anomaly snapshots, reference limits, contiguous event sequences, and unique operation IDs. Corruption makes estimates unavailable and blocks writes rather than exposing a partial view.

`authority_verified` must be the literal boolean `False`; `True` is rejected because MemKraft cannot verify external authority. Writes are serialized under the governance lock and are idempotent by operation ID plus canonical argument fingerprint. An operation ID reused with different arguments raises a typed `DelayError`.

This Preview is additive, single-host, local-filesystem-only, and Python 3.9 compatible. It adds no CLI, MCP, network, scheduler, or execution surface.
