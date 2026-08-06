# State Contract

State Contract is MemKraft's runtime-neutral guard against distributed project-state drift.

A host collects observations from Git, the active Python runtime, the public release, and a project snapshot. MemKraft evaluates those plain JSON-like observations deterministically, returns a release-readiness report, and can append the report to its local audit log.

MemKraft does **not** run Git, install packages, query registries, push branches, publish releases, or remediate drift. Those remain host responsibilities.

## Default contract

The default contract blocks release readiness when any of these are true:

- source, imported runtime, and installed distribution versions differ
- the runtime imports from an ephemeral source under `/tmp`, `/private/tmp`, or macOS temporary directories
- the current branch has no same-name remote-tracking branch
- the working tree is dirty
- the project snapshot's public version differs from the observed public release
- the project snapshot's development version differs from the observed source version

Public and development versions are intentionally separate. During pre-release development, source `3.4.0` and public `3.3.0` are healthy when the snapshot records those two values in their respective fields.

## Core API

```python
from memkraft import MemKraft
from memkraft.state_contract import evaluate

report = evaluate(observations)                  # pure, zero writes
mem = MemKraft(base_dir="./memory")
report = mem.state_contract_check(observations)  # pure, zero writes
record = mem.state_contract_record(
    observations,
    operation_id="release-3.4.0-preflight",
)
history = mem.state_contract_history()
```

Reports are deterministic for the same values regardless of mapping insertion order. `critical` and `error` findings make `release_ready` false; warnings are advisory.

Recorded reports are appended to `.memkraft/state_contract/events.jsonl` under the existing governance lock. An exact retry of an `operation_id` returns the original record. Reusing that ID with a different report fails closed. Corrupt log lines remain visible through `history["skipped"]` and block future writes.

## Host adapter

```bash
PYTHONPATH=src python scripts/check_project_state.py \
  --repo . \
  --python "$(command -v python)" \
  --public-version 3.3.0 \
  --snapshot-public-version 3.3.0 \
  --snapshot-development-version 3.4.0
```

The adapter prints one JSON report and exits nonzero when `release_ready` is false. It is intended for a trusted repository and trusted interpreter: Git configuration and the selected Python executable are executable trust boundaries. It uses local Git inspection, a read-only `git ls-remote` query to verify the actual remote branch SHA, and a bounded Python runtime probe, but does not itself publish, install, or mutate Git/package state.

The public and snapshot versions are authoritative observations supplied by the host; this adapter does not fetch or parse their sources. A release workflow must obtain them from PyPI/GitHub and the project card before invoking the gate. State Contract verifies cross-state consistency, not the honesty of caller-supplied observations.

`--allow-ephemeral-source` removes only the ephemeral-path constraint. It is intended for explicit local experiments and must not be used in a release gate.

## Generic constraints

Custom callers may pass constraints with dotted observation paths. P0 supports only:

- `equals`
- `truthy`
- `forbidden_path_prefixes`

Unknown kinds and severities fail validation rather than being ignored.
