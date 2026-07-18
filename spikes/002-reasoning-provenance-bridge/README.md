# Spike 002: reasoning-recall provenance bridge

## Question and verdict

**Question:** Can a `MemKraft.reasoning_recall()` hit be bridged to spike 001's
allowlisted executor only after fail-closed verification of its local trajectory?

**Verdict:** **Yes, as a local-integrity feasibility spike.** A real MemKraft
trajectory carrying exactly one strict, versioned `procedure_ref` can be recalled,
verified against a deterministic six-entry registry, and dispatched to
`execute_validated_path(..., trusted=True)`. Missing, malformed, forged, duplicated,
out-of-store, unsuccessful, or oversized evidence falls back without executing
trajectory prose.

## Trust boundary

Untrusted inputs are the task, recall-hit dictionary, JSONL bytes, all trajectory
text, and every path supplied by the hit. The bridge trusts only:

- this spike's fixed registry and parser identity;
- the locally installed spike 001 executor imported from a fixed sibling path;
- a process-local random HMAC key held by the bridge module;
- the caller-provided `base_dir` as the intended MemKraft store root; and
- ordinary OS filesystem behavior during bounded, no-follow file access.

A step must contain exactly one metadata object of this shape:

```json
{
  "procedure_ref": {
    "id": "F.modular_exponentiation",
    "version": 1,
    "registry_digest": "<sha256>"
  }
}
```

The digest is SHA-256 over canonical JSON containing procedure ID, version, and
the stable parser identity `spike001.execute_validated_path.exact-grammar.v1`.
The registry contains exactly spike 001's six IDs, all at version 1. IDs are never
inferred from title, lesson, tags, signature, action, thought, or other prose.

## Verification behavior

Before dispatch, the bridge requires:

- a typed hit with nonempty safe `task_id` and path, and `status == "success"`;
- a path resolving directly under
  `realpath(base_dir)/.memkraft/trajectories`, named `<task_id>.jsonl`;
- no traversal component, symlink, directory, or other non-regular file;
- at most 64 KiB and 256 JSONL records;
- valid JSON objects with exactly one first `start` and one final `complete`;
- known record kinds and the same `task_id` on every record;
- successful completion and hit/record agreement for title, lesson, and pattern
  signature in addition to task identity, status, and path;
- exactly one `procedure_ref`, strict keys/types (including rejecting Boolean as
  an integer version), and an exact ID/version/digest registry match.

Only the verified ID is passed to spike 001. Spike 001 independently requires the
new task to full-match that ID's exact grammar and enforces its own input/resource
bounds. Verification errors return an `ExecutionResult(status="fallback", ...)`;
trajectory content is never interpreted with `eval`, `exec`, shell commands, or
data-selected imports.

## Security limitation

The registry SHA-256 is compatibility metadata, not authorization. Trusted seeding
also binds task ID, canonical path, exact trajectory-byte SHA-256, and procedure
reference into a frozen manifest whose canonical snapshot is authenticated by a
process-local HMAC. This detects trajectory-store writes and caller-side manifest
field mutation when the attacker cannot read the bridge key.

This is **not durable signing**. Arbitrary code execution, debugger/memory access,
or trusted bridge/executor replacement is inside the trust boundary. The spike does
not establish author identity, remote attestation, cross-process persistence, or
durable provenance across restarts and registry upgrades.

## Strict TDD evidence

Tests were written first against the absent bridge API.

- **RED:** `python -m pytest -q spikes/002-reasoning-provenance-bridge/test_bridge.py`
  stopped during collection with `ModuleNotFoundError: No module named 'bridge'`
  (`1 error in 0.08s`, exit 2).
- **GREEN (focused):** after the minimal implementation, the same command reported
  `40 passed in 0.07s`.
- **GREEN (combined):** `python -m pytest -q
  spikes/002-reasoning-provenance-bridge/test_bridge.py
  spikes/001-reasoning-path-executor/test_executor.py` reported
  `65 passed in 0.09s`.
- **Ruff:** checking both spikes' Python files reported `All checks passed!`.

Coverage includes real temporary MemKraft stores and real
`trajectory_start` / `trajectory_log` / `trajectory_complete` /
`reasoning_recall` calls, all six exact grammars (including F), unknown/G and wrong
grammars, wrong-family references, injection-bearing lessons, natural-language-only
lessons, malformed hit shapes, failed/forged/mismatched paths and identities,
traversal, symlinks, non-regular files, malformed JSON, duplicate/conflicting refs,
wrong registry fields/types, duplicate/missing lifecycle records, and byte/line
bounds.

## Non-claims

This standalone spike edits no product or benchmark code and makes **no live latency,
throughput, production-readiness, cross-host provenance, or benchmark-improvement
claim**. It is a narrow feasibility result only.
