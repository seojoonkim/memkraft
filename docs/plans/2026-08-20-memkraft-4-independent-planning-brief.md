# MemKraft 4.0 independent planning brief

## User request
Create and critically review a final MemKraft 4.0 plan based on the Self-Evolving AI essay below, using three independent lanes: exact Fable5, GPT-5.6-Sol, and Grok 4.6. Do not implement code or deploy. Produce decision-grade product/technical planning input.

## Verified project baseline
- Repository: /Users/gimseojun/sano-workspace/projects/memkraft-release-sync
- Baseline release: MemKraft 3.8.0.
- Existing 3.8 preview already has append-only improvement lifecycle: proposal, artifact revision registration, evaluation receipt, explicit promotion, CAS activation, rollback, deterministic projection, idempotency and fail-closed validation.
- MemKraft is a substrate/ledger, not scheduler, executor, deployer, model router, orchestrator, evaluator judge, or authority. It must not store secrets, executable artifact bodies, commands, or silently activate/promote changes.
- Existing plan is included below and must be challenged, not merely summarized.

## Scope contract
Every lane must address all of these at top level:
1. Define MemKraft 4.0 product thesis and boundary in the Self-Evolving Agent ecosystem.
2. Decide what belongs in 3.9.x hardening versus 4.0 stable release.
3. Design the full lifecycle: experience -> candidate/proposal -> replay/evaluation -> explicit authorization/promotion -> activation -> health observation -> rollback.
4. Compare artifact kinds in a risk ladder: correction/memory policy, skill, tool adapter, prompt/router/planner/reviewer, workflow; identify what is in/out for first vertical slice.
5. Specify public data contracts, lineage, digests, corpus/evaluator binding, capability manifest, scope, authority and privacy boundaries.
6. Design evaluator and benchmark strategy with quality, regression, cost, latency, variance, inconclusive, drift and replay determinism.
7. Define failure modes, threat model, rollback/kill-switch, red-team checks and non-goals.
8. Provide prioritized milestones, acceptance criteria, Go/No-Go gates and concrete implementation handoff for the current repository.
9. Preserve existing 3.x compatibility and append-only truth model. Do not claim unverified benchmark numbers or that MemKraft itself executes or self-authorizes.

## Required output format
- Executive verdict
- What to retain/change/delete from current draft
- Proposed 4.0 architecture and contracts
- 3.9.x prerequisites and milestone plan
- First vertical slice with concrete APIs/files/tests
- Evaluation/benchmark and safety gates
- Risks, disagreements and falsification tests
- Final Go/No-Go recommendation
Clearly label verified baseline facts versus proposals/hypotheses. Be specific and concise but substantive. Korean output preferred.

## Self-Evolving AI source essay
요즘 AI의 자기개선은 모델 weight를 바꾸지 않고도 Memory, Skill, Tool, Workflow, Harness를 개선하는 방식으로 크게 발전한다. 반복 경험에서 성공 절차를 Skill로 저장하고, 반복되는 Tool 조합을 전용 Tool로 만들며, 실패 원인을 Memory로 남긴다. AlphaEvolve는 생성된 프로그램을 실제 실행해 속도·정확도·메모리 등을 측정하는 객관적 Evaluator로 더 나은 변형만 남긴다. Darwin Godel Machine은 Coding Agent가 자기 프로그램을 수정하고 테스트·Reviewer·다중 후보 선택 구조를 진화시킨다. 핵심은 모델 능력과 Agent 시스템 능력이 다르며, Memory·Tool·Context·Recovery·Evaluator·Dynamic Workflow가 실제 성능을 좌우한다는 점이다. Training과 Inference의 경계도 경험이 Memory/Skill/Tool 개선과 새 학습 데이터로 순환하면서 흐려진다. Everything is a plugin 철학처럼 Model, Tool, Skill, Session, Storage, Sandbox, Scheduling, Agent Loop를 조립 가능하게 해야 Self-Evolving이 가능하다. 단, 평가기와 권한 경계가 핵심이며 무제한 자동 자기수정으로 이해해서는 안 된다.

## Current draft (challenge it)
     1|# MemKraft 4.0 Self-Evolving Agent Substrate
     2|
     3|**Status:** proposal for design review  
     4|**Baseline:** MemKraft 3.8.0  
     5|**Theme:** Experience → Evaluation → Promotion → Activation → Rollback  
     6|**Decision rule:** 4.0 is justified only if this lifecycle becomes a stable, replayable public contract without granting MemKraft execution or self-authorization power.
     7|
     8|## 1. Executive decision
     9|
    10|MemKraft 4.0 should not be a model-training release. It should be a **Self-Evolving Agent Substrate** release: a durable governance and evidence layer that lets a host agent turn repeated experience into safer revisions of its surrounding harness.
    11|
    12|The unit of improvement moves from:
    13|
    14|```text
    15|model
    16|→ model + memory
    17|→ model + agent system
    18|→ model + evaluated, versioned, reversible agent system
    19|```
    20|
    21|A 4.0-compatible host may improve a Memory policy, Skill, Tool adapter, Prompt, Router, Planner, Reviewer, or Workflow. MemKraft stores the causal lineage and enforces evidence, version binding, explicit authority boundaries, compare-and-swap activation, and rollback. The host remains responsible for model calls, evaluation execution, artifact mutation, deployment, scheduling, and authorization.
    22|
    23|## 2. Why 3.8 is the correct baseline
    24|
    25|The 3.8.0 release already contains the required primitives:
    26|
    27|- Continual Improvement Ledger: proposal, evaluation receipt, explicit promotion, artifact revision, activation, rollback.
    28|- Execution protocol: goals, gates, receipts, leases, assessments, and handoffs.
    29|- Outcome and provenance records for linking experience to later decisions.
    30|- Project Memory Compiler for deterministic project-scoped context.
    31|- Focus and Decision Authority ledgers for bounded ambient context and provenance.
    32|- Fail-closed validation, append-only records, idempotency, CAS activation, and deterministic projection.
    33|- Search, evidence context, numeric aggregation, correction policy, and evaluation corpus support.
    34|
    35|The missing piece is not another isolated ledger. It is a stable composition contract that defines how a host converts these records into an improvement loop and how an evaluator proves that a candidate is better for a declared scope.
    36|
    37|## 3. Core public contract
    38|
    39|### 3.1 Experience
    40|
    41|An experience is an opaque reference to a completed or failed host run. MemKraft does not infer quality from raw conversation text and does not silently promote every failure into a lesson.
    42|
    43|Required properties:
    44|
    45|- stable `experience_id` or host-owned reference;
    46|- task and artifact scope;
    47|- input/context snapshot reference;
    48|- outcome reference;
    49|- tool and model metadata as opaque, privacy-scoped references;
    50|- failure or correction classification;
    51|- replayability status;
    52|- redaction/privacy declaration.
    53|
    54|Experience records must distinguish:
    55|
    56|- observed fact: what happened;
    57|- diagnosis: why the host believes it happened;
    58|- proposed change: what should be different;
    59|- evaluation result: whether the change helped.
    60|
    61|These must never be collapsed into one mutable memory item.
    62|
    63|### 3.2 Improvement proposal
    64|
    65|The existing `improvement_proposal` becomes the canonical proposal record. 4.0 adds a closed artifact-kind enum:
    66|
    67|```text
    68|memory_policy | skill | tool_adapter | prompt | router | planner |
    69|reviewer | workflow | evaluator_config
    70|```
    71|
    72|Each proposal remains bound to:
    73|
    74|- artifact id and kind;
    75|- immutable base revision;
    76|- candidate digest and opaque locator;
    77|- experience references;
    78|- rationale and expected behavior change;
    79|- required evaluation kinds;
    80|- declared scope and blast radius;
    81|- rollback target or rollback strategy.
    82|
    83|MemKraft stores identifiers and digests, never executable artifact bodies, secrets, patches, or commands.
    84|
    85|### 3.3 Evaluator receipt
    86|
    87|An evaluator is a host-provided adapter, not a MemKraft-owned model judge. It executes a declared test/evaluation and returns an immutable receipt.
    88|
    89|A receipt must include:
    90|
    91|- evaluator id and evaluator version;
    92|- evaluation kind;
    93|- candidate digest and base revision id;
    94|- corpus/replay-set digest;
    95|- metric definitions and aggregation policy references;
    96|- baseline result reference;
    97|- candidate result reference;
    98|- pass/fail/inconclusive verdict;
    99|- variance/confidence information when applicable;
   100|- environment/runtime fingerprint;
   101|- evidence references;
   102|- evaluator exit status and timeout classification.
   103|
   104|The core must reject a receipt that omits the candidate/base binding or claims a pass without a verifiable host evidence reference. MemKraft does not calculate domain metrics, but it must preserve enough metadata to prevent an evaluator result from being detached from the artifact and corpus it judged.
   105|
   106|### 3.4 Promotion
   107|
   108|Promotion remains explicit. A passing receipt is necessary but not sufficient.
   109|
   110|Promotion requires:
   111|
   112|1. all required evaluator kinds have a latest passing receipt;
   113|2. receipts match the exact candidate digest and base revision;
   114|3. the active base has not changed;
   115|4. declared regression gates pass;
   116|5. scope is authorized by the host;
   117|6. a human or authorized host decision is recorded separately from the model's proposal;
   118|7. the candidate revision is already registered;
   119|8. rollback information is present for non-initial activation.
   120|
   121|No evaluator receipt may directly mutate proposal status. No model output may be treated as authority.
   122|
   123|### 3.5 Activation and rollback
   124|
   125|Activation is a separate state-of-the-world change. The host applies the artifact, then records the external receipt. MemKraft records the CAS transition and keeps every prior activation event.
   126|
   127|Rollback is a new activation event pointing to a previously active revision. It is never deletion, mutation, or history rewrite.
   128|
   129|4.0 should add an optional activation health window record, but not an automatic scheduler. The host may later report post-activation outcomes against the activated revision; an automated rollback policy belongs to the host adapter and must require explicit authorization.
   130|
   131|## 4. Self-evolving loop
   132|
   133|```text
   134|1. Host executes a task
   135|2. Host records outcome and correction/failure evidence
   136|3. Candidate extractor proposes a bounded revision
   137|4. MemKraft stores proposal and immutable artifact revision
   138|5. Host evaluator replays fixed corpus and live-shadow cases
   139|6. MemKraft stores evaluator receipts
   140|7. Authorized host decision promotes or rejects
   141|8. Host applies the promoted artifact
   142|9. MemKraft records activation with CAS and external receipt
   143|10. Host observes post-activation outcomes
   144|11. Regression or degradation creates a new proposal for rollback
   145|12. Rollback activates the prior known-good revision
   146|```
   147|
   148|The loop must be **bounded**. Each cycle declares:
   149|
   150|- maximum candidate count;
   151|- maximum evaluation budget;
   152|- fixed replay corpus digest;
   153|- allowed artifact kinds;
   154|- maximum scope;
   155|- expiry or review deadline;
   156|- rollback target;
   157|- stop reason.
   158|
   159|MemKraft records these declarations but does not schedule or execute the loop.
   160|
   161|## 5. Architecture
   162|
   163|### 5.1 Plugin-shaped boundaries
   164|
   165|The 4.0 host integration should use explicit adapters:
   166|
   167|- `ExperienceSource`: imports sanitized run/outcome references;
   168|- `CandidateFactory`: produces an opaque candidate artifact and digest;
   169|- `Evaluator`: executes deterministic or shadow evaluation;
   170|- `PromotionAuthority`: records the external decision;
   171|- `ArtifactApplier`: applies a revision outside MemKraft;
   172|- `ActivationObserver`: reports post-activation outcomes;
   173|- `RollbackAuthority`: authorizes and applies rollback.
   174|
   175|These are interfaces/contracts, not hidden internal agents. Each adapter has a version and capability declaration so ablation and replay can compare different harness compositions.
   176|
   177|### 5.2 Artifact capability manifest
   178|
   179|Every artifact revision should optionally declare a capability manifest:
   180|
   181|```json
   182|{
   183|  "artifact_kind": "skill",
   184|  "requires": ["memory.read", "tool.kubectl"],
   185|  "provides": ["deploy.kubernetes"],
   186|  "side_effect_class": "external_write",
   187|  "data_scope": "project",
   188|  "review_required": true,
   189|  "rollback_supported": true
   190|}
   191|```
   192|
   193|This manifest is descriptive and fail-closed for missing fields. It does not grant permissions. The host policy remains the authority that decides whether a capability can execute.
   194|
   195|### 5.3 Revision graph without mutable graph state
   196|
   197|Keep the existing append-only event model. Add derived views for:
   198|
   199|- proposal lineage;
   200|- artifact revision ancestry;
   201|- evaluator coverage;
   202|- current active revision;
   203|- promotion and activation history;
   204|- rollback availability;
   205|- post-activation health evidence.
   206|
   207|The source of truth remains append-only records. Derived graphs are rebuildable and never authoritative.
   208|
   209|## 6. Safety and governance requirements
   210|
   211|4.0 must explicitly reject the following designs:
   212|
   213|- self-modifying MemKraft core code;
   214|- model-generated authorization;
   215|- automatic promotion based only on self-reported confidence;
   216|- evaluator access to secrets without host policy;
   217|- artifact body or executable command storage in the ledger;
   218|- silent activation from a receipt;
   219|- replacing a failed candidate without preserving the failure;
   220|- deleting or rewriting historical evidence;
   221|- global/profile/shared activation without an opaque host authorization reference;
   222|- evaluator corpus changes without a new corpus digest;
   223|- metric definitions changing while retaining the old receipt;
   224|- rollback to a revision that was never active or never registered.
   225|
   226|Privacy and security:
   227|
   228|- experience refs may be private pointers;
   229|- user content is not copied into proposal summaries by default;
   230|- credentials, tokens, prompts containing secrets, and raw tool payloads are prohibited from ledger records;
   231|- cross-project references require an explicit host scope and authorization reference;
   232|- export must support redacted public-safe projections.
   233|
   234|## 7. 3.9.x prerequisites before 4.0
   235|
   236|### 3.9.0: contract hardening
   237|
   238|- Extract canonical version metadata and release checks into a reusable release helper.
   239|- Stabilize improvement ledger schemas and error codes.
   240|- Add artifact-kind and capability-manifest validation.
   241|- Add evaluator receipt schema with candidate/base/corpus binding.
   242|- Add migration/read-only projection tests.
   243|
   244|### 3.9.1: replay and evaluator foundation
   245|
   246|- Add a deterministic replay corpus manifest and digest contract.
   247|- Add evaluator adapter protocol and a local reference evaluator.
   248|- Add baseline-versus-candidate comparison receipts.
   249|- Add tests for stale corpus, changed metric definition, timeout, inconclusive verdict, and partial evidence.
   250|
   251|### 3.9.2: activation observation
   252|
   253|- Add post-activation observation records.
   254|- Add health regression and rollback readiness projections.
   255|- Add shadow activation/read-only plan support in the host adapter.
   256|- Do not enable automatic rollback by default.
   257|
   258|### 3.9.3: compatibility freeze
   259|
   260|- Freeze 4.0 public schemas.
   261|- Publish migration guide and 3.x deprecation schedule.
   262|- Run replay benchmarks across all artifact kinds.
   263|- Verify fresh wheel, installed runtime, release manifest, Git tag, and source import convergence in CI.
   264|
   265|## 8. 4.0 breaking changes
   266|
   267|Potential breaking changes should be limited to contracts that are currently explicitly preview or deprecated:
   268|
   269|- remove old `search_v2`, `search_smart`, and `search_hybrid` aliases only after the documented deprecation window;
   270|- promote improvement records from Preview schema 1 to stable schema 2 only with an explicit envelope migration;
   271|- reject previously accepted ambiguous evaluator receipts that lack corpus or candidate binding;
   272|- require artifact kind and scope for new proposals;
   273|- change no existing canonical event, truth, sleep, execution, or memory semantics unless separately approved.
   274|
   275|Existing 3.x stores must remain readable. Migration should be lazy/read-only first, then explicit and append-only. Never rewrite the original ledger in place.
   276|
   277|## 9. Acceptance criteria
   278|
   279|4.0 cannot be called complete unless all gates pass:
   280|
   281|### Functional
   282|
   283|- one experience can produce a proposal with complete provenance;
   284|- one candidate can be evaluated against a fixed corpus;
   285|- stale candidate/base/corpus receipts are rejected;
   286|- promotion requires all declared evaluations and explicit authority;
   287|- activation uses CAS and records an external application receipt;
   288|- rollback preserves the full previous history and returns to a previously active revision;
   289|- every projection is deterministic after JSONL line reordering;
   290|- all operations are idempotent and mismatched retries fail closed.
   291|
   292|### Safety
   293|
   294|- no MemKraft API can execute a command, deploy an artifact, schedule work, or grant permission;
   295|- no model-generated field can set `authority_verified=true`;
   296|- no secret or artifact body is stored in the ledger;
   297|- unauthorized profile/shared scope fails before append;
   298|- corrupt logs fail closed and cannot accept new appends.
   299|
   300|### Evaluation
   301|
   302|- fixed replay benchmark includes success, failure, correction, regression, and rollback cases;
   303|- candidate improvements report both quality and operational cost;
   304|- evaluator variance and inconclusive outcomes are visible;
   305|- benchmark results identify corpus, evaluator, artifact, and environment digests;
   306|- no claim of improvement is made from a single unreplicated run.
   307|
   308|### Release
   309|
   310|- full regression passes;
   311|- fresh wheel metadata equals runtime `__version__`;
   312|- source checkout is forced for repository tests;
   313|- release manifest, Git tag, changelog, release notes, and installed artifact converge;
   314|- version-integrity and release-lineage gates run on every PR and main push.
   315|
   316|## 10. Recommended first vertical slice
   317|
   318|Do not begin with autonomous code mutation. Begin with one narrow, high-value loop:
   319|
   320|> **Correction policy improvement for a single project-scoped host.**
   321|
   322|The host observes repeated user corrections, creates a candidate correction-policy revision, evaluates it on a frozen correction corpus, promotes it only after explicit host authorization, activates it through the host, and can roll back to the prior policy.
   323|
   324|Why this slice:
   325|
   326|- MemKraft already has correction policy, outcomes, evaluation corpus, improvement ledger, and provenance primitives.
   327|- The evaluator can be deterministic and replayable.
   328|- The artifact is a policy, not executable code.
   329|- The blast radius is project-scoped.
   330|- The value is directly measurable: correction recurrence, false application, latency, and policy precision.
   331|- It validates the entire 4.0 contract before adding Tool or Workflow self-modification.
   332|
   333|After this slice passes, add Skill revision, then Tool adapter revision, then Workflow/Planner revision. Each higher-risk artifact kind requires a separate capability manifest, evaluator family, authorization policy, and rollback benchmark.
   334|
   335|## 11. Final recommendation
   336|
   337|Proceed with a **3.9.x hardening line**, not an immediate 4.0 implementation dump. The 4.0 direction is strategically strong and consistent with MemKraft's existing design, but its differentiator must be trustworthy evolution rather than unrestricted self-modification.
   338|
   339|The product promise should be:
   340|
   341|> MemKraft turns agent experience into evidence-backed, versioned, reversible improvements to the agent system, while keeping execution and authority outside the memory substrate.
   342|
   343|That is a meaningful answer to Self-Evolving AI: not “the model changes itself,” but “the system learns which changes are justified, proves what they changed, and can safely return to what worked.”
   344|

## Existing ledger contract
     1|# Continual Improvement Ledger Preview
     2|
     3|MemKraft 3.3의 Continual Improvement Ledger는 여러 런타임이 실행 경험, 개선 제안, 평가 증거, 명시적 승격, artifact revision 활성화와 rollback을 같은 append-only 감사 기록으로 연결할 수 있게 하는 Python-only Preview다.
     4|
     5|이 기능은 MKEP/0 wire registry를 확장하지 않는다. MemKraft는 scheduler, executor, deployer, model router, agent orchestrator가 아니며 artifact 내용을 수정하거나 외부 환경에 적용하지 않는다. LLM 호출, 실제 적용, 배포, cooldown과 scheduling은 host adapter가 담당한다.
     6|
     7|## Public Python API
     8|
     9|`from memkraft import MemKraft`로 생성한 기존 객체에 다음 additive methods가 제공된다.
    10|
    11|- `improvement_propose(...)`
    12|- `artifact_register_revision(...)`
    13|- `improvement_record_evaluation(...)`
    14|- `improvement_set_status(...)` — `to_status="promoted"`와 `promoted_revision_id`를 사용한 명시적 승격 포함
    15|- `artifact_activate_revision(...)`
    16|- `artifact_rollback_revision(...)`
    17|- `improvement_project(...)`
    18|- `improvement_plan_promotion(...)`
    19|- `improvement_plan_activation(...)`
    20|
    21|모든 write API는 호출자가 `now`를 주입한다. 같은 `operation_id`와 같은 의미의 payload는 저장된 결과를 돌려주며, 같은 key를 다른 payload에 재사용하면 fail-closed한다.
    22|
    23|## Lifecycle
    24|
    25|1. Host가 실행 경험을 provenance로 연결해 proposal을 기록한다.
    26|2. Candidate artifact revision은 digest, locator, lineage만 등록한다. MemKraft는 내용을 해석하거나 적용하지 않는다.
    27|3. Evaluation receipt는 inert evidence다. receipt 기록만으로 proposal이나 active artifact 상태가 바뀌지 않는다.
    28|4. Promotion은 별도의 명시적 상태 전이다. 필수 최신 receipt가 모두 pass이고 immutable proposal base/candidate binding이 일치해야 한다.
    29|5. Activation은 promotion과 별도이며 `expected_active_revision_id` CAS가 맞을 때만 append된다.
    30|6. Rollback은 과거에 active였던 revision을 다시 가리키는 새 activation event다. 이전 revision이나 activation을 삭제·덮어쓰지 않는다.
    31|
    32|## Safety properties
    33|
    34|- `authority_verified=True`는 core에서 허용하지 않는다. 인증 권한은 host 소유다.
    35|- Promotion은 evaluation success만으로 자동 수행되지 않는다.
    36|- Stale evaluation, invalid transition, missing revision, CAS mismatch와 idempotency mismatch는 hard gate다.
    37|- `improvement_plan_promotion`과 `improvement_plan_activation`은 append helper를 호출하지 않는 완전한 read-only dry-run이다.
    38|- Projection은 `(event_seq, id)` 순으로 fold하므로 physical JSONL line order와 무관하게 결정적이다.
    39|- Corrupt line은 projection에서 보고되며, corrupt log 위의 새 append는 fail-closed한다.
    40|- Rollback target은 같은 artifact에서 과거 active였어야 한다. 새 revision activation을 rollback으로 위장할 수 없다.
    41|
    42|## Storage and scope
    43|
    44|기록은 local append-only improvement ledger에 저장된다. Artifact payload나 prompt text 자체가 아니라 identifier, digest, locator, lineage와 opaque host receipt reference만 저장한다. Multi-host consensus, authenticated authority, deployment execution과 artifact mutation은 Preview 범위가 아니다.
    45|
    46|## Compatibility
    47|
    48|이 Preview는 기존 MKEP/0 transport와 동작을 변경하지 않는다. MKEP/0 wire registry는 계속 정확히 15 operations이며 improvement error registry는 execution protocol error registry와 분리된다.
    49|
