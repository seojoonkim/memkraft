# MemKraft 3.3.0 — Execution State Kernel + Execution Protocol (MKEP/0)

**Authoritative development-ready specification. Additive preview.**

Status: locked. This document supersedes the three input analyses (ARCHITECT, PROTOCOL, REDTEAM) wherever they conflict. Where an input analysis is adopted, it is adopted as amended here. No unresolved P0 architecture question remains; every open item is recorded in §20 as Deferred with a default.

Citation convention: **[VF]** = verified against a file in the research pack, with path and line range. **[DES]** = a design decision made in this document; it is normative but is not a claim about existing code. **[ASSUME]** = must be checked in Slice 0 before implementation begins.

---

## 1. Product thesis and scope

### 1.1 Thesis

Long-running agents do not fail because they forget. They fail because at the resumption point nothing can answer *what is settled and what is not*.

MemKraft already owns the hard part of durable local state: single-line atomic appends under `flock` **[VF `src/memkraft/store_core.py:162-171`]**, inode-revalidating lock acquisition that survives compaction **[VF `store_core.py:86-116`]**, skip-and-count handling of corrupt lines rather than whole-load failure **[VF `store_core.py:194-201`]**, and tmp+fsync+`os.replace` compaction **[VF `store_core.py:298-304`]**. What it lacks is a *typed execution axis*: goals, gates, evidence, exclusion, and a verdict about whether a run should proceed.

The runtime-neutral thesis adds exactly one claim on top: **the value of a resumable execution substrate scales with the number of runtimes that can read and write it identically.** A kernel reachable only from one Python process is a library. A kernel reachable identically from Python, from a JSON-over-stdio subprocess, and from a read-only MCP projection is a substrate — the same goal survives an agent moving from Hermes to OpenClaw to a bare shell script.

The price of that is one thing and one thing only: a canonical wire format with a stable digest, and a conformance kit that proves two implementations agree byte-for-byte. §5 and §16 pay that price.

### 1.2 What ships in 3.3.0

An additive preview containing:

- A durable typed execution record set (§4) written through the existing store, with no parallel storage layer.
- A deterministic projection and a single transition table (§4.6, §4.7).
- Scoped leases with monotonic fence tokens and an explicitly enumerated set of fence-protected mutations (§7).
- Evidence receipts with snapshot binding that closes the reopen-replay hole (§8).
- Advisory assessment, split into a pure read and an explicit append (§9).
- Public-safe handoff export/import from a caller-supplied envelope, with no cross-base read anywhere in the code path (§10).
- One bounded command envelope, MKEP/0, over a closed 15-entry operation registry (§5, §6).
- Python typed API (primary), CLI JSON transport (`memkraft exec call`), and a **read-only** two-tool MCP projection (§11, §12, §13).
- Adapter specifications for Hermes (in-process Python), OpenClaw (TypeScript → CLI subprocess), and a generic subprocess adapter (§14, §15).
- A language-neutral conformance kit with 32 named cases (§16).

### 1.3 Anti-goals (structurally enforced, not merely promised)

| Anti-goal | Structural enforcement |
|---|---|
| **Not a scheduler** | No field in any record, request, or response may name a future instant *at which to act*. `expires_at` is permitted because it is an input to a validity computation. `next_check_at`, `retry_after`, `poll_interval`, `cadence`, `cron` are prohibited names. Conformance `NS-01` greps all schemas and all response fixtures. |
| **Not a workflow DAG** | Gates carry no `depends_on`, `order`, `priority`, `assignee`, or edges. `MAX_GATES_PER_GOAL = 64`. |
| **Not a queue** | No `WorkItem` type or synonym. Conformance `NS-02` greps `work_item`, `workitem`, `task_queue`, `dispatch` in `src/memkraft/execution_*.py`. |
| **Not a retry engine** | `retryable` is a *factual property of an error* ("the identical bytes could succeed later"), never advice about when. Core never states a delay and never retries. |
| **Not a model router** | No provider, model, token, or cost field anywhere in the protocol. |
| **Not a notification system** | Core performs zero network I/O. Envelope transport is the runtime's job. |
| **Not a human authorization system** | `authority_verified` is forced `false` on every record; a caller-supplied `true` is a hard error. There is no authorization decision anywhere in core. |
| **Not distributed consensus** | Fencing is valid only under local-filesystem `flock` semantics **[VF `store_core.py:60-67`]**. `guarantees.multi_host: false` is machine-readable in `describe`. |
| **Not a control plane** | Core never spawns, signals, supervises, or waits on a process. |
| **No parallel storage layer** | Every write goes through `store_core.append`; every read through `store_core.read_all` **[VF `store_core.py:146`, `:175`]**. One envelope, one lock kind. |
| **No speculative packaging** | One distribution, no `memkraft.execution` subpackage. |

**Standing No-Go:** if multi-host operation enters the roadmap, this kernel is the wrong artifact and must not be built. The correct answer at that point is consensus, and consensus is not MemKraft's job. This is recorded as decision D-01 in §20.

---

## 2. Baseline, version target, and Slice-0 verification

### 2.1 The version discrepancy, resolved

The authoritative implementation baseline is remote `origin/main` commit `b9453da`, tagged `v3.2.0`; live verification on 2026-08-04 found both `pyproject.toml` and `src/memkraft/__init__.py` at `3.2.0`. The local `main` checkout remains at `927941f` / `3.1.0` and has unrelated dirty files, so it is **not** an implementation baseline and must not be used for development.

**Locked resolution.** Create a clean branch/worktree from `origin/main` and target **3.3.0**. Slice 0 must re-fetch, verify the base commit/tag and confirm all release metadata sources agree before writing code. If upstream has advanced or any source reads ≥ 3.3.0, stop and re-pin the plan to the next available 3.x minor.

**Gate G0.** CI asserts `pyproject.toml` version, `src/memkraft/__init__.py:__version__`, and the CHANGELOG heading are byte-equal. The repository already has a release-metadata consistency guard (commit `15b5e02 ci: guard release metadata consistency`); extend it rather than adding a second one.

### 2.2 Verified baseline facts this design depends on

| Fact | Source |
|---|---|
| Atomic unit is exactly one line: one `os.write` of a newline-terminated JSON line under `flock` | `store_core.py:162-171` |
| Lock re-validates the inode after acquisition and retries on replacement | `store_core.py:86-116` |
| Lock acquisition is blocking `LOCK_EX` with **no** `LOCK_NB` path | `store_core.py:63`, `:100` |
| Corrupt lines are skipped and counted, never fatal | `store_core.py:194-201`, `:216` |
| Compaction is tmp → `fsync` → `os.replace`, preserving **file order** of surviving lines | `store_core.py:289-296`, `:298-304` |
| `schema_version` is forced to 1 by the store, overriding caller input | `store_core.py:157` |
| `id` (uuid4 hex) and `created_at` are store-assigned when absent | `store_core.py:156`, `:158-160` |
| `read_all` treats a missing file as empty | `store_core.py:203-204` |
| `mark_tombstone` performs a full `read_all(include_tombstoned=True)` before appending | `store_core.py:228-230` |
| `append` mkdirs parent directories | `store_core.py:165` |
| `requires-python = ">=3.9"` | `pyproject.toml:11` |
| MCP is an optional extra, `mcp>=1.0` | `pyproject.toml:30` |
| MCP exposes exactly 4 tools today with a pure, unit-testable `dispatch` | `mcp.py:11-16`, `:43-94`, `:97-124` |
| MCP results are `str(result)` in a single `TextContent`; exceptions become `f"error: {e}"` | `mcp.py:151-157` |
| MCP constructs one process-global `MemKraft()` with no `base_dir` | `mcp.py:137` |
| Mixins are flattened by `setattr`, last-write-wins, with additive-only enforcement for exactly two designated mixins | `__init__.py:74`, `:116-126` |
| `base_dir` resolves arg → `$MEMKRAFT_DIR` → `./memory` | `core.py:115-118` |
| `init()` creates `.memkraft/{snapshots,channels,tasks,agents}` — no `execution/` | `core.py:127-130`, `:182-188` |
| CLI entry point is `memkraft.cli:main` | `pyproject.toml:38` |
| OpenClaw decision-capable hooks are the bolded set only; the rest are observation | `docs/plugins/hooks.md:133-135`, `:137-222` |
| `before_tool_call` / `before_install` default to 15 s per handler and **fail closed** on timeout | `docs/plugins/hooks.md:107-110` |
| A timed-out handler is **not cancelled**; it keeps running and its side effects continue | `docs/plugins/hooks.md:100-105` |
| Decision handlers run sequentially by descending priority; observation handlers run in parallel and may overlap later events | `docs/plugins/hooks.md:52-56` |
| "Do not use priority to order observation side effects" | `docs/plugins/hooks.md:55-56` |
| `session_end` drain is **2 s total across all sessions and all handlers** | `docs/plugins/hooks.md:195-199` |
| `gateway_stop` is 5 s per handler; shutdown continues on timeout | `docs/plugins/hooks.md:111-113` |
| `subagent_ended` carries `targetSessionKey`, not `agentId`/`childSessionKey` | `docs/plugins/hooks.md:209` |
| `subagent_spawning` is deprecated | `docs/plugins/hooks.md:207` |
| Hook timeouts are operator-overridable, max 600000 ms | `docs/plugins/hooks.md:75-98` |
| `before_agent_reply` exposes `eligibleTriggers` (`cron`/`heartbeat`/`user`) | `docs/plugins/hooks.md:66-73` |
| OpenClaw is TypeScript/Node; version 2026.7.2 | `package.json:2-9`, `:22-24` |

### 2.3 Not verifiable from the pack

- **OpenClaw MCP client support is not established.** The pack contains hook docs and `package.json`; none describes an MCP client. No design here may assume OpenClaw can consume MemKraft's MCP server. The OpenClaw adapter is subprocess-only (§15).
- **Nothing about Hermes is in the pack.** §14 is an adapter *contract* to be implemented in the Hermes repository, not a description of existing Hermes code.
- `derived_views.py`, `outcomes.py`, `context_compiler.py` are not in the pack. `_governance_lock`, `_append_audit`, and `usage_id` composition are load-bearing and unreviewed.

### 2.4 Slice-0 assumptions (blocking)

| # | Assumption | Falsified by | Blast radius |
|---|---|---|---|
| A1 | `DerivedViewsMixin._governance_lock()` exists and wraps `store_core._lock_current_inode` | reading `derived_views.py` | build a local lock helper on the verified `_lock_current_inode`; no protocol change |
| A2 | `DerivedViewsMixin._append_audit` suppresses duplicate `operation_id` | reading `derived_views.py` | implement dedup locally; no protocol change |
| A3 | `ContextCompilerMixin.compile_context` derives `usage_id` as sha256 over a fixed identity dict | reading `context_compiler.py` | **release-blocking**; §12.4 and gate G10 must be re-derived |
| A4 | No module named `execution*` exists under `src/memkraft/` | `ls src/memkraft` | rename modules |
| A5 | `cli.py` registers subcommand groups by a uniform pattern | reading `cli.py` | CLI wiring changes; protocol unaffected |
| A6 | `mcp.Server.call_tool` in the installed `mcp>=1.0` supports `structuredContent` | integration test | fall back to a JSON-text-only `TextContent`; §13 amended |

**Normative:** A1–A3 must be verified by reading the real files before Slice 1. A3 is verified **first**; if it is wrong, stop and re-derive §12.4.

---

## 3. Normative boundary: core / runtime / adapter

One sentence: **core owns facts and verdicts; the runtime owns time and action; the adapter owns translation and nothing else.**

### 3.1 Core (`memkraft`, L1 kernel + L2 protocol)

Core **MUST**:

1. Validate and durably append typed execution records through `store_core.append`, reusing envelope v1.
2. Reject invalid transitions at append time with a typed error, leaving the on-disk line count **unchanged**.
3. Compute projections deterministically: identical bytes + identical injected `now` ⇒ identical `projection_digest`.
4. Accept `now` as a caller-injected tz-aware timestamp on every verdict path, and call no wall clock in any `execution_*` module.
5. Force `authority_verified: false` on every record; reject any request supplying `true`.
6. Mark every assessment `advisory: true`.
7. Grant at most one valid lease per `(goal_id, scope_key)` relative to `now`, and issue strictly increasing `fence_token` per goal.
8. Canonicalize every request and response per MKCJSON/1 (§5) and expose stable digests.
9. Reject a repeated `operation_id` whose canonical fingerprint differs; return `already_applied` when it matches.
10. Restrict all filesystem access to paths under `self.base_dir`.
11. Expose capability discovery with no side effects and no file creation.

Core **MUST NOT**:

1. Spawn, kill, signal, supervise, or wait on any process.
2. Implement scheduling, cadence, cron, polling, backoff, or retry.
3. Emit any field naming a future instant at which to act.
4. Select models or providers, meter tokens, or enforce rate limits.
5. Deliver notifications or transport handoff envelopes.
6. Open any path derived from caller input, or any path outside `self.base_dir`.
7. Verify human identity or authority; grant, check, or enforce authorization.
8. Model priority, ordering, assignment, or dependency edges.
9. Perform network I/O of any kind.
10. Introduce a `WorkItem` type or synonym.

### 3.2 Runtime (Hermes, OpenClaw, shell, anything)

Runtime **MUST**:

1. Mint `execution_run_id` and own its lifetime.
2. Inject `now` from its own clock on every call.
3. **Actually check the fence token before performing any side effect the lease protects.** This is where safety lives. `should_run` is not permission.
4. Decide cadence, backoff, retry, and abandonment.
5. Transport handoff envelopes between bases.
6. Perform any human authentication it requires *before* calling MemKraft.

Runtime **MUST NOT**:

1. Treat `recommendation == "should_run"` as authorization.
2. Fabricate `authority_claim: "human"` to route around a gate and then treat the result as evidence of human approval.
3. Write another instance's `base_dir` directly.

### 3.3 Adapter

Adapter **MUST**:

1. Translate runtime events into MKEP operations and MKEP responses into runtime effects, and do nothing else stateful.
2. Be idempotent under replay: supply a deterministic `operation_id` derived from runtime-stable identifiers.
3. Bound every MemKraft call strictly inside the host's hook budget, with margin.
4. Fail closed where the host hook is fail-closed; fail open where the hook is observational.

Adapter **MUST NOT**:

1. Add domain semantics absent from the protocol (no adapter-local gate ordering, no retry of a rejected transition with mutated parameters).
2. Cache a projection across a mutation without re-reading.
3. Hold a lease across a boundary whose completion it cannot guarantee.
4. Invoke the CLI through a shell string. All invocation is argv + stdin (§15.1).

### 3.4 Execution-graph adapter profile (Graph Engineering)

The emerging "Graph Engineering" practice for agents is an **adapter/runtime discipline**, not a new MKEP domain model. In this document it means: contract-bearing work nodes, real data-dependency edges, fan-out/fan-in, independent verifier nodes, explicit merge ownership, failure isolation, and bounded convergence loops. Claude Code Dynamic Workflows, LangGraph, and Temporal all expose variants of these ideas, but their resume, checkpoint, isolation, and merge guarantees are not equivalent.

**Locked boundary:** MKEP/0 does not add node, edge, reducer, merge, checkpoint, loop, or workflow records. It does not validate graph reachability, cycles, dependencies, or parent completion. The runtime owns graph topology and execution. MemKraft records only durable facts and verdicts produced at that boundary.

Adapter mapping in 3.3.0:

| Graph-engineering concern | Existing MKEP/0 representation |
|---|---|
| one work-node contract | one or more `gate.declare` records; `gate_id` is adapter-minted and opaque |
| execution attempt | `execution_run_id`; a retry is a new runtime-minted id |
| single-parent lineage | `parent_execution_run_id` |
| write ownership / conflict isolation | `scope_key` plus lease/fence and `expect_execution_seq` |
| structured node output | artifact digest and summary in `receipt.record`; detailed runtime data remains behind `artifact_path` or `provenance_id` |
| independent verifier | a distinct gate/receipt pair and a distinct `execution_run_id`; the runtime proves independence, not core |
| fan-in / multi-parent merge | runtime-owned merge; record the merged artifact as a receipt and keep the ordered parent set in the referenced provenance artifact or handoff payload |
| cross-runtime continuation | typed `handoff.payload`; runtime checkpoint tokens remain inside that payload and are never dereferenced by core |

`binding_digest` remains the privacy-preserving equality handle for richer runtime-local bindings. The adapter may hash a canonical object containing step, attempt, parent-set, or checkpoint references, but core stores no such raw identifiers and provides no graph query over them.

**Compatibility gate before promoting a 3.4 backlog item into a schema proposal:** first demonstrate a real adapter query that cannot be answered from `execution_run_id`, `parent_execution_run_id`, `binding_digest`, gate/receipt provenance, and typed handoff payload. Synthetic convenience is insufficient. A proposal must include traces from at least two runtimes with different replay models and show that keeping the correlation in provenance artifacts causes a correctness failure, not merely an extra lookup. Until then, the wire and 15-operation registry remain unchanged. Backlog placement is not a release commitment.

---

## 4. Domain model

### 4.1 Storage layout

```
<base_dir>/.memkraft/execution/events.jsonl
<base_dir>/.memkraft/origin_instance_id    # random uuid4 hex, created lazily on first export
```

**Locked:** a **single append-only log for all record types**, because global ordering is a precondition of deterministic projection.

**Locked (D-14):** `init()` is amended to create `.memkraft/execution/` explicitly; the per-base `origin_instance_id` is not created by `init()` and remains lazy until first handoff export rather than relying on `store_core.append`'s incidental `mkdir` **[VF `store_core.py:165`, `core.py:182-188`]**.

**Locked (D-15):** **the execution log is never compacted in 3.3.0.** `compact` is not wired to it and a test asserts so. This single decision eliminates the sequence-reuse hazard, the `seq_high_water` second file, and the resulting two-file atomicity break that REDTEAM correctly identified as fatal (F-5). Compaction of the execution log is Deferred (§20, D-15) with a documented rationale: reclaiming space from an append-only audit log is a retention feature, and retention is not in preview scope.

### 4.2 Ordering — the `event_seq` question, resolved

ARCHITECT specified `event_seq` allocated under lock with a `seq_high_water` sidecar file. REDTEAM showed this breaks one-line atomicity and can produce gaps that its own gate forbids, and correctly noted that **compaction preserves file order** **[VF `store_core.py:289-296`]**, undermining the stated reason for distrusting file order.

**Locked resolution.** Keep `event_seq`, drop the sidecar file, and forbid compaction of the execution log (§4.1).

- `event_seq` is allocated as `max(event_seq over all lines, including tombstoned) + 1`, computed **after** acquiring `_governance_lock()` and re-reading with `include_tombstoned=True`. Any value read before the lock is discarded.
- Because the log is never compacted, the max never decreases. No sidecar, no second file, no gap window. The atomic unit remains exactly one line.
- Projection sorts by `(event_seq, id)`. Timestamps are data, never sort keys.

**Cost, stated honestly.** Every append performs a full read of the goal-scoped log under the lock. This is **not** additive overhead: every guarded transition already requires the projection to evaluate its guard (a gate cannot pass without a matching receipt; a goal cannot satisfy with a pending required gate). The read is inherent to correctness, not to sequencing. It is O(n) per append and therefore O(n²) over a goal's lifetime. This is bounded by:

- `MAX_GATES_PER_GOAL = 64` and `MAX_ACTIVE_LEASES_PER_GOAL = 16`, which cap the structural growth rate.
- Measured gate G11 (§18.4), which specifies corpus, machine class, repetitions, and statistic rather than promising an absolute number.
- A deferred, gate-triggered projection cache (§20, D-16) that must be deletable with byte-identical output.

**Locked:** we do not claim this is cheap. We claim it is measured, capped, and has a defined escape hatch.

### 4.3 Record types (11)

Handoff records are **retained** (the prompt mandates handoff and a two-runtime handoff conformance test), but tightened per §10. Count:

**Declarations** (immutable; removable only by tombstone):
1. `goal_declared`
2. `gate_declared`
3. `evidence_receipt`
4. `run_assessment`
5. `handoff_declared`
6. `handoff_imported`

**Transitions** (validated against the transition table):
7. `goal_transition`
8. `gate_transition`
9. `handoff_transition`

**Lease events:**
10. `lease_grant`
11. `lease_release`

Normative count for validation: **11 record types**, enumerated exactly as above. (ARCHITECT's "9" and REDTEAM's "7" were different counting conventions; this is the enumeration that matters.)

### 4.4 Common record fields

Required on every record:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | int | forced to 1 by the store **[VF `store_core.py:157`]** |
| `execution_schema` | int | `1` — domain version, independent of the envelope |
| `record_type` | string | one of the 11 |
| `event_seq` | int ≥ 1 | allocated per §4.2 |
| `goal_id` | string | namespaced grammar, §4.5 |
| `emitted_at` | string | canonical UTC form of the caller's injected `now` |
| `operation_id` | string | idempotency key; §6.6 |
| `privacy` | enum | `public_safe` \| `local_private` \| `private_pointer`; default `local_private` |
| `authority_claim` | enum | `agent` \| `human` \| `system` — an **unverified claim** |
| `authority_verified` | bool | **always forced `false`**; caller-supplied `true` ⇒ hard error |

Optional: `execution_run_id`, `parent_execution_run_id`, `binding_digest`.

**Locked (D-17):** `authorization_evidence` is **not shipped in 3.3.0**. REDTEAM is right that an unvalidated security-shaped field is worse than no field: it invites callers to believe something was checked. It is tracked only as an evidence-gated 3.4 backlog candidate behind a real scheme registry (§20).

**`created_at` seam, stated explicitly.** The store fills `created_at` with `datetime.now(timezone.utc)` when absent **[VF `store_core.py:158-160`]**. This is envelope metadata, is excluded from every fingerprint and every digest (§5.5), and is never on a verdict path. It is a real impurity and conformance case `DT-04` pins that it cannot leak into determinism.

### 4.5 Identity and namespace grammar

REDTEAM's I-14 is correct: an unnamespaced `goal_id` collides the moment two runtimes share a base, which is the explicit runtime-neutral scenario.

**Locked grammar.**

| Identity | Grammar | Minted by | Meaning to core |
|---|---|---|---|
| `goal_id` | `^[a-z0-9][a-z0-9._-]{1,31}/[a-z0-9][a-z0-9._-]{1,63}$` — `<namespace>/<name>` | caller | opaque; the namespace is **not** validated against any registry |
| `gate_id` | `^[a-z0-9][a-z0-9._-]{2,79}$`, unique within a goal | caller | opaque |
| `execution_run_id` | `^[a-z0-9]{8,64}$` | **runtime** | opaque |
| `parent_execution_run_id` | same | runtime | opaque lineage pointer |
| `scope_key` | `^[a-z0-9][a-z0-9._/-]{0,79}$`; defaults to `gate_id` | caller | opaque exclusion key |
| `holder` | opaque string ≤ 256 | runtime | opaque **hint** with no integrity guarantee |
| `to_actor` | opaque string ≤ 256 | runtime | opaque |
| `lease_id` | `^[0-9a-f]{32}$` | core | internal |
| `handoff_id` | `^[0-9a-f]{32}$` | core | internal |
| `receipt_id` | envelope `id`, `^[0-9a-f]{32}$` | core | internal |
| `origin_instance_id` | uuid4 hex, `^[0-9a-f]{32}$` | core, once per base | opaque, **path-free** |
| `operation_id` | `^[0-9a-f]{64}$` or opaque ≤ 128 | caller or core | idempotency key |
| `fence_token` | positive integer, monotonic per goal | **core (output)** | ordering token |
| `binding_digest` | `^[0-9a-f]{64}$` | adapter | opaque; core supports **equality only** |

**`execution_run_id`, not `run_id`.** OpenClaw carries its own `runId` **[VF `/tmp/openclaw-reference/docs/plugins/hooks.md:209`]** in a different namespace. Sharing the name guarantees miswiring on first contact (REDTEAM I-15). Renamed.

**Namespace and actor claims are opaque audit identity, not authenticated identity.** This is normative language that must appear verbatim in `THREAT_MODEL.md`:

> A namespace in a `goal_id`, a `holder` string, a `to_actor` string, an `authority_claim`, and a `binding_digest` are **audit identity**: they record what a caller asserted. They are not authenticated identity. MemKraft does not verify them, cannot verify them, and no MemKraft behavior grants or withholds any capability on the basis of them. Any process with write access to the base can assert any of them.

**Lease-theft consequence, stated (REDTEAM §3).** Because `holder` is unverified and fencing compares integers only, a process that reads the log, takes `max+1`, and claims another agent's `holder` string can take a lease with full protocol compliance. On a single-host base with a single trusted OS user this is out of the threat model. It is documented, not mitigated.

**Runtime bindings are not core concepts.** `binding_digest` is computed by the adapter — recommended `sha256(mkcjson({runtime, session_ref, run_ref}))` — and core offers exactly one operation on it: equality. Core never stores `session_ref` itself, so an OpenClaw `sessionKey` (which may embed channel/guild identifiers) never enters a MemKraft base and never enters a handoff envelope. Core has no notion of session, turn, subagent, card, profile, board, lane, or cron.

### 4.6 State machines

Expressed as **one data structure**, `_TRANSITIONS: Dict[Tuple[str, str, str], _Rule]` keyed by `(entity_kind, from_state, to_state)`.

```
goal:    open     -> satisfied    guard: every required gate in {passed, waived}
         open     -> abandoned    guard: reason present
         (all other pairs)        REJECTED

gate:    pending  -> passed       guard: fresh pass receipt (§8.2)
         pending  -> failed       guard: fresh fail receipt (§8.2)
         pending  -> waived       guard: authority_claim == "human" (UNVERIFIED)
         passed   -> pending      guard: reopen_reason present
         failed   -> pending      guard: reopen_reason present
         failed   -> passed       guard: fresh pass receipt (§8.2)
         failed   -> waived       guard: authority_claim == "human" (UNVERIFIED)
         waived   -> (nothing)    ABSORBING; every outbound transition REJECTED
         (all other pairs)        REJECTED

handoff: offered  -> accepted     exactly once
         accepted -> completed
         (all other pairs)        REJECTED
         "expired" is NOT a state; it is (now > expires_at) computed at projection
         "completed" never expires

lease:   NO state machine. A lease is valid iff a lease_grant exists for
         (goal_id, scope_key), no later lease_release or superseding lease_grant
         applies, and now < expires_at. Reclaim is ONE lease_grant append carrying
         supersedes_lease_id and supersede_reason in {expired, released_by_holder}.
```

**Locked (D-18):** `supersede_reason: "revoked"` is removed. REDTEAM I-23 is correct that no operation produces it; a dead enum value invites someone to invent semantics for it.

**Exit criterion for the transition slice:** if this logic is expressed as branching instead of a single table, the slice does not pass. Asserted mechanically by an AST check that `execution_projection.py` contains no `if`/`elif` chain over `to_status` literals.

### 4.7 Projection: `skipped` vs `rejected_transitions`

Two counters, never merged.

- **`skipped`** — invalid JSON or non-dict line. IO-layer damage. Already counted by the store **[VF `store_core.py:194-201`]**. Does **not** set `consistent: false`. Surfaced as warning `W_LINES_SKIPPED`.
- **`rejected_transitions`** — a transition against an undeclared id, or a `(kind, from, to)` absent from `_TRANSITIONS`. Each carries a concrete reason string. **Sets `consistent: false`.**

`consistent: false` ⇒ every subsequent `apply` returns `E_PROJECTION_INCONSISTENT`, and `assess.run` returns exactly `repair` / `projection_inconsistent` with no other recommendation reachable. This is unconditional.

### 4.8 Gates are advisory bookkeeping — say it out loud

REDTEAM F-6 is correct and this document adopts it without softening.

`waived` requires only `authority_claim == "human"`, which is an unverified string; a satisfied goal accepts waived gates. Therefore **any caller can satisfy any goal by waiving every gate.** Combined with format-only validation of `content_sha256`, a caller can also manufacture a receipt.

The design response is not to pretend otherwise. It is:

1. **Say so verbatim** in the Python docstrings, the CLI `--help`, the `describe` output (`guarantees.gates_are_advisory: true`), and `THREAT_MODEL.md`:
   > Gates are advisory bookkeeping. `authority_claim` is not verified. A caller with write access can waive any gate or record any receipt. Gates make what happened *legible and attributable*; they do not make it *impossible*.
2. **Make abuse visible, not blocked.** The projection carries `unverified_waivers` and `receipts_without_provenance` counters; any assessment whose pass depends on a waiver carries `caveats: ["waiver_unverified"]`.
3. **Remove the waive path from the model-reachable surface entirely.** MCP is read-only in preview (§13). A model cannot waive.

This is the honest bound of a local, unauthenticated substrate, and it is stated in machine-readable form so an adapter that needs more can refuse to start.

### 4.9 Caps

| Cap | Value | Basis |
|---|---|---|
| `MAX_GATES_PER_GOAL` | 64 | arbitrary; chosen to cap DAG-shaped growth |
| `MAX_ACTIVE_LEASES_PER_GOAL` | 16 | arbitrary; unbounded fan-out *is* a work dispatcher |
| record serialized size | 16 KiB | bounds a single atomic line |
| request bytes | 256 KiB | |
| response bytes | 1 MiB | |
| handoff payload | 32768 B | |
| default string field | 512 | |
| `holder`, `to_actor` | 256 | |
| default list length | 32 | |
| JSON depth | 8 | |
| `ttl_seconds` | 1 … 86400 | |

**Locked:** `MAX_GATES_PER_GOAL` and `MAX_ACTIVE_LEASES_PER_GOAL` are **stated as arbitrary defaults** in the docs, not derived. Pretending they were derived would be false precision.

**Locked (D-19):** there is **no numeric cap on the public Python API method count.** ARCHITECT's "9 methods" cap was arithmetic fiction — it was satisfied by overloading `lease` into acquire/renew/release and `handoff` into declare/transition/export/import, pushing complexity into parameter modes where CI cannot see it (REDTEAM §2.1). The real constraint is the **closed operation registry** (§6.2): 15 named operations, additive-only, each with a hand-written adapter and a hand-written per-op key set. That is a semantic cap. It replaces the numeric one.

---

## 5. Canonical JSON and the digest contract

### 5.1 RFC 8785 is not implementable here — the honest statement

Full RFC 8785 (JCS) requires ECMAScript `Number::toString` serialization for all numbers. Python 3.9's stdlib `repr(float)` agrees with ES6 for most values but diverges on exponent formatting (Python `1e-07` vs JS `1e-7`). Reproducing ES6 number-to-string exactly requires a vendored implementation or a dependency, and MemKraft is stdlib-only.

**Therefore full RFC 8785 is rejected.** We ship **MKCJSON/1**, a strict subset of JCS chosen so the divergent cases cannot occur.

### 5.2 MKCJSON/1

1. **Top level** is a JSON object.
2. **Numbers: integers only**, in `[-(2^53 - 1), 2^53 - 1]`. Any float, exponent form, or `-0` is `E_TYPE`. Serialized as shortest decimal integer, no `+`, no leading zeros, `-0` normalized to `0`.
   *This single rule eliminates the entire RFC 8785 number problem.* Durations are integer seconds. There are no fractional quantities anywhere in the record set.
3. **Object keys** match `^[a-z][a-z0-9_]{0,63}$` (ASCII only), sorted ascending by byte value. Because keys are ASCII, Python's code-point sort, Go's byte sort, Rust's `str` sort, and JS's UTF-16 code-unit sort are all identical — this removes JCS's non-BMP key-ordering hazard.
4. **Strings** are valid Unicode scalar values, NFC-normalized by the producer. Lone surrogates are `E_TYPE`. Escaping is exactly: `"`→`\"`, `\`→`\\`, `\b \f \n \r \t` short forms, other `U+0000`–`U+001F` as `\u00xx` with lowercase hex, everything else literal UTF-8. This is byte-identical to Python `json.dumps(..., ensure_ascii=False)` and to `JSON.stringify`.
5. **Booleans/null**: `true`, `false`, `null`.
6. **Separators** `,` and `:`, no whitespace anywhere.
7. **Arrays** preserve order and are never sorted.
8. **Encoding** UTF-8, no BOM, no trailing newline inside the canonical form.
9. **Depth** ≤ 8, keys per object ≤ 64, array length ≤ 32.

Python 3.9 reference:

```python
def mkcjson(obj) -> bytes:
    return json.dumps(_check(obj), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
```

`_check` enforces rules 2–5 and 9 and raises the corresponding `E_*` code. `sort_keys=True` is correct **only because of rule 3**.

**NFC normalization mutates caller strings**, so `x != read(write(x))` for non-NFC input. This is the correct tradeoff for cross-runtime digest stability, and it must be documented in `V3_API.md`, not only here.

### 5.3 Cross-language claim

- **Node**: `JSON.stringify` over a key-sorted object with integer-only numbers produces identical bytes; escaping matches rule 4.
- **Go**: `encoding/json` with `SetEscapeHTML(false)` over pre-sorted keys; Go sorts map keys by byte order, matching rule 3; `json.Number` for integers.
- **Rust**: `serde_json` with `preserve_order` off (BTreeMap = byte order), `i64`.

This claim is not asserted in prose alone — conformance case `CJ-03` pins it with hex vectors, and `XR-01` requires a second-language runtime to reproduce them.

### 5.4 Digest

`digest(x) = hex(sha256(mkcjson(x)))`, lowercase, 64 characters.

**Critical:** digests are computed over `mkcjson(...)` of the *logical object*, never over the raw file line. The store writes with `separators=(",", ":")` and `ensure_ascii=False` but **without `sort_keys`** **[VF `store_core.py:162`]**, so on-disk bytes are not canonical bytes. Conformance case `CJ-06` asserts the digest is **not** equal to `sha256(file_line)`, so nobody later "optimizes" by hashing the line.

### 5.5 The six digests

| Name | Input | Purpose |
|---|---|---|
| `request_digest` | request object minus `request_id` | idempotency fingerprint basis |
| `response_digest` | response minus `response_digest` | transport integrity / truncation detection |
| `record_fingerprint` | stored record minus exactly `{id, created_at, event_seq}` | idempotency comparison |
| `payload_digest` | handoff `payload` | envelope self-consistency |
| `projection_digest` | projection result minus `{evaluated_at, digest}` | replay determinism |
| `inputs_digest` | assessment input projection | assessment purity |

**Normative:** `record_fingerprint` excludes **exactly** `{id, created_at, event_seq}` and nothing else. This exclusion set is load-bearing: without it every legitimate retry fails, because the store mints a fresh `id` and `created_at` on each append **[VF `store_core.py:156`, `:158-160`]**. Conformance case `ID-02` asserts that widening the set changes a golden digest, so silent widening fails CI.

### 5.6 Time profile (MKEP-TIME/1)

- Wire form: `YYYY-MM-DDTHH:MM:SS` + optional `.ffffff` (1–6 digits) + offset `Z` or `±HH:MM`.
- **Offset is mandatory.** Naive ⇒ `E_TIME_NAIVE`.
- No leap seconds (`:60` ⇒ `E_TIME_FORMAT`). Year in `[1970, 9999]`.
- **Canonical form**, used inside every stored record and every digest input: converted to UTC, `%Y-%m-%dT%H:%M:%SZ`, whole seconds, fractional part **truncated, not rounded**. Truncation is chosen because rounding can push an `expires_at` across a boundary.
- Python 3.9 note: `datetime.fromisoformat` does **not** accept `Z`. Implementations must substitute `Z` → `+00:00` before parsing. Conformance case `TM-03`.

**`now` rules:**

1. `now` is required for every `apply` and for every `now`-sensitive `query` (`state.read`, `assess.run`, `handoff.export`).
2. Core **never** validates `now` against the system clock. A `now` far in the past or future is accepted and produces a deterministic result. That is the point.
3. `now` need not be monotonic across requests. Ordering is `(event_seq, id)`, never by timestamp.

---

## 6. The command envelope and operation registry

### 6.1 One envelope, two kinds

There is exactly one request shape, discriminated by `kind`:

```json
{
  "mkep": "0",
  "kind": "apply",
  "request_id": "01JKX7Q2M0000000000000000A",
  "op": "gate.transition",
  "now": "2026-08-04T11:22:33Z",
  "target": { "goal_id": "hermes/release-3-3-0", "gate_id": "tests-green" },
  "args": { "to_status": "passed", "receipt_id": "3f2a…", "authority_claim": "agent" },
  "precondition": {
    "operation_id": "b91c…64hex",
    "expect_state": "pending",
    "fence_token": 7
  },
  "binding": {
    "execution_run_id": "a1b2c3d4e5f6",
    "parent_execution_run_id": null,
    "holder": "worker-3",
    "binding_digest": "9c…64hex"
  },
  "capabilities_digest": "4e…64hex"
}
```

```json
{ "mkep": "0", "kind": "query", "request_id": "…", "op": "state.read",
  "now": "2026-08-04T11:22:33Z", "target": { "goal_id": "hermes/release-3-3-0" },
  "args": { "include": ["gates", "leases", "handoffs"] } }
```

| Field | Required | Rule |
|---|---|---|
| `mkep` | yes | exactly `"0"`; otherwise `E_VERSION_UNSUPPORTED` |
| `kind` | yes | `apply` \| `query` |
| `request_id` | yes | ULID `^[0-9A-HJKMNP-TV-Z]{26}$` or `^[0-9a-f]{32}$`. Correlation only; **never** an idempotency key |
| `op` | yes | closed enum, §6.2 |
| `now` | yes for `apply` and `now`-sensitive `query` | §5.6 |
| `target` | yes | identity keys only; closed per-op key set |
| `args` | yes | closed per-op key set |
| `precondition` | `apply` only, required | §6.5 |
| `binding` | optional | §4.5; opaque to core |
| `capabilities_digest` | optional | mismatch ⇒ `E_CAPABILITY_DRIFT` |

**The envelope is closed.** Any top-level key outside this table ⇒ `E_UNKNOWN_FIELD`. There is no `extra`, `meta`, `passthrough`, or `raw` field, ever.

### 6.2 Operation registry — closed, 15 entries

| `op` | Kind | `target` keys | `args` (required / optional) |
|---|---|---|---|
| `goal.declare` | apply | `goal_id` | `title`, `intent`, `constraints[]`, `success_criteria[]` / `owner_hint`, `parent_goal_id`, `privacy` |
| `goal.transition` | apply | `goal_id` | `to_status`, `reason` / `authority_claim` |
| `gate.declare` | apply | `goal_id`, `gate_id` | `description`, `verification{check_kind,check_ref}` / `required`, `scope_key` |
| `gate.transition` | apply | `goal_id`, `gate_id` | `to_status` / `receipt_id`, `reopen_reason`, `authority_claim` |
| `receipt.record` | apply | `goal_id`, `gate_id` | `verdict`, `content_sha256`, `summary`, `observed_at` / `provenance_id`, `artifact_path` |
| `lease.acquire` | apply | `goal_id`, `scope_key` | `holder`, `ttl_seconds` / `expected_fence` |
| `lease.release` | apply | `goal_id`, `scope_key` | `lease_id` / `released_by` |
| `handoff.declare` | apply | `goal_id` | `to_actor`, `payload`, `payload_schema`, `expires_at` |
| `handoff.transition` | apply | `goal_id`, `handoff_id` | `to_state` |
| `handoff.import` | apply | `goal_id` | `envelope` |
| `assess.record` | apply | `goal_id` | `assessment` |
| `assess.run` | query | `goal_id` | *(none)* |
| `state.read` | query | `goal_id` | / `include[]` |
| `handoff.export` | query | `goal_id`, `handoff_id` | *(none)* |
| `describe` | query | *(empty)* | *(none)* |

That is 11 apply + 4 query = **15 rows**; the registry constant is `len(_OPS) == 15` and a conformance case asserts it.

**Notes.**
- `fence_token` never appears in `args`. It lives only in `precondition` (§7.3), which is what makes fencing unforgettable rather than per-op optional.
- `receipt_id` is not a `target` key on `receipt.record`: the receipt reuses the envelope `id`, so it is core-allocated and returned in `result.receipt_id`.
- `assess.run` is a **query** — it does not append. `assess.record` appends a previously computed assessment. This resolves REDTEAM I-16: a heartbeat-driven runtime can poll `assess.run` freely without unbounded log growth.

### 6.3 Why this is not a generic command bus

Four structural locks:

1. **Closed enum, additive-only.** Dispatch is a literal dict of 15 entries. There is no dynamic registration, no plugin hook, no `op` string that becomes an attribute lookup. Adding an operation requires a `capabilities_digest` change *and* a conformance fixture.
2. **Closed per-op key sets.** `target` and `args` keys are enumerated per operation. **No `**kwargs` forwarding** to kernel methods — each op has a hand-written adapter function. This is what stops "the bus grew a field."
3. **No expression surface.** `args` values are scalars, flat string lists, and one bounded `payload` object (handoff only). No selectors, no query language, no globs, no filters beyond `include` from a closed list.
4. **The wire is a projection, not the API.** The primary Python API is typed methods (§11). The envelope exists so that non-Python runtimes reach the same semantics. A Python caller never constructs an envelope.

### 6.4 Response

Exactly one shape, success or failure:

```json
{
  "mkep": "0",
  "request_id": "01JKX7Q2M0000000000000000A",
  "op": "gate.transition",
  "ok": true,
  "outcome": "applied",
  "result": { "record_id": "…", "event_seq": 42, "gate_status": "passed" },
  "state": { "execution_seq": 42, "projection_digest": "…64hex", "consistent": true },
  "warnings": [ { "code": "W_WAIVER_UNVERIFIED", "message": "…" } ],
  "request_digest": "…64hex",
  "response_digest": "…64hex"
}
```

```json
{ "mkep": "0", "request_id": "…", "op": "gate.transition", "ok": false,
  "error": { "code": "E_FENCE_STALE", "message": "fence 5 < current 7 for scope 'tests-green'",
             "class": "lease", "retryable": false,
             "details": { "scope_key": "tests-green", "current_fence_token": 7 } },
  "state": { "execution_seq": 41, "projection_digest": "…", "consistent": true },
  "request_digest": "…", "response_digest": "…" }
```

`outcome` ∈ `applied` | `already_applied` | `no_op` | `read`.

**Normative:** `error.message` must not contain an absolute filesystem path, `base_dir`, hostname, or username. `error.details` carries digests, counts, and key paths — **never values**, so that `local_private` content cannot leak into a log the runtime ships elsewhere. Conformance `SC-01`.

### 6.5 Preconditions

Required on every `apply`:

```json
"precondition": {
  "operation_id": "…64hex",          // required
  "expect_state": "pending",          // optional
  "expect_execution_seq": 41,         // optional
  "fence_token": 7                    // conditionally required, §7.3
}
```

- `operation_id` defaults to `request_digest` when omitted.
- `expect_state` mismatch ⇒ `E_PRECONDITION_STATE` with `details.actual`.
- `expect_execution_seq` is optimistic concurrency on the goal's projection version. Mismatch ⇒ `E_PRECONDITION_SEQ`.

This is the **only** optimistic-concurrency mechanism. No ETags, no version counters, no `if_match` variants.

### 6.6 Idempotency — exact rule

On `apply`, under the governance lock:

1. Scan for a prior record with the same `operation_id`.
2. **No match** → validate, allocate `event_seq`, append, `outcome: "applied"`.
3. **Match, `record_fingerprint` equal** → append nothing; `outcome: "already_applied"`; return the original record's `id` and `event_seq`.
4. **Match, fingerprint differs** → `E_IDEMPOTENCY_MISMATCH`; append nothing; **file line count unchanged**.

`E_IDEMPOTENCY_MISMATCH.details` must include `{"stored_fingerprint", "request_fingerprint", "differing_keys": ["ttl_seconds"]}`. `differing_keys` is the sorted key-path list. This is the difference between a debuggable protocol and a 3am incident.

**Normative:** `already_applied` must not re-run side effects and must not refresh a lease TTL. A renewal is a distinct `operation_id`.

### 6.7 Error registry

Stable, closed, additive-only. A code is never repurposed.

| Code | Class | Retryable |
|---|---|---|
| `E_MALFORMED_JSON` | input | no |
| `E_UNKNOWN_FIELD` | input | no |
| `E_MISSING_FIELD` | input | no |
| `E_TYPE` | input | no |
| `E_PATTERN` | input | no |
| `E_TIME_NAIVE` | input | no |
| `E_TIME_FORMAT` | input | no |
| `E_VERSION_UNSUPPORTED` | negotiation | no |
| `E_UNKNOWN_OP` | negotiation | no |
| `E_CAPABILITY_DRIFT` | negotiation | no |
| `E_LIMIT_EXCEEDED` | limits | no |
| `E_GATE_CAP` | limits | no |
| `E_NOT_DECLARED` | state | no |
| `E_ALREADY_DECLARED` | state | no |
| `E_INVALID_TRANSITION` | state | no |
| `E_PRECONDITION_STATE` | state | no |
| `E_PRECONDITION_SEQ` | state | no |
| `E_CONFLICT` | state | no |
| `E_HANDOFF_EXPIRED` | state | no |
| `E_PROJECTION_INCONSISTENT` | state | no |
| `E_EVIDENCE_REQUIRED` | evidence | no |
| `E_EVIDENCE_STALE` | evidence | no |
| `E_AUTHORITY_CLAIM_REQUIRED` | evidence | no |
| `E_AUTHORITY_VERIFIED_FORBIDDEN` | evidence | no |
| `E_IDEMPOTENCY_MISMATCH` | idempotency | no |
| `E_FENCE_REQUIRED` | lease | no |
| `E_FENCE_STALE` | lease | no |
| `E_LEASE_HELD` | lease | **yes** |
| `E_LEASE_CAP` | lease | no |
| `E_DIGEST_MISMATCH` | integrity | no |
| `E_STORE_BUSY` | io | **yes** |
| `E_STORE_IO` | io | **yes** |
| `E_INTERNAL` | io | **yes** |

Warnings: `W_WAIVER_UNVERIFIED`, `W_LINES_SKIPPED`, `W_TRUNCATED_RESPONSE`, `W_RECEIPT_UNPROVENANCED`.

---

## 7. Leases, fencing, and protected mutations

### 7.1 The first-acquisition hole, resolved

ARCHITECT made `fence_token` a **required input** to `lease` for pedagogical reasons. REDTEAM F-3 correctly showed this is incoherent: on a first acquisition the caller must supply a token it cannot know, for a lease it does not hold, and the staleness rule would reject any guess.

**Locked resolution: `fence_token` is an output.**

- `lease.acquire` **returns** `fence_token`. It takes an optional `expected_fence` for renew/supersede semantics.
- Every **lease-protected mutation** carries `precondition.fence_token`, the value returned by the acquire that authorized it.

This preserves everything the required-argument design was trying to teach — a lease is not a mutex, and holding one obliges you to present the token — while making the primary path definable.

### 7.2 Grant, renew, reclaim

Under the lock, after re-reading:

- **No valid lease on the scope** → grant. `fence_token = goal_max_fence + 1`. `supersedes_lease_id = null`.
- **Valid lease, same holder, same `operation_id`, identical params** → `already_applied`, no append, TTL unchanged.
- **Valid lease, same holder, new `operation_id`** → renew: one `lease_grant` line with a new `fence_token`, `supersedes_lease_id = <prev>`, `supersede_reason = "released_by_holder"`.
- **Valid lease, different holder** → `E_LEASE_HELD` (retryable). The error returns `holder_digest`, **never the raw `holder`** — leaking a runtime-minted holder string to a competing caller is an information disclosure. Conformance `SC-02`.
- **Expired lease** (`expires_at ≤ now`) → reclaim as **exactly one appended line** with `supersedes_lease_id` and `supersede_reason: "expired"`. There is no `expired` record type; expiry is a projection.
- **Active leases ≥ 16** → `E_LEASE_CAP`.

**Locked (D-20):** `write_scopes` is **removed**. Its interaction with `scope_key` exclusivity was never specified (REDTEAM §2.4), and it adds no expressiveness a caller cannot obtain by coarsening `scope_key`. Exclusivity is per `scope_key`, full stop.

### 7.3 Which mutations are fence-protected — the exact table

Every mutating operation has a **deterministically derived scope key**:

| `op` | derived `scope_key` | Fence-protected? |
|---|---|---|
| `gate.declare` | `args.scope_key` if given, else `target.gate_id` | **yes** |
| `gate.transition` | the gate's declared `scope_key` | **yes** |
| `receipt.record` | the gate's declared `scope_key` | **yes** |
| `goal.transition` | the literal `"goal"` | **yes** |
| `handoff.declare` | `"handoff"` | **yes** |
| `handoff.transition` | `"handoff:" + handoff_id` | **yes** |
| `handoff.import` | `"handoff"` | **yes** |
| `assess.record` | — | no (observation-only append) |
| `goal.declare` | — | no (once-only creation) |
| `lease.acquire` / `lease.release` | `target.scope_key` | special: `expected_fence`, §7.2 |

At apply time, under the lock, core projects the goal's leases at `now`:

1. **No valid lease covers the derived scope** → `precondition.fence_token` must be **absent**. Present ⇒ `E_UNKNOWN_FIELD`. (This prevents cargo-culting a token that means nothing.)
2. **A valid lease exists**:
   - `fence_token` absent ⇒ **`E_FENCE_REQUIRED`**, write refused.
   - `fence_token < goal_max_fence` ⇒ **`E_FENCE_STALE`**, write refused, line count unchanged.
   - otherwise proceed.

**The invariant this buys:** a stale holder cannot write anything into a scope that has been re-leased. Rule 1 combined with rule 2a is load-bearing — the fence becomes mandatory *whenever it could matter*, so forgetting it is a hard error rather than a silent bypass.

**The honest bound:** if nobody ever takes a lease, nothing is fenced. Fencing protects contested scopes, not all scopes. Requiring a lease before every mutation would force lease ceremony on single-agent use, which will simply be bypassed. This is decision D-05 in §20.

### 7.4 Locking must be non-blocking on hook-facing paths

**[VF]** `store_core` offers only blocking `LOCK_EX` (`store_core.py:63`, `:100`). REDTEAM F-4 is correct: a MemKraft write inside OpenClaw's `before_tool_call` — which **fails closed on timeout** **[VF `/tmp/openclaw-reference/docs/plugins/hooks.md:107-110`]** — turns a lock wait into a denied user tool call. Worse, a handler that timed out **is not cancelled** **[VF `:100-105`]** and may still hold the flock when the next hook fires, producing lock convoying invisible to the host.

**Locked (D-06, required upstream change):** add a bounded non-blocking acquisition path to `store_core`:

```python
def _lock_current_inode(path, *, timeout_s: Optional[float] = None): ...
```

implemented as `LOCK_EX | LOCK_NB` with bounded retry and jitter, raising `StoreBusy` on expiry. `StoreBusy` maps to `E_STORE_BUSY` (retryable). Default behavior (`timeout_s=None`) is unchanged blocking `LOCK_EX`, preserving every existing caller byte-for-byte. All MKEP apply paths pass an explicit `timeout_s`, defaulting to 2.0 s and overridable per request via the CLI `--lock-timeout` flag.

---

## 8. Evidence receipts and gate evaluation

### 8.1 Receipts are inert

`receipt.record` appends an `evidence_receipt` and **never** transitions a gate. Its response includes `"gate_status_unchanged": true`. Passing a gate is always a second, separate call. This makes "evidence exists" and "gate passed" independently auditable, which is what gate G3 measures.

Receipts are immutable; there is no update operation. `content_sha256` is validated for **format only** (`^[0-9a-f]{64}$`) — core never hashes anything to check it. A receipt recorded without a `provenance_id` increments `receipts_without_provenance` and emits `W_RECEIPT_UNPROVENANCED`. `artifact_path` is a reference; content is never inlined, and the field is `local_private` by default.

### 8.2 Snapshot binding — closing the temporal hole

REDTEAM I-9 identified a real hole: if `pending → passed` requires only that a pass receipt *exists in the log*, then a gate that was passed with receipt R, reopened to `pending`, can be re-passed by pointing at the **same stale receipt R**. Reopening is defeated.

**Locked resolution: receipts bind to a snapshot, and gates evaluate against a freshness watermark with no temporal hole.**

1. Every `evidence_receipt` records `observed_seq` = the `event_seq` of the highest record in the goal's log **at the moment the lock was acquired for the receipt's own append**. Because allocation and append happen inside one lock region (§4.2), `observed_seq` is exactly `event_seq - 1` for that receipt. It is stored explicitly rather than derived so that the binding survives any future change to allocation.
2. Every gate's projection carries `reopened_at_seq` = the `event_seq` of the most recent transition **into** `pending` for that gate, or `0` if the gate has never been reopened.
3. A `gate.transition` into `passed` or `failed` is guarded by: there exists an `evidence_receipt` for this `gate_id` with the matching `verdict` **and** `receipt.event_seq > gate.reopened_at_seq`.
4. If the only matching receipt predates the reopen ⇒ **`E_EVIDENCE_STALE`**, with `details = {receipt_event_seq, reopened_at_seq}`.
5. If `receipt_id` is supplied explicitly in `args`, it must satisfy the same freshness condition; a stale explicit `receipt_id` is `E_EVIDENCE_STALE`, not silently replaced by a fresher one.

**Why there is no temporal hole.** The comparison is between two `event_seq` values allocated under the same lock on the same append-only, never-compacted log. It does not involve wall-clock time, does not involve two files, and does not involve any value the caller can supply. There is no window in which a receipt could be appended, observed as fresh, and then have the watermark move underneath it: the reopen that would move the watermark is itself an append that takes the same lock and receives a strictly higher `event_seq`. A receipt appended before a reopen has a strictly lower seq; a receipt appended after has a strictly higher one. The order is total and locally observable.

### 8.3 Gate invariants

| ID | Invariant | Error |
|---|---|---|
| I-E1 | `pending → passed` requires a **fresh** `verdict="pass"` receipt for that gate | `E_EVIDENCE_REQUIRED` / `E_EVIDENCE_STALE` |
| I-E2 | `pending → failed` requires a **fresh** `verdict="fail"` receipt | same |
| I-E3 | `passed → pending` and `failed → pending` require `reopen_reason`; `waived → *` is forbidden | `E_MISSING_FIELD` / `E_INVALID_TRANSITION` |
| I-E4 | `failed → passed` requires a fresh pass receipt **and** a `reopen_reason` — a failed gate cannot be flipped silently | `E_MISSING_FIELD` |
| I-E5 | `waived` requires `authority_claim == "human"` (unverified) and increments `unverified_waivers` | `E_AUTHORITY_CLAIM_REQUIRED` |
| I-E6 | A receipt for an undeclared `gate_id` is rejected | `E_NOT_DECLARED` |
| I-E7 | `open → satisfied` requires every `required=true` gate in `{passed, waived}`; the error lists blockers | `E_INVALID_TRANSITION` |
| I-E8 | More than 64 gates per goal | `E_GATE_CAP` |

I-E4 is an addition beyond both input analyses, closing the `failed → passed` inconsistency REDTEAM flagged in §2.5.

---

## 9. Advisory assessment

### 9.1 Split read and write

**Locked:** `assess.run` is a **query** that computes and returns a recommendation without appending. `assess.record` is an `apply` that persists a supplied assessment.

This resolves REDTEAM I-16 (heartbeat polling causing unbounded log growth) and removes ARCHITECT's implicit "every dry run pollutes the log."

`assess.record` validates that the supplied `assessment` object is internally consistent (allowed pair, `advisory: true`, `inputs_digest` present) but does **not** re-verify that it matches the current projection — it is a record of what a runtime concluded at some `now`, which is exactly what an audit log should hold. `inputs_digest` makes after-the-fact verification possible.

### 9.2 Allowed pairs

Anything outside this table is rejected with `E_PATTERN`.

| `recommendation` | allowed `reason_code` |
|---|---|
| `should_run` | `gates_open`, `lease_acquired` |
| `wait` | `lease_held_by_other`, `blocking_gate_pending`, `cooldown_not_elapsed` |
| `ask_human` | `waiver_required`, `constraint_conflict`, `evidence_inconclusive` |
| `stop` | `goal_satisfied`, `goal_abandoned`, `quota_exhausted`, `max_runs_exceeded` |
| `repair` | `stale_lease_detected`, `projection_inconsistent`, `handoff_incomplete`, `phases_incomplete` |

### 9.3 Invariants

- **I-D1 purity.** Same log bytes + same injected `now` ⇒ same `inputs_digest` and same `recommendation`. No wall clock, no randomness, no environment read.
- **I-D2 inconsistency dominates.** `consistent == false` ⇒ `repair` / `projection_inconsistent`, unconditionally, with no other recommendation reachable.
- **I-D3 waiver caveat.** Any recommendation whose evaluation depended on a waived gate carries `caveats: ["waiver_unverified"]`.
- **I-D4 observation only.** An assessment mutates nothing. Conformance `AU-01` asserts every gate is byte-identical before and after.
- **I-D5 no timing.** `wait` never says when. There is no `next_check_at`, and requests to add one are refused citing §1.3.

### 9.4 Explicit no-authorization language

This text is normative and must appear verbatim in the Python docstring of `assess_run`, in `memkraft exec --help`, in the MCP tool description, and in `THREAT_MODEL.md`:

> `assess_run` returns an **advisory recommendation**. It is not an authorization, a permission, a grant, or an approval. MemKraft does not execute anything and does not authorize anything. A `should_run` recommendation carries no more force than a comment. Safety rests entirely with the caller that acquires a lease and presents its fence token before performing a side effect. A caller that treats `should_run` as permission has no protection against concurrent execution, and MemKraft cannot provide any.

Conformance case `AU-02` scans every response schema and every response fixture for keys or enum values matching `allow|permit|authoriz|granted|approved|permission` and fails on any hit, and asserts `advisory: true` is present on every assessment.

### 9.5 The cadence question, answered so it stays answered

An operator will observe that a cron tick returning `wait` 99% of the time is wasteful and will ask for `next_check_at`. The answer is no, and the reason is written here so it does not get re-litigated:

`reason_code` already carries everything a runtime needs to pick its own interval. `blocking_gate_pending` means poll on gate-change events, not on a timer. `lease_held_by_other` means poll at roughly the lease TTL, which the runtime knows because it can read `expires_at` from `state.read`. `cooldown_not_elapsed` is a runtime-owned concept that MemKraft does not model at all. In every case the runtime has strictly more information than MemKraft does about what it can afford. Adding a time field would move a runtime policy decision into a substrate that has no basis for making it, and would be the first brick in a scheduler.

---

## 10. Handoff: export, import, isolation

### 10.1 Privacy model, resolved

REDTEAM I-10 is correct that a **record-level** `privacy` field cannot drive **field-level** export filtering, and that having both a privacy tag and a regex as "the only basis" is incoherent.

**Locked resolution: core does not scrape records to build an export.**

The `payload` is **supplied by the caller** at `handoff.declare`. The caller decides what is safe to share; core stores that payload verbatim, tags the `handoff_declared` record `public_safe`, and exports exactly that payload and nothing else. There is no field-level privacy machinery because there is nothing to filter — core never assembles an envelope from private state.

The regex scan is retained but **reclassified**: it is a **lint that fails closed**, not a security control. `THREAT_MODEL.md` must say so in exactly these words:

> The export redaction scan is a lint. It catches obvious mistakes — absolute paths, common secret prefixes — and refuses the export when it fires. It is not a security control, it does not detect novel secret formats, and it must not be relied upon to make an untrusted payload safe. Deciding what is safe to share is the caller's responsibility, discharged at `handoff.declare` time.

Scan patterns (export fails closed with `E_PATTERN` and the **rule name**, never the matched text):

- absolute POSIX path: `^/` or `(?<![\w-])/(?:Users|home|var|tmp|opt|etc)/`
- Windows path: `[A-Za-z]:\\`
- the literal `str(self.base_dir)` or any ancestor
- the current `$USER` / `os.getlogin()` value, when non-empty
- `sk-[A-Za-z0-9]{16,}`, `ghp_[A-Za-z0-9]{20,}`, `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`, `xox[baprs]-[A-Za-z0-9-]{10,}`

**Fail closed, do not substitute `[redacted]`.** Silent redaction produces an envelope that looks complete but has had semantics quietly removed, and the exporter never learns. Failing loudly forces the caller to fix the source.

### 10.2 Envelope

```json
{
  "envelope_schema": "memkraft.handoff/1",
  "origin_instance_id": "7c9e6679742548c9a3b1f0c9d5a7e42c",
  "goal_id": "hermes/migrate-billing-schema",
  "handoff_id": "aa0e5f6c1d9b309f2c1a44b7e34d1288",
  "payload_schema": "memkraft.handoff.context/1",
  "payload": {
    "summary": "v3 backup verified; staging suite still red on 2 billing tests.",
    "open_gates": ["staging-suite-green"],
    "next_intent": "Re-run the staging suite after the v4 view fix lands."
  },
  "payload_digest": "e3c4b5d6…64hex",
  "expires_at": "2026-08-05T00:00:00Z",
  "exported_at": "2026-08-04T10:05:00Z",
  "envelope_digest": "9f2c1a44…64hex"
}
```

`payload_schema` is validated only as an opaque string matching `^[a-z0-9][a-z0-9._-]{0,63}/[0-9]{1,3}$`. There is **no registry** of known payload schemas — a registry forces a core release for every adapter payload change.

`origin_instance_id` is a random uuid4 written once to `.memkraft/origin_instance_id`. It is **never** derived from `base_dir`, hostname, username, or any path.

### 10.3 The linkability tradeoff, documented

REDTEAM I-12 is correct and this is not mitigated in 3.3.0. `origin_instance_id` is a stable identifier embedded in every envelope this base ever exports. Every recipient of any handoff learns it and can join across handoffs. Path leakage is solved; **linkability is introduced**.

`THREAT_MODEL.md` must state:

> `origin_instance_id` is a stable, per-base random identifier present in every exported envelope. It contains no path, hostname, or username, but it is a persistent correlator: any party that receives two envelopes from the same base can link them, and parties that collude can join across recipients. If unlinkability across recipients is required, do not use handoff export in 3.3.0.

Per-recipient derived identifiers (HMAC of a base secret with a recipient label) are Deferred (§20, D-21).

### 10.4 Import rules

1. `handoff.import` accepts an **envelope object supplied by the caller** and nothing else. There is no path, base_dir, profile, or URL parameter in the operation's key set, in the Python signature, or in the CLI. **The API surface makes a cross-base read inexpressible.**
2. Verify `envelope_digest`, then `payload_digest` against the actual `payload` ⇒ `E_DIGEST_MISMATCH` on failure.
3. The triple `(origin_instance_id, handoff_id, payload_digest)` already present ⇒ `outcome: "already_applied"`, no append.
4. Same `(origin_instance_id, handoff_id)` with a **different** `payload_digest` ⇒ `E_CONFLICT`. This is the tamper/fork signal and must not be silently accepted.
5. `expires_at ≤ now` and state ≠ `completed` ⇒ `E_HANDOFF_EXPIRED`.
6. A new local `handoff_id` is allocated; `imported_from` records the origin triple.

**Idempotency holds only against honest senders.** REDTEAM I-13 is correct: `origin_instance_id` is self-asserted, so a party that regenerates it defeats the triple-key dedupe. `THREAT_MODEL.md` states this. There is no authenticity guarantee: `payload_digest` proves self-consistency, not provenance. Envelope signing is Deferred (§20, D-22).

### 10.5 Partial failure

Origin and copy are **independent state machines**.

- Export succeeded, delivery failed → origin stays `offered`. Re-export is byte-identical (same `envelope_digest`). Delivery is runtime-owned.
- Import succeeded, ack lost → re-import returns `already_applied`.
- A remote `accepted` does **not** change the origin. The origin reaches `completed` only when a reverse confirmation envelope is imported at the origin — which is an ordinary `handoff.declare` + export in the reverse direction, using the existing record types. There is no special confirmation record type (closing REDTEAM's I-6 gap).
- Crash after `accepted` → the next `assess.run` yields `repair` / `handoff_incomplete`. A second accept with a **different** `operation_id` is `E_CONFLICT`; with the **same** `operation_id` it is `already_applied`. This distinction is what makes hook re-fire safe without weakening the invariant.

There is no distributed transaction, no two-phase commit, and no automatic reconciliation.

---

## 11. Python API (primary surface)

### 11.1 Shape

Typed methods on a new `ExecutionStateMixin`. **The generic envelope is not the Python API.** Typed methods project into the single closed wire dispatcher; a Python caller never builds an envelope.

```python
# src/memkraft/execution_state.py
from __future__ import annotations

class ExecutionStateMixin:

    def goal_declare(self, goal_id, title, intent, constraints, success_criteria, *,
                     now, owner_hint=None, parent_goal_id=None,
                     privacy="local_private", authority_claim="agent",
                     execution_run_id=None, operation_id=None) -> Dict[str, Any]: ...

    def goal_transition(self, goal_id, to_status, *, reason, now,
                        fence_token=None, authority_claim="agent",
                        operation_id=None) -> Dict[str, Any]: ...

    def gate_declare(self, goal_id, gate_id, description, verification, *,
                     now, required=True, scope_key=None, fence_token=None,
                     privacy="local_private", operation_id=None) -> Dict[str, Any]: ...

    def gate_transition(self, goal_id, gate_id, to_status, *, now,
                        fence_token=None, receipt_id=None, reopen_reason=None,
                        authority_claim="agent", operation_id=None) -> Dict[str, Any]: ...

    def receipt_record(self, goal_id, gate_id, verdict, content_sha256, summary, *,
                       now, fence_token=None, observed_at=None, provenance_id=None,
                       artifact_path=None, execution_run_id=None,
                       privacy="local_private", operation_id=None) -> Dict[str, Any]: ...

    def lease_acquire(self, goal_id, scope_key, holder, ttl_seconds, *,
                      now, expected_fence=None, operation_id=None) -> Dict[str, Any]: ...

    def lease_release(self, goal_id, scope_key, lease_id, *,
                      now, released_by=None, operation_id=None) -> Dict[str, Any]: ...

    def assess_run(self, goal_id, *, now) -> Dict[str, Any]: ...          # pure, no append

    def assess_record(self, goal_id, assessment, *, now,
                      operation_id=None) -> Dict[str, Any]: ...

    def handoff_declare(self, goal_id, to_actor, payload, *, now,
                        payload_schema="memkraft.handoff.context/1",
                        expires_at=None, fence_token=None,
                        operation_id=None) -> Dict[str, Any]: ...

    def handoff_transition(self, goal_id, handoff_id, to_state, *, now,
                           fence_token=None, operation_id=None) -> Dict[str, Any]: ...

    def handoff_export(self, goal_id, handoff_id, *, now) -> Dict[str, Any]: ...

    def handoff_import(self, goal_id, envelope, *, now,
                       fence_token=None, operation_id=None) -> Dict[str, Any]: ...

    def execution_state(self, goal_id, *, now,
                        include=None) -> Dict[str, Any]: ...

    def execution_describe(self) -> Dict[str, Any]: ...
```

Fifteen methods, one per registry entry, one-to-one. No overloaded `action=` parameter anywhere. **This is the point of dropping the numeric cap**: the surface is now honest, and CI enforces the one-to-one mapping (`test_execution_surface.py` asserts `set(_OPS) == set(_PUBLIC_METHODS)` modulo naming convention) rather than a count that overloading could satisfy.

### 11.2 Signature rules

1. `now` is **keyword-only and required** on every method except `execution_describe`. There is no default and there is no wall-clock fallback.
2. `handoff_import` accepts `envelope` as a dict. It must never accept a path parameter, in any release.
3. Every method returns a dict; none returns `None`.
4. Additive compatibility: new parameters in later 3.x releases must be keyword-only with a default reproducing prior behavior byte-for-byte. No positional parameter may be inserted, reordered, or removed within 3.x.
5. Python ≥ 3.9 only: `from __future__ import annotations` at the top of every new module; no `match`, no runtime `X | Y`, no `tomllib`.

### 11.3 Errors

```python
class ExecutionError(ValueError):
    code: str
    error_class: str          # input|negotiation|limits|state|evidence|idempotency|lease|integrity|io
    retryable: bool
    details: Dict[str, Any]

class ValidationError(ExecutionError): ...
class TransitionError(ExecutionError): ...
class EvidenceError(ExecutionError): ...
class ConflictError(ExecutionError): ...
class FenceError(ConflictError): ...
class NotDeclaredError(ExecutionError): ...
class InconsistentStateError(ExecutionError): ...
class StoreBusyError(ExecutionError): ...
```

All inherit `ValueError`, preserving the existing broad-catch contract. `code` is the same stable string the wire returns, so a Python caller and a CLI caller branch on identical values.

### 11.4 Mixin registration

**[VF]** Mixins are flattened by `setattr` with last-write-wins and additive-only enforcement for exactly two designated mixins (`__init__.py:74`, `:116-126`). REDTEAM I-25 is correct.

**Locked:** `ExecutionStateMixin` is added to `_ADDITIVE_ONLY_MIXINS`, and a test asserts that no name it contributes already exists on the composed class. Silent shadowing of an existing method is a release blocker.

---

## 12. CLI transport

### 12.1 Two subcommands, full lifecycle

```
memkraft exec call    # one MKEP request on stdin -> one MKEP response on stdout
memkraft exec state <goal_id> [--json]
```

`memkraft exec call` is a **transport**, not an operation. Lifecycle completeness lives in the `op` field of the JSON body, which is versioned and capability-discoverable. The discoverable `--help` surface grows by two lines while the machine-facing surface is complete.

This resolves REDTEAM F-2 directly: with a read-only CLI, the runtime-neutral thesis is unimplemented, because the OpenClaw adapter — which cannot be assumed to speak MCP — would have no write path at all.

### 12.2 Contract

```
$ printf '%s' '{"mkep":"0","kind":"query","request_id":"…","op":"state.read",
  "now":"2026-08-04T09:42:10Z","target":{"goal_id":"hermes/migrate-billing"}}' \
  | memkraft exec call --base-dir ./memory
```

1. Exactly one JSON object in, exactly one JSON object out. `--jsonl` reads one request per line and writes one response per line, **in order, never batching or reordering**.
2. **stdout carries only the response JSON.** No banners, no progress, no color. This requires suppressing the `init()` verbose banner **[VF `core.py:143`]** on this path and routing `_require_mcp`-style diagnostics **[VF `mcp.py:39-40`]** to stderr. Conformance `CL-01` asserts stdout parses as JSON for **every** fixture including every error.
3. stderr is human diagnostics only, never machine-parsed, empty on success.
4. Exit codes:

| Code | Condition |
|---|---|
| 0 | `ok: true` |
| 1 | `ok: false`, `error.class` ∈ {input, negotiation, limits} — the request is wrong |
| 2 | `ok: false`, `error.class` ∈ {state, evidence, idempotency, lease, integrity} — well-formed, the world says no |
| 3 | `ok: false`, `error.class` = io — retryable |
| 64 | CLI usage error (bad flag); **no JSON on stdout** |
| 70 | internal crash before a response could be formed; **no JSON on stdout** |

The 1/2/3 split lets a shell caller branch on "fix your call" / "state conflict" / "try again" without parsing JSON. **Guarantee:** for any exit code in {0,1,2,3}, stdout contains exactly one valid response envelope.

5. `--base-dir` selects the base; otherwise `$MEMKRAFT_DIR`, otherwise `./memory` **[VF `core.py:115-118`]**. Adapters **must** pass `--base-dir` explicitly, because a subprocess inherits the host's cwd, which is not the workspace — silently writing the wrong base is otherwise the default failure.
6. `--lock-timeout <seconds>` bounds lock acquisition (§7.4), default 2.0.
7. No environment variable may alter protocol semantics. `$MEMKRAFT_DIR` selects a base and nothing else.
8. `op: "describe"` must succeed on an uninitialized base and **create zero files**, so capability probing is safe.

### 12.3 Cold start

`import memkraft` flattens the full mixin set at import time and may touch SQLite. Cold start is therefore a first-class cost, measured by gate G12 (§18.4) and structurally mitigated in §15.3.

### 12.4 Context compiler integration

`compile_context(..., goal_id=None, execution_budget=0)`.

**Normative:** an `execution` section is produced **only if** `goal_id is not None` **and** `execution_budget > 0`. Section order is unchanged; `execution` is appended last. The other sections receive `budget - execution_budget` — a reduction the caller passed in and can see. No silent encroachment.

**Normative (release-blocking):** when no `execution` section is produced, the output must be **byte-identical** to the 3.2.x baseline, including the identity dict fed to the `usage_id` hash. A `context_schema: 1` key may be added to the *result* dict but **must not** enter the identity dict.

**Documented aliasing (REDTEAM N-26):** because `context_schema` is deliberately excluded from the identity dict to protect the golden `usage_id`, two callers on different `context_schema` versions produce the **same `usage_id`** for different payloads. This is the correct tradeoff, and outcome attribution will silently merge across schema versions. It is written down in `V3_API.md`, not left implicit.

Execution section content: `pending && required` gates only, at most 8, ≤ 400 tokens per goal.

---

## 13. MCP projection — read-only in preview

### 13.1 Two tools, no mutation (four closed query operations)

```
memkraft_execution_query      # state.read | assess.run | handoff.export
memkraft_execution_describe   # capabilities
```

Both `readOnlyHint: true`. The four existing tools **[VF `mcp.py:43-94`]** are unchanged.

### 13.2 Why MCP mutation is not enabled in preview

Three independent reasons, any one sufficient:

1. **`receipt_record` over MCP is model self-attestation.** `content_sha256` is format-checked only; core never hashes anything. A model that wants a gate to pass calls `receipt_record(verdict="pass", content_sha256=<any 64 hex>, summary="looks good")`. Combined with the waive path (§4.8), that is two independent routes for a model to satisfy any goal. Receipts must be written by an adapter from an *observed* tool result, not asserted by the model that wanted the result.
2. **The MCP server has one process-global `MemKraft()` with no `base_dir`** **[VF `mcp.py:137`]**. Every MCP client shares one base regardless of profile. Enabling mutation there would defeat per-base isolation at exactly the layer where isolation matters most.
3. **`authority_claim` arriving over MCP is a model-supplied string.** A model can claim `"human"` and waive gates.

`state.read` and `assess.run` are safe to expose because they are pure. `assess.run` is safe **only because it is now a query** (§9.1) — exposing ARCHITECT's appending version would have been a write dressed as a read, which is a misuse magnet.

MCP mutation is Deferred (§20, D-23) behind per-client base selection and adapter-mediated receipt provenance.

### 13.3 Encoding — fixing the `str(result)` defect

**[VF]** The current handler returns `types.TextContent(text=str(result))` (`mcp.py:155`) and errors as `f"error: {e}"` (`mcp.py:157`). Both are defects: `str(dict)` emits Python repr with single quotes — not JSON, so no client can parse it — and the error path erases the exception type entirely, so a model cannot distinguish a retryable conflict from a permanent rejection and will retry invalid requests in a loop.

**Normative:**

```python
payload = dispatch_mkep(mk, request)          # always a dict; never raises past here
text = mkcjson(payload).decode("utf-8")
return types.CallToolResult(
    content=[types.TextContent(type="text", text=text)],
    structuredContent=payload,
    isError=not payload["ok"],
)
```

1. `structuredContent` is the response object; the `TextContent` mirror is its MKCJSON bytes. `response_digest` must validate against the text.
2. **Never `str()`, `repr()`, or f-string interpolation of a result.** A lint case greps the MCP module for `str(result)` and `f"error:` and fails CI.
3. Protocol errors are `ok: false` responses with `isError: true`, not thrown exceptions. Only transport failures raise.
4. The MCP layer performs **no** validation, defaulting, or coercion of its own; it is a byte-faithful conduit to the same `dispatch_mkep` the CLI uses. Conformance `MC-01` asserts CLI and MCP produce identical `response_digest` for identical requests.
5. **`now` on MCP.** MCP tool calls carry no caller clock. The MCP layer — not any `execution_*` module — reads `datetime.now(timezone.utc)` and injects it. This is the single sanctioned wall-clock read outside `store_core`, it lives in `mcp.py`, and it is documented as such. Adding a `now` parameter to the tool schema is rejected: a model supplying its own timestamp is a correctness and audit hazard.
6. Tool descriptions must contain the §9.4 no-authorization sentence verbatim. The advisory framing enforced in Python docstrings evaporates at the MCP boundary otherwise.

---

## 14. Hermes adapter

Implemented in the Hermes repository, in-process Python, using the typed API (not the CLI — it shares a process and language).

### 14.1 Mapping

| Hermes concept | MKEP concept | Direction |
|---|---|---|
| Kanban card | stores a `goal_id` string | **Kanban → MemKraft, one-way only** |
| Card move | nothing — cards and goals are independent | — |
| Cron tick | a call to `assess_run` (pure); the tick is invisible to core | Hermes → MemKraft |
| Agent session | `execution_run_id`, `holder` | Hermes → MemKraft |
| Profile | **not represented**; realized as `base_dir` selection | Hermes → filesystem |
| Human approval in the Hermes UI | `authority_claim: "human"` | Hermes → MemKraft, **unverified** |

**Cardinality is locked: one card ↔ one goal.** The card stores `goal_id`; `goal_id` is not derived from the card id, so a card can be recreated without orphaning its goal. The namespace segment is `hermes/`.

### 14.2 Hook points

| Hermes event | Call | Semantics |
|---|---|---|
| card created with a goal intent | `goal_declare` + N × `gate_declare` | fail card creation if declare fails |
| cron tick on an active card | `assess_run` | pure read; act on `recommendation`; append via `assess_record` **only** when the recommendation changes |
| before dispatching an agent | `lease_acquire`; abort dispatch on `E_LEASE_HELD` | **authoritative** — this is where the fence is enforced |
| agent produces a verifiable artifact | `receipt_record` with `fence_token` | |
| verification concludes | `gate_transition` with `receipt_id` and `fence_token` | |
| operator clicks "waive" | `gate_transition` to `waived`, `authority_claim: "human"` | recorded as an **unverified claim**; the UI must say so |
| card closed | `goal_transition` to `satisfied` or `abandoned` | `satisfied` fails with a pending required gate — surface that error, do not swallow it |
| card deleted | explicit `forget({"goal_id": ...})` | see §14.4 |

### 14.3 Divergence, honestly

REDTEAM is right that "one-way reference" prevents write loops but not divergence: a card in `done` with a goal in `open` is still two truths. The adapter contract therefore requires:

- The card **must not** store a mirrored copy of goal status. It may render a projection fetched at display time, labelled as fetched.
- A reconciliation view lists cards whose goal status disagrees with the card column. Reconciliation is a human-facing report, not an automatic write.

`assess_run` returning `should_run` does **not** authorize dispatch. Hermes must still acquire the lease and must pass the returned `fence_token` to the dispatched agent, which must present it on every fence-protected call.

### 14.4 Goal lifetime

Deleting a card does not delete the goal. `forget({"goal_id": ...})` is explicit. Note the cost: `mark_tombstone` performs a full `read_all(include_tombstoned=True)` per call **[VF `store_core.py:228-230`]**, so tombstoning a goal record-by-record is O(n²). **Locked (D-24):** ship a batch tombstone path for `forget` that reads once and appends N tombstone lines under a single lock acquisition, or goal deletion is unusable at scale. This is in Slice 7.

---

## 15. OpenClaw adapter

### 15.1 Transport

OpenClaw's plugin runtime is TypeScript/Node **[VF `package.json:22-24`]** and MCP client support is **not established** by the pack. Therefore: the adapter is an OpenClaw plugin that invokes `memkraft exec call` as a subprocess.

**Normative invocation rules:**

- **No shell-string invocation, ever.** Use `execFile`/`spawn` with an argv array: `["memkraft", "exec", "call", "--base-dir", base, "--lock-timeout", "1.5"]`.
- Request JSON goes on **stdin**; response JSON is read from **stdout**; stderr is logged and never parsed.
- A bounded subprocess timeout, strictly below the enclosing hook budget.
- Exit codes 0–3 mean a response is on stdout and must be parsed. 64/70 mean no response; treat as `E_INTERNAL`.

### 15.2 Hook classification — authoritative vs observational

**[VF `/tmp/openclaw-reference/docs/plugins/hooks.md:133-135`]** Bolded hooks accept a decision result; the rest are observation-only.

| Hook | Class | Adapter use |
|---|---|---|
| `before_prompt_build` | contributory, not decision-capable (`:143`) | inject the goal projection into the prompt. **Not** an enforcement point. |
| `before_agent_run` | **authoritative** (`:144`) | `assess.run`; block on `stop` or on `E_PROJECTION_INCONSISTENT` |
| `before_tool_call` | **authoritative**, 15 s default, **fails closed** (`:165`, `:107-110`) | `lease.acquire` for write-class tools; block on `E_LEASE_HELD` |
| `after_tool_call` | **observation** (`:166`) | `receipt.record` |
| `agent_end` | **observation** (`:147`) | final receipt, `lease.release`, `assess.record` |
| `session_start` / `session_end` | **observation** (`:191`) | mint `execution_run_id`; see §15.5 |
| `subagent_spawned` / `subagent_ended` | **observation** (`:205`) | lineage; correlate via `targetSessionKey` (`:209`) |
| `gateway_start` / `gateway_stop` | **observation** (`:215`) | `describe` probe on start; **no durable cleanup on stop** |

**Non-authoritative observation hooks are marked as such and must never be used for enforcement.** An observation handler's return value is ignored, and observation handlers **run in parallel and may overlap later events** **[VF `:52-56`]**, so any enforcement written there is both ineffective and racy. The docs state explicitly: *"Do not use priority to order observation side effects"* **[VF `:55-56`]**.

`subagent_spawning` is deprecated **[VF `:207`]** and must not be used.

### 15.3 Timeout semantics and what they force

Three verified facts drive everything:

1. A timed-out handler **is not cancelled**; it keeps running and its side effects continue **[VF `:100-105`]**.
2. `before_tool_call` **fails closed** on timeout — the tool call is rejected **[VF `:107-110`]**.
3. `session_end` shares a **2-second total drain budget across all sessions and all handlers** **[VF `:195-199`]**.

Consequences, all normative:

- **C1 — Every write reachable from a timing-out hook must be idempotent, because a timed-out call may still land.** The adapter supplies a deterministic `operation_id` derived from `(sessionKey, hookName, toolName, toolCallId)`. On the next attempt the same `operation_id` returns `already_applied` instead of a second grant. Without this, one disk stall produces two leases for one tool call.
- **C2 — Budget the subprocess strictly inside the hook budget.** With a 15 s default, set the subprocess timeout to ~4 s and `--lock-timeout 1.5`, and return a decision on the adapter's own timer. Never let the hook budget be the thing that expires: that outcome is fail-closed *and* leaves the write in flight.
- **C3 — Never block on the lock from a fail-closed hook.** `--lock-timeout` is mandatory on every hook-originated call. `E_STORE_BUSY` is handled by the adapter, not by waiting.
- **C4 — Fail closed on protocol error from `before_tool_call`.** For a substrate whose purpose is knowing what is settled, under-blocking is worse than over-blocking.
- **C5 — `E_LEASE_HELD` maps to a block, not an approval prompt.** A lease conflict is a machine fact, not a human question; escalating it turns contention into an interruption.
- **C6 — Never do durable cleanup in `session_end`.** 2 s total across all sessions and handlers permits, at most, a **single append with no read**. Leases must self-expire via TTL. Best-effort `lease.release` is an optimization the adapter must not depend on; reclaim is the next holder's job via one `lease_grant` with `supersede_reason: "expired"`.
- **C7 — Same rule for `gateway_stop`** (5 s per handler, shutdown continues) **[VF `:111-113`]**. Correctness after an abrupt stop comes from `assess.run` returning `repair` on the next start, not from a shutdown path.
- **C8 — Read the operator's effective timeout.** Operators can override plugin-authored `timeoutMs`, max 600000 ms **[VF `:75-98`]**. The adapter must not hardcode 15000 and must document a recommended `hooks.timeouts` block.
- **C9 — `after_tool_call` and `agent_end` handlers must be independently correct in any order**, because observation ordering is not guaranteed.
- **C10 — `completed_phases` cannot be derived from parallel observation hooks.** REDTEAM I-19 is correct. The adapter computes phases at a **single terminal hook** (`agent_end`) from its own accumulated state, never by assuming hook ordering.
- **C11 — `subagent_ended` correlation uses `targetSessionKey`** matched against `subagent_spawned.childSessionKey` **[VF `:209`]**. OpenClaw's `runId` is **not** MemKraft's `execution_run_id`.
- **C12 — Heartbeat-triggered assessment uses `assess.run` (query) only.** `eligibleTriggers` **[VF `:66-73`]** distinguishes `cron`/`heartbeat`/`user`; appending on every heartbeat would be unbounded growth with no retention policy.

### 15.4 Wiring sketch

```typescript
api.on("before_agent_run", async (event) => {
  const r = await mxp({
    kind: "query", op: "assess.run",
    target: { goal_id: goalFor(event) },
  });
  if (!r.ok) return { block: { reason: `memkraft: ${r.error.code}` } };   // C4
  if (r.result.recommendation === "stop") {
    return { block: { reason: `memkraft stop: ${r.result.reason_code}` } };
  }
  return;   // advisory: should_run / wait / ask_human do NOT authorize
}, { priority: 60 });

api.on("before_tool_call", async (event) => {
  if (!isWriteClass(event.toolName)) return;
  const r = await mxp({
    kind: "apply", op: "lease.acquire",
    target: { goal_id: goalFor(event), scope_key: scopeFor(event.toolName) },
    args: { holder: `openclaw/${event.sessionKey}`, ttl_seconds: 300 },
    precondition: {
      operation_id: opId(event.sessionKey, "before_tool_call",
                         event.toolName, event.toolCallId),   // C1
    },
  });
  if (!r.ok) return { block: { reason: `memkraft: ${r.error.code}` } };   // C4, C5
  rememberFence(event, r.result.fence_token);                             // §7.1
  return;
}, { matcher: ["exec", "apply_patch"], priority: 50 });
```

`matcher` takes canonical OpenClaw tool ids; wildcards, blanks, and empty lists are invalid **[VF `:62`]**.

### 15.5 Lease lifetime

Because neither `session_end` (2 s shared) nor `gateway_stop` (5 s, best-effort) is reliable:

**Normative:** TTL is `min(expected_tool_duration × 3, 900)` seconds, renewed from `after_tool_call` while work continues. A lease must not be held across a turn boundary. Expiry-based reclaim is the primary release path; explicit release is an optimization.

### 15.6 Not claimed

- No claim that OpenClaw supports MCP.
- No claim about OpenClaw cron or heartbeat internals beyond `eligibleTriggers`.
- OpenClaw internal hooks (`docs/automation/hooks.md`) are not used: handlers there must not own long-lived resources **[VF `:30`]** and `event.messages` is delivered only for `command:new`, `command:reset`, `session:compact:*` **[VF `:140-145`]**. Nothing MKEP needs fits that surface.

### 15.7 Staging: observation-only first

REDTEAM's recommendation is adopted as a **release sequencing decision**, not a scope cut:

- **3.3.0 ships the full adapter specification** (this section), and ships an **observation-only reference plugin**: `after_tool_call`, `agent_end`, `subagent_ended` → receipts and lineage. No policy hooks, no leases.
- The authoritative-hook wiring in §15.2/§15.4 is **enabled only after gate G12** (§18.4) demonstrates the CLI cold-start p95 fits inside the fail-closed budget with margin. If G12 fails, the observation-only plugin ships and the policy-hook path is descoped from 3.3.0 and said so plainly. The kernel ships either way.

---

## 16. Generic subprocess adapter

### 16.1 Contract

```
request      -> stdin   (one JSON object, UTF-8)
response     <- stdout  (one JSON object, UTF-8, newline-terminated)
diagnostics  <- stderr  (never parsed)
exit code    = 0|1|2|3|64|70 per §12.2
```

### 16.2 Reference client (shipped as a documented example, not public API)

```python
# examples/mkep_subprocess_client.py
from __future__ import annotations
import json, subprocess
from typing import Any, Dict, Optional

class MKEPError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool, details: Dict[str, Any]):
        super().__init__(f"{code}: {message}")
        self.code, self.retryable, self.details = code, retryable, details

def call(request: Dict[str, Any], *, base_dir: str,
         timeout: float = 10.0, lock_timeout: float = 2.0) -> Dict[str, Any]:
    body = json.dumps(request, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    proc = subprocess.run(
        ["memkraft", "exec", "call",
         "--base-dir", base_dir,
         "--lock-timeout", str(lock_timeout)],
        input=body, capture_output=True, timeout=timeout,   # argv, never a shell string
    )
    if proc.returncode in (64, 70) or not proc.stdout:
        raise MKEPError("E_INTERNAL",
                        proc.stderr.decode("utf-8", "replace")[:512], True, {})
    resp = json.loads(proc.stdout.decode("utf-8"))
    if not resp.get("ok"):
        e = resp["error"]
        raise MKEPError(e["code"], e["message"], e.get("retryable", False),
                        e.get("details", {}))
    return resp["result"]
```

### 16.3 Adapter obligations

1. Call `describe` once at startup; refuse to run on `mkep` mismatch or on any `guarantees` value the adapter requires and does not get.
2. Supply a deterministic `operation_id` for every `apply`, derived from runtime-stable identifiers.
3. Treat a subprocess timeout as **unknown**, not failure. Recovery is re-sending the identical request with the identical `operation_id`, yielding `already_applied`.
4. Never parse stderr.
5. Bound the subprocess timeout below any enclosing runtime budget, and always pass `--lock-timeout`.
6. Enforce `limits` from `describe` client-side, so oversize input fails locally.
7. Retry only on `retryable: true`, with the runtime's own backoff. Core states no delay.
8. Always pass `--base-dir` explicitly.

---

## 17. Capability and version negotiation

`describe` (query, no `now`, safe on an uninitialized base, creates zero files):

```json
{
  "mkep": "0",
  "implementation": "memkraft",
  "implementation_version": "3.3.0",
  "execution_schema": 1,
  "envelope_schema_version": 1,
  "canonical_json": "MKCJSON/1",
  "time_profile": "MKEP-TIME/1",
  "stability": "preview",
  "ga_decision_deadline": "2027-02-04",
  "ops": ["assess.record","assess.run","describe","gate.declare","gate.transition",
          "goal.declare","goal.transition","handoff.declare","handoff.export",
          "handoff.import","handoff.transition","lease.acquire","lease.release",
          "receipt.record","state.read"],
  "error_codes": ["…sorted…"],
  "transports": ["python", "cli", "mcp"],
  "mcp_ops": ["state.read", "assess.run", "handoff.export", "describe"],
  "limits": {
    "max_record_bytes": 16384, "max_request_bytes": 262144,
    "max_response_bytes": 1048576, "max_handoff_bytes": 32768,
    "max_gates_per_goal": 64, "max_active_leases_per_goal": 16,
    "max_string_len": 512, "max_list_len": 32,
    "max_ttl_seconds": 86400, "max_payload_depth": 8
  },
  "guarantees": {
    "atomic_unit": "single_line_append",
    "clock": "caller_injected",
    "scope": "single_host_local_filesystem",
    "multi_host": false,
    "network_filesystem": false,
    "authority_verified": false,
    "gates_are_advisory": true,
    "should_run_is_advisory": true,
    "cross_base_read": false,
    "mcp_mutation": false,
    "envelope_authenticity": false,
    "execution_log_compaction": false
  },
  "capabilities_digest": "…64hex"
}
```

`guarantees` is deliberately a **negative capability list**. `multi_host: false`, `authority_verified: false`, `gates_are_advisory: true`, and `envelope_authenticity: false` are machine-readable honesty: an adapter that requires any of them refuses to start rather than discovering the gap in production.

**Negotiation rules:**

1. `mkep` is `"0"`. A client sending `"1"` gets `E_VERSION_UNSUPPORTED` with `details.supported: ["0"]`. There is no downgrade dance.
2. Growth is **additive within `0`**: new `op`, new `error_code`, new optional `args` key, new warning code. These change `capabilities_digest` but not `mkep`.
3. Breaking changes require `mkep: "1"`, served **alongside** `"0"`, never replacing it.
4. `limits` values may only **increase** within `0`. A decrease is breaking.
5. An adapter must not send an `op` absent from `ops`, and must enforce `limits` client-side.
6. A client that pins behavior sends `capabilities_digest`; drift ⇒ `E_CAPABILITY_DRIFT` rather than silent semantic change.
7. `execution_schema` may go 1 → 2 in a later 3.x minor; readers must continue to read schema-1 records. Records are never rewritten in place.

Cross-implementation negotiation (a non-MemKraft server speaking MKEP/0) is permitted by the protocol but carries no conformance claim in 3.3.0.

---

## 18. Conformance kit

### 18.1 Fixture schema (exact)

Language-neutral. One directory per case:

```
tests/conformance/0/<case_id>/
  case.json          # metadata + determinism inputs
  seed/events.jsonl  # pre-existing log lines, applied verbatim (may be absent)
  request.json       # one request envelope
  requests.jsonl     # OR an ordered sequence, one per line
  expect.json        # expectations
  README.md          # one line: what this pins and why
```

`case.json`:

```json
{
  "case_id": "FN-03",
  "title": "stale fence write rejected",
  "mkep": "0",
  "level": "L2",
  "tags": ["lease", "fence"],
  "origin_instance_id": "00000000000000000000000000000001",
  "now_sequence": ["2026-08-04T10:00:00Z", "2026-08-04T10:00:05Z"],
  "transports": ["python", "cli"]
}
```

`expect.json`:

```json
{
  "responses": [
    { "ok": false, "error_code": "E_FENCE_STALE",
      "error_class": "lease", "retryable": false,
      "response_digest": "…64hex" }
  ],
  "final": {
    "projection_digest": "…64hex",
    "log_line_count": 3,
    "lines_delta": 0,
    "consistent": true,
    "rejected_transitions": 0,
    "skipped": 0
  }
}
```

**Runner obligations:**

- Determinism inputs are supplied entirely by the fixture: `now_sequence` provides every `now`; `origin_instance_id` is fixed; `request_id` is fixed in the request file.
- `created_at` and record `id` are store-assigned and non-deterministic **[VF `store_core.py:156-160`]**, and are therefore **excluded from every expectation digest** — `projection_digest` and `response_digest` are computed over fingerprint-normalized structures (§5.5).
- Every case runs on **every transport** in `transports` and must produce byte-identical `result` and digests. Divergence is a failure.
- The runner emits `results.json` with per-case pass/fail and observed digests.
- Conformance levels: **L1** = query + validation cases; **L2** = L1 + apply/lease/handoff; **L3** = L2 + concurrency and crash cases.
- **A partial pass is not a pass.** An implementation advertising a projection cache additionally runs the entire suite with the cache deleted between every call and must produce identical output.

### 18.2 Named cases (32)

**Canonicalization and digest**
1. `CJ-01 canonical_key_order_ascii_only` — `{b, a, _z}` sorted; a non-ASCII key ⇒ `E_PATTERN`.
2. `CJ-02 float_rejected_integer_only` — `1.0`, `1e3`, `-0` ⇒ `E_TYPE`; `2^53` ⇒ `E_LIMIT_EXCEEDED`.
3. `CJ-03 string_escaping_matches_golden_bytes` — control characters, `"`, `\`, emoji, CJK; hex vectors pinned; must reproduce in the second-language runtime.
4. `CJ-04 lone_surrogate_rejected` — `\ud800` ⇒ `E_TYPE`.
5. `CJ-05 nfc_normalization_stable` — NFD input is stored and echoed as NFC; digest matches the NFC golden.
6. `CJ-06 digest_is_over_canon_not_file_line` — asserts `digest != sha256(raw_line)`, since the store does not sort keys.

**Time**
7. `TM-01 naive_timestamp_rejected` — `now` without an offset ⇒ `E_TIME_NAIVE`.
8. `TM-02 offset_normalized_to_utc` — `+09:00` and `Z` for the same instant yield identical stored `emitted_at` and identical digests.
9. `TM-03 z_suffix_parsed_on_py39` — `Z` accepted (the `fromisoformat` trap).

**Determinism**
10. `DT-01 projection_digest_stable_1000_calls` — 1000 reads, same log, same `now`, zero variance.
11. `DT-02 injected_now_only_no_wall_clock` — the same case run hours apart with the same `now` yields identical digests.
12. `DT-03 ordering_by_event_seq_not_file_order` — shuffled seed lines produce the same digest.
13. `DT-04 created_at_excluded_from_fingerprint` — two records differing only in `created_at` fingerprint identically.

**Idempotency**
14. `ID-01 replay_same_operation_id_no_append` — `already_applied`, `lines_delta == 0`, TTL unchanged.
15. `ID-02 fingerprint_exclusion_set_is_exactly_three` — widening `{id, created_at, event_seq}` breaks a golden digest.
16. `ID-03 idempotency_mismatch_rejected_with_differing_keys` — same `operation_id`, differing params ⇒ `E_IDEMPOTENCY_MISMATCH` with `differing_keys: ["ttl_seconds"]`, `lines_delta == 0`.

**Evidence and gates**
17. `EV-01 gate_pass_without_receipt_rejected` — `E_EVIDENCE_REQUIRED`, `lines_delta == 0`.
18. `EV-02 stale_evidence_after_reopen_rejected` — pass with R, reopen, re-pass with the same R ⇒ `E_EVIDENCE_STALE` carrying `{receipt_event_seq, reopened_at_seq}`. **This is §8.2's hole.**
19. `EV-03 waive_requires_human_claim_and_counts_unverified` — missing claim ⇒ `E_AUTHORITY_CLAIM_REQUIRED`; on success `unverified_waivers == 1` and `W_WAIVER_UNVERIFIED` is emitted.
20. `EV-04 authority_verified_true_rejected` — ⇒ `E_AUTHORITY_VERIFIED_FORBIDDEN`.
21. `EV-05 all_forbidden_transitions_rejected` — ≥ 40 `(kind, from, to)` pairs, 100% rejected, `lines_delta == 0` for every one; includes every `waived → *` target.

**Lease and fence**
22. `FN-01 fence_required_when_scope_leased` — `gate.transition` without `fence_token` on a leased gate ⇒ `E_FENCE_REQUIRED`.
23. `FN-02 stale_fence_write_rejected` — token 5 vs current 7 ⇒ `E_FENCE_STALE`, `lines_delta == 0`. (Fence forbidden when unleased is asserted in the same fixture family: supplying a token with no lease ⇒ `E_UNKNOWN_FIELD`.)
24. `FN-03 reclaim_is_single_append_with_supersedes` — expired lease reclaimed by exactly one new line carrying `supersedes_lease_id` and `supersede_reason: "expired"`; `fence_token` strictly increases; the 17th active lease ⇒ `E_LEASE_CAP`. **L3 additionally runs 16 processes × 100 rounds asserting at most one valid lease per `scope_key` at all times and free parallelism across distinct scopes.**

**Isolation, advisory, cross-runtime**
25. `IS-01 no_cross_base_read` — export an envelope, **delete the origin base tree entirely**, then import from the envelope bytes into a fresh base: import succeeds. Additionally, the import path is run with `open` monkeypatched and asserts **zero** opens outside the target `base_dir`. Additionally, the exported envelope bytes are scanned for `base_dir`, `$HOME`, `$USER`, and every §10.1 pattern with **zero hits**, and export fails closed (not `[redacted]`) when a pattern is planted.
26. `AU-01 advisory_is_not_authorization` — an `assess.run` returning `should_run` leaves every gate byte-identical; every response schema and fixture is scanned for keys or enums matching `allow|permit|authoriz|granted|approved|permission` with zero hits; `advisory: true` is present on every assessment; and even after `should_run`, a fence-protected write into a leased scope without a token still fails with `E_FENCE_REQUIRED`.

**Two-runtime handoff and transport equivalence**
27. `XR-01 two_runtime_handoff` — Runtime A (Python reference) declares a goal, records a receipt, passes a gate, declares a handoff, and exports. The envelope bytes are moved by the test harness (not by MemKraft). Runtime B (the second-language implementation) imports into an independent base, transitions to `accepted`, produces a reverse-confirmation envelope, and A imports it and reaches `completed`. Assertions: both bases' `projection_digest` match their goldens; re-import at either end returns `already_applied` with `lines_delta == 0`; a tampered `payload` ⇒ `E_DIGEST_MISMATCH`; the same `(origin_instance_id, handoff_id)` with a different `payload_digest` ⇒ `E_CONFLICT`; and B's `results.json` equals A's modulo the runtime name field.
28. `CL-01 cli_transport_equivalence_and_stdout_purity` — for every fixture, the CLI transport returns byte-identical `result` and `response_digest` to the Python transport; stdout parses as JSON for every case including every error code in §6.7; exit codes match §12.2; stderr is ignorable.
29. `MC-01 mcp_projection_equivalence_and_readonly` — MCP `query` and `describe` produce `response_digest` identical to CLI for identical requests; `structuredContent` equals the parsed `TextContent`; a lint asserts no `str(result)` or `f"error:` idiom in the MCP module; and no MCP tool can reach any `apply` op.
30. `IN-01 inconsistent_projection_forces_repair` — a seeded undeclared-gate transition sets `consistent: false` and `rejected_transitions == 1` while `skipped == 0`; `assess.run` returns exactly `repair` / `projection_inconsistent`; every subsequent `apply` returns `E_PROJECTION_INCONSISTENT`. A separate seeded corrupt line sets `skipped == 1` and leaves `consistent: true`.

**Namespace and neutrality lints**
31. `NS-01 no_scheduling_vocabulary` — zero occurrences of `next_check_at`, `retry_after`, `poll_interval`, `cadence`, `cron` in any schema, response fixture, or `describe` output.
32. `NS-02 runtime_neutral_source` — case-insensitive grep over `src/memkraft/execution_*.py` finds zero occurrences of `hermes`, `openclaw`, `kanban`, `profile_name`, `session_key`, `work_item`, `workitem`; and zero occurrences of `datetime.now(`.

That is 32 named cases against a minimum of 20 required. Minimum fixture corpus: ≥ 40 forbidden-transition pairs, ≥ 20 canonicalization vectors, ≥ 12 idempotency cases, ≥ 10 lease/fence cases, ≥ 8 handoff cases, ≥ 6 inconsistency cases, ≥ 5 redaction cases. **Total ≥ 100 fixture directories.**

### 18.3 Second runtime

`XR-01` and `CJ-03` require a second-language implementation. **Locked: Go.** Node is cheaper to write, but Go exercises the byte-order key-sort assumption (§5.3) far more convincingly, and the whole cross-language digest claim rests on that assumption. The Go implementation is a read-and-verify conformance runner plus the canonicalizer and the handoff import path — not a full kernel.

### 18.4 Quantitative gates

**Performance measurement protocol** (applies to G11 and G12; no gate may be stated as a bare absolute number without it):

- **Corpus:** a generated log with 40 goals; the goal under test holds 10 000 records; the file holds ~400 000 records total. This is deliberate — `execution_state` reads the whole file, not one goal's slice, so measuring 10 000 records in isolation understates real load by the goal count (REDTEAM I-17).
- **Machine class:** results are reported for the CI runner class in use and are **comparative, not absolute**. The gate is expressed as a regression bound against a baseline measured on the same runner in the same CI run, plus a soft absolute ceiling that produces a warning, not a failure, when exceeded.
- **Repetitions:** 200 iterations after 20 warmup iterations, single process.
- **Statistic:** p95, reported alongside p50 and max.
- **Baseline:** for G11, the baseline is the same operation measured against a 1 000-record corpus in the same run; the gate is on the *ratio*. For G12, the baseline is `memkraft --version` cold start in the same run, isolating protocol cost from interpreter cost.

| ID | Gate | Threshold | Blocking |
|---|---|---|---|
| G0 | Version metadata consistency across `pyproject.toml`, `__init__.py`, CHANGELOG | exact match | yes |
| G1 | Deterministic replay | 1000 calls, identical `projection_digest`, 0 failures | yes |
| G2 | Invalid transitions fail closed | ≥ 40 pairs, 100% rejected, `lines_delta == 0` | yes |
| G3 | Receipt coverage | 100% of `passed` gates have a fresh matching receipt; a violation sets `consistent: false` | yes |
| G4 | No double occupancy | 16 processes × 100 rounds; valid leases per `scope_key` ≤ 1 at all times; distinct scopes verified parallel; **20 consecutive green runs** | **yes — one flake halts the project** |
| G5 | Crash recovery | 100 SIGKILLs injected **at the write boundary inside the lock region**, not at random; 100% projection success; loss ≤ 1 trailing partial line; that line counted as `skipped`, `rejected_transitions == 0` | yes |
| G6 | Concurrent writers | 8 processes × 500 receipts ⇒ 4000 records present, `skipped == 0`, zero duplicate `event_seq`, **no gaps** (guaranteed by §4.1's no-compaction rule) | yes |
| G7 | Handoff isolation and recovery | `IS-01` and `XR-01` green; kill after `accepted` ⇒ next `assess.run` yields `repair`/`handoff_incomplete`; zero path/base_dir strings in any exported envelope | yes |
| G8 | Surface integrity | `set(_OPS)` maps one-to-one onto public methods; `len(_OPS) == 15`; 2 CLI subcommands; 2 new MCP tools, both read-only; `ExecutionStateMixin` registered as additive-only with a collision test | yes |
| G9 | Context overhead | `execution` section ≤ 400 tokens per goal and ≤ `execution_budget` | yes |
| G10 | Context no-regression | golden `usage_id` byte-match at `goal_id=None` and at `execution_budget=0`; compiled output byte-identical to baseline; retrieval benchmark delta = 0 | **yes — failure forbids release** |
| G11 | Projection performance | p95 ratio (10 000-record goal vs 1 000-record goal, same run) ≤ 12×; soft ceiling 150 ms produces a warning | yes; **failure is the only trigger permitted to introduce a cache**, and the cache must then pass "delete cache ⇒ identical output" |
| G12 | CLI transport latency | p95 of `memkraft exec call` for `state.read` on the standard corpus ≤ `p95(memkraft --version) + 400 ms` | yes; **failure descopes the OpenClaw policy-hook path (§15.7), not the kernel** |
| G13 | Conformance | 100% of ≥ 100 fixtures pass at L2 on every declared transport; L3 on the CI concurrency lane | yes |
| G14 | Runtime neutrality | `NS-01` and `NS-02` empty | yes |
| G15 | No log compaction | a test asserts `compact` is not reachable for `events.jsonl` | yes |

---

## 19. Repository plan, slices, migration, rollback, release

### 19.1 File plan

```
src/memkraft/
  execution_state.py         # ExecutionStateMixin: validation, append, event_seq, leases
  execution_projection.py    # pure project(records, now); the single _TRANSITIONS dict
  execution_handoff.py       # declare/transition/export/import + fail-closed redaction lint
  execution_protocol.py      # mkcjson, digests, envelopes, error registry, closed dispatcher, describe
  execution_cli.py           # `memkraft exec call` / `memkraft exec state`
  store_core.py              # MODIFIED: bounded non-blocking lock path (§7.4), batch tombstone (§14.4)
  mcp.py                     # MODIFIED: 2 read-only tools; structured JSON encoding (§13.3)
  cli.py                     # MODIFIED: register the `exec` group
  core.py                    # MODIFIED: init() creates .memkraft/execution/ only; origin_instance_id stays lazy
  __init__.py                # MODIFIED: register ExecutionStateMixin as additive-only
  context_compiler.py        # MODIFIED: dual opt-in goal_id / execution_budget

tests/
  test_execution_records.py
  test_execution_projection.py
  test_execution_lease.py
  test_execution_evidence.py
  test_run_assessment.py
  test_execution_handoff.py
  test_execution_protocol.py
  test_execution_cli.py
  test_execution_mcp.py
  test_execution_context.py
  test_execution_forget.py
  test_execution_surface.py
  test_execution_graph_adapter_examples.py # adapter-only mappings; no graph semantics in core
  test_store_core_lock_nb.py
  conformance/
    runner.py
    fixtures/0/<100+ cases>/
    go/                      # second-runtime verifier (§18.3)

examples/
  mkep_subprocess_client.py

docs/
  EXECUTION_PROTOCOL.md      # §5–§10, §17 — normative wire spec
  ADAPTERS.md                # §14–§16
  GRAPH_ENGINEERING_ADAPTERS.md # execution-graph mapping and non-equivalence guide (§3.4)
  THREAT_MODEL.md            # MODIFIED: §19.5 verbatim paragraphs
  MIGRATIONS.md              # MODIFIED
  V3_API.md                  # MODIFIED: NFC normalization, usage_id aliasing
  CHANGELOG.md               # MODIFIED: preview label + dated GA decision deadline
```

**No new package, no subpackage, no separate distribution.**

### 19.2 Slices (RED → GREEN → REFACTOR)

**Slice 0 — Baseline verification (blocking, no production code)**

```bash
git rev-parse HEAD
grep -n '^version' pyproject.toml; grep -n '__version__' src/memkraft/__init__.py
grep -n '_governance_lock\|_append_audit' src/memkraft/derived_views.py
grep -n 'usage_id' src/memkraft/context_compiler.py
ls src/memkraft | grep -i execution || echo "A4 ok"
python -m pytest -q                        # record baseline pass count
python tools/pin_golden_usage_id.py        # writes tests/golden/usage_id.json
```

Acceptance: A1–A4 confirmed or this spec amended before any code; `3.3.0` confirmed strictly greater than HEAD; golden `usage_id` committed. **If A3 is wrong, stop and re-derive §12.4.**

**Slice 1 — RED/GREEN: canonicalization, digests, time**

RED: `test_execution_protocol.py` — the 20 canonicalization vectors, float/big-int rejection, lone surrogate, NFC, `Z`-suffix parsing, offset normalization, truncation, digest-not-over-file-line.
GREEN: `execution_protocol.py` `mkcjson`, `_check`, `digest`, time parse/canonicalize.
Command: `python -m pytest tests/test_execution_protocol.py -q`
Acceptance: `CJ-01`…`CJ-06`, `TM-01`…`TM-03` green.

**Slice 2 — RED/GREEN: non-blocking lock**

RED: `test_store_core_lock_nb.py` — held lock + `timeout_s=0.1` raises `StoreBusy` within 200 ms; `timeout_s=None` preserves the exact prior blocking behavior; inode revalidation still fires on replacement.
GREEN: bounded `LOCK_EX | LOCK_NB` retry in `store_core`.
Command: `python -m pytest tests/test_store_core_lock_nb.py -q && python -m pytest -q`
Acceptance: new tests green; **existing suite pass count identical to Slice 0.**

**Slice 3 — RED/GREEN: declarations, append, `event_seq`**

RED: naive datetime rejected; duplicate `goal_id` rejected; namespaced-grammar violations rejected; `authority_verified: true` rejected; idempotent replay excluding `{id, created_at, event_seq}`; invalid input leaves line count unchanged; `event_seq` allocated after lock re-read; tombstoned records consume sequence numbers; `compact` unreachable for the execution log.
GREEN: `execution_state.py` declarations + `_alloc_event_seq`; `init()` creates `execution/`; first handoff export lazily creates `origin_instance_id`; mixin registered additive-only.
Command: `python -m pytest tests/test_execution_records.py -q && python -m pytest -q`
Acceptance: new tests green; existing pass count unchanged; G15 green.

**Slice 4 — RED/GREEN/REFACTOR: projection and the single transition table**

RED: 1000-call digest stability; corrupt line ⇒ `skipped`, `consistent` stays true; undeclared-id transition ⇒ `rejected_transitions` + reason + `consistent: false`; ordering by `(event_seq, id)` with shuffled input; all 40 forbidden pairs.
GREEN: `execution_projection.py`.
REFACTOR (hard exit criterion): `_TRANSITIONS` is **one dict**. Asserted by `len(_TRANSITIONS) >= 12` plus an AST check that the module contains no `if`/`elif` chain over `to_status` literals. **If the logic is branched, the slice does not pass.**
Acceptance: G1, G2 green.

**Slice 5 — RED/GREEN: evidence, snapshot binding, gates**

RED: pass without receipt ⇒ `E_EVIDENCE_REQUIRED`; **re-pass after reopen with the pre-reopen receipt ⇒ `E_EVIDENCE_STALE`**; explicit stale `receipt_id` not silently upgraded; `failed → passed` requires both a fresh receipt and a `reopen_reason`; waive requires `authority_claim="human"` and increments `unverified_waivers`; `open → satisfied` blocked by a pending required gate with blockers listed; 65th gate ⇒ `E_GATE_CAP`.
GREEN: `observed_seq` on receipts, `reopened_at_seq` in the projection, guards in `_TRANSITIONS`.
Acceptance: `EV-01`…`EV-05` green; G3 green.

**Slice 6 — RED/GREEN: leases and fencing (highest risk)**

RED: same scope exclusive; distinct scopes parallel; `fence_token` strictly increases and is an **output**; `expected_fence` mismatch rejected; fence absent on a leased scope ⇒ `E_FENCE_REQUIRED`; fence present on an unleased scope ⇒ `E_UNKNOWN_FIELD`; stale fence ⇒ `E_FENCE_STALE` with `lines_delta == 0`; reclaim is exactly one append; 17th lease ⇒ `E_LEASE_CAP`; `E_LEASE_HELD` returns `holder_digest` not `holder`; 16-process × 100-round concurrency.
GREEN: lease projection and grant/renew/reclaim in `execution_state.py`.
Command:
```bash
python -m pytest tests/test_execution_lease.py -q
for i in $(seq 1 20); do python -m pytest tests/test_execution_lease.py::test_concurrent_acquire -q || break; done
```
Acceptance: **20/20 consecutive green on the concurrency test. One flake ⇒ full stop; do not proceed to Slice 7.** G4, G6 green.

**Slice 7 — RED/GREEN: assessment, forget, batch tombstone**

RED: allowed-pair validation; `inputs_digest` reproducibility; `advisory: true` forced; `consistent: false` ⇒ unconditional `repair`; waiver caveat; `assess.run` appends nothing (line count unchanged across 100 calls); `assess.record` appends exactly one; `forget({"goal_id": …})` reads once and appends N tombstones under one lock.
GREEN: `assess_run`, `assess_record`, batch tombstone in `store_core`.
Command: `python -m pytest tests/test_run_assessment.py tests/test_execution_forget.py -q && grep -rn 'datetime.now(' src/memkraft/execution_*.py`
Acceptance: purity tests green; grep empty (G14 half).

**Slice 8 — RED/GREEN: handoff and isolation**

RED: single accept; second accept with a different `operation_id` ⇒ `E_CONFLICT`, same `operation_id` ⇒ `already_applied`; `payload_digest` mismatch ⇒ `E_DIGEST_MISMATCH`; expiry projected not stored; 32 KiB cap; re-import ⇒ `already_applied` with `lines_delta == 0`; same `(origin, handoff_id)` different digest ⇒ `E_CONFLICT`; **export fails closed on a planted pattern**; zero path strings in the envelope; **import with the origin base deleted succeeds**; zero out-of-base `open()` calls.
GREEN: `execution_handoff.py`.
Acceptance: `IS-01` green; G7 green.

**Slice 9 — RED/GREEN: protocol dispatcher and conformance runner**

RED: every error code reachable; unknown op/field rejection; envelope closedness; `describe` on an uninitialized base creating zero files; `capabilities_digest` drift; `mkep` mismatch; precondition mismatches.
GREEN: closed dispatcher + `describe`; wire the conformance runner; author ≥ 100 fixtures.
Command: `python -m pytest tests/test_execution_protocol.py tests/conformance -q`
Acceptance: ≥ 100 fixtures green at L2; `len(_OPS) == 15`.

**Slice 10 — RED/GREEN: CLI and MCP transports**

RED: stdout is pure JSON on every path including every error; exit-code mapping; transport equivalence; `--base-dir` and `--lock-timeout` honored; MCP tools return structured JSON; no MCP tool reaches an `apply` op; the four existing MCP tools byte-unchanged; the `str(result)` lint.
GREEN: `execution_cli.py`, `cli.py` wiring, `mcp.py` changes.
Acceptance: `CL-01`, `MC-01` green; G8, G12 measured.

**Slice 11 — RED/GREEN: context compiler**

RED: `test_usage_id_unchanged_when_goal_id_none` (**release-critical**); `test_usage_id_unchanged_when_execution_budget_zero`; **`test_compiled_output_byte_identical_when_feature_unused`**; section order preserved; `execution_budget` visibly reduces other sections; section ≤ 400 tokens.
GREEN: dual opt-in in `context_compiler.py`.
Acceptance: golden `usage_id` from Slice 0 matches byte-for-byte; G9, G10 green.

**Slice 12 — Second runtime, docs, release gates**

RED: `XR-01` two-runtime handoff via the Go verifier; `CJ-03` vectors reproduced in Go.
GREEN: Go conformance runner; `EXECUTION_PROTOCOL.md`, `ADAPTERS.md`, `GRAPH_ENGINEERING_ADAPTERS.md`, `THREAT_MODEL.md`, `V3_API.md`, `MIGRATIONS.md`, CHANGELOG with the dated GA deadline. `GRAPH_ENGINEERING_ADAPTERS.md` must include Claude Code Dynamic Workflows, LangGraph, and Temporal mappings; state explicitly that their resume/checkpoint semantics are non-equivalent; and show a fan-out/fan-in plus independent-verifier example using only the existing 15 operations.
Command:
```bash
python -m pytest -q
python -m compileall -q src/memkraft
python -m pytest tests/test_execution_graph_adapter_examples.py -q
python -c "import ast,pathlib; [ast.parse(p.read_text(), feature_version=(3,9)) for p in pathlib.Path('src/memkraft').rglob('*.py')]"
grep -rniE 'hermes|openclaw|kanban|work_?item|profile_name|session_key' src/memkraft/execution_*.py
go test ./tests/conformance/go/...
```
Acceptance: all greps empty; the adapter examples replay deterministically without introducing a graph operation or raw runtime checkpoint field; G13, G14 green; every gate G0–G15 green.

### 19.3 Go/No-Go checkpoints

- **After Slice 4:** G1, G2 green; `_TRANSITIONS` is a single data structure; existing suite unchanged ⇒ Go. Branched transition logic ⇒ No-Go, refactor.
- **After Slice 6:** G4 green 20/20, G6 green ⇒ Go. **A single G4 flake is an immediate full stop.** If leases are not exact, nothing built on them means anything.
- **After Slice 8:** G5, G7 green ⇒ Go. Assessment no longer pure ⇒ No-Go, redesign.
- **After Slice 10:** G13 green ⇒ Go. G12 failing ⇒ the OpenClaw **policy-hook** path is descoped to the observation-only plugin (§15.7) and said so plainly in the CHANGELOG; the kernel ships.
- **After Slice 11:** G9, G10 green ⇒ release Go. **Golden `usage_id` broken ⇒ release forbidden.**
- **Standing No-Go:** multi-host enters the roadmap.

### 19.4 Migration and rollback

**Migration: none required.** Execution state lives only in new files under `.memkraft/execution/`. After installing 3.3.0 an existing base gains zero files until the first `goal.declare` — except `origin_instance_id`, which is created lazily on the first handoff export, not at init, so an install that never uses handoff creates nothing. Existing callers passing no `goal_id` observe byte-identical behavior (G10).

**Rollback: safe, requires no action.** Downgrading to 3.2.x is a no-op: the prior version ignores `.memkraft/execution/` entirely. No data loss, nothing to delete, zero breaking changes to undo. Re-upgrading resumes from the same log.

Two documented caveats:
1. The `store_core` non-blocking lock parameter is additive with an unchanged default; a downgrade loses the parameter but no existing caller passed it.
2. A projection cache, if ever introduced (§20, D-16), must be deletable with byte-identical output, so a downgrade/upgrade cycle cannot produce a stale cache. Enforced by test as a precondition of introducing it.

### 19.5 Release train and required THREAT_MODEL text

| Version | Content |
|---|---|
| **3.3.0** | additive preview: the kernel, MKEP/0, CLI transport, read-only MCP projection, conformance kit, observation-only OpenClaw reference plugin. Zero breaking changes. |
| 3.4 backlog | Evidence-gated candidates only; not a promised release scope. Any promoted item requires its own acceptance evidence and compatibility review. If an item needs `execution_schema` 1 → 2, schema-1 records must remain readable. |
| 3.5.0 | GA — preview label removed, API frozen, `describe.stability: "stable"`. |
| 4.0 | **Not planned.** Revisit only if envelope `schema_version` must go 1 → 2, or an existing public signature must be removed or re-meant. Neither is required. |

The 3.3.0 CHANGELOG must carry a **dated GA decision deadline** (`2027-02-04`, six months out). This is the only real defense against permanent preview, and `describe.ga_decision_deadline` exposes it machine-readably.

**`THREAT_MODEL.md` must contain these six statements verbatim:**

1. *Gates are advisory bookkeeping.* `authority_claim` is not verified. A caller with write access can waive any gate or record any receipt. Gates make what happened legible and attributable; they do not make it impossible.
2. *Namespace and actor claims are opaque audit identity, not authenticated identity* (full text in §4.5).
3. *`should_run` is advisory* (full text in §9.4).
4. *Network filesystems are not supported.* Fence-token safety depends on local `flock` semantics and inode revalidation. On NFS, SMB, or FUSE these guarantees do not hold and the kernel is outside its support envelope.
5. *The export redaction scan is a lint, not a security control* (full text in §10.1).
6. *`origin_instance_id` is a persistent cross-recipient correlator* (full text in §10.3).

Plus three disclosed residual risks: `holder` spoofing enables lease theft by any process with write access; `content_sha256` is format-checked only, so receipts are self-attestations; and handoff idempotency holds only against honest senders, because `origin_instance_id` is self-asserted.

---

## 20. Decision ledger

### Accepted

| # | Decision | Rationale / source |
|---|---|---|
| D-01 | Single-host, local-filesystem only; multi-host is a standing No-Go | all three analyses agree; consensus is not MemKraft's job |
| D-02 | Release target 3.3.0, additive preview; no 4.0 | prompt; G0 verifies baseline metadata |
| D-03 | One command envelope over a **closed 15-entry registry**, not a generic bus | four structural locks, §6.3 |
| D-04 | **Typed Python methods are the primary API**; the envelope is a projection | rejects a generic `apply/query` blob as the Python surface |
| D-05 | `fence_token` is an **output**; `expected_fence` is the optional input; fence-protected mutations enumerated in §7.3; fence mandatory-when-leased, forbidden-when-unleased | resolves REDTEAM F-3 while keeping PROTOCOL's fail-closed derivation |
| D-06 | Bounded non-blocking lock path added to `store_core`, default behavior unchanged | REDTEAM F-4; required before any hook-facing adapter |
| D-07 | Keep `event_seq`; drop `seq_high_water`; **forbid compaction of the execution log** | preserves one-line atomicity and eliminates seq reuse and gaps |
| D-08 | **Snapshot binding** via `observed_seq` / `reopened_at_seq`; stale evidence ⇒ `E_EVIDENCE_STALE` | closes REDTEAM I-9; no temporal hole (§8.2) |
| D-09 | `assess.run` is a **pure query**; `assess.record` appends | resolves unbounded log growth from heartbeat polling |
| D-10 | **MCP is read-only in preview** (2 new tools); mutation deferred | three independent reasons, §13.2 |
| D-11 | CLI is 2 subcommands with a full-lifecycle JSON transport | resolves REDTEAM F-2 without discoverable-surface sprawl |
| D-12 | **MKCJSON/1**, not full RFC 8785; integers only, ASCII keys, NFC | honest stdlib-implementable contract, §5.1 |
| D-13 | Namespaced `goal_id` (`<ns>/<name>`); `run_id` renamed `execution_run_id` | REDTEAM I-14, I-15 |
| D-14 | `init()` creates `.memkraft/execution/` explicitly | REDTEAM I-24 |
| D-15 | Execution log is never compacted in 3.3.0 | precondition of D-07 |
| D-18 | `supersede_reason: "revoked"` removed | dead enum, REDTEAM I-23 |
| D-19 | **No numeric cap on public API method count**; the closed registry is the cap | ARCHITECT's 9-cap was satisfiable by overloading |
| D-20 | `write_scopes` removed; exclusivity is per `scope_key` only | undefined interaction, no added expressiveness |
| D-24 | Batch tombstone path for `forget` | REDTEAM I-18; goal deletion otherwise O(n²) |
| D-25 | Handoff `payload` is **caller-supplied**; core never scrapes records to build an export; the redaction scan is a fail-closed lint | resolves record-level vs field-level privacy incoherence |
| D-26 | Adapters invoke the CLI via argv + stdin, never a shell string | prompt; §15.1, §16.2 |
| D-27 | Performance gates specify corpus, machine-class caveat, repetitions, statistic, and a same-run baseline comparison | prompt; §18.4 |
| D-28 | Second conformance runtime is **Go** | exercises the byte-order key-sort assumption |
| D-29 | OpenClaw policy-hook wiring is gated on G12; observation-only plugin ships regardless | §15.7 |
| D-37 | Graph Engineering is an adapter/runtime profile, not a core graph schema; 3.3.0 uses existing gates, receipts, run lineage, scopes, provenance, and handoff without changing the 15-operation registry | §3.4; Claude Code/LangGraph/Temporal contracts are materially non-equivalent |

### Rejected

| # | Rejected proposal | Why |
|---|---|---|
| R-01 | `fence_token` as a required input to lease acquisition (ARCHITECT §6.2.2) | first acquisition undefined; a required argument the caller cannot compute teaches nothing |
| R-02 | `seq_high_water` sidecar file (ARCHITECT §5.4) | breaks one-line atomicity; creates a gap window that its own gate forbids |
| R-03 | Numeric public API cap of 9 (ARCHITECT §6.1) | satisfiable by overloading `lease`/`handoff` into parameter modes CI cannot inspect |
| R-04 | `receipt.record` exposed over MCP (ARCHITECT §8.1.2) | model self-attestation with a decorative checksum |
| R-05 | Appending `run_assessment` on every evaluation (ARCHITECT §5.7) | unbounded growth under heartbeat triggers; no read-only preview |
| R-06 | Full RFC 8785 canonicalization | ES6 number serialization not reproducible on Python 3.9 stdlib |
| R-07 | Silent `[redacted]` substitution on export (ARCHITECT §10.3 alternative) | produces an envelope that looks complete with semantics quietly removed |
| R-08 | Dropping handoff entirely from v0 (REDTEAM §11) | prompt mandates handoff and a two-runtime handoff conformance test; tightened per D-25 instead |
| R-09 | Dropping `event_seq` entirely for file-order sorting (REDTEAM §2.3) | file order is stable only because we forbid compaction; making the ordering key explicit is cheaper to reason about and survives a future compaction decision |
| R-10 | `authorization_evidence` format-only field in 3.3.0 | an unvalidated security-shaped field is worse than no field |
| R-11 | Unnamespaced `goal_id` | collides the moment two runtimes share a base |
| R-12 | A generic `execution_call` MCP tool | hands the model the full lifecycle and defeats D-10 |
| R-13 | Raising the CLI to 12+ named subcommands | 12 permanent `--help` names and compatibility obligations for a preview feature |
| R-14 | A daemon/socket transport in preview | makes MemKraft own a process lifecycle, contradicting §3.1 |
| R-15 | `next_check_at` / `retry_after` in any form | §1.3 and §9.5 |
| R-16 | A `runtime` discriminator field on records | makes the record format runtime-aware and creates a migration problem the first time a goal moves runtimes |
| R-17 | A `payload_schema` whitelist in core | forces a core release for every adapter payload change |
| R-18 | First-class node, edge, merge, reducer, checkpoint, or loop records in MKEP/0 | makes core own workflow topology or invent false common semantics across runtimes |
| R-19 | Raw multi-parent or checkpoint references in the 3.3.0 binding schema | existing provenance artifacts, `binding_digest`, and typed handoff payload cover preview use cases without creating an adjacency-list growth path |

### 3.4 feature backlog (evidence-gated; none is a release commitment or live P0)

| # | Item | Default until decided | Earliest |
|---|---|---|---|
| D-16 | Projection cache | not shipped; promote **only** on G11 failure, and must pass "delete cache ⇒ identical output" | 3.4 backlog |
| D-17 | `authorization_evidence` + scheme registry | field absent; promote only with a real verification scheme and threat-model tests | 3.4 backlog |
| D-21 | Per-recipient derived `origin_instance_id` (HMAC) | stable UUID with documented linkability; promote only with a concrete unlinkability requirement | 3.4 backlog |
| D-22 | Handoff envelope signing / authenticity | `payload_digest` is self-consistency only; promote only with a key-management and verification contract | 3.4 backlog |
| D-23 | MCP mutation | read-only; promote only after per-client base selection and adapter-mediated receipt provenance exist | 3.4 backlog |
| D-30 | Execution log compaction / retention | never compacted; promote only after a measured retention problem and sequence-safety proof | 3.4 backlog |
| D-31 | `strace`/`dtruss`-based isolation proof for `IS-01` | the monkeypatched-`open` assertion plus the deleted-origin-base test are the shipped proofs | GA |
| D-32 | Windows support for fencing | **POSIX-only declared**; the `msvcrt` fallback is best-effort and untested for fencing | GA |
| D-33 | NDJSON/streaming beyond `--jsonl` | `--jsonl` only, in order, never batched | GA |
| D-34 | Cross-implementation conformance claims for non-MemKraft servers | protocol permits, no claim made | GA |
| D-35 | ReasoningBank lesson extraction from completed goals | out of scope | post-GA |
| D-36 | Bidirectional linking between goals and `decisions` | out of scope; would freeze both | post-GA |
| D-38 | Optional raw step, attempt, multi-parent, or checkpoint correlation fields | absent; promote only after a two-runtime correctness failure proves existing lineage, provenance, digest, and handoff references insufficient | 3.4 backlog |

**No P0 architecture question remains open.** Every item above has a shipped default that is safe, tested, and machine-readable in `describe.guarantees`.

---

## 21. First-implementation work order

Do these in order. Do not start item 6 until items 1–5 are complete and their acceptance conditions hold.

1. **Verify the baseline.** Run the Slice-0 command block (§19.2). Confirm A1–A4 and that `3.3.0` is strictly greater than the version at HEAD. Pin and commit `tests/golden/usage_id.json`. If A3 (the `usage_id` identity composition) is wrong, **stop** and re-derive §12.4 before anything else. Output: a short verification note committed alongside the golden.

2. **Write the six THREAT_MODEL paragraphs first** (§19.5), before any code. They are the design's honest boundary, and writing them first prevents the boundary from drifting to match the implementation. Include the three residual-risk disclosures.

3. **Ship the canonicalizer.** Slice 1. `mkcjson`, `_check`, `digest`, and the time profile, with the 20 pinned hex vectors as fixtures. Everything downstream — idempotency, projection digests, handoff, conformance — depends on these bytes being right, and they are the cheapest thing to get wrong silently.

4. **Ship the non-blocking lock.** Slice 2. This is an upstream `store_core` change with an unchanged default, and it is a hard precondition for any hook-facing adapter. Verify the existing suite pass count is byte-identical afterward.

5. **Ship declarations and `event_seq`.** Slice 3, including `init()` creating `.memkraft/execution/`, the additive-only mixin registration with a collision test, and the assertion that `compact` is unreachable for the execution log.

6. **Ship the projection and the single transition table.** Slice 4. The hard exit criterion is mechanical: `_TRANSITIONS` is one dict and the module contains no branch chain over `to_status`. If it is branched, refactor before proceeding — every later slice's guards live in that table.

7. **Ship evidence with snapshot binding.** Slice 5. Write `EV-02` (re-pass after reopen with the stale receipt) **before** the implementation; it is the case that proves the temporal hole is closed, and it is easy to write an implementation that passes every other evidence test while leaving it open.

8. **Ship leases and fencing, then stop and prove it.** Slice 6. Run the 16-process concurrency test 20 consecutive times. **One flake halts the project.** Nothing built above an inexact lease means anything, and this is the last point at which discovering that is cheap.

At that point the kernel is real and the remaining slices (assessment, handoff, protocol, transports, context, second runtime) proceed as specified in §19.2 with their own gates.
