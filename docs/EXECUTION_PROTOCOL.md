# MemKraft Execution Protocol (MKEP/0)

MKEP/0 is a **Preview**, runtime-neutral protocol for durable execution facts. Its GA decision deadline is **2027-02-04**. The core owns an append-only local log and deterministic projections; a host runtime owns scheduling, work execution, graph topology, delivery, policy enforcement, and clocks.

## Wire contract

Every request is a closed object with `mkep`, `kind`, `request_id`, `op`, `target`, and `args`; apply requests also carry `precondition`. Clock-sensitive calls require caller-injected `now` in MKEP-TIME/1. Responses are closed success or error envelopes and include a digest over fingerprint-normalized MKCJSON/1.

MKCJSON/1 accepts object roots, ASCII keys matching `^[a-z][a-z0-9_]{0,63}$`, booleans, null, NFC strings, arrays, objects, and integers in ±(2^53−1). It rejects floats and lone surrogates. Keys are sorted, separators are compact, UTF-8 is emitted directly, and SHA-256 digests are lowercase hex over canonical logical objects—not JSONL file bytes. `id`, `created_at`, and `event_seq` are excluded only where a fingerprint contract says so.

## Closed operation registry

The 15 operations are: `assess.record`, `assess.run`, `describe`, `gate.declare`, `gate.transition`, `goal.declare`, `goal.transition`, `handoff.declare`, `handoff.export`, `handoff.import`, `handoff.transition`, `lease.acquire`, `lease.release`, `receipt.record`, and `state.read`.

No operation schedules work or authorizes it. `assess.run` is a pure advisory query. Every mutation is idempotent by `operation_id`; runtimes retry an unknown transport outcome only with the byte-equivalent request and same id. Leases are local filesystem coordination with fencing, not identity authentication.

## Storage and transports

Execution records live at `<base_dir>/.memkraft/execution/events.jsonl`. The atomic unit is one append-only line; this log is not compacted in 3.3.0. Projection orders by `(event_seq,id)`, never timestamp. Scope is one trusted host and local filesystem.

Python and CLI call the same dispatcher. CLI clients pass `--base-dir` and `--lock-timeout`, send JSON on stdin, parse only stdout, and treat timeout as unknown. MCP exposes only `describe`, `state.read`, `assess.run`, and `handoff.export`.

## Conformance

The language-neutral corpus under `tests/conformance/fixtures/0` contains 32 named cases and 167 total fixture directories. The Python runner executes practical L2 cases; the Go verifier reads the complete generated corpus, reproduces CJ-03 canonical bytes/digest, verifies Python-origin XR-01 envelopes, and tests replay, tamper rejection, and origin conflict. Run `go test ./tests/conformance/go/...`.
