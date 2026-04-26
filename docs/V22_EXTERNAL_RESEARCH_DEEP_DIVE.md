# MemKraft 외부 리서치 심층 분석 (2026-04-26)

> **목표:** 업계 최신 연구/프로젝트에서 MemKraft에 적용 가능한 아이디어를 추출
> **철학:** zero-dep, plain Markdown, no LLM inside, bitemporal
> **리서치 범위:** 논문 15+편, 오픈소스 프로젝트 8개, 벤치마크 3개

---

## Part 1: 업계 컨버전스 — "파일 기반 메모리가 표준이 되었다"

### 핵심 발견
**2026년 3대 벤더 (Anthropic, OpenAI, Google)가 모두 같은 패턴으로 수렴:**

| 벤더 | 구현 | 패턴 |
|------|------|------|
| Anthropic | Claude Code CLAUDE.md | 파일 기반, 에이전트가 직접 읽기/쓰기 |
| OpenAI | ChatGPT persistent memory | 구조화된 facts 저장 |
| Google | Gemini memory | preference + entity 추적 |

**agent-memory.bruegs.com 벤치마크:**
- Flat-file only 메모리 → **LoCoMo 74%** (복잡한 시스템과 경쟁 가능)
- Workspace 파일 (TOOLS.md, USER.md, SOUL.md) 인덱싱 → **recall +87~113%**
- **BM25 + Vector 혼합** (0.6/0.4) → 단독 대비 일관되게 우수
- **Temporal Decay 비활성화** → 소규모 코퍼스(<500 파일)에서 무의미

**MemKraft 시사점:**
> 이미 우리는 파일 기반이다. BM25를 추가하면 검색 품질 대폭 향상 가능.
> Workspace 파일 인덱싱은 이미 track_document로 가능.

---

## Part 2: MAGMA — 4중 그래프 (가장 주목할 논문)

**논문:** MAGMA: A Multi-Graph based Agentic Memory Architecture (2026-01)
**핵심:** 메모리를 4개의 독립 그래프에 동시 표현:

| 그래프 | 설명 | MemKraft 현재 상태 |
|--------|------|-----------------|
| **Semantic** | 의미 유사성 기반 클러터링 | ❌ 없음 (IDF만) |
| **Temporal** | 시간순 이벤트 체인 | ✅ bitemporal |
| **Causal** | 원인→결과 관계 | ❌ 없음 |
| **Entity** | 엔티티 간 관계 | ✅ graph.py |

**적용 아이디어 — "QuadGraph" (4중 그래프):**
```
MemKraft graph.py 현재: entity graph (1개)
MemKraft graph.py 목표: 4개 graph layer

1. Entity Graph (기존) — Simon --works_at--> Hashed
2. Temporal Graph (신규) -- event1 --before--> event2
3. Causal Graph (신규) -- "CEO 사임" --causes--> "새 CEO 임명"
4. Semantic Graph (신규) -- "블록체인" --related_to--> "암호화폐"
```

**구현 방식 (zero-dep):**
- SQLite edges 테이블에 `graph_type` 컬럼 추가 (entity/temporal/causal/semantic)
- 기존 `graph_edge(from, relation, to)` → `graph_edge(from, relation, to, graph_type="entity")`
- 검색 시 4개 그래프 병렬 탐색 → 결과 통합

**예상 효과:** multi-hop reasoning 대폭 강화 (A-MEM 논문: 6배 향상)

---

## Part 3: SimpleMem — 의미 무손실 압축

**논문:** SimpleMem: Efficient Lifelong Memory (2026-01)
**핵심 수치:**
- **98% 토큰 절감** (원본 대비)
- **Mem0 대비 +26% F1**
- Mem0 대비 85-93% 토큰 절감

**원리:** Semantic Lossless Compression
1. 원본 텍스트 → atomic facts 추출 (의미 손실 없이 분해)
2. 중복/유사 facts 병합
3. 컨텍스트에 필요한 최소 단위만 주입

**MemKraft 적용 — "Compact Mixin":**
```python
class CompactMixin:
    def compact_memory(self, entity: str) -> str:
        """entity의 모든 facts를 의미 무손실 압축"""
        facts = self.fact_history(entity)
        # 1. 중복 제거 (같은 field, 같은 value)
        # 2. 시간순 정렬 후 최신만 보존 (이전 versions는 history로)
        # 3. 핵심 facts만 추출 (role, company, preference 등)
        return compressed_text
    
    def compact_all(self, max_chars: int = 15000) -> str:
        """전체 메모리를 max_chars 이내로 압축"""
```

**기존 `compact()` API와 차이:**
- 기존 compact: tier 기반 오래된 entity를 archival로 이동
- 신규 compact_memory: **같은 entity 내에서** facts를 의미 보존 압축

---

## Part 4: Memory Consolidation (수면 통합)

**연구:** SimpleMem + Zettelkasten + CLS Theory
**핵심:** 에이전트가 "자면서" 메모리를 정리/통합

**CLS (Complementary Learning Systems) 이론:**
- 뇌는 깨어있을 때 → 빠른 학습 (hippocampus)
- 뇌는 잘 때 → 느린 통합 (neocortex)
- 에이전트도: 실시간 → append / 유휴시간 → consolidation

**MemKraft 적용 — "Dream Cycle v2" (이미 AGENTS.md에 있는 개념을 MemKraft 내장):**
```python
class ConsolidationMixin:
    def consolidate(self, strategy="auto"):
        """
        유휴 시간에 호출. 메모리를 정리/통합.
        
        strategy:
        - "auto": 기본 (중복 제거 + stale fact 정리 + graph 정리)
        - "aggressive": 더 공격적 (오래된 facts 병합)
        - "dry_run": 변경 없이 리포트만
        """
        # 1. 중복 facts 병합 (같은 entity, 같은 field, 같은 value)
        # 2. stale facts 정리 (valid_until이 오래된 것)
        # 3. graph orphan 정리 (연결 없는 노드)
        # 4. observation log 재생성 (핵심 facts 기반)
```

**차별점:** 경쟁자들은 LLM으로 consolidation → 비용/지연. MemKraft는 패턴 매칭으로 → 비용 0.

---

## Part 5: Hybrid Search — BM25 + IDF + Fuzzy (3-way)

**현재 MemKraft search:**
- exact match + IDF (token frequency) + fuzzy (difflib)

**업계 표준 (2026):**
- BM25 + Vector similarity (0.6/0.4)
- 또는 BM25 + IDF + Fuzzy + Graph (4-way)

**MemKraft 적용 — BM25 추가:**
```python
def _bm25_score(self, query_tokens, doc_tokens, k1=1.5, b=0.75):
    """BM25 scoring (stdlib only, no external dep)"""
    # 표준 BM25 공식
    # IDF 부분은 기존 _idf_score 활용
    # 평균 문서 길이 보정 (b 파라미터)
```

**기존 search에 BM25 통합:**
```python
def search(self, query, ...):
    # 1. exact match (기존)
    # 2. IDF score (기존, 개선)
    # 3. fuzzy match (기존)
    # 4. BM25 score (신규) ← 추가
    # 최종 점수 = weighted combination
```

**예상 효과:** 검색 정확도 +5~10pp (terminology mismatch 해결)

---

## Part 6: Reciprocal Rank Fusion (RRF)

**Blake Crosley 사례 (2026-02):**
- 49,746 text chunks, 15,800 files
- BM25 + Vector → RRF로 퓨전
- cosine similarity로 off-task 감지

**MemKraft 적용:**
```python
def _rrf_fusion(self, *result_lists, k=60):
    """
    Reciprocal Rank Fusion — 여러 검색 결과를 순위 기반으로 통합
    RRF_score(d) = Σ 1/(k + rank_i(d))
    """
```

**장점:** 점수 정규화 불필요. 순위만으로 통합. zero-dep.

---

## Part 7: Causal Graph (인과 관계)

**MAGMA 논문의 핵심 기여:**
- "A happened because B" 관계 추적
- "What caused X?" 질문에 답변 가능

**MemKraft 적용:**
```python
# graph_edge에 causal relation 추가
mk.graph_edge("CEO 사임", "caused_by", "건강 악화", graph_type="causal")
mk.graph_edge("새 CEO 임명", "caused_by", "CEO 사임", graph_type="causal")

# 인과 체인 탐색
mk.graph_causal_chain("새 CEO 임명")
# → ["CEO 사임", "건강 악화"]
```

**구현:** graph.py의 edges 테이블에 graph_type 추가. causal 전용 탐색 메서드 추가.

---

## Part 8: Semantic Clustering (의미 클러터링)

**MAGMA의 Semantic Graph:**
- 비슷한 주제의 facts를 클러터로 묶음
- 검색 시 클러터 단위로 탐색 → 노이즈 감소

**MemKraft 적용 (no-LLM 버전):**
- IDF 기반 토큰 유사도로 클러터링
- 같은 entity의 facts 중 공통 토큰이 많은 것끼리 묶기
- `search()` 시 클러터 대표만 컨텍스트에 주입

---

## Part 9: EU AI Act Compliance (규제 대응)

**2026년 8월 2일 EU AI Act 발효:**
- AI 에이전트의 메모리 투명성 요구
- "어디서 기억을 얻었는지" 추적 의무

**MemKraft 장점:**
- ✅ Plain Markdown → 사람이 직접 읽고 검증 가능
- ✅ Bitemporal → "언제 알았는지" 정확히 추적
- ✅ Source tracking → 모든 fact에 출처 기록
- ✅ No LLM inside → 결정 과정 투명

**차별화 메시지:**
> "EU AI Act ready out of the box. Every fact has a source, a valid time, and a recorded time. No black box."

---

## Part 10: 실행 아이디어 통합 (우선순위)

### Tier 1 — 즉시 적용 (zero-dep, 기존 구조 확장)
| # | 아이디어 | 출처 | 예상 효과 | 작업량 |
|---|---------|------|---------|--------|
| 1 | **BM25 scoring 추가** | agent-memory.bruegs.com | +5~10pp | 3일 |
| 2 | **Causal graph layer** | MAGMA | multi-hop 강화 | 3일 |
| 3 | **Semantic clustering** | MAGMA | 검색 노이즈 감소 | 4일 |
| 4 | **Memory consolidation (수면)** | SimpleMem + CLS | 토큰 50%+ 절감 | 5일 |
| 5 | **Reciprocal Rank Fusion** | Blake Crosley | 검색 퓨전 품질 | 2일 |

### Tier 2 — 중간 (구조 변경 필요)
| # | 아이디어 | 출처 | 예상 효과 | 작업량 |
|---|---------|------|---------|--------|
| 6 | **QuadGraph (4중 그래프)** | MAGMA | multi-hop 6x | 10일 |
| 7 | **LoCoMo 벤치마크 harness** | Snap Research | 검증 도구 | 7일 |
| 8 | **Semantic Lossless Compression** | SimpleMem | 토큰 98% 절감 | 7일 |
| 9 | **EU AI Act compliance 문서** | EU 규제 | 마케팅 차별화 | 3일 |

### Tier 3 — 장기 (대규모 설계)
| # | 아이디어 | 출처 | 예상 효과 | 작업량 |
|---|---------|------|---------|--------|
| 10 | **Observation Log (no-LLM)** | Mastra OM | +3~5pp | 10일 |
| 11 | **Zettelkasten 자동 링크** | A-Mem | reasoning 강화 | 10일 |
| 12 | **한국어 LoCoMo 생성** | 자체 | 독점 벤치마크 | 14일 |

---

## 핵심 전략 업데이트

### 이전: "No-LLM SOTA"
### 확장: "No-LLM SOTA + EU AI Act Ready + Zero-Cost Consolidation"

**MemKraft의 3가지 독보적 차별점:**
1. **No-LLM** — 비용 0, 지연 0, 재현성 100%
2. **Transparent** — plain Markdown, 사람이 직접 검증 가능
3. **Bitemporal** — "언제 알았는지" 추적 (EU AI Act 핵심 요구사항)

**경쟁자가 복제할 수 없는 것:**
- LLM inside 시스템은 비용/지연을 근본적으로 제거 불가
- Vector DB 시스템은 투명성 확보 어려움
- Graph-only 시스템은 bitemporal 시간 추론 약함

---

*작성: Zeon, 2026-04-26*
*근거: MAGMA (2026-01), SimpleMem (2026-01), A-Mem (NeurIPS 2025), agent-memory.bruegs.com (2026-03), HermesOS (2026-04), vectorize.io (2026-04), Mastra OM, EU AI Act*
