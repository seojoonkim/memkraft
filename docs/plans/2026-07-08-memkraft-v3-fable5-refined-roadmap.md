# MemKraft 버전 마일스톤 재설계 보고서

**대상:** 리서치 팩(vooy-informed 로드맵, V3_PARADIGM_PLAN, vooy 마스터 스펙, Letta/Mem0/Zep·Graphiti/LangMem/LlamaIndex 문서 발췌)
**결론 요약:** 현행 계획의 방향(라이프사이클 중심 v3)은 옳지만, 릴리스 절단면이 잘못되어 있다. 2.14가 화물열차이고, 후보→리졸버 사이 스키마 단절이 있으며, 게이트 대부분에 숫자가 없고, 삭제 의미론이 사이드카 7개가 증식한 뒤에야 등장한다. 아래에서 2.13/2.14/2.15/3.0 4단 릴리스 트레인으로 재절단하고, 3.0의 SemVer 근거를 "마케팅"이 아닌 실제 계약 변경으로 재정의한다.

---

## 1. 현행 계획 비판

방향성 자체(검색 시스템 → 라이프사이클 기억 OS)는 유지할 가치가 있다. 문제는 실행 설계다. 심각도 순으로 나열한다.

**C1. 2.14.0이 화물열차다.** compiled truth/timeline, compile_context, 아웃컴 루프, 툴 메모리, 거버넌스, 컨텍스트 품질 게이트 — 서로 성격이 다른 6개 서브시스템이 한 마이너 릴리스에 묶여 있다. 하나라도 늦으면 전부 늦고, 하나라도 설계가 틀리면 릴리스 전체가 재작업된다. 특히 "파생 뷰"(truth/timeline/sleep)와 "소비 계층"(compiler/outcome)은 안정화 리듬이 완전히 다르다. 릴리스를 하나 더 쪼개야 한다(→ 2.15 신설).

**C2. candidate ↔ resolver 스키마 단절.** `remember_candidate`는 자유 텍스트(`text`, `entity_hint`)를 저장하는데, `resolver_dry_run`은 `subject/predicate/object_value/valid_from/source_quote` 구조화 dict를 입력으로 받는다. **이 둘을 잇는 추출 단계가 계획 어디에도 없다.** vooy 스펙에는 Extractor가 명시적 컴포넌트인데, MemKraft 로드맵은 그걸 빼먹고 양끝만 가져왔다. 현행대로면 resolver는 입력을 만들 수 없는 API로 출시된다.

**C3. 자기 릴리스에서 실행 불가한 게이트.** `context_packet_shape` 시나리오가 2.13(A1)에 들어 있는데 대상 API인 `compile_context`는 2.14다. "Phase B가 시작되면"이라는 단서가 붙은 게이트는 게이트가 아니다. 각 시나리오는 자기 릴리스의 API만으로 실행 가능해야 한다.

**C4. 사이드카 증식에 공통 계약이 없다.** candidates.jsonl, last_interactions.json, compiled_truth.jsonl, timeline.jsonl, utility_events.jsonl, 툴 콜 로그, audit 로그 — 계획대로면 `.memkraft/` 아래 7개 스토어가 생기는데, 공통 레코드 엔벨로프도, `schema_version` 필드도, 원자적 쓰기 규약도, 다중 프로세스 락도, 컴팩션 전략도 없다. "corrupt line은 스킵한다"는 방어는 있지만 corrupt line이 생기지 않게 하는 설계가 없다. 여러 에이전트가 같은 `base_dir`에 동시에 append하는 순간 터진다.

**C5. 삭제 의미론이 너무 늦게 온다.** `forget`/tombstone이 2.14 말미(B5)에 등장하는데, 그 시점엔 이미 6개 스토어에 레코드가 퍼져 있다. 삭제는 사후 기능이 아니라 **모든 reader가 처음부터 존중해야 하는 스토리지 계약**이다. tombstone 전파를 나중에 소급 적용하는 것은 소급 마이그레이션 + 전 reader 수정이라는 최악의 작업이 된다. vooy 스펙 스스로 "privacy by design은 부가기능이 아니라 핵심 스키마"라고 썼는데, MemKraft 로드맵은 정확히 그 반대로 배치했다.

**C6. 숫자 없는 게이트가 많다.** "p95 target should be measured and documented"(A5), "ranking difference is measurable"(C2), "source coverage remains high"(B6) — 이것들은 게이트가 아니라 숙제 미루기다. 숫자 없는 게이트는 릴리스 당일 반드시 통과한다.

**C7. 토큰 추정기가 미정의다.** 3.0의 핵심 주장인 "compiled context가 raw top-k보다 토큰을 덜 쓴다"는 `raw_topk_tokens`와 `compiled_tokens`가 **동일한 추정기**로 측정될 때만 의미가 있다. stdlib-first라 tiktoken도 없다. 추정기 스펙이 없으면 이 게이트는 조작 가능하고, 더 나쁘게는 compiler가 단순 truncation으로 게이트를 통과할 수 있다. 압축률 게이트는 반드시 `required_facts_recalled`와 AND로 묶여야 하며, 현행 계획은 그 결합을 명시하지 않는다.

**C8. 아웃컴 루프의 부작용이 미설계다.** "성공 아이템은 부스트, 실패 아이템은 다운랭크"만 있고 (a) 부스트 상한, (b) 감쇠(half-life), (c) rich-get-richer 방지, (d) 오염된 피드백(agent가 잘못 보고) 대비, (e) pinned 아이템 보호가 없다. 피드백 루프는 잘못 설계하면 기억 시스템을 스스로 망가뜨리는 유일한 기능이다.

**C9. 자기 채점 벤치마크.** 픽스처를 만드는 사람이 통과 기준도 정하고 구현도 한다. dev/holdout 분리, 적대 픽스처, 외부 벤치마크 샘플이 없다. Zep/Graphiti가 LoCoMo 94.7%@155ms, LongMemEval 90.2%@162ms를 **공개 벤치마크 수치**로 내세우는 시장에서, 내부 픽스처만으로 "3.0 자격"을 주장하면 2.12 프루닝 실험에서 배웠다는 교훈("쉬운 벤치마크는 자기기만을 허용한다")을 스스로 어기는 것이다.

**C10. timeline의 정체성 모호.** `record_event`가 timeline.jsonl에 직접 쓰는데, 동시에 "compiled truth는 fact/event 데이터에서 재생성 가능해야 한다"고 한다. timeline이 원본 이벤트 로그인가, 파생 뷰인가? 둘을 겸하면 재생성 불변식이 깨진다. 원본(`events.jsonl`)과 뷰(`timeline` 조회 API)를 분리해야 한다.

**C11. 3.0의 SemVer 근거가 없다.** "기존 API는 전부 유지하고 새 API만 추가"라면 그건 정의상 2.16이다. 메이저 버전은 마케팅 이벤트가 아니라 호환성 계약 변경이다. 현행 계획은 무엇이 깨지는지 한 줄도 명시하지 않는다(→ §8에서 재정의).

**C12. `do_not_remember` 의미론 미정.** 패턴이 substring인지 regex인지 glob인지, 대소문자·정규화 규칙은 무엇인지 없다. 잘못된 패턴 하나가 조용히 정상 기억을 전부 차단하는 시스템은 governance가 아니라 사고다.

**C13. 17개 API 동결은 표면적이 너무 넓다.** Mem0가 add/search/update/delete 4개 동사로 시장을 먹는 동안 MemKraft 3.0은 17개를 "stable"로 동결하려 한다. 동결은 되돌릴 수 없다. 동결 전에 표면 다이어트가 먼저다.

**C14. CI가 없다.** 모든 게이트가 `/tmp`로 수동 실행이다. "100% of retrieval PRs include a Gym gate"라는 v3 계획의 약속은 CI 강제 없이는 지켜지지 않는다.

---

## 2. 비교 메모리 시스템에서 얻는 교훈

| 시스템 | 채택할 것 | 기각/경계할 것 |
|---|---|---|
| **Letta/MemGPT** | 평가를 제품의 1급 요소로 취급(Letta Evals의 suites/graders/**gates** 구조는 Memory Gym이 가야 할 형태 — 게이트를 설정 파일로 선언). 메모리 계층화(core vs archival)와 compaction의 명시적 API화. | OS 메타포 과잉. 대화 중 tool call 남발로 인한 지연 — MemKraft의 fast path(overlay/last-interaction)가 LLM 없이 도는 설계는 유지. |
| **Mem0** | **API 표면 최소주의.** add → extract/store → recall 3단 파이프라인을 몇 개 동사로 노출. session/user/agent/run 단위의 메모리 스코프 개념 — MemKraft `session_id`를 임시 파라미터가 아닌 정식 스코프 모델로 승격할 근거. | 매니지드 인프라 전제. MemKraft는 로컬-퍼스트 유지. |
| **Zep/Graphiti** | (1) **bi-temporal + fact invalidation**: 삭제 대신 유효기간 만료·supersede — resolver의 `CONTRADICTION`/`UPDATE` 의미론과 정확히 일치, disputed 상태 유지. (2) **LLM 없는 하이브리드 검색**(BM25+벡터+그래프, no LLM rerank) — hot path에서 LLM 배제 원칙 검증. (3) **공개 벤치마크로 정확도+지연을 함께 공표**하는 규율. (4) 에피소드 단위 ingestion + provenance 유지. | 자동 그래프 추출의 오염 위험(vooy 스펙도 지적). MVP에 전용 그래프 DB 불필요 — MemKraft의 SQLite/사이드카 노선 유지. |
| **LangMem** | (1) **hot path vs background 이분법**을 문서 최상위 개념으로: MemKraft의 session overlay(=hot path) + sleep(=background)과 동형. 이 구도를 API 문서의 뼈대로 차용. (2) semantic/episodic/procedural 3분류. (3) **스토리지-불가지 함수형 프리미티브 + 스토어 통합 분리** — MemKraft 코어 로직이 사이드카 파일 포맷에 결합되지 않게 하는 근거. | 프레임워크(LangGraph) 종속. MemKraft는 stdlib 유지. |
| **LlamaIndex Memory** | 메모리를 RAG 인덱싱과 별개 모듈로 유지. 대용량 외부 문서는 메모리가 아니라 인덱스의 일이다. | compile_context가 문서 RAG를 재발명하지 않게 경계. |
| **ChatGPT Memory (vooy 경유)** | "기억했어요" 통지 + 사용자 삭제권이라는 소비자 UX 최소선 — MemKraft에서는 `forget`/`export`/`audit`가 그 기계적 대응물. | UX 자체는 vooy 레이어 소관. |

**종합 교훈 3줄:**
1. 이기는 시스템은 **정확도와 지연을 같은 표에** 공개한다. 게이트는 항상 (품질, p95) 쌍이어야 한다.
2. hot path에서 LLM을 빼는 것은 최적화가 아니라 아키텍처 원칙이다. MemKraft의 결정적 resolver/overlay/추출기 노선은 옳다 — 단, 결정적 추출기가 실제로 존재해야 한다(C2).
3. 삭제하지 말고 invalidate하라. tombstone + supersede + disputed가 스토리지 계약의 기본형이다.

---

## 3. 재설계된 릴리스 트레인

핵심 재절단: 기존 2.14를 **2.14(파생 뷰 + sleep + 거버넌스)**와 **2.15(컴파일러 + 아웃컴)**로 분리하고, 스토리지 계약과 클레임 추출기를 2.13 최전방으로 당긴다. 거버넌스를 2.15가 아닌 2.14에 두는 이유: tombstone 전파의 최난도 구간이 파생 뷰이므로, 뷰가 태어나는 릴리스에서 삭제가 함께 검증되어야 한다.

### 3.1 — 2.13.0 «Capture & Resolve» (라이프사이클 전반부)

**테마:** 컴파일하기 전에 안전하게 붙잡고 판정한다. 스토리지 계약이 모든 것에 선행한다.

**정확한 범위:**
1. **스토리지 계약 v1** (`src/memkraft/store_core.py` 신설):
   - 공통 레코드 엔벨로프: `{id, schema_version: 1, created_at, tombstone: bool, provenance_id?}` — 이후 모든 사이드카가 이 엔벨로프를 사용.
   - 원자적 append: `fcntl.flock` 배타 락 + 단일 `write()` 호출 + 개행 완결성 검사. 컴팩션: temp 파일 재작성 + `os.replace`.
   - **tombstone reader 계약**: 모든 reader는 tombstone 레코드를 기본 은닉. 사용자용 `forget` CLI는 2.14지만, 계약과 reader 준수는 여기서 확정(C5 해소).
2. **결정적 클레임 추출기** `extract_claims(text, *, entity_hint=None) -> list[dict]` — 규칙 기반(stdlib), `{subject, predicate, object_value, valid_from?, source_quote, confidence}` 산출. 추출 실패 시 빈 리스트 + 후보는 `CANDIDATE_REVIEW` 표시. LLM 없음. **C2 단절 해소** — candidate 레코드에 선택 필드 `claims: []` 추가.
3. **candidate 사이드카**: `remember_candidate` / `list_candidates`, 엔벨로프 사용, 만료(TTL 기본 24h), provenance 연동.
4. **session overlay**: `session_overlay(session_id, query, top_k)` — 토큰 중첩 검색, `memory_state: "session_overlay"` 라벨 강제(스키마로, 관례가 아니라).
5. **resolver dry-run**: 8개 verdict, claims dict 입력, 결정적 규칙만.
6. **last-interaction index**: `record_interaction` / `last_interaction`, `occurred_at` 단조 갱신, 동률 시 `interaction_id` 사전순 tie-break(명문화).
7. **Gym 확장**: 레지스트리 + 숫자 임계값. `context_packet_shape`는 2.15로 이동(C3 해소).

**비-목표:** compile_context, compiled truth/timeline, sleep 변경, 아웃컴 루프, 툴 메모리, 거버넌스 CLI, 임베딩 관련 일체, vooy 도메인 스키마(Person/Place/Product).

**API(신규):** `extract_claims`, `remember_candidate`, `list_candidates`, `session_overlay`, `resolver_dry_run`, `record_interaction`, `last_interaction`.

**스토리지/스키마:**
```text
<base_dir>/.memkraft/candidates.jsonl      (envelope v1)
<base_dir>/.memkraft/last_interactions.jsonl  + 스냅샷 last_interactions.json
```
last_interactions는 append 로그 + 주기 스냅샷 구조로 변경(단일 JSON 파일 통재작성은 락 아래에서도 crash-torn 위험).

**테스트 게이트 / 지표:**

| 게이트 | 임계값 |
|---|---|
| `search_recall` (기존) | recall@5 ≥ 2.12 baseline, 회귀 0.00 허용 |
| `session_overlay_recall` | 동일 세션 즉시 재현 1.00 (픽스처 20건), 타 세션 누출 0건, 만료 후보 노출 0건 |
| `last_interaction` | 정확도 1.00 (200 이벤트, 역순 삽입 포함), p95 < 5ms @ 10k subjects |
| `resolver_verdicts` | 라벨 픽스처 120케이스 정확도 ≥ 0.95, 결정성(동일 입력 100회 동일 출력) 1.00, source_quote 부재 → active 승격 경로 0건 |
| `provenance_coverage` | 신규 candidate 레코드 100% (`provenance_id` 또는 명시 `unknown`) |
| 동시성 | 4 프로세스 × 250 append 후 corrupt line 0건 |

### 3.2 — 2.14.0 «Derived Views, Sleep & Governance» (기억의 소화와 통제)

**테마:** 원본과 파생을 분리하고, 파생은 재생성 가능하며, 삭제는 어디에나 도달한다.

**정확한 범위:**
1. **이벤트 로그 원본화**: `record_event` → `events.jsonl`(원본). `timeline(subject_id)`은 조회 API/캐시 뷰(C10 해소).
2. **compiled truth**: `compile_truth(dry_run=)` / `current_truth(subject_id)` — facts+events에서 언제든 재생성 가능한 캐시. resolver verdict(`UPDATE`/`CORRECTION`)가 supersede를 구동.
3. **sleep**: `mk.sleep(strategy, dry_run=True)` + CLI `memkraft sleep --dry-run|--apply`. apply는 저널 파일 선기록 → 뷰 갱신 → 저널 삭제(중단 복구 가능). 파생 레코드 provenance 강제.
4. **거버넌스 최소선**: `forget(scope, mode="soft_delete")`, `export_memory`, `audit_log`, `do_not_remember(pattern, match="exact"|"glob")` — **의미론 명문화**(C12): 기본 exact(정규화: NFC + casefold), glob는 opt-in, regex는 비지원. 차단 시 audit 기록.
5. **tombstone 전파 검증**: forget된 레코드가 search/overlay/current_truth/timeline/export 어디에도 미노출.

**비-목표:** compile_context, 아웃컴 루프, 툴 메모리, hard-delete 자동화(soft delete + 컴팩션 시 물리 제거만), 암호화/KMS(vooy 소관).

**API(신규):** `record_event`, `timeline`, `compile_truth`, `current_truth`, `sleep`, `forget`, `export_memory`, `audit_log`, `do_not_remember`.

**스토리지:** `events.jsonl`, `compiled_truth.jsonl`(캐시, 재생성 가능 마킹), `audit.jsonl`, `dnr_rules.jsonl`, `sleep_journal.json`(일시적).

**테스트 게이트 / 지표:**

| 게이트 | 임계값 |
|---|---|
| 뷰 재생성 동등성 | 전체 재생성 결과 == 증분 갱신 결과 (레코드 집합 동일, 픽스처 3종) |
| sleep dry-run | 파일시스템 변경 0바이트 (해시 비교) |
| sleep apply 중단 복구 | 저널 존재 상태에서 재실행 시 일관 상태 복원, search/compile 무장애 |
| 파생 provenance | sleep 산출 레코드 100% source span 또는 명시 unknown; 무단 source-less 파생 0건 |
| forget 전파 | 6개 스토어 전부에서 미노출 1.00 (스토어별 개별 테스트) |
| do_not_remember | 차단 픽스처 30건 차단율 1.00, 대조군 30건 오차단 0건 |
| 성능 | `current_truth` p95 < 20ms @ 3k facts; 전체 재생성 < 5s @ 10k 레코드; `search_recall` 무회귀 |

### 3.3 — 2.15.0 «Context Compiler & Outcome Loop» (기억의 소비와 학습)

**테마:** 기억을 검색 결과가 아니라 과업 기판으로 쓰고, 결과가 미래를 바꾼다 — 단, 안전 상한 안에서.

**정확한 범위:**
1. **토큰 추정기 스펙**(C7 해소): `estimate_tokens(text) = ceil(len(text)/4)` (CJK 가중치 ×1.7, 문서화·고정). raw/compiled 비교는 반드시 동일 추정기.
2. **`compile_context(task, budget, objective, subject_id?, session_id?)`**: 섹션 구조(current_truth / timeline / session_candidates / procedural / open_actions / conflicts / sources), `usage_id` + item id 발급, `miss=True` 시 환각 사실 0 계약, 예산 초과 시 아이템 경계 절단.
3. **아웃컴 루프**: `report_outcome(usage_id, success, reward?, evidence?)` → `utility_events.jsonl`. **안전 설계 명시(C8 해소)**: 부스트/페널티는 기본 점수의 ±20% 상한, half-life 30일 감쇠, 실패 아이템은 다운랭크만(삭제 금지), pinned 불변, reward는 [-1,1] 클램프.
4. **툴/API 절차 기억 lite**: `log_tool_call` / `tool_patterns`, 크레덴셜 차단(패턴 + 엔트로피 휴리스틱), `privacy_level="secret"` 거부.
5. **Gym**: `context_packet_shape`(2.13에서 이월), `context_quality`, `outcome_learning`.

**비-목표:** LLM 요약 기반 컴파일(결정적 조립만), 자동 프롬프트 최적화, 임베딩 필수화, 멀티 워크스페이스 유틸리티 공유.

**테스트 게이트 / 지표:**

| 게이트 | 임계값 |
|---|---|
| `context_quality` (홀드아웃 30 task) | `compiled_tokens ≤ 0.70 × raw_topk_tokens` **AND** `required_facts_recalled ≥ 0.90` (AND 결합 필수) |
| 라벨 정확도 | 후보 라벨링 1.00, source_coverage ≥ 0.95, miss=True 시 렌더된 사실 0건 |
| `outcome_learning` | 성공 3회 보고 → 해당 아이템 순위 상승, 실패 3회 → 하강; 부스트 상한 초과 0건; pinned 순위 불변 |
| 크레덴셜 레드팀 | 40개 크레덴셜형 문자열 픽스처 저장 0건, 정상 문자열 40건 오탐 ≤ 2건 |
| 성능 | `compile_context` p50 ≤ 100ms / p95 ≤ 300ms @ 3k docs (사전계산 뷰 사용), `search_recall` 무회귀 |

### 3.4 — 3.0.0 «Memory OS» (계약 변경 + 증명)

**테마:** 새 기능이 아니라 **계약**을 출시한다. 3.0의 코드 델타는 작아야 한다.

**정확한 범위:**
1. **파괴적 변경(명시 목록, §8 근거):**
   - `consolidate()`/`sleep(apply)`가 source-less 파생 레코드 생성을 **기본 거부** (`allow_unsourced=True` 탈출구 제공, 3.2에서 제거 예고).
   - `search_v2` / `search_smart` / `search_hybrid` → `search(mode="v2"|"smart"|"hybrid")`로 통합, 구 이름은 DeprecationWarning alias (제거는 4.0).
   - 공개 API 표면 동결: 17개가 아닌 **12개 코어 동사**로 다이어트(C13): `remember`, `remember_candidate`, `search`, `compile_context`, `resolver_dry_run`, `record_event`, `last_interaction`, `sleep`, `report_outcome`, `forget`, `export_memory`, `why`. 나머지는 stable-but-secondary 문서 티어.
2. **lifecycle replay 벤치마크**: capture → extract_claims → resolve → compile → outcome → 재컴파일 순위 변화 검증.
3. **외부 벤치마크 샘플**: LongMemEval 스타일 50문항 서브셋 + 한/영 혼합 적대 쿼리 세트 결과를 릴리스 노트에 정확도+p95 표로 공표(C9 해소).
4. `docs/V3_API.md`(호환성 표, deprecation 일정), CHANGELOG, wheel smoke, Hermes provider 무-`source_path` 동작 확인.

**비-목표:** 신규 서브시스템 추가 일체. 3.0에서 새 기능을 넣고 싶어지면 그것은 2.15.x로 보낸다.

---

## 4. 3.0 필수 vs 선택

**필수(하나라도 빠지면 3.0이 아니라 2.16):**
- 스토리지 계약 v1 + tombstone 전파 (2.13/2.14)
- candidate + extract_claims + resolver + session overlay (라이프사이클 전반부)
- events 원본 + current_truth/timeline 재생성 가능 뷰
- sleep dry-run/apply + 파생 provenance 강제
- compile_context + 토큰 효율 AND-게이트
- report_outcome이 실제로 순위를 바꾸는 것 (안전 상한 포함) — "improves with use"는 3대 약속이므로 타협 불가
- forget/export/audit + do_not_remember(exact)
- lifecycle replay 게이트 + 외부 벤치 샘플 공표
- API 표면 다이어트 + deprecation 경로

**선택(3.0.x/3.1로 이월 가능):**
- 툴/API 절차 기억 lite (유용하지만 3대 약속 어디에도 필수 아님)
- do_not_remember glob 매칭 (exact만으로 최소선 충족)
- 임베딩 하이브리드 개선 일체
- export JSON 포맷 추가 (markdown이 최소선)
- 유틸리티 감쇠 파라미터 튜닝 자동화
- Memory Browser류 UX (전부 vooy 소관)

---

## 5. 의존성 그래프

```text
[2.13] store_core(엔벨로프+락+tombstone계약) ──┬─→ candidates ──→ session_overlay
                                              ├─→ extract_claims ──→ resolver_dry_run
                                              └─→ last_interaction_index
        gym_registry(+숫자게이트) ← 모든 시나리오가 의존
                                              
[2.14] events.jsonl(원본) ──→ timeline뷰 ──┐
        resolver_dry_run ──→ compiled_truth ─┼─→ sleep(dry/apply, 저널)
        store_core.tombstone ──→ forget/export/audit/DNR ──→ 전파검증(뷰 포함)
                                              
[2.15] token_estimator ──→ compile_context ←── current_truth, timeline,
                                │               session_overlay, tool_patterns(opt)
                                └─→ usage_id ──→ report_outcome ──→ utility ──→ 재컴파일 순위
                                              
[3.0]  lifecycle_replay ← (2.13 전부 + 2.14 전부 + 2.15 compile/outcome)
        API동결/deprecation ← 표면 다이어트 결정
        외부벤치 공표 ← gym CI 안정화
```

임계 경로: **store_core → extract_claims → resolver → compiled_truth → compile_context → outcome → replay.** session overlay, last-interaction, 거버넌스 CLI, 툴 메모리는 임계 경로 밖이라 병렬 진행 가능.

## 6. 이슈/PR 슬라이스

각 PR은 단독 머지 가능, 게이트 그린 필수.

**2.13 (8 PR):** ① docs: 로드맵 재절단 반영(+context_packet_shape 이월 표기) ② bench: 2.12 baseline 재측정·`docs/bench/baselines/2.12.json` 고정 ③ feat: store_core 엔벨로프+flock+tombstone reader ④ test: gym 레지스트리+숫자 임계값+구조화 실패 ⑤ ci: `gym-gate` GitHub Actions ⑥ feat: extract_claims+픽스처 ⑦ feat: candidates 사이드카 ⑧ feat: session overlay / ⑨ feat: resolver 120케이스 / ⑩ feat: last-interaction — (⑧⑨⑩은 병렬)

**2.14 (7 PR):** ① feat: events.jsonl+timeline 뷰 ② feat: compiled_truth+재생성 동등성 테스트 ③ feat: sleep dry-run ④ feat: sleep apply+저널 복구 ⑤ feat: forget/export/audit ⑥ feat: do_not_remember(exact) ⑦ test: tombstone 6-스토어 전파 스위트

**2.15 (6 PR):** ① feat: token_estimator 스펙+테스트 ② feat: compile_context 골격(섹션+miss+예산) ③ feat: usage_id+report_outcome+utility(상한/감쇠) ④ feat: 순위 반영+outcome_learning 게이트 ⑤ feat: tool memory lite+레드팀 픽스처 ⑥ bench: context_quality 홀드아웃 게이트

**3.0 (5 PR):** ① feat: search 모드 통합+deprecation alias ② feat: consolidate provenance 기본 강제+탈출구 ③ bench: lifecycle_replay ④ bench: 외부 벤치 샘플 러너+결과 표 ⑤ docs/release: V3_API.md+CHANGELOG+wheel smoke

## 7. 벤치마크 프로토콜

1. **픽스처 거버넌스:** 픽스처는 고정 시드 프로그램 생성. `fixtures/dev/`(개발 중 참조 가능)와 `fixtures/holdout/`(게이트 전용, 개발 중 열람 금지 — CI에서만 실행) 분리. 적대 세트는 2.12의 `alpha zeta` 실패 클래스 + 한/영 혼합 쿼리 + 동철이의어 최소 30건 상시 포함.
2. **코퍼스 스케일:** 100 / 1k / 3k / 10k 문서. 게이트 숫자는 3k 기준, 10k는 추세 기록용.
3. **지연 측정:** 콜드 1회 + 웜 200회, p50/p95 보고, 머신 스펙 기록. 회귀 허용: 지연 +15% 이내, recall −0%p(무회귀).
4. **토큰 추정:** §3.3의 단일 추정기를 모든 게이트가 공유. 추정기 변경은 baseline 동시 재측정 없이 금지.
5. **게이트 출력:** 기존 JSON 스키마 유지 + `{scenario, thresholds, observed, pass, baseline_ref}` 필수 필드. 실패는 traceback이 아닌 구조화 실패 + 비제로 종료.
6. **baseline 고정:** 릴리스마다 `docs/bench/baselines/<version>.json` 커밋. 다음 릴리스 게이트는 직전 baseline 참조.
7. **CI:** `src/` 또는 `benchmarks/` 변경 PR에 `gym-gate` 잡 필수. 로컬 `/tmp` 실행은 개발 편의일 뿐 릴리스 근거가 아니다.
8. **외부 벤치:** 3.0 릴리스 노트에 (정확도, p95) 쌍으로 공표 — Graphiti가 세운 공개 규율을 따른다. 자체 픽스처 수치만으로 3.0을 선언하지 않는다.

## 8. SemVer 논거

- **2.13 / 2.14 / 2.15 = 마이너:** 전부 순수 추가(신규 API, 신규 사이드카). 기존 `remember`/`search`/ReasoningBank 동작 불변, 게이트로 무회귀 증명. SemVer 정의상 정확히 MINOR다.
- **3.0 = 메이저인 이유(세 가지 실제 계약 변경):**
  1. **동작 변경:** `consolidate`/`sleep(apply)`의 source-less 파생 쓰기 기본 거부 — 기존 사용자 스크립트가 깨질 수 있는 명백한 breaking change.
  2. **인터페이스 재편:** `search_v2`/`search_smart`/`search_hybrid`의 deprecation과 1차 소비 인터페이스의 `compile_context` 교체 선언.
  3. **안정성 계약 발효:** 12-동사 표면 동결은 그 자체로 사용자와의 계약 변경이며, 동결 이후의 유지 비용을 짊어지겠다는 약속이다.
- **역-원칙:** 위 셋 중 하나도 출시하지 못하면 그 릴리스는 3.0이 아니라 2.16이다. "라이프사이클이 완성됐다"는 마케팅 서사는 메이저 범프의 근거가 될 수 없다 — 근거는 오직 호환성 계약이다. 현행 계획의 "3.0인데 아무것도 안 깨짐"은 이 원칙 위반이었고, 재설계는 그것을 교정한다.

## 9. 즉시 다음 10개 태스크

1. **docs:** `docs/V3_PARADIGM_PLAN.md`와 vooy-informed 로드맵에 본 재절단(2.15 신설, context_packet_shape 이월, 거버넌스 2.14 이동, extract_claims 신설) 반영. 코드 변경 없음.
2. **bench:** 2.12 baseline 재측정 후 `docs/bench/baselines/2.12.json` 커밋 — 이후 모든 게이트의 기준점.
3. **feat:** `src/memkraft/store_core.py` — 레코드 엔벨로프 v1, flock append, tombstone reader 계약, 컴팩션. 동시성 테스트(4프로세스×250 append) 포함.
4. **test:** Gym 레지스트리 확장 — 신규 시나리오명 수용, 숫자 임계값을 `gates.py`에 상수로, 잘못된 파라미터의 구조화 실패(비제로 종료, traceback 금지).
5. **ci:** `.github/workflows/gym-gate.yml` — `src/`·`benchmarks/` 변경 PR에 search_recall 게이트 강제. 이후 시나리오는 추가만 하면 됨.
6. **feat:** `extract_claims` 결정적 추출기 + 라벨 픽스처 — C2 단절을 코드가 늘어나기 전에 해소.
7. **feat:** candidates 사이드카(`remember_candidate`/`list_candidates`) — 엔벨로프 사용, TTL, provenance 연동.
8. **feat:** session overlay + `session_overlay_recall` Gym 시나리오(재현 1.00 / 누출 0 게이트).
9. **feat:** resolver dry-run + 120케이스 라벨 픽스처 + `resolver_verdicts` 시나리오(정확도 ≥0.95, 결정성 1.00).
10. **feat:** last-interaction index(append 로그+스냅샷 구조) + `last_interaction` 시나리오(정확도 1.00, p95 <5ms @10k).

이 10개가 끝나면 2.13.0은 릴리스 가능 상태이며, 임계 경로(store_core → claims → resolver)가 뚫려 2.14의 compiled_truth 작업을 즉시 시작할 수 있다.

---

## 10. 추가 개선 여지 — 실행 전 보강해야 할 빠진 안전장치

위 재설계는 릴리스 절단면과 3.0 기준을 크게 개선했지만, 구현에 들어가기 전 아래 항목을 더 보강하면 실패 확률을 낮출 수 있다. 이 섹션은 “기능 추가”가 아니라 **로드맵 실행 안전장치**다.

### 10.1 마이그레이션/호환성 계획을 별도 산출물로 분리

**문제:** `store_core`와 envelope v1이 들어오면 기존 `.memkraft/` sidecar와 markdown 기반 데이터가 공존한다. 현재 계획은 새 스키마를 정의하지만, 기존 사용자의 디렉터리를 언제/어떻게 업그레이드하는지 명시하지 않는다.

**보강:** 2.13 첫 PR 이후 바로 `docs/MIGRATIONS.md`를 만들고, 각 릴리스에 아래 표를 유지한다.

```text
from_version | to_version | automatic? | command | rollback | data_loss_risk
2.12.x       | 2.13.0     | no/doctor  | memkraft doctor --migrations | backup dir | none expected
```

**추가 API/CLI 후보:**

```bash
memkraft doctor --base-dir <dir> --migrations
memkraft migrate --base-dir <dir> --to 2.13 --dry-run
memkraft migrate --base-dir <dir> --to 2.13 --apply
```

**게이트:**

- 2.12 fixture 디렉터리에서 `migrate --dry-run`은 파일 변경 0바이트.
- `migrate --apply` 후 기존 `search` 결과 무회귀.
- 적용 전 자동 백업 디렉터리 생성 또는 명시적 `--no-backup` 필요.

### 10.2 공개 API 표면을 “stable / preview / internal” 3단으로 문서화

**문제:** 3.0에서 12개 코어 동사를 stable로 동결한다고 했지만, 2.13~2.15 사이 신규 API가 너무 빨리 stable처럼 굳어질 수 있다.

**보강:** 모든 신규 API는 최초 출시 때 `preview`로 문서화하고, 3.0에서만 stable 승격한다.

```text
stable: remember, search, why
preview: remember_candidate, extract_claims, resolver_dry_run, sleep, compile_context, report_outcome
internal: store_core helpers, low-level JSONL readers, benchmark fixture generators
```

**게이트:** 3.0 전까지 README 상단에는 preview 경고를 유지하고, `docs/V3_API.md`에서 stable 승격/비승격 목록을 명시한다.

### 10.3 threat model을 별도 문서로 작성

**문제:** governance와 credential redaction이 있지만, “무엇을 막는가”가 아직 없다. 메모리 시스템은 실패 모드가 일반 라이브러리보다 위험하다.

**보강:** 2.14 전에 `docs/THREAT_MODEL.md` 작성.

최소 위협 목록:

- secret capture: API key, token, cookie, password, private key 저장
- prompt injection memory poisoning: 악성 문장이 future context에 주입
- stale truth: 과거 사실이 현재 사실처럼 주입
- cross-session leakage: session overlay가 다른 session에 노출
- deletion failure: forget 후 derived view/export/search에 잔존
- provenance laundering: derived record가 원 출처 없이 확정 사실처럼 보임
- utility poisoning: 잘못된 outcome report가 ranking을 오염

**게이트:** 각 위협은 최소 1개 regression test 또는 Gym fixture와 연결한다.

### 10.4 외부 벤치마크는 “3.0 필수” 전에 현실성 spike 필요

**문제:** LongMemEval/LoCoMo 스타일 외부 벤치 공표는 좋지만, 라이선스/데이터 형식/로컬 실행 비용이 릴리스 막판 리스크가 될 수 있다.

**보강:** 2.13 중 `spike: external benchmark adapter feasibility`를 추가한다.

**산출물:**

- `benchmarks/external/README.md`
- 사용 가능한 공개 dataset 후보와 라이선스
- 최소 20문항 smoke adapter
- 실행 시간/비용 기록

**3.0 게이트 조정:** 외부 벤치 전체 통합이 막히면, 최소한 “외부 형식 adapter + 공개 fixture subset + 결과표”는 필수로 유지한다.

### 10.5 release checklist와 rollback checklist를 문서화

**문제:** 2.12 배포 때 PyPI/GitHub/Hermes smoke를 수동으로 잘 검증했지만, refined roadmap에는 릴리스 체크리스트가 직접 들어있지 않다.

**보강:** `docs/RELEASE_CHECKLIST.md` 추가.

최소 체크:

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario search_recall --gate --out /tmp/memkraft-search.json
python3 -m build
python3 -m twine check dist/*
python3 -m venv /tmp/memkraft-smoke && /tmp/memkraft-smoke/bin/pip install dist/*.whl
/tmp/memkraft-smoke/bin/memkraft --version
/tmp/memkraft-smoke/bin/memkraft doctor --base-dir /tmp/memkraft-smoke-memory
```

추가 Hermes smoke:

- installed package import without `source_path`
- remember/search smoke under `HERMES_HOME`
- profile-local memory path 확인

Rollback:

- PyPI yanked release 기준
- GitHub release note 수정 기준
- post-release regression 발견 시 patch version cut 기준

### 10.6 “텍스트/마크다운 원본 vs sidecar 파생”의 불변식 명문화

**문제:** MemKraft는 markdown-first 역사와 `.memkraft/` sidecar가 공존한다. 구현자가 어느 쪽을 source of truth로 볼지 헷갈릴 수 있다.

**보강:** 2.13 문서에 다음 불변식을 추가한다.

```text
- User-authored markdown remains durable source material.
- .memkraft/*.jsonl records are machine-readable operational records.
- compiled_truth and other derived views are always rebuildable caches unless explicitly documented otherwise.
- search must never require derived caches to exist; caches improve quality/latency, not availability.
```

**게이트:** derived cache 삭제 후에도 기존 search/doctor가 동작해야 한다.

### 10.7 `extract_claims` 범위를 매우 좁게 시작

**문제:** 결정적 claim extractor는 욕심내면 바로 NLU 프로젝트가 된다.

**보강:** 2.13에서 `extract_claims`는 아래 4종만 지원한다.

1. `X prefers Y`
2. `X uses Y`
3. `X is located at/in Y`
4. `X changed/updated/corrected Y to Z`

한국어는 2.13에서 최소 패턴만:

- `X는 Y를 선호`
- `X는 Y를 사용`
- `X는 Y에 있음`
- `X를 Y로 수정/변경`

그 외는 후보로만 남기고 `CANDIDATE_REVIEW`.

**게이트:** false positive 최소화가 recall보다 우선. 2.13 목표는 “많이 뽑기”가 아니라 “틀린 active 승격 방지”.

### 10.8 `compile_context`의 성공 기준에 “사용자에게 유용한 실패” 포함

**문제:** `miss=True`일 때 환각하지 않는 것은 좋지만, agent 입장에서는 다음 행동도 필요하다.

**보강:** `compile_context`가 miss일 때도 아래를 반환하게 한다.

```json
{
  "miss": true,
  "recommended_action": "ask_user|search_sessions|inspect_files|web_search|none",
  "reason": "No source-backed memory matched the task"
}
```

**게이트:** empty memory/context miss fixture에서 recommended_action이 deterministic하게 나온다.

### 10.9 구현 순서를 더 작게 쪼개기

현재 “즉시 다음 10개 태스크”는 PR 단위로 좋지만, 첫 구현자에게는 아직 크다. 2.13의 첫 3개 PR은 아래 micro-slice로 쪼갠다.

1. `store_core` append/read only, no tombstone.
2. tombstone filtering only.
3. compaction only.
4. concurrency test only.
5. Gym registry accepts new names, returns stub metrics.
6. Gym thresholds fail structured JSON.
7. CI runs only `search_recall` first.
8. `extract_claims` English 4-pattern only.
9. `extract_claims` Korean 4-pattern only.
10. `remember_candidate` writes envelope without resolver integration.

### 10.10 최종 판단

더 개선한다면 우선순위는 다음 5개다. **(2026-07-08 전부 작성 완료 — 아래 링크가 각 항목의 실행 문서다.)**

1. [`docs/MIGRATIONS.md`](../MIGRATIONS.md) — 2.12.x→2.13.0 dry-run/apply/rollback 정책, 마이그레이션 표, `doctor --migrations`/`migrate` CLI 계약, 테스트 게이트 (§10.1 구체화)
2. [`docs/THREAT_MODEL.md`](../THREAT_MODEL.md) — 위협 7종(T1~T7) × 완화 × 테스트 연결, 위협×릴리스 매트릭스 (§10.3 구체화)
3. [`docs/RELEASE_CHECKLIST.md`](../RELEASE_CHECKLIST.md) — 로컬 테스트→Gym 게이트→build/twine→wheel smoke→Hermes smoke→PyPI/GitHub 검증→rollback/yank/patch 정책 (§10.5 구체화)
4. [`benchmarks/external/README.md`](../../benchmarks/external/README.md) — 외부 벤치마크 adapter spike: LongMemEval/LoCoMo 후보·라이선스 리스크·20문항 smoke adapter 계약·지표·비-목표 (§10.4 구체화)
5. [`docs/plans/2026-07-08-memkraft-2.13-micro-slices.md`](2026-07-08-memkraft-2.13-micro-slices.md) — 2.13 TDD micro-slice 13개(S1~S13): store_core 축, gym/CI 축, extract_claims 축, candidate→overlay→resolver, last-interaction (§10.9 구체화)

이 다섯 개가 갖춰졌으므로 refined roadmap은 “좋은 전략 문서”에서 “다른 에이전트에게 바로 던져도 덜 망가지는 실행 문서”가 된다. 구현 착수점은 micro-slices의 S1(store_core append/read)이다.
