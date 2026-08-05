# Continual Improvement Ledger Preview

MemKraft 3.3의 Continual Improvement Ledger는 여러 런타임이 실행 경험, 개선 제안, 평가 증거, 명시적 승격, artifact revision 활성화와 rollback을 같은 append-only 감사 기록으로 연결할 수 있게 하는 Python-only Preview다.

이 기능은 MKEP/0 wire registry를 확장하지 않는다. MemKraft는 scheduler, executor, deployer, model router, agent orchestrator가 아니며 artifact 내용을 수정하거나 외부 환경에 적용하지 않는다. LLM 호출, 실제 적용, 배포, cooldown과 scheduling은 host adapter가 담당한다.

## Public Python API

`from memkraft import MemKraft`로 생성한 기존 객체에 다음 additive methods가 제공된다.

- `improvement_propose(...)`
- `artifact_register_revision(...)`
- `improvement_record_evaluation(...)`
- `improvement_set_status(...)`
- `improvement_promote(...)`
- `artifact_activate_revision(...)`
- `artifact_rollback_revision(...)`
- `improvement_project(...)`
- `improvement_plan_promotion(...)`
- `improvement_plan_activation(...)`

모든 write API는 호출자가 `now`를 주입한다. 같은 `operation_id`와 같은 의미의 payload는 저장된 결과를 돌려주며, 같은 key를 다른 payload에 재사용하면 fail-closed한다.

## Lifecycle

1. Host가 실행 경험을 provenance로 연결해 proposal을 기록한다.
2. Candidate artifact revision은 digest, locator, lineage만 등록한다. MemKraft는 내용을 해석하거나 적용하지 않는다.
3. Evaluation receipt는 inert evidence다. receipt 기록만으로 proposal이나 active artifact 상태가 바뀌지 않는다.
4. Promotion은 별도의 명시적 상태 전이다. 필수 최신 receipt가 모두 pass이고 immutable proposal base/candidate binding이 일치해야 한다.
5. Activation은 promotion과 별도이며 `expected_active_revision_id` CAS가 맞을 때만 append된다.
6. Rollback은 과거에 active였던 revision을 다시 가리키는 새 activation event다. 이전 revision이나 activation을 삭제·덮어쓰지 않는다.

## Safety properties

- `authority_verified=True`는 core에서 허용하지 않는다. 인증 권한은 host 소유다.
- Promotion은 evaluation success만으로 자동 수행되지 않는다.
- Stale evaluation, invalid transition, missing revision, CAS mismatch와 idempotency mismatch는 hard gate다.
- `improvement_plan_promotion`과 `improvement_plan_activation`은 append helper를 호출하지 않는 완전한 read-only dry-run이다.
- Projection은 `(event_seq, id)` 순으로 fold하므로 physical JSONL line order와 무관하게 결정적이다.
- Corrupt line은 projection에서 보고되며, corrupt log 위의 새 append는 fail-closed한다.
- Rollback target은 같은 artifact에서 과거 active였어야 한다. 새 revision activation을 rollback으로 위장할 수 없다.

## Storage and scope

기록은 local append-only improvement ledger에 저장된다. Artifact payload나 prompt text 자체가 아니라 identifier, digest, locator, lineage와 opaque host receipt reference만 저장한다. Multi-host consensus, authenticated authority, deployment execution과 artifact mutation은 Preview 범위가 아니다.

## Compatibility

이 Preview는 기존 MKEP/0 transport와 동작을 변경하지 않는다. MKEP/0 wire registry는 계속 정확히 15 operations이며 improvement error registry는 execution protocol error registry와 분리된다.
