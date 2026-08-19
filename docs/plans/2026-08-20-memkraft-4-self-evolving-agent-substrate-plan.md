# MemKraft 4.0 Self-Evolving Agent Substrate

**Status:** implemented in 4.0.0; retained as design rationale  
**Baseline:** MemKraft 3.8.0  
**Theme:** Experience → Evaluation → Promotion → Activation → Rollback  
**Decision rule:** 4.0 is justified only if this lifecycle becomes a stable, replayable public contract without granting MemKraft execution or self-authorization power.

## 1. Executive decision

MemKraft 4.0 should not be a model-training release. It should be a **Self-Evolving Agent Substrate** release: a durable governance and evidence layer that lets a host agent turn repeated experience into safer revisions of its surrounding harness.

The unit of improvement moves from:

```text
model
→ model + memory
→ model + agent system
→ model + evaluated, versioned, reversible agent system
```

A 4.0-compatible host may improve a Memory policy, Skill, Tool adapter, Prompt, Router, Planner, Reviewer, or Workflow. MemKraft stores the causal lineage and enforces evidence, version binding, explicit authority boundaries, compare-and-swap activation, and rollback. The host remains responsible for model calls, evaluation execution, artifact mutation, deployment, scheduling, and authorization.

## 2. Why 3.8 is the correct baseline

The 3.8.0 release already contains the required primitives:

- Continual Improvement Ledger: proposal, evaluation receipt, explicit promotion, artifact revision, activation, rollback.
- Execution protocol: goals, gates, receipts, leases, assessments, and handoffs.
- Outcome and provenance records for linking experience to later decisions.
- Project Memory Compiler for deterministic project-scoped context.
- Focus and Decision Authority ledgers for bounded ambient context and provenance.
- Fail-closed validation, append-only records, idempotency, CAS activation, and deterministic projection.
- Search, evidence context, numeric aggregation, correction policy, and evaluation corpus support.

The missing piece is not another isolated ledger. It is a stable composition contract that defines how a host converts these records into an improvement loop and how an evaluator proves that a candidate is better for a declared scope.

## 3. Core public contract

### 3.1 Experience

An experience is an opaque reference to a completed or failed host run. MemKraft does not infer quality from raw conversation text and does not silently promote every failure into a lesson.

Required properties:

- stable `experience_id` or host-owned reference;
- task and artifact scope;
- input/context snapshot reference;
- outcome reference;
- tool and model metadata as opaque, privacy-scoped references;
- failure or correction classification;
- replayability status;
- redaction/privacy declaration.

Experience records must distinguish:

- observed fact: what happened;
- diagnosis: why the host believes it happened;
- proposed change: what should be different;
- evaluation result: whether the change helped.

These must never be collapsed into one mutable memory item.

### 3.2 Improvement proposal

The existing `improvement_proposal` becomes the canonical proposal record. 4.0 adds a closed artifact-kind enum:

```text
memory_policy | skill | tool_adapter | prompt | router | planner |
reviewer | workflow | evaluator_config
```

Each proposal remains bound to:

- artifact id and kind;
- immutable base revision;
- candidate digest and opaque locator;
- experience references;
- rationale and expected behavior change;
- required evaluation kinds;
- declared scope and blast radius;
- rollback target or rollback strategy.

MemKraft stores identifiers and digests, never executable artifact bodies, secrets, patches, or commands.

### 3.3 Evaluator receipt

An evaluator is a host-provided adapter, not a MemKraft-owned model judge. It executes a declared test/evaluation and returns an immutable receipt.

A receipt must include:

- evaluator id and evaluator version;
- evaluation kind;
- candidate digest and base revision id;
- corpus/replay-set digest;
- metric definitions and aggregation policy references;
- baseline result reference;
- candidate result reference;
- pass/fail/inconclusive verdict;
- variance/confidence information when applicable;
- environment/runtime fingerprint;
- evidence references;
- evaluator exit status and timeout classification.

The core must reject a receipt that omits the candidate/base binding or claims a pass without a verifiable host evidence reference. MemKraft does not calculate domain metrics, but it must preserve enough metadata to prevent an evaluator result from being detached from the artifact and corpus it judged.

### 3.4 Promotion

Promotion remains explicit. A passing receipt is necessary but not sufficient.

Promotion requires:

1. all required evaluator kinds have a latest passing receipt;
2. receipts match the exact candidate digest and base revision;
3. the active base has not changed;
4. declared regression gates pass;
5. scope is authorized by the host;
6. a human or authorized host decision is recorded separately from the model's proposal;
7. the candidate revision is already registered;
8. rollback information is present for non-initial activation.

No evaluator receipt may directly mutate proposal status. No model output may be treated as authority.

### 3.5 Activation and rollback

Activation is a separate state-of-the-world change. The host applies the artifact, then records the external receipt. MemKraft records the CAS transition and keeps every prior activation event.

Rollback is a new activation event pointing to a previously active revision. It is never deletion, mutation, or history rewrite.

4.0 should add an optional activation health window record, but not an automatic scheduler. The host may later report post-activation outcomes against the activated revision; an automated rollback policy belongs to the host adapter and must require explicit authorization.

## 4. Self-evolving loop

```text
1. Host executes a task
2. Host records outcome and correction/failure evidence
3. Candidate extractor proposes a bounded revision
4. MemKraft stores proposal and immutable artifact revision
5. Host evaluator replays fixed corpus and live-shadow cases
6. MemKraft stores evaluator receipts
7. Authorized host decision promotes or rejects
8. Host applies the promoted artifact
9. MemKraft records activation with CAS and external receipt
10. Host observes post-activation outcomes
11. Regression or degradation creates a new proposal for rollback
12. Rollback activates the prior known-good revision
```

The loop must be **bounded**. Each cycle declares:

- maximum candidate count;
- maximum evaluation budget;
- fixed replay corpus digest;
- allowed artifact kinds;
- maximum scope;
- expiry or review deadline;
- rollback target;
- stop reason.

MemKraft records these declarations but does not schedule or execute the loop.

## 5. Architecture

### 5.1 Plugin-shaped boundaries

The 4.0 host integration should use explicit adapters:

- `ExperienceSource`: imports sanitized run/outcome references;
- `CandidateFactory`: produces an opaque candidate artifact and digest;
- `Evaluator`: executes deterministic or shadow evaluation;
- `PromotionAuthority`: records the external decision;
- `ArtifactApplier`: applies a revision outside MemKraft;
- `ActivationObserver`: reports post-activation outcomes;
- `RollbackAuthority`: authorizes and applies rollback.

These are interfaces/contracts, not hidden internal agents. Each adapter has a version and capability declaration so ablation and replay can compare different harness compositions.

### 5.2 Artifact capability manifest

Every artifact revision should optionally declare a capability manifest:

```json
{
  "artifact_kind": "skill",
  "requires": ["memory.read", "tool.kubectl"],
  "provides": ["deploy.kubernetes"],
  "side_effect_class": "external_write",
  "data_scope": "project",
  "review_required": true,
  "rollback_supported": true
}
```

This manifest is descriptive and fail-closed for missing fields. It does not grant permissions. The host policy remains the authority that decides whether a capability can execute.

### 5.3 Revision graph without mutable graph state

Keep the existing append-only event model. Add derived views for:

- proposal lineage;
- artifact revision ancestry;
- evaluator coverage;
- current active revision;
- promotion and activation history;
- rollback availability;
- post-activation health evidence.

The source of truth remains append-only records. Derived graphs are rebuildable and never authoritative.

## 6. Safety and governance requirements

4.0 must explicitly reject the following designs:

- self-modifying MemKraft core code;
- model-generated authorization;
- automatic promotion based only on self-reported confidence;
- evaluator access to secrets without host policy;
- artifact body or executable command storage in the ledger;
- silent activation from a receipt;
- replacing a failed candidate without preserving the failure;
- deleting or rewriting historical evidence;
- global/profile/shared activation without an opaque host authorization reference;
- evaluator corpus changes without a new corpus digest;
- metric definitions changing while retaining the old receipt;
- rollback to a revision that was never active or never registered.

Privacy and security:

- experience refs may be private pointers;
- user content is not copied into proposal summaries by default;
- credentials, tokens, prompts containing secrets, and raw tool payloads are prohibited from ledger records;
- cross-project references require an explicit host scope and authorization reference;
- export must support redacted public-safe projections.

## 7. 3.9.x prerequisites before 4.0

### 3.9.0: contract hardening

- Extract canonical version metadata and release checks into a reusable release helper.
- Stabilize improvement ledger schemas and error codes.
- Add artifact-kind and capability-manifest validation.
- Add evaluator receipt schema with candidate/base/corpus binding.
- Add migration/read-only projection tests.

### 3.9.1: replay and evaluator foundation

- Add a deterministic replay corpus manifest and digest contract.
- Add evaluator adapter protocol and a local reference evaluator.
- Add baseline-versus-candidate comparison receipts.
- Add tests for stale corpus, changed metric definition, timeout, inconclusive verdict, and partial evidence.

### 3.9.2: activation observation

- Add post-activation observation records.
- Add health regression and rollback readiness projections.
- Add shadow activation/read-only plan support in the host adapter.
- Do not enable automatic rollback by default.

### 3.9.3: compatibility freeze

- Freeze 4.0 public schemas.
- Publish migration guide and 3.x deprecation schedule.
- Run replay benchmarks across all artifact kinds.
- Verify fresh wheel, installed runtime, release manifest, Git tag, and source import convergence in CI.

## 8. 4.0 breaking changes

Potential breaking changes should be limited to contracts that are currently explicitly preview or deprecated:

- remove old `search_v2`, `search_smart`, and `search_hybrid` aliases only after the documented deprecation window;
- promote improvement records from Preview schema 1 to stable schema 2 only with an explicit envelope migration;
- reject previously accepted ambiguous evaluator receipts that lack corpus or candidate binding;
- require artifact kind and scope for new proposals;
- change no existing canonical event, truth, sleep, execution, or memory semantics unless separately approved.

Existing 3.x stores must remain readable. Migration should be lazy/read-only first, then explicit and append-only. Never rewrite the original ledger in place.

## 9. Acceptance criteria

4.0 cannot be called complete unless all gates pass:

### Functional

- one experience can produce a proposal with complete provenance;
- one candidate can be evaluated against a fixed corpus;
- stale candidate/base/corpus receipts are rejected;
- promotion requires all declared evaluations and explicit authority;
- activation uses CAS and records an external application receipt;
- rollback preserves the full previous history and returns to a previously active revision;
- every projection is deterministic after JSONL line reordering;
- all operations are idempotent and mismatched retries fail closed.

### Safety

- no MemKraft API can execute a command, deploy an artifact, schedule work, or grant permission;
- no model-generated field can set `authority_verified=true`;
- no secret or artifact body is stored in the ledger;
- unauthorized profile/shared scope fails before append;
- corrupt logs fail closed and cannot accept new appends.

### Evaluation

- fixed replay benchmark includes success, failure, correction, regression, and rollback cases;
- candidate improvements report both quality and operational cost;
- evaluator variance and inconclusive outcomes are visible;
- benchmark results identify corpus, evaluator, artifact, and environment digests;
- no claim of improvement is made from a single unreplicated run.

### Release

- full regression passes;
- fresh wheel metadata equals runtime `__version__`;
- source checkout is forced for repository tests;
- release manifest, Git tag, changelog, release notes, and installed artifact converge;
- version-integrity and release-lineage gates run on every PR and main push.

## 10. Recommended first vertical slice

Do not begin with autonomous code mutation. Begin with one narrow, high-value loop:

> **Correction policy improvement for a single project-scoped host.**

The host observes repeated user corrections, creates a candidate correction-policy revision, evaluates it on a frozen correction corpus, promotes it only after explicit host authorization, activates it through the host, and can roll back to the prior policy.

Why this slice:

- MemKraft already has correction policy, outcomes, evaluation corpus, improvement ledger, and provenance primitives.
- The evaluator can be deterministic and replayable.
- The artifact is a policy, not executable code.
- The blast radius is project-scoped.
- The value is directly measurable: correction recurrence, false application, latency, and policy precision.
- It validates the entire 4.0 contract before adding Tool or Workflow self-modification.

After this slice passes, add Skill revision, then Tool adapter revision, then Workflow/Planner revision. Each higher-risk artifact kind requires a separate capability manifest, evaluator family, authorization policy, and rollback benchmark.

## 11. Final recommendation

Proceed with a **3.9.x hardening line**, not an immediate 4.0 implementation dump. The 4.0 direction is strategically strong and consistent with MemKraft's existing design, but its differentiator must be trustworthy evolution rather than unrestricted self-modification.

The product promise should be:

> MemKraft turns agent experience into evidence-backed, versioned, reversible improvements to the agent system, while keeping execution and authority outside the memory substrate.

That is a meaningful answer to Self-Evolving AI: not “the model changes itself,” but “the system learns which changes are justified, proves what they changed, and can safely return to what worked.”
