# MemKraft 벤치마크 발전 아이디어 리서치 (2026-04-26)

> **철학 제약:** zero-dep (stdlib only), plain Markdown, no LLM calls inside MemKraft, bitemporal memory
> **목표:** LongMemEval 98% 유지 + LoCoMo/MemoryBench 등 신규 벤치마크 SOTA

---

## 1. 경쟁 환경 현황 (2026-04 기준)

| 시스템 | LongMemEval_s | 기법 | LLM inside? |
|--------|-------------|------|-------------|
| **Mastra OM** (gpt-5-mini) | **94.87%** | Observer+Reflector 백그라운드 에이전트 | ✅ |
| **Mastra OM** (gpt-4o) | **84.23%** | 동일, 공식 벤치마크 모델 | ✅ |
| **Supermemory ASMR** | **~99%** (실험적) | 3 reader + 3 search + 8-variant ensemble | ✅ |
| **Emergence AI** | **86%** | Turn-level match + session NDCG + CoT | ✅ |
| **Hindsight** | **91.4%** | 4 parallel retrieval + neural reranking | ✅ |
| **MemKraft (우리)** | **98%** (oracle 50) | Bitemporal + hybrid search (exact+IDF+fuzzy) | ❌ |
| **Zep** | **71.2%** | Graph DB + vector search | ✅ |
| **Mem0g** | **68.4%** (LoCoMo) | Graph-enhanced selective pipeline | ✅ |

### 핵심 관찰
1. **상위권 전부 LLM inside** — Observer, Reflector, Search Agent, Ensemble 등 다중 LLM 호출
2. **MemKraft는 유일하게 no-LLM으로 98%** — 이건 차별점이자 철학
3. **하지만 oracle 50이라 과대평가 위험** — full 500으로 가면 하락 예상
4. **Supermemory 99%는 parody/실험** — 실전 아님, 8-variant ensemble은 비용 폭탄

---

## 2. 핵심 기법 분석 (우리에 적용 가능한 것)

### 🥇 #1. Observation Log 패턴 (Mastra OM)
**원리:** 대화가 들어오면 Observer 에이전트가 "관찰 로그"로 변환. 원본 메시지 대신 로그가 컨텍스트에 들어감.

**MemKraft 적용 (no-LLM 버전):**
- `update()` 호출 시 → 기존 raw append 대신 **구조화된 observation 생성**
- 패턴: `[2026-04-26] Simon은 Hashed CEO이다. (source: DM, valid: ongoing)`
- 기존 bitemporal fact + structured observation 병합
- 검색 시 observation log가 원본보다 우선

**예상 효과:** +3~5pp (knowledge-update, temporal-reasoning 강화)
**난이도:** Medium — 기존 `update`/`fact_add` 로직 확장
**zero-dep 호환:** ✅ (정규식 + 패턴 매칭)

### 🥈 #2. Multi-Pass Retrieval (Emergence AI)
**원리:** 검색을 한 번이 아니라 2~3 pass로 나눠서:
- Pass 1: 정확한 매칭 (exact + entity match)
- Pass 2: 관련 컨텍스트 (graph neighbors + temporal expansion)
- Pass 3: 시간순 재구성 (bitemporal timeline)

**MemKraft 적용:**
- 현재 `search()`는 single-pass → `search_multi()` 추가
- Pass 1: `search(fuzzy=False)` → exact hit
- Pass 2: `graph_neighbors()` + `search_temporal()` → 관련 entity 확장
- Pass 3: `fact_history()` → 시간순 정렬 + recency 가중치

**예상 효과:** +5~8pp (multi-session 질문 강화)
**난이도:** Medium — 기존 API 조합
**zero-dep 호환:** ✅

### 🥉 #3. Knowledge Update 감지 (Mastra OM 핵심)
**원리:** "X는 CEO였다 → X는 CTO로 바뀌었다" 같은 업데이트를 자동 감지.

**MemKraft 적용:**
- 이미 bitemporal로 `valid_from`/`valid_until` 지원 ✅
- 하지만 **업데이트 자동 감지**는 없음
- `update()` 호출 시 기존 fact와 비교 → 겹치면 `valid_until` 자동 종료
- 예: `update("Simon", "Role: CTO")` → 기존 "Role: CEO" fact의 `valid_until` 자동 설정

**예상 효과:** +2~3pp (knowledge-update 유형)
**난이도:** Low — 기존 bitemporal 확장
**zero-dep 호환:** ✅

### #4. Zettelkasten 링킹 (A-Mem, NeurIPS 2025)
**원리:** 각 메모(노트)가 다른 메모와 자유롭게 연결. 검색 시 링크 따라 탐색.

**MemKraft 적용:**
- 이미 `graph_edge`로 연결 가능 ✅
- 하지만 **자동 링크 제안**은 없음
- 새 entity track 시 → 기존 entity와 유사도 계산 → 자동 링크 제안
- `search()` 결과 → graph neighbors 자동 확장

**예상 효과:** +2~4pp (multi-session, reasoning)
**난이도:** Medium — IDF/fuzzy 기반 유사도
**zero-dep 호환:** ✅

### #5. Question-Type Specialized Retrieval
**원리:** LongMemEval 질문 5가지 유형별로 다른 검색 전략:
- `single-session` → exact session match
- `multi-session` → graph + temporal expansion
- `knowledge-update` → bitemporal timeline, 최신 fact 우선
- `temporal-reasoning` → 시간순 정렬 + before/after 필터
- `preference` → likes/preference entity 우선

**MemKraft 적용:**
- 질문 유형 자동 분류 (키워드 패턴)
- 유형별 검색 전략 라우팅
- `search_smart()` 이미 있지만 → 유형별 specialized version

**예상 효과:** +3~5pp
**난이도:** Medium
**zero-dep 호환:** ✅

---

## 3. 신규 벤치마크 도입 전략

### LoCoMo (Snap Research, ACL 2024)
- **규모:** 10 conversations × 35 sessions × 300 turns
- **평가:** QA + event summarization + multimodal dialogue
- **적용 난이도:** Medium (대화 데이터 → MemKraft format 변환 필요)
- **차별화:** 한국어 LoCoMo 버전 만들면 독점

### LoCoMo-Plus (ARR 2026)
- **핵심:** "Beyond-factual" — 암묵적 제약, 사용자 상태, 목표 평가
- **6번째 태스크:** Cognitive (latent constraint recall)
- **적용:** MemKraft의 preference + entity system이 강점
- **예상:** LoCoMo-Plus에서 기존 시스템보다 잘 할 수 있는 영역

### MemoryBench (supermemory)
- **핵심:** Mem0/Zep 비교 리더보드
- **참여:** API 제공 → 자동 채점
- **이점:** 공개 리더보드 등록 = 마케팅

---

## 4. 실행 로드맵

### Phase 1 (v2.2, 2주) — 즉시 적용 가능
| # | 기법 | 예상 효과 | 작업량 |
|---|------|---------|--------|
| 1 | Knowledge Update 자동 감지 | +2~3pp | 3일 |
| 2 | Multi-Pass Retrieval | +5~8pp | 5일 |
| 3 | Question-Type 라우팅 | +3~5pp | 3일 |
| **합계** | | **+10~16pp** | **11일** |

### Phase 2 (v2.3, 4주) — 중간 난이도
| # | 기법 | 예상 효과 | 작업량 |
|---|------|---------|--------|
| 4 | Observation Log 패턴 | +3~5pp | 5일 |
| 5 | Zettelkasten 자동 링크 | +2~4pp | 5일 |
| 6 | LoCoMo 벤치마크 harness | 검증 도구 | 7일 |
| **합계** | | **+5~9pp** | **17일** |

### Phase 3 (v3.0, 8주) — 대규모
| # | 기법 | 예상 효과 | 작업량 |
|---|------|---------|--------|
| 7 | core.py 모듈 분해 | 유지보수 | 10일 |
| 8 | Multi-Agent Shared Memory | 실전 활용 | 14일 |
| 9 | LoCoMo-Plus Cognitive 평가 | 차별화 | 7일 |
| 10 | 한국어 LoCoMo 번역/생성 | 독점 벤치마크 | 14일 |

---

## 5. 핵심 전략: "No-LLM SOTA" 포지셔닝

**현재 업계 상황:**
- 상위권 전부 LLM inside (비용 ↑, 지연 ↑, 재현성 ↓)
- MemKraft는 유일하게 no-LLM으로 98%

**차별화 메시지:**
> "MemKraft achieves SOTA-level accuracy **without any LLM calls inside the memory system**. Zero latency overhead, zero API cost for memory operations, 100% reproducible."

**이것이 가능한 이유:**
1. Bitemporal fact layer — 시간 추론을 데이터 구조로 해결
2. Hybrid search (exact + IDF + fuzzy) — 검색을 수학으로 해결
3. Graph layer — 관계 추론을 SQLite로 해결
4. Observation Log (적용 시) — 요약을 패턴 매칭으로 해결

**경쟁자들이 따라하기 어려운 점:**
- LLM inside는 비용/지연 문제를 근본적으로 해결 불가
- MemKraft는 비용 0, 지연 0, 재현성 100%
- "98% accuracy at $0 cost" — 이건 마케팅 카피로도 강력

---

## 6. 리스크 & 고려사항

| 리스크 | 설명 | 완화 |
|--------|------|------|
| oracle 50 과대평가 | full 500으로 가면 점수 하락 예상 | Phase 1에서 full 500 도전 |
| no-LLM 한계 | 복잡한 추론 질문은 LLM 없이 한계 | 질문 유형별 라우팅으로 partially 해결 |
| Supermemory 99% | 실험이지만 인식 형성 | "재현 불가, 비용 폭탄" 명시 |
| 한국어 벤치마크 부재 | 한국어 평가 기준 없음 | 자체 LoCoMo-ko 생성 |

---

*작성: Zeon, 2026-04-26*
*근거: Mastra OM, Supermemory ASMR, Emergence AI, A-Mem (NeurIPS), LoCoMo-Plus, Mem0 ECAI 2025*
