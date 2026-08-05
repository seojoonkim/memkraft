# MemKraft — Continual Improvement Ledger (P0 Preview)

**Status:** development-ready plan. Additive preview. Python-API only.
**Date:** 2026-08-06
**Branch:** `feat/continual-improvement-ledger`
**Baseline:** `dfc9b9e` (MemKraft 3.3.0, MKEP/0 execution kernel shipped)
**Owner:** implementation planner
**Scope:** one new production module, one primary test file, `__init__.py` wiring, one docs page. No version bump, no changelog edit, no MKEP registry change.

Citation convention (inherited from the 3.3.0 plan): **[VF]** = verified against a file at this baseline, path and line cited. **[DES]** = design decision made here. **[ASSUME]** = assumption stated conservatively; must hold or the slice stops.

---

## 1. Thesis and architecture

### 1.1 Thesis

An agent that runs, learns something, and changes how it behaves currently leaves no durable, replayable trace of *why the change happened, what evidence supported it, who decided, and what it replaced*. MemKraft already owns durable local evidence (`store_core` append under `flock` **[VF `src/memkraft/store_core.py:204-224`]**, inode-revalidating lock acquisition **[VF `store_core.py:120-157`]**, corrupt-line skip-and-count rather than whole-load failure **[VF `store_core.py:226-268`]**) and a typed execution axis (MKEP/0).

The Continual Improvement Ledger adds exactly one axis on top: **an append-only causal chain from proposal → evaluation → explicit promotion → artifact revision → activation → rollback.** MemKraft records and constrains that chain. It never produces it.

### 1.2 What MemKraft is *not* in this feature

| Not | Structural enforcement |
|---|---|
| Not an evaluator | No scoring, no metric computation, no threshold arithmetic. `evaluation_receipt.verdict` is a caller-supplied enum; core only checks freshness and binding. |
| Not a scheduler | No field may name a future instant *at which to act*. Prohibited names asserted in tests: `next_check_at`, `retry_after`, `poll_interval`, `cadence`, `cron`, `due_at`. |
| Not an orchestrator / workflow DAG | Proposals carry no `depends_on`, `order`, `priority`, `assignee`, no edges between proposals. |
| Not a deployer / patch executor | Core stores no artifact body, no diff, no patch, no command, no code, no secret. Only opaque locators + content digests. |
| Not an authenticated authority | `authority_verified` is forced `false` on every record, exactly as MKEP/0 does **[VF `execution_state.py:204-208`]**. |
| Not an automatic promotion engine | There is no code path from `evaluation_receipt` to a promoted proposal. Promotion is a separate caller-initiated append that must pass every guard. |
| No parallel storage layer | Every write goes through `store_core.append`, every read through `store_core.read_all`, under the existing `_governance_lock` **[VF `execution_state.py:310-318`, `derived_views.py:78`]**. |
| No protocol surface | No CLI, no MCP, no MKEP op. MKEP/0's `_OPS` stays at fifteen entries **[VF `execution_dispatch.py:270`]**. |

### 1.3 Architecture in one paragraph

One new append-only JSONL log, `<base_dir>/.memkraft/improvement/events.jsonl`, never compacted. One new mixin, `ImprovementLedgerMixin`, mixed into the same classes that already receive `ExecutionStateMixin` **[VF `src/memkraft/__init__.py:80-81,125-126`]**. Writes go through a local `_improvement_append` that mirrors the MKEP append path — governance lock → post-lock re-read → duplicate/idempotency scan → guard → `event_seq = high_water + 1` → `store_core.append` — but is a *separate function on a separate log*, because improvement records must not carry `goal_id` and must not be projected by `execution_projection.project`. Reads go through one pure `improvement_project(...)` function in the same module that folds records in `(event_seq, id)` order into a deterministic view.

### 1.4 Relationship to existing subsystems (reuse, do not duplicate)

- `store_core.append` / `read_all` / lock helpers: used verbatim. No new locking primitive.
- `execution_protocol`: reuse `digest`, `mkcjson`, `canonical_timestamp`, `parse_timestamp`, and the `ExecutionError`/`ValidationError`/`ConflictError` hierarchy **[VF `execution_protocol.py:107-144, 213-290`]**. New error codes are added to the improvement module's own registry, **not** to `_ERROR_REGISTRY`, because that registry is part of the frozen MKEP/0 wire contract.
- `provenance` / evidence / ReasoningBank / `outcomes` / `candidates` / `resolver` / `prompt_tune` / `convergence`: referenced only as **opaque refs** (`evidence_refs`, `experience_refs`). The ledger never imports them and never resolves them. This is what keeps the feature additive.
- MKEP Goal/Gate/Evidence/Lease/Handoff: untouched. An improvement record may carry an optional `execution_run_id` for correlation, but **never** a `goal_id` — coupling improvement lineage to a goal would make every improvement goal-scoped, which is wrong for profile- and project-level artifacts.

---

## 2. Assumptions, resolved conservatively

| ID | Assumption | Conservative resolution if false |
|---|---|---|
| A-1 | `_governance_lock(timeout_s=...)` exists with the historical no-arg fallback **[VF `execution_state.py:310-318`, `derived_views.py:78`]**. | Copy the same `TypeError`-sniffing fallback verbatim; it is already proven. |
| A-2 | A single governance lock serializes both `execution/events.jsonl` and `improvement/events.jsonl`. | Accepted, and *desirable*: it makes cross-log ordering deterministic. Cost is contention, which is acceptable at preview scale. |
| A-3 | `store_core.append` forces `schema_version`, `id`, `created_at` onto the record **[VF `store_core.py:187-224`]**. | The fingerprint exclusion set must match exactly (`{id, created_at, event_seq}`), as MKEP does **[VF `execution_state.py:58`]**. |
| A-4 | Python 3.9 is the floor (`requires-python = ">=3.9"` **[VF `pyproject.toml:11`]**). | No `match`, no `X | Y` annotations, no `dict[str, ...]` subscripting at runtime, no `slots=True`, no `functools.cache`. `typing.Dict/List/Optional` only. Stdlib only. |
| A-5 | The ledger has no authenticated caller identity available at this baseline. | Therefore P0 **invents no auth**: `authority_verified` is always `false`, and any scope beyond the process's own working tree requires an opaque host-issued reference that core stores but explicitly does not verify. |
| A-6 | Callers may retry any write with the same `operation_id`. | Exact-payload retry returns the stored record; same id + different semantic payload is a hard `E_IMPROVEMENT_IDEMPOTENCY_MISMATCH`. |

---

## 3. Record families — critical reduction

The brief lists six candidate families. Two are cut.

### 3.1 Cut: `experience_run`

**Rationale.** A "correlation envelope only" record stores no state, gates nothing, and is projected into nothing. Its entire content is an identifier plus refs — which is a *field*, not a record. MemKraft already owns run-level experience in `outcomes`, ReasoningBank, and MKEP's `execution_run_id`.

**Replacement.** `improvement_proposal.experience_refs: List[str]` (≤8 opaque refs, ≤256 chars each) plus the optional common `execution_run_id`. If a caller later needs a first-class run record, MKEP already has one.

### 3.2 Cut: `promotion_event` as a distinct family

**Rationale.** Promotion and rejection are the same thing structurally: an explicit, attributable, guarded status transition on a proposal. Two families would mean two guard implementations, two projection branches, and a `rejected` state with no home.

**Replacement.** One family, `improvement_proposal_status`, carrying `from_status` / `to_status`. `to_status == "promoted"` is the explicit promotion event; it is not reachable except through the transition table in §5.3. Nothing is lost: promotion remains a distinct, separately-guarded, separately-auditable append.

### 3.3 Shipped families (five)

| `record_type` | Purpose | Mutable? |
|---|---|---|
| `improvement_proposal` | Immutable declaration of a proposed change, bound to a target artifact, a base revision, and a candidate revision digest. | No |
| `evaluation_receipt` | Caller-supplied verdict about one proposal, bound to the exact (candidate_digest, base_revision_id) pair it judged. | No |
| `improvement_proposal_status` | Guarded lifecycle transition, including promotion and rejection. | Append-only |
| `artifact_revision` | Immutable registration of a content-addressed revision of an artifact. Body never stored. | No |
| `artifact_activation` | Compare-and-swap pointer move to a registered revision. Rollback is one of these. | Append-only |

`artifact_revision` and `artifact_activation` stay separate because rollback *requires* activating a revision that already exists and must not be re-registered.

---

## 4. Common fields

Every improvement record carries, before family-specific fields:

```
schema_version: 1                 # forced by store_core.append
record_type: str                  # one of the five
improvement_schema: 1             # this feature's own schema axis
emitted_at: str                   # canonical_timestamp(now); `now` is injected, never read from the clock
privacy: "public_safe" | "local_private" | "private_pointer"   # default "local_private"
authority_claim: "agent" | "human" | "system"                  # advisory, default "agent"
authority_verified: False         # always; caller-supplied True is a hard error
scope: "session" | "project" | "profile" | "shared"
host_authorization_ref: Optional[str]   # opaque, ≤256 chars; required for profile/shared
execution_run_id: Optional[str]   # ^[a-z0-9]{8,64}$, correlation only
operation_id: str                 # resolved: caller value, else digest(record)
event_seq: int                    # core-allocated, monotonic per log
id, created_at                    # forced by store_core.append
```

**No `goal_id` anywhere.** [DES]

**Bounds.** `_MAX_STRING = 512`, `_MAX_REF = 256`, `_MAX_LIST = 8`, `_MAX_REQUIRED_EVALUATIONS = 8`, digests `^[0-9a-f]{64}$`, ids `^[a-z0-9][a-z0-9._-]{2,79}$`. Identical validator style to `execution_state._pattern/_text/_text_list/_enum` **[VF `execution_state.py:126-192`]**, reimplemented locally (≈40 lines) rather than imported, so MKEP's validators stay free to change under their own conformance kit.

### 4.1 Blast radius / scope, and why it fails closed

`scope` is an explicit closed enum of four values. [DES]

- `session`, `project` — accepted on a bare model claim. Their blast radius is the caller's own working tree, which the caller already has write access to.
- `profile`, `shared` — a model claim is **not** sufficient. The record must carry a non-empty `host_authorization_ref`, an opaque string the host issued. Missing → `E_IMPROVEMENT_SCOPE_UNAUTHORIZED`, nothing written.

Core **does not verify** `host_authorization_ref`. It stores it, projects it, and `authority_verified` stays `false`. The docstring and the docs page must say this in the same sentence as the field name: *this is an attribution breadcrumb the host can later audit, not an authorization check MemKraft performed.* No auth is invented in P0.

---

## 5. Family schemas and transitions

### 5.1 `improvement_proposal` (immutable)

```
proposal_id: str            # ^[a-z0-9][a-z0-9._-]{2,79}$, unique in log
artifact_id: str            # ^[a-z0-9][a-z0-9._-]{2,79}$
base_revision_id: Optional[str]   # the artifact_revision this proposal was authored against; None = artifact not yet registered
candidate_digest: str       # sha256 of the proposed content, computed by the caller
candidate_locator: Optional[str]  # opaque ≤256 chars; where the body lives. Never fetched.
summary: str                # ≤512 chars, human-readable
rationale: str              # ≤512 chars
evidence_refs: List[str]    # ≤8 opaque refs
experience_refs: List[str]  # ≤8 opaque refs
required_evaluations: List[str]   # 1–8 unique evaluation_kind names that must PASS before promotion
```
Uniqueness enforced on `("improvement_proposal", ("proposal_id",))` via the same `declared["unique"]` mechanic MKEP uses **[VF `execution_state.py:382-410`]**.

**`required_evaluations` must contain 1–8 unique kinds.** P0 never permits evidence-free promotion. A host that wants a lightweight gate must still name and record at least one evaluation kind; missing or empty input is rejected before append. [DES]

### 5.2 `evaluation_receipt` (immutable)

```
proposal_id: str            # must exist
evaluation_kind: str        # ≤80 chars, matches an entry in required_evaluations or is extra
verdict: "pass" | "fail" | "inconclusive"
evaluated_candidate_digest: str   # MUST equal proposal.candidate_digest
evaluated_base_revision_id: Optional[str]  # MUST equal proposal.base_revision_id
evidence_refs: List[str]    # ≤8
notes: Optional[str]        # ≤512
```
Core computes no verdict. It checks that the candidate digest and base revision exactly equal the immutable proposal bindings at append time, and rechecks those bindings plus the artifact's active base at promotion time (§5.4). For a new artifact, both proposal and receipt use `None` as the base.

### 5.3 `improvement_proposal_status`

```
proposal_id: str
from_status: str            # must equal the projected current status
to_status: str
reason: Optional[str]       # ≤512
promoted_revision_id: Optional[str]   # required iff to_status == "promoted"
```

Projected statuses and the complete transition table:

| from | to | allowed | guard |
|---|---|---|---|
| `draft` | `under_evaluation` | ✅ | — |
| `draft` | `rejected` | ✅ | — |
| `draft` | `promoted` | ❌ | `E_IMPROVEMENT_TRANSITION` |
| `under_evaluation` | `promoted` | ✅ | §5.4 promotion gate |
| `under_evaluation` | `rejected` | ✅ | — |
| `rejected` | `promoted` | ❌ | `E_IMPROVEMENT_TRANSITION` |
| `rejected` | anything | ❌ | terminal |
| `promoted` | anything | ❌ | terminal |
| any | same value | ❌ | `E_IMPROVEMENT_TRANSITION` (no no-op transitions) |

A proposal with no status record projects as `draft`. `from_status` mismatch against the projection → `E_IMPROVEMENT_TRANSITION` with `{"actual": ..., "supplied": ...}`, nothing written.

### 5.4 The promotion gate (the only place evaluations matter)

To append `to_status == "promoted"`, **all** must hold against the post-lock log view:

1. `from_status == "under_evaluation"`.
2. For every kind in `proposal.required_evaluations`, the **latest receipt by `(event_seq, id)`** must exist and have `verdict == "pass"`. Missing → `E_IMPROVEMENT_EVALUATION_MISSING`; latest `fail` or `inconclusive` → `E_IMPROVEMENT_EVALUATION_FAILED`. An older pass never overrides a newer failure.
3. **Freshness.** Each satisfying receipt must have `evaluated_candidate_digest == proposal.candidate_digest` and `evaluated_base_revision_id == proposal.base_revision_id`. The artifact's currently active revision must also still equal `proposal.base_revision_id`; for a new artifact both are `None`. Any mismatch → `E_IMPROVEMENT_EVALUATION_STALE`. A changed active base requires a new proposal, not rebinding the old proposal with a new receipt.
4. `promoted_revision_id` is required and must name a previously registered `artifact_revision` for the same `artifact_id` whose `content_digest == proposal.candidate_digest` and whose `proposal_id` equals this proposal.

Promotion **does not activate anything.** Activation is a separate call. [DES] — keeping them separate is what keeps "promoted" (a decision) distinct from "active" (a state of the world), and is what makes rollback expressible without un-deciding anything.

### 5.5 `artifact_revision` (immutable)

```
artifact_id: str
revision_id: str            # ^[a-z0-9][a-z0-9._-]{2,79}$, unique per (artifact_id, revision_id)
content_digest: str         # sha256 hex, caller-computed
locator: Optional[str]      # opaque ≤256; never fetched, never parsed
parent_revision_id: Optional[str]   # must exist for same artifact if supplied
proposal_id: Optional[str]  # lineage; must exist if supplied
provenance_refs: List[str]  # ≤8 opaque
```
No body. No diff. No command. Enforced by a test that greps the module for `subprocess`, `open(`, `urllib`, `exec`, `eval`.

### 5.6 `artifact_activation` (CAS)

```
artifact_id: str
to_revision_id: str              # must be a registered revision of this artifact
expected_active_revision_id: Optional[str]   # CAS operand; None means "expect nothing active yet"
from_revision_id: Optional[str]  # core-allocated == the observed active revision; not caller-supplied
activation_kind: "activate" | "rollback"
proposal_id: Optional[str]       # lineage
external_receipt_ref: Optional[str]  # opaque ≤256; the host's proof it actually applied the change
reason: Optional[str]            # ≤512
```

**CAS.** Under the lock, project the artifact's current active revision. If it differs from `expected_active_revision_id` → `E_IMPROVEMENT_ACTIVATION_CONFLICT` with `{"actual", "expected"}`, nothing written. Otherwise `from_revision_id` is set by core to the observed value and the record is appended. Because allocation happens inside the same lock as the read, no two activations can both believe they won; the projection therefore has exactly one active revision per artifact at every prefix of the log. [DES]

**Rollback** is `activation_kind == "rollback"` targeting an already-registered revision. It appends; it never deletes, tombstones, or rewrites. It keeps full lineage: `from_revision_id`, `to_revision_id`, `proposal_id`, `external_receipt_ref`. Core additionally requires that a rollback target has been active before (i.e. appears as `to_revision_id` of an earlier activation for this artifact) → otherwise `E_IMPROVEMENT_ROLLBACK_TARGET` — because "rollback" to something never active is an activation, and calling it a rollback would launder the audit trail. [DES]

---

## 6. Public API (exact signatures)

All methods live on `ImprovementLedgerMixin` in `src/memkraft/improvement_ledger.py`. All take a required keyword-only `now`. All writers accept `operation_id=None`. All writers return the MKEP-shaped result dict:

```python
{"outcome": "applied" | "already_applied", "record_id": str, "event_seq": int,
 "operation_id": str, "record_fingerprint": str, "record": Dict[str, Any]}
```

```python
def improvement_propose(self, proposal_id, artifact_id, summary, rationale,
                        candidate_digest, *, now, base_revision_id=None,
                        candidate_locator=None, evidence_refs=(),
                        experience_refs=(), required_evaluations=(),
                        scope="project", host_authorization_ref=None,
                        privacy="local_private", authority_claim="agent", authority_verified=False,
                        execution_run_id=None, operation_id=None): ...

def improvement_record_evaluation(self, proposal_id, evaluation_kind, verdict,
                                  evaluated_candidate_digest, *, now,
                                  evaluated_base_revision_id=None,
                                  evidence_refs=(), notes=None,
                                  scope="project", host_authorization_ref=None,
                                  privacy="local_private", authority_claim="agent", authority_verified=False,
                                  execution_run_id=None, operation_id=None): ...

def improvement_set_status(self, proposal_id, from_status, to_status, *, now,
                           reason=None, promoted_revision_id=None,
                           scope="project", host_authorization_ref=None,
                           privacy="local_private", authority_claim="agent", authority_verified=False,
                           execution_run_id=None, operation_id=None): ...

def artifact_register_revision(self, artifact_id, revision_id, content_digest, *,
                               now, locator=None, parent_revision_id=None,
                               proposal_id=None, provenance_refs=(),
                               scope="project", host_authorization_ref=None,
                               privacy="local_private", authority_claim="agent", authority_verified=False,
                               execution_run_id=None, operation_id=None): ...

def artifact_activate_revision(self, artifact_id, to_revision_id, *, now,
                               expected_active_revision_id=None,
                               activation_kind="activate", proposal_id=None,
                               external_receipt_ref=None, reason=None,
                               scope="project", host_authorization_ref=None,
                               privacy="local_private", authority_claim="agent", authority_verified=False,
                               execution_run_id=None, operation_id=None): ...

def artifact_rollback_revision(self, artifact_id, to_revision_id, *, now,
                               expected_active_revision_id=None, **kwargs): ...
    # thin wrapper: activation_kind="rollback". ~4 lines. Kept because a
    # rollback call site that reads `activate(..., kind="rollback")` is a
    # future misread waiting to happen.

# --- reads: zero writes, no lock upgrade, pure over the log ---
def improvement_project(self, *, now, artifact_id=None, proposal_id=None): ...
def improvement_plan_promotion(self, proposal_id, promoted_revision_id, *, now): ...
def improvement_plan_activation(self, artifact_id, to_revision_id, *, now,
                                expected_active_revision_id=None,
                                activation_kind="activate"): ...
```

### 6.1 Dry-run contract

`improvement_plan_promotion` and `improvement_plan_activation` run the *identical* guard functions the writers run, against a read-only snapshot, and return:

```python
{"ok": bool, "blockers": [{"code": str, "message": str, "details": {...}}, ...],
 "current_status": str | None, "active_revision_id": str | None,
 "required_evaluations": [{"evaluation_kind": str, "satisfied": bool,
                           "verdict": str | None, "stale": bool}, ...],
 "snapshot_event_seq": int}
```
They never take the write lock, never call `store_core.append`, and never raise on a blocked plan — blockers are data. Guards are shared pure functions (`_check_promotion(view, proposal, ...) -> List[blocker]`) so the dry run cannot drift from the enforced path. [DES] Tested by asserting file mtime, size, and line count are unchanged, and by asserting the module-level `append` symbol is never called (monkeypatched to raise).

### 6.2 Projection shape

```python
{"schema": 1, "generated_at": <canonical now>, "high_water_seq": int,
 "skipped_lines": int,
 "proposals": {proposal_id: {"status": str, "artifact_id": str,
                             "candidate_digest": str, "required_evaluations": [...],
                             "evaluations": {kind: {"verdict", "evaluated_candidate_digest",
                                                    "evaluated_base_revision_id", "event_seq"}},
                             "status_history": [{"from_status","to_status","event_seq"}]}},
 "artifacts": {artifact_id: {"active_revision_id": str | None,
                             "revisions": {revision_id: {"content_digest", "parent_revision_id",
                                                         "proposal_id", "event_seq"}},
                             "activations": [{"from_revision_id","to_revision_id",
                                              "activation_kind","proposal_id",
                                              "external_receipt_ref","event_seq"}]}}}
```
Fold order is `(event_seq, id)` — total and stable **[VF pattern: `execution_projection.py:109-117, 295`]**. Replaying the same lines in any file order yields a byte-identical `mkcjson` digest.

### 6.3 Corrupt lines

`store_core.read_all` already skips and counts unparseable lines **[VF `store_core.py:226-268`]**. Improvement semantics on top:
- The count surfaces as `projection["skipped_lines"]` (reporting, not silence).
- **Writes fail closed when `skipped_lines > 0`**: `_improvement_append` raises `E_IMPROVEMENT_LOG_CORRUPT` before any guard, because a guard reasoning over a partial view could promote past an evaluation it cannot see. Reads still work and still report. [DES] This is the deliberate asymmetry: a damaged ledger stays readable and auditable but stops accepting new decisions.
- A structurally-parseable line missing `event_seq` or `record_type` is counted as corrupt by the projection and triggers the same write-side failure.

### 6.4 Error codes (module-local registry, not MKEP's)

`E_IMPROVEMENT_VALIDATION`, `E_IMPROVEMENT_PATTERN`, `E_IMPROVEMENT_NOT_FOUND`, `E_IMPROVEMENT_ALREADY_EXISTS`, `E_IMPROVEMENT_IDEMPOTENCY_MISMATCH`, `E_IMPROVEMENT_TRANSITION`, `E_IMPROVEMENT_EVALUATION_MISSING`, `E_IMPROVEMENT_EVALUATION_FAILED`, `E_IMPROVEMENT_EVALUATION_STALE`, `E_IMPROVEMENT_ACTIVATION_CONFLICT`, `E_IMPROVEMENT_ROLLBACK_TARGET`, `E_IMPROVEMENT_SCOPE_UNAUTHORIZED`, `E_IMPROVEMENT_LOG_CORRUPT`, `E_IMPROVEMENT_STORE_BUSY`, `E_AUTHORITY_VERIFIED_FORBIDDEN`.

All subclass `ExecutionError` **[VF `execution_protocol.py:107`]** so callers already catching MemKraft errors keep working. A test asserts `set(ERROR_REGISTRY)` in `execution_protocol` is unchanged from baseline.

---

## 7. Files

**Create**
- `src/memkraft/improvement_ledger.py` — the whole feature: validators, record builders, `_improvement_append`, guards, `ImprovementLedgerMixin`, `project_improvement(records, now, ...)` pure function. Target ≤ 600 lines.
- `tests/test_improvement_ledger.py` — primary test file.
- `docs/IMPROVEMENT_LEDGER.md` — API + contracts + explicit non-goals + the "we did not verify authorization" statement.
- `docs/plans/2026-08-06-memkraft-continual-improvement-ledger-preview-plan.md` — this file.

**Modify**
- `src/memkraft/__init__.py` — import `ImprovementLedgerMixin`, add to both class bases alongside `ExecutionStateMixin` **[VF `__init__.py:52, 80-81, 125-126`]**, extend `__all__`.
- `docs/V3_API.md` — one section linking to `docs/IMPROVEMENT_LEDGER.md`.

**Explicitly not modified:** `execution_*.py` (all), `store_core.py`, `cli.py`, `mcp.py`, `pyproject.toml`, `CHANGELOG.md`, any version file, any MKEP registry.

**Deviation note (planning req. 10).** No separate projection module. The projection is ~110 lines of pure folding over five record types and shares every constant and status name with the writers; splitting it would create an import pair that must be kept in lockstep for no isolation benefit. `execution_projection.py` exists as a separate module because it is consumed by the dispatcher, the CLI, the MCP projection, and a cache — this projection has exactly one consumer. If the module exceeds ~650 lines during Slice 3, split at that point, not before.

---

## 8. Implementation — three TDD micro-slices + integration

Each slice: RED command → expected failure → minimal GREEN → focused verification → adjacent verification → commit boundary. No slice is committed with a failing adjacent check.

### Slice 1 — Substrate, proposals, revision registration, idempotency, corrupt-line fail-closed

**RED**
```bash
python -m pytest tests/test_improvement_ledger.py -x -q -k "slice1"
```
**Expected failure:** `ModuleNotFoundError: No module named 'memkraft.improvement_ledger'` (collection error), then after the stub: `AttributeError: 'MemKraft' object has no attribute 'improvement_propose'`.

**Tests in this slice**
- proposal append returns `outcome == "applied"`, `event_seq == 1`, record has no `goal_id` key.
- second proposal → `event_seq == 2`; sequence is monotonic across families later.
- exact retry with same `operation_id` → `already_applied`, same `record_id`, log line count unchanged.
- same `operation_id`, changed `summary` → `E_IMPROVEMENT_IDEMPOTENCY_MISMATCH`, `differing_keys == ["summary"]`, line count unchanged.
- duplicate `proposal_id` → `E_IMPROVEMENT_ALREADY_EXISTS`, nothing written.
- `authority_claim="human"` still stores `authority_verified is False`; passing `authority_verified=True` → `E_AUTHORITY_VERIFIED_FORBIDDEN`.
- `scope="shared"` without `host_authorization_ref` → `E_IMPROVEMENT_SCOPE_UNAUTHORIZED`, nothing written; with a ref → applied, and `authority_verified` is still `False`.
- bounds: 513-char `summary`, 9 `evidence_refs`, bad `candidate_digest` → `E_IMPROVEMENT_VALIDATION` / `E_IMPROVEMENT_PATTERN`.
- register an immutable artifact revision; duplicate `(artifact_id, revision_id)`, missing parent, or missing proposal lineage fails closed.
- corrupt line appended by hand → next write raises `E_IMPROVEMENT_LOG_CORRUPT`; `improvement_project` still returns with `skipped_lines == 1`.

**GREEN (minimal):** module skeleton, validators, `_common_improvement_fields`, `_improvement_append` (lock → read → corrupt check → uniqueness → idempotency scan → guard → seq → append), `improvement_propose`, `artifact_register_revision`, a projection containing proposal/revision declarations plus high-water/corruption metadata, and the minimal `ImprovementLedgerMixin` wiring into both public `MemKraft` classes. Public wiring is additive and lands here so every slice tests the real API; Slice 4 only completes exports/docs and end-to-end coverage.

**Focused verification**
```bash
python -m pytest tests/test_improvement_ledger.py -q
```
**Adjacent verification**
```bash
python -m pytest tests/test_store_core.py tests/test_store_core_concurrency.py \
  tests/test_execution_records.py tests/test_execution_baseline.py -q
```
**Commit:** `feat: add improvement ledger substrate, proposals, and revisions`

---

### Slice 2 — Evaluations, lifecycle transitions, promotion gate, dry-run

**RED**
```bash
python -m pytest tests/test_improvement_ledger.py -x -q -k "slice2"
```
**Expected failure:** `AttributeError: 'MemKraft' object has no attribute 'improvement_record_evaluation'`.

**Tests**
- receipt for unknown `proposal_id` → `E_IMPROVEMENT_NOT_FOUND`.
- receipt whose `evaluated_candidate_digest` ≠ proposal's → `E_IMPROVEMENT_VALIDATION`, nothing written.
- `draft → promoted` → `E_IMPROVEMENT_TRANSITION`; `rejected → promoted` → same; `promoted → rejected` → same; `draft → draft` → same.
- `from_status` disagreeing with projection → `E_IMPROVEMENT_TRANSITION` with `{"actual","supplied"}`.
- `under_evaluation → promoted` with a required kind never recorded → `E_IMPROVEMENT_EVALUATION_MISSING`.
- same, with `verdict="fail"` → `E_IMPROVEMENT_EVALUATION_FAILED`; with `"inconclusive"` → same code.
- fail then a later pass for the same kind → promotion succeeds; pass then a later fail → promotion fails (latest-by-`(event_seq,id)` per kind wins).
- proposal with empty `required_evaluations` is rejected at declaration; no evidence-free promotion path exists.
- promotion without `promoted_revision_id`, with an unregistered revision, or with a revision whose digest/proposal lineage differs is rejected.
- `improvement_plan_promotion(proposal_id, promoted_revision_id, ...)` validates the same mandatory revision and evaluation guards as the writer; blocked plans return `ok=False` with the same codes and write nothing (mtime, size, line count unchanged; `append` monkeypatched to raise is never hit). Active-base drift is implemented in the shared guard here but its end-to-end activation scenario is tested in Slice 3 after activation exists.
- promotion is idempotent under the same `operation_id`; a second promote with a new id → `E_IMPROVEMENT_TRANSITION` (terminal).

**GREEN:** `improvement_record_evaluation`, `improvement_set_status`, `_TRANSITIONS` table, pure `_check_promotion(view, proposal) -> List[blocker]`, `improvement_plan_promotion`, and the proposal half of the projection.

**Focused / adjacent verification:** as Slice 1, plus `tests/test_execution_projection.py tests/test_execution_dispatcher.py`.

**Commit:** `feat: add improvement evaluation receipts and promotion gate`

---

### Slice 3 — CAS activation, rollback, replay determinism

**RED**
```bash
python -m pytest tests/test_improvement_ledger.py -x -q -k "slice3"
```
**Expected failure:** `AttributeError: 'MemKraft' object has no attribute 'artifact_activate_revision'`.

**Tests**
- first activation with `expected_active_revision_id=None` → applied; `from_revision_id is None`.
- first activation with `expected_active_revision_id="r0"` → `E_IMPROVEMENT_ACTIVATION_CONFLICT`.
- activate `r2` with stale expectation `r0` while `r1` is active → conflict, `{"actual": "r1", "expected": "r0"}`, nothing written.
- activate unregistered revision → `E_IMPROVEMENT_NOT_FOUND`.
- **rollback:** `r1 → r2 → rollback to r1` → applied, `activation_kind == "rollback"`, `from_revision_id == "r2"`, `to_revision_id == "r1"`, `proposal_id` and `external_receipt_ref` retained; the `r2` revision record and the `r1→r2` activation are both still present and untombstoned; log line count strictly increased.
- rollback to a revision that was never active → `E_IMPROVEMENT_ROLLBACK_TARGET`.
- **stale promotion after activation:** proposal and receipt bound to active base `r1`; `r2` is activated before promotion; promotion and its dry-run both return `E_IMPROVEMENT_EVALUATION_STALE`. Re-recording against `r2` is rejected because it does not match the proposal; caller must create a new proposal based on `r2`.
- **single active invariant:** after any sequence, `len([a for a in projection active]) == 1` per artifact; a property-style loop over 30 scripted operations asserts it at every prefix.
- **replay determinism:** project the full log twice, and project a copy of the file rebuilt in shuffled *physical* order (same `event_seq` values) → identical `digest(projection)`.
- `improvement_plan_activation` blocked case writes nothing.
- no-body guard: `grep` the module source for `subprocess|urllib|socket|\bexec\(|\beval\(|os.system` → no match.
- no-scheduler guard: no record field name in `{next_check_at, retry_after, poll_interval, cadence, cron, due_at}`.

**GREEN:** `artifact_activate_revision` (CAS guard inside the lock), `artifact_rollback_revision`, `improvement_plan_activation`, and activation history/active-pointer projection.

**Focused / adjacent verification:** as above plus `tests/test_execution_projection_cache.py tests/test_execution_leases.py`.

**Commit:** `feat: add artifact revisions, CAS activation, and rollback lineage`

---

### Slice 4 — Integration wiring, docs, full regression (additive only)

- `__init__.py`: public class-base wiring already landed in Slice 1; now extend `__all__` if not already required by that wiring and verify imports remain additive.
- Integration test in the same file: one end-to-end story — propose → register candidate revision → under_evaluation → two receipts → dry-run says ok → promote the registered revision → activate with CAS → a competing activation conflicts → rollback → projection shows correct active revision and full lineage — asserted purely through the public `MemKraft` object, with no private helper touched.
- Python 3.9 grammar gate (below).
- `docs/IMPROVEMENT_LEDGER.md` + one link section in `docs/V3_API.md`.

**Commit:** `feat: wire improvement ledger into public API and document it`

---

## 9. Acceptance matrix

| # | Contract | Assertion | Slice |
|---|---|---|---|
| A1 | Idempotent retry | same `operation_id` + same payload → `already_applied`, identical `record_id`, line count unchanged | 1 |
| A2 | Idempotency mismatch | same `operation_id` + different payload → `E_IMPROVEMENT_IDEMPOTENCY_MISMATCH`, `differing_keys` non-empty, line count unchanged | 1 |
| A3 | Dry-run no write | `plan_*` with `append` monkeypatched to raise → returns normally; file size, mtime, line count unchanged | 2, 3 |
| A4 | Transition guards | `draft→promoted`, `rejected→promoted`, `promoted→*`, self-transition all `E_IMPROVEMENT_TRANSITION` | 2 |
| A5 | Evaluation completeness | empty required list rejected; missing → `E_IMPROVEMENT_EVALUATION_MISSING`; latest fail/inconclusive → `E_IMPROVEMENT_EVALUATION_FAILED` | 2 |
| A6 | Evaluation freshness | receipt must equal proposal candidate/base bindings and active base must remain unchanged; rebinding an old proposal is rejected | 2 |
| A6b | Promotion revision binding | registered revision is mandatory and must match artifact, candidate digest, and proposal lineage | 2 |
| A7 | CAS | mismatched `expected_active_revision_id` → `E_IMPROVEMENT_ACTIVATION_CONFLICT`, nothing written | 3 |
| A8 | Single active | at every log prefix, exactly ≤1 active revision per artifact in projection | 3 |
| A9 | Rollback | new activation record, lineage retained, zero deletions/tombstones, line count increases | 3 |
| A10 | Rollback honesty | rollback to never-active revision → `E_IMPROVEMENT_ROLLBACK_TARGET` | 3 |
| A11 | Replay determinism | two projections + shuffled-physical-order projection have identical `digest(...)` | 3 |
| A12 | Corrupt records | projection reports `skipped_lines`; writes raise `E_IMPROVEMENT_LOG_CORRUPT` | 1 |
| A13 | Scope fails closed | `profile`/`shared` without `host_authorization_ref` → `E_IMPROVEMENT_SCOPE_UNAUTHORIZED` | 1 |
| A14 | No invented auth | `authority_verified is False` on every stored record; caller `True` → hard error | 1 |
| A15 | No content storage | source grep finds no exec/network/file-read symbols; no field stores a body or command | 3 |
| A16 | Monotonic seq | `event_seq` strictly increasing across all five families in one log | 1–3 |
| A16b | Dry-run/writer parity | promotion dry-run and writer receive the same promoted revision and execute the same pure guards | 2 |
| A17 | No `goal_id` | no improvement record contains `goal_id` | 1 |
| A18 | Python 3.9 grammar | `ast.parse` under a 3.9 feature-version check passes | 4 |

**A18 command**
```bash
python - <<'PY'
import ast, pathlib
src = pathlib.Path("src/memkraft/improvement_ledger.py").read_text()
ast.parse(src, feature_version=(3, 9))
print("py39-grammar OK")
PY
```
Plus a manual scan for `X | Y` annotations and PEP 585 runtime subscripting, which `feature_version` does not catch.

---

## 10. Regression and MKEP/0 compatibility

**Full suite**
```bash
python -m pytest tests/ -q
```

**MKEP/0 registry unchanged** — must be run before and after, output diffed:
```bash
git diff --stat dfc9b9e -- src/memkraft/execution_dispatch.py \
  src/memkraft/execution_protocol.py src/memkraft/execution_state.py \
  src/memkraft/execution_projection.py src/memkraft/execution_handoff.py \
  src/memkraft/execution_cli.py src/memkraft/mcp.py src/memkraft/cli.py \
  src/memkraft/store_core.py
# expected: no output
```
```bash
python - <<'PY'
from memkraft.execution_dispatch import _OPS, MCP_OPS
from memkraft.execution_protocol import ERROR_REGISTRY
assert len(_OPS) == 15, len(_OPS)
print("ops:", sorted(_OPS))
print("mcp_ops:", list(MCP_OPS))
print("errors:", sorted(ERROR_REGISTRY))
PY
```
The op list, MCP op list, and error-code list must be byte-identical to the same script's output at `dfc9b9e`. A test in `tests/test_improvement_ledger.py` pins `len(_OPS) == 15` and the sorted error-code tuple so a future edit fails loudly.

**Compatibility checks**
- `pip install -e . && python -c "import memkraft; memkraft.MemKraft"` succeeds.
- No new third-party import: `grep -nE "^(import|from) " src/memkraft/improvement_ledger.py` yields only stdlib plus `.store_core` / `.execution_protocol`.
- Existing `.memkraft/` layouts untouched: the new log is created lazily on first write; a base dir with no `improvement/` directory projects to an empty view without creating anything.
- Canonical source preserved: no existing public symbol renamed, removed, or re-exported differently; `__all__` only grows.

---

## 11. YAGNI review of this plan (self-review pass)

Trimmed during review, recorded so the trims are not silently re-added:

1. **`experience_run` record family — cut.** Became two bounded ref lists on the proposal. (§3.1)
2. **`promotion_event` family — cut.** Merged into `improvement_proposal_status`. Five families, not six. (§3.2)
3. **Separate projection module — cut.** One consumer, shared constants. Split trigger documented (~650 lines). (§7)
4. **A `superseded` proposal status — cut.** `rejected` covers "not going forward"; a fifth status with no distinct guard is vocabulary, not capability.
5. **Score / metric / threshold fields on `evaluation_receipt` — cut.** Core does not evaluate; a `score` field invites a comparison MemKraft must not make. `verdict` + opaque `evidence_refs` is sufficient and honest.
6. **Auto-activation on promotion — cut.** Never proposed as a feature; explicitly recorded as forbidden so nobody "helpfully" adds it. (§5.4)
7. **Batch/multi-record write API — cut.** One record per call. Callers loop.
8. **Compaction / migration for the improvement log — cut.** Never compacted, exactly like the execution log (D-15 precedent).
9. **A `scope` beyond the four-value enum — cut.** No `custom` escape hatch; an open scope string would defeat §4.1's fail-closed rule.
10. **CLI/MCP/MKEP op surface — cut by mandate and re-checked here.** Adding one op would fork the frozen 15-entry registry and require a conformance-kit revision. Python API only.
11. **`artifact_rollback_revision` wrapper — kept** despite being 4 lines, on legibility grounds; this is the one place the plan spends complexity budget on ergonomics, and the reason is written at the call site.

Remaining surface: 5 record families, 6 write methods (one a wrapper), 3 read methods, 1 module, 1 test file, 2 docs touches, 1 wiring edit.

---

## 12. Deferred (with defaults, not commitments)

| ID | Item | Default if never revisited |
|---|---|---|
| D-I1 | Verified-authority adapter that could set `authority_verified=True` | Stays absent; the field stays `false` forever. Nothing depends on it flipping. |
| D-I2 | MKEP op / CLI / MCP exposure | Never. Requires a protocol version and a conformance-kit revision, which is a different feature. |
| D-I3 | Cross-artifact atomic activation (activate N artifacts as one unit) | Not offered. It is distributed-commit-shaped and the standing MKEP No-Go on consensus applies. |
| D-I4 | Projection cache | Not built. Preview logs are small; measure before caching. |
| D-I5 | Proposal supersession chains | Not built. Callers express it with `parent_revision_id` lineage and a new proposal. |



