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

`remember_candidate`, `list_candidates`, `session_overlay`, `forget_candidates`, `compact_memory`, `extract_claims`, `resolver_dry_run`, `record_interaction`, `last_interaction`, `timeline`, `audit_log`, `do_not_remember`, `truth_status`, `compile_evidence_context`, `aggregate_numeric_evidence`, `live_sync_apply`, `live_sync_events`, `live_sync_freshness`, `live_sync_repair`, `embedding_sync_path`, `embedding_index_state`는 공개되어 있으나 preview/secondary다. Preview는 additive 필드나 더 엄격한 검증이 minor release에서 생길 수 있다. 내부 `store_core`, JSONL helper와 benchmark fixture generator는 공개 API가 아니다.

### Runtime-neutral execution preview (3.3.0)

MKEP/0은 Python typed methods와 `memkraft exec call|state` CLI를 통해 제공되는 Preview 계약이다. 공개 실행 동사는 `goal_declare`, `gate_declare`, `receipt_record`, `goal_transition`, `gate_transition`, `lease_acquire`, `lease_release`, `execution_state`, `assess_run`, `assess_record`, `handoff_declare`, `handoff_transition`, `handoff_export`, `handoff_import`, `execution_forget`이다. Wire registry는 정확히 15 operations이며 [EXECUTION_PROTOCOL.md](EXECUTION_PROTOCOL.md)가 canonical transport contract다. MCP는 `describe`, `state.read`, `assess.run`, `handoff.export` read-only projection만 노출한다.

### Continual improvement ledger preview

Python-only additive [Continual Improvement Ledger Preview](CONTINUAL_IMPROVEMENT_LEDGER.md)는 실행 경험에서 proposal, evaluation evidence, explicit promotion, artifact activation과 append-only rollback까지의 감사 가능한 lifecycle을 제공한다. 이 기능은 MKEP/0 wire registry에 operation을 추가하지 않으며 scheduler, executor, deployer 또는 authenticated authority 역할을 하지 않는다. Dry-run은 무쓰기이고 promotion과 activation은 분리되며 stale evidence, invalid transition, CAS·idempotency mismatch를 fail-closed한다.

실행 기능은 `compile_context(..., goal_id=None, execution_budget=0)`에 dual opt-in이다. `goal_id is not None`이고 `execution_budget > 0`일 때만 마지막 `execution` section이 생긴다. 사용하지 않을 때 기존 output과 identity input은 byte-identical이다. `context_schema`는 golden `usage_id`를 지키기 위해 identity dict에서 제외되므로 서로 다른 context schema payload가 같은 `usage_id`를 가질 수 있고 outcome attribution이 schema version 사이에서 합쳐질 수 있다. 이 aliasing은 의도된 Preview trade-off다.

Gates와 `should_run`은 advisory이며 권한 부여가 아니다. Authority/namespace/holder/binding은 인증되지 않은 audit identity다. Scope는 single-host local filesystem이고 execution log compaction, multi-host coordination, graph schema, checkpoint/resume, envelope authenticity는 제공하지 않는다. GA decision deadline은 **2027-02-04**다.

### Evidence context preview

`compile_evidence_context(query, *, results=None, top_k=..., budget=..., adjacent_windows=...)`는 검색 hit의 원문 span과 provenance를 hard budget 안의 JSONL evidence context로 컴파일한다. `results`가 주어지면 caller의 hit 순서와 원래 `hit_rank`를 감사 경계로 보존하며 입력을 변경하지 않는다. Query anchor가 있는 span이 numeric auxiliary span보다 우선한다.

명시적인 latest/past/compare intent가 있을 때만 hit metadata의 유효한 `valid_from` 또는 Markdown `Date`를 사용해 anchored evidence 선택 순서를 조정한다. 날짜가 없거나 유효하지 않으면 supplied order로 fallback하고, timestamp만 있다는 이유로 unanchored source를 포함하지 않는다. Neutral query는 기존 ordering을 유지한다. 유효한 source timestamp는 UTC ISO 형식의 선택적 `source_time`으로 evidence record와 JSONL에 보존된다. Hard budget이 부족하면 본문 evidence를 우선하고 `source_time`만 생략한다. 반환에는 렌더링된 `text`, 구조화된 `evidence`, source 목록, 생략 수, token 추정치, 결정적 `usage_id`가 포함된다. 반환 필드는 Preview이므로 additive 확장이 가능하지만 verbatim span, provenance, hard-budget, deterministic ordering 계약은 유지한다.

### Numeric aggregation preview

`aggregate_numeric_evidence(query, *, results=None, top_k=50)`는 명시적인 `sum`, `count`, `duration` intent에 한해 검색 hit 원문에서 구조화된 수치 결과와 provenance를 만든다. 반환은 `operation`, `status`, `value`, `unit`, `facts`, `excluded`, `reason`, `scope`를 포함한다. 금액과 수량은 binary float가 아닌 decimal 의미로 계산하며 source file/span/text와 caller hit rank를 보존한다.

이 API는 **fail-closed**다. 혼합 단위·통화, unitful/unitless 혼합, 한 줄의 복수 수량, 상충하는 동일 `fact_id`, provenance 없는 cross-file duplicate, 불명확한 event predicate, 두 개가 아닌 duration endpoint, 읽을 수 없거나 base directory 밖의 source가 있으면 부분 합계를 확정하지 않고 `ambiguous`, `incomplete`, 또는 `unsupported`를 반환한다. 단위 환산, 단가×수량, 평균·비율, 상대 날짜, 숫자 단어, 전체 corpus 완전성 추정은 3.0.1 계약에 포함되지 않는다.

### Compiled truth freshness preview

`truth_status()`는 인자 없이 호출하는 additive Preview API이며 정확히 다음 필드를 반환한다.

- `schema_version`: 현재 `1`
- `stale`: 현재 canonical event/policy snapshot과 마지막으로 적용된 sleep transaction이 다른지 나타내는 boolean
- `live_transaction_id`: 현재 event, policy, 기본 sleep strategy로 계산한 결정적 transaction id
- `applied_transaction_id`: 최신 적용 sleep transaction id 또는 적용 이력이 없으면 `null`
- `pending_event_count`: 마지막 적용 snapshot 이후 추가된 canonical source event 수

이 상태 조회는 compiled truth를 자동 재구축하지 않으며 filesystem에 파일이나 레코드를 쓰지 않는다. 따라서 `stale=true`여도 `current_truth(subject_id)`는 마지막으로 적용된 compiled snapshot을 계속 읽고, 명시적인 `sleep(dry_run=False)` 뒤에 새 값으로 바뀐다. `event_ids`가 없는 legacy sleep journal은 compaction 전후의 정확한 차집합을 증명할 수 없으므로, stale 상태에서 현재 raw source event 전체를 pending으로 세는 보수적인 동작을 한다.

`current_truth`의 process-local snapshot cache는 compiled file과 canonical event file 각각의 mtime/size 및 inode identity가 바뀌면 무효화된다. deny policy는 캐시하지 않고 매 호출마다 한 번 읽으며, corrupt line이 있으면 fail-closed로 `{}`를 반환한다. 또한 캐시된 compiled 행도 현재 deny policy와 canonical tombstone에 대조한다. compiled 파일뿐 아니라 canonical event/provenance 읽기에서 corrupt line이 하나라도 발견되면 값을 되살리지 않고 fail-closed로 숨긴다. `do_not_remember(..., dry_run=False)`와 `forget(..., dry_run=False)`는 compiled snapshot을 즉시 재구축하지 않지만, 이미 캐시된 값은 per-read policy/tombstone 검사로 즉시 숨긴다. 정규 snapshot 재구축은 다음 `sleep(dry_run=False)`가 수행한다.

### Candidate governance preview

`do_not_remember(subject, key)`는 candidate의 **추출된 구조화 claim**만 검사한다. 3.0.1의 명시적 key 매핑은 `prefers`, `uses`, `is_located` predicate와 changed claim의 `field`다. subject와 매핑된 key가 정책에 일치하는 candidate는 `list_candidates()`에서 제외되고, `session_overlay()`도 이 단일 필터 경로를 상속한다. Claim이 없는 자유 텍스트 candidate는 정책 문구를 추측해 자동으로 숨기지 않는다.

`forget_candidates(candidate_id=..., dry_run=True)` 또는 `forget_candidates(session_id=..., dry_run=True)`로 candidate를 명시적으로 tombstone 처리할 수 있다. 두 selector는 정확히 하나만 필요하며 상호 배타적이다. 기본 dry-run은 JSON-safe 계획만 반환한다. `dry_run=False`는 기존 tombstone 형식과 governance audit(`action=forget_candidates`)을 사용하며 재시도에 멱등이다. `session_id` selector는 claim 없는 자유 텍스트까지 해당 세션에서 명시적으로 제거하는 blunt option이다.

### Local sidecar compaction preview

`compact_memory(dry_run=True)`는 `.memkraft/events.jsonl`과 `.memkraft/candidates.jsonl`만 대상으로 per-store `kept`, `removed_tombstoned`, `removed_markers`, `removed_corrupt` 수를 보고한다. 기본 dry-run은 파일을 쓰지 않는다. `dry_run=False`는 store core의 기존 lock/compact 경로로 local active sidecar의 tombstoned 원본, marker, corrupt line을 물리적으로 제거한다. 없는 sidecar는 생성하지 않는 no-op 성공이다. Visible `export_memory`, `timeline`, `current_truth`, `list_candidates` 의미는 compaction 전후 동일하다.

Compaction은 **현재 로컬 active sidecar 파일만** 정리한다. Backup, VCS, filesystem snapshot, 외부 복사본에서의 삭제를 보장하지 않으므로 필요한 backup 보존·삭제 정책은 운영자가 별도로 관리해야 한다.

### Local-first live sync preview

`live_sync_apply(path, operation, old_path=None, embeddings="auto", provenance=True)`는 canonical Markdown 변경을 명시적으로 파생 상태에 전달한다. operation은 `create`, `modify`, `delete`, `move`이며 move는 `old_path`가 필수다. 반환 필드는 Preview이므로 additive 확장이 가능하다. `.memkraft/` 내부와 비Markdown 대상은 canonical 입력으로 승격되지 않는다.

`live_sync_events()`는 lazy-created derived event log를 읽고, `live_sync_freshness()`는 canonical Markdown과 process-local BM25 및 optional embedding index를 비교한다. `live_sync_repair()`는 Markdown에서 파생 인덱스를 재구축한다. Event log와 index는 canonical source가 아니며 삭제 가능하다. `embedding_sync_path()`와 `embedding_index_state()`는 embedding extra가 선택된 사용자를 위한 secondary API이고 default search mode를 바꾸지 않는다.

## Search modes

정식 진입점은 `search(query, mode="legacy"|"v2"|"smart"|"hybrid", ...)`이며 기본 `legacy`는 기존 호출을 그대로 보존한다. `search_v2`, `search_smart`, `search_hybrid`는 4.0 이전에 제거하지 않는 `DeprecationWarning` alias다. `record_event` → `append_event`, roadmap의 `resolve_claims` → `resolver_dry_run`도 같은 정책이다. 경고는 호출자 위치를 가리키며 migration 문서와 최소 한 major release의 유예 없이 제거하지 않는다.

## Provenance

원본 event는 비어 있지 않은 `source` 또는 `provenance`가 반드시 필요하다. Truth, timeline, context가 노출하는 항목은 원본 source를 보존한다. Sleep/compile은 source 없는 사실을 새 canonical event로 쓰지 않는다. 사용량·outcome·audit·journal 같은 운영 레코드는 source item/usage id/명시적 action으로 연결되며 사실 원본으로 승격되지 않는다.

## Lifecycle

capture → truth → sleep → governance → context → outcome 순서는 동일 fixture에서 결정적이다. Truth는 event log에서 재구축 가능하고, sleep apply는 transaction-id 기반 멱등이며, `forget` tombstone과 do-not-remember 정책은 truth/context/export에 전파된다. 파생 캐시는 권위 데이터가 아니며 삭제 후 재구축할 수 있다.

같은 sleep transaction id가 journal의 **최신 sleep 항목**일 때만 apply 재시도가 `already_applied`다. Canonical 상태가 과거 snapshot으로 되돌아온 경우(예: forget 후 compaction)에는 과거에 같은 id가 있어도 새 sleep 항목을 적용하여 `truth_status()`의 최신 적용 상태를 갱신한다.

## Derived-view benchmark artifacts

`benchmarks/results/derived-views-before.json`은 같은 코드·데이터에서 warm 읽기 전에 compiled/event process-local snapshot cache를 모두 비우는 `--mode no-cache` 측정이며, `derived-views-after.json`은 두 snapshot cache를 사용하는 기본 production 측정이다. deny policy parse와 캐시된 행의 provenance/policy 필터 비용은 양쪽 모두 포함된다. 두 artifact는 single-run 진단용 cache/no-cache baseline이며 의미 있는 성능 향상을 입증하는 hard performance claim이 아니다. 재현 명령은 각각 다음과 같다.

```bash
PYTHONPATH=src python3 benchmarks/derived_views_bench.py --mode no-cache --sizes 100,1000 --out benchmarks/results/derived-views-before.json
PYTHONPATH=src python3 benchmarks/derived_views_bench.py --sizes 100,1000 --out benchmarks/results/derived-views-after.json
```

## Governance boundary

거버넌스는 `.memkraft/events.jsonl`, tombstone, deny policy, audit/journal 경계 안에서 canonical visibility를 통제한다. 사용자 markdown을 암묵적으로 재작성하지 않고, search 호출은 migration을 수행하지 않는다. `dry_run=False`만 lifecycle/governance 변경을 적용한다. 외부 모델 출력, benchmark adapter, context ranking은 거버넌스 정책을 우회해 source-less canonical write를 만들 권한이 없다.
