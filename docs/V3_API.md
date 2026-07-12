# MemKraft 3.0 API contract

이 문서는 3.0 공개 계약이다. 구현 세부나 roadmap보다 우선한다.

## Stable core

3.0에서 의미·입력 검증·반환 형태를 호환 유지하는 lifecycle 동사는 다음이다.

- capture: `append_event(subject_id, key, value, source=...|provenance=...)`
- derive/read: `compile_truth` (`dry_run=True`), `current_truth` (`subject_id`)
- lifecycle: `sleep` (`strategy="default", dry_run=True`), `forget` (`target, dry_run=True`)
- consume/feedback: `compile_context` (`task, budget, ...`), `report_outcome` (`usage_id, outcome, ...`)
- 기존 코어 `track`, `update`, `search`, `why`, `export_memory`도 제거되지 않는다.

기본적으로 파괴 동작은 dry-run이다. 3.x에서 위 이름을 제거하거나 위치 인자의 의미를 바꾸지 않는다.

## Preview and secondary

`remember_candidate`, `list_candidates`, `session_overlay`, `extract_claims`, `resolver_dry_run`, `record_interaction`, `last_interaction`, `timeline`, `audit_log`, `do_not_remember`는 공개되어 있으나 preview/secondary다. Preview는 additive 필드나 더 엄격한 검증이 minor release에서 생길 수 있다. 내부 `store_core`, JSONL helper와 benchmark fixture generator는 공개 API가 아니다.

## Search modes

정식 진입점은 `search(query, mode="legacy"|"v2"|"smart"|"hybrid", ...)`이며 기본 `legacy`는 기존 호출을 그대로 보존한다. `search_v2`, `search_smart`, `search_hybrid`는 4.0 이전에 제거하지 않는 `DeprecationWarning` alias다. `record_event` → `append_event`, roadmap의 `resolve_claims` → `resolver_dry_run`도 같은 정책이다. 경고는 호출자 위치를 가리키며 migration 문서와 최소 한 major release의 유예 없이 제거하지 않는다.

## Provenance

원본 event는 비어 있지 않은 `source` 또는 `provenance`가 반드시 필요하다. Truth, timeline, context가 노출하는 항목은 원본 source를 보존한다. Sleep/compile은 source 없는 사실을 새 canonical event로 쓰지 않는다. 사용량·outcome·audit·journal 같은 운영 레코드는 source item/usage id/명시적 action으로 연결되며 사실 원본으로 승격되지 않는다.

## Lifecycle

capture → truth → sleep → governance → context → outcome 순서는 동일 fixture에서 결정적이다. Truth는 event log에서 재구축 가능하고, sleep apply는 transaction-id 기반 멱등이며, `forget` tombstone과 do-not-remember 정책은 truth/context/export에 전파된다. 파생 캐시는 권위 데이터가 아니며 삭제 후 재구축할 수 있다.

## Governance boundary

거버넌스는 `.memkraft/events.jsonl`, tombstone, deny policy, audit/journal 경계 안에서 canonical visibility를 통제한다. 사용자 markdown을 암묵적으로 재작성하지 않고, search 호출은 migration을 수행하지 않는다. `dry_run=False`만 lifecycle/governance 변경을 적용한다. 외부 모델 출력, benchmark adapter, context ranking은 거버넌스 정책을 우회해 source-less canonical write를 만들 권한이 없다.
