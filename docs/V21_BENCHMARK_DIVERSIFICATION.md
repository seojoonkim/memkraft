# MemKraft v2.1 벤치마크 다변화 리서치 보고서

**작성일:** 2026-04-26  
**담당:** MemKraft v2.1 로드맵 Task #5  
**목적:** LongMemEval 단일 의존 탈피 → 다중 벤치마크 포트폴리오 구축  
**현재 상태:** LongMemEval oracle N=50, semantic majority vote → **98.0%**

---

## 목차

1. [현황 진단](#1-현황-진단)
2. [벤치마크 7종 비교 분석](#2-벤치마크-7종-비교-분석)
3. [Top 3 채택 추천](#3-top-3-채택-추천)
4. [LLM Judge 자체화 계획](#4-llm-judge-자체화-계획)
5. [CI/CD 통합 전략](#5-cicd-통합-전략)
6. [6개월 로드맵](#6-6개월-로드맵)
7. [포지셔닝 전략](#7-포지셔닝-전략)

---

## 1. 현황 진단

### 1.1 현재 벤치마크 스택

```
benchmarks/
├── longmemeval/          ← 유일한 평가 벤치마크
│   ├── V5_REPORT.md      ← 최신 결과: 98.0% (semantic majority vote)
│   ├── data/
│   ├── evaluator.py
│   ├── harness.py
│   ├── llm_judge.py      ← Claude Haiku 단일 judge
│   └── run_*.sh
└── personamem/           ← 폴더만 존재, 미구현
    └── __pycache__
```

### 1.2 단일 의존의 위험성

| 리스크 | 설명 | 심각도 |
|--------|------|--------|
| **천장 효과** | N=50 oracle에서 98% → 개선 여지 1개 문항뿐 | 🔴 높음 |
| **과적합 위험** | LME 특화 튜닝 → 실제 성능과 괴리 가능 | 🔴 높음 |
| **측정 다양성 부족** | 대화형 QA 위주 → 절차 기억·연속학습 미측정 | 🟡 중간 |
| **커뮤니티 인지도** | 논문 발표 시 단일 벤치마크 → 리뷰어 지적 | 🟡 중간 |
| **temperature 불안정** | GPU non-associativity로 결과 가변 | 🟡 중간 |

### 1.3 LLM Judge 현황

- **현재:** Claude Haiku 단일 judge (`JUDGE_MODEL` env)
- **단점:** 단일 모델 bias, API 비용 발생, 재현성 취약
- **환경변수 확인 결과:** `LITELLM_VHH_BASE_URL` 미등록, `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL` 존재
- **개선 필요:** 다중 judge 합의 + 로컬 judge 옵션

---

## 2. 벤치마크 7종 비교 분석

### 2.1 종합 비교표

| 벤치마크 | 유형 | 데이터 규모 | 라이선스 | SOTA (최고 성능) | MemKraft 적용 난이도 | 차별화 가능성 |
|---------|------|------------|---------|----------------|-------------------|-------------|
| **LongMemEval** | 대화형 장기 기억 QA | 500 문항 (oracle 50 샘플링) | CC-BY (HF) | Zep 94.8% (DMR), MemKraft ~98% (oracle) | ✅ 이미 구현 | ⭐⭐⭐ (천장 도달) |
| **LoCoMo** | 초장기 대화 (QA+요약+멀티모달) | 10 대화 × 300턴, 9K 토큰 avg, 35 세션 | Apache 2.0 | 논문 내 RAG 기법 ~60% QA F1 (인간 ~85%) | 🟡 중간 | ⭐⭐⭐⭐⭐ |
| **MemoryBench (supermemory)** | 대화형 메모리+RAG 통합 평가 | LoCoMo·LME 포함 다중 데이터셋 | MIT | Supermemory > Mem0 > Zep (자체 리포트) | 🟢 낮음 | ⭐⭐⭐⭐ |
| **MemoryBench (arXiv 2510.17281)** | 절차 기억·연속 학습 | 다중 도메인·언어, 사용자 피드백 시뮬레이션 | 연구용 (미공개 일부) | "SOTA 기법 모두 불만족" (논문 주장) | 🔴 높음 | ⭐⭐⭐ |
| **MEMENTO** | 에이전트 개인화 기억 (personalized) | 미발표 독립 벤치마크 (관련 연구: arXiv 2502.xxxx 추정) | 미확인 | 미확인 | 🔴 높음 (데이터 미공개) | ⭐⭐ |
| **LongBench v2** | 장문 컨텍스트 다중 추론 (MC) | 503 문항, 8K~2M 컨텍스트 | CC-BY-4.0 | o1-preview 57.7% (인간 53.7%) | 🟡 중간 | ⭐⭐⭐ |
| **MTEB (Memory/Retrieval)** | 임베딩·검색 다중 태스크 | 58개+ 데이터셋 (전체 MTEB) | Apache 2.0 | text-embedding-3-large (OpenAI) | 🟡 중간 | ⭐⭐⭐⭐ |

### 2.2 개별 벤치마크 상세 분석

---

#### 2.2.1 LoCoMo (Long-term Conversational Memory)

**기본 정보**
- **출처:** Snap Research × UNC Chapel Hill × USC (ACL 2024)
- **논문:** arXiv:2402.17753 — *"Evaluating Very Long-Term Conversational Memory of LLM Agents"*
- **프로젝트:** https://snap-research.github.io/locomo/
- **GitHub:** https://github.com/snap-research/LoCoMo

**데이터셋 특성**
- 10개 대화, 각 ~300턴, 평균 9K 토큰, 최대 35 세션
- 페르소나 + 시간적 이벤트 그래프로 생성 → 인간 검수
- 태스크: QA (단일홉·다중홉·시간·상식), 이벤트 요약, 멀티모달 대화 생성
- 이미지 공유·반응 포함 (멀티모달)
- 라이선스: **Apache 2.0** ✅

**SOTA 현황**
- 논문 실험: RAG 기법 적용 시 QA F1 ~60% (장기 LLM 직접 ~45%)
- 인간 성능: ~85% (QA F1 기준)
- 리더보드: PapersWithCode에 59개 SOTA 항목 등록 ([wizwand.com](https://www.wizwand.com/dataset/locomo))
- 상위권: RAG + GPT-4 계열 ~65-70% 추정 (공개 리더보드 기준)

**MemKraft 적용 분석**
```
입력: 10개 대화 × 35 세션 → session별 log_event() ingest
검색: mk.search(question, top_k=10)
평가: QA F1 (사실) + ROUGE (요약) + BERTScore (대화)
비용: 전체 실행 시 ~3시간 (N=10 × 35 세션 × 300턴)
```
- **장점:** 실세계 대화 패턴, 시간 추론 포함, Apache 2.0
- **단점:** N=10으로 통계적 유의성 낮음, 멀티모달 구현 별도 필요
- **난이도:** 🟡 **중간** (QA만 먼저 구현 시 낮음)

**차별화 가능성:** ⭐⭐⭐⭐⭐
- MemKraft의 **시간적 팩트 (bitemporal)** 기능과 직접 매핑
- temporal reasoning 서브태스크에서 기존 RAG 대비 우위 예상
- 커뮤니티 가시성 높음 (ACL 2024 발표, 활발한 후속 연구)

---

#### 2.2.2 MemoryBench (supermemoryai)

**기본 정보**
- **출처:** SuperMemory AI (오픈소스 커뮤니티)
- **GitHub:** https://github.com/supermemoryai/memorybench
- **라이선스:** MIT ✅

**데이터셋 특성**
- 플러그인 형 프레임워크: LoCoMo, LongMemEval 등 포함
- 제공자(Provider): Supermemory, Mem0, Zep
- Judge: GPT-4o, Claude, Gemini 교체 가능
- 파이프라인: Ingest → Index → Search → Answer → Evaluate
- Web UI 포함 (실시간 검사)

**SOTA 현황**
- 자체 리포트: Supermemory > Mem0 > Zep (정량 수치 미공개)
- MemKraft는 아직 등록 미됨 → **제출 기회**

**MemKraft 적용 분석**
```
방식: MemoryBench에 MemKraft provider 어댑터 구현
인터페이스: ingest()/search() → memorybench Provider 인터페이스 구현
비용: LoCoMo + LME 동시 평가, 단일 run으로 여러 벤치마크
```
- **장점:** 멀티-프레임워크 비교 가시화, 커뮤니티 등록 가능
- **단점:** 프레임워크 의존성, bun 런타임 필요 (TypeScript)
- **난이도:** 🟢 **낮음** (Python wrapper 작성 후 HTTP adapter)

**차별화 가능성:** ⭐⭐⭐⭐
- "MemKraft vs Mem0 vs Zep" 공개 비교 가능
- 마케팅·논문 기여도 높음

---

#### 2.2.3 MemoryBench (arXiv 2510.17281, Qingyao Ai et al.)

**기본 정보**
- **출처:** 학술 연구 (cs.LG, cs.IR)
- **논문:** arXiv:2510.17281v4 — *"MemoryBench: A Benchmark for Memory and Continual Learning in LLM Systems"*
- **라이선스:** 일부 미공개 (연구용)

**데이터셋 특성**
- 절차적 기억(procedural memory) + 선언적 기억(declarative memory) 이중 분류
- 사용자 피드백 시뮬레이션 (LLM-as-User)
- 다중 도메인·언어 포함
- 연속 학습 능력 평가 (동적 업데이트)

**SOTA 현황**
- "모든 SOTA 기법이 불만족스럽다"고 논문 명시
- 기존 메모리 아키텍처에서 대규모 격차 확인

**MemKraft 적용 분석**
- MemKraft의 `mk.update()` (증분 업데이트) + decay 평가에 직접 적합
- 절차적 기억 = MemKraft의 `log_event()` + fact 체계와 연계
- **단점:** 데이터셋 일부 미공개, LLM-as-User 시뮬레이터 자체 구현 필요
- **난이도:** 🔴 **높음**

**차별화 가능성:** ⭐⭐⭐
- 연속 학습 + decay 기능 검증에 유일한 전문 벤치마크

---

#### 2.2.4 MEMENTO (에이전트 개인화 기억)

**기본 정보**
- **관련 연구:** "Embodied Agents Meet Personalization" (arXiv, May 2025, Taeyoon Kwon et al.)
- **MS 자체 MEMENTO:** 확인된 독립 벤치마크 없음 (MS MEMENTO != 일반 커뮤니티 공개)
- **대안 해석:** Personalized Memory 에이전트 평가 분야

**데이터셋 특성**
- 개인화 지식 활용 능력 (past interactions → personalized assistance)
- 체화 에이전트 (embodied agents) 컨텍스트
- 사용자 특화 지식 recall 평가

**SOTA 현황**
- 해당 논문: 기존 에이전트 대비 메모리 활용 분야 도전 과제 다수 확인
- 독립 리더보드 없음

**MemKraft 적용 분석**
- **접근법:** PersonaMem 폴더 활용 + 개인화 평가 직접 설계
- `mk.track()` + `mk.fact_add()` 로 페르소나 팩트 누적 → recall 테스트
- **난이도:** 🔴 **높음** (자체 설계 필요)

**차별화 가능성:** ⭐⭐
- 독립 공개 벤치마크 미확립 → 자체 PersonaMem 확장으로 커버 가능

---

#### 2.2.5 LongBench v2

**기본 정보**
- **출처:** THU-KEG (Tsinghua University) (ACL 2025)
- **논문:** arXiv:2412.15204 — *"Towards Deeper Understanding and Reasoning on Realistic Long-context Multitasks"*
- **GitHub:** https://github.com/THUDM/LongBench
- **Dataset:** https://huggingface.co/datasets/THUDM/LongBench-v2
- **라이선스:** CC-BY-4.0 ✅

**데이터셋 특성**
- 503 문항, 객관식 (4지선다)
- 컨텍스트: 8K ~ 2M 토큰
- 태스크: 단일문서 QA, 다중문서 QA, 장문 ICL, 대화 이력, 코드 레포, 구조적 데이터
- 수집: 100명+ 고학력 전문가 → 자동+수동 검수
- 인간 전문가 정확도: 53.7% (15분 제한)

**SOTA 현황**
- 직접 답변: 50.1% (최고 모델)
- o1-preview (추론 포함): 57.7% → 인간 초월
- 논문: https://arxiv.org/abs/2412.15204

**MemKraft 적용 분석**
```python
# LongBench v2: 대화 이력 이해 서브태스크만 추출
dialogue_tasks = [q for q in dataset if q.task == "long_dialogue_history"]
# MemKraft ingest → 대화 이력 → MC 선택
```
- 대화 이력 이해 서브태스크에 MemKraft 적용 가능
- 객관식 → LLM judge 불필요 (자동 채점)
- **단점:** 전체 LongBench v2는 장기 기억 특화 아님, 서브태스크 분리 필요
- **난이도:** 🟡 **중간**

**차별화 가능성:** ⭐⭐⭐
- 학계 인지도 높음 (ACL 2025, THU-KEG)
- 대화 이력 서브태스크: MemKraft 강점 영역

---

#### 2.2.6 MTEB (Massive Text Embedding Benchmark)

**기본 정보**
- **출처:** Hugging Face + 커뮤니티 (ICLR 2024)
- **리더보드:** https://huggingface.co/spaces/mteb/leaderboard
- **라이선스:** Apache 2.0 ✅

**데이터셋 특성**
- 58개+ 데이터셋, 7개 태스크 유형
- 태스크: Classification, Clustering, Pair Classification, Reranking, Retrieval, STS, Summarization
- 장기 기억 직접 평가 ≠, 但 **검색 성능**이 메모리 retrieval과 직결
- 평가 언어: 영어 중심, 다국어 지원

**SOTA 현황**
- MTEB 전체 1위: text-embedding-3-large (OpenAI), Gemini 계열 경쟁
- Retrieval 서브셋: 활발한 리더보드 업데이트

**MemKraft 적용 분석**
```python
# MemKraft 내부 임베딩 성능 → MTEB Retrieval 서브셋으로 분리 평가
# mk.search() 기저 임베딩 모델을 MTEB 평가 체계로 벤치마킹
```
- **단점:** 장기 기억 직접 평가 아님, MemKraft 위치 설명 필요
- **장점:** 검색 레이어 품질 독립 검증 → 기술 문서에 유용
- **난이도:** 🟡 **중간** (임베딩 래퍼 구현)

**차별화 가능성:** ⭐⭐⭐⭐
- "MemKraft retrieval이 MTEB 기준으로도 상위" 주장 가능
- 임베딩 모델 ablation study에 활용

---

#### 2.2.7 MS MARCO / Long-context Retrieval

**기본 정보**
- **출처:** Microsoft Research
- **데이터셋:** 1M+ 실제 Bing 쿼리 + 문서 패시지
- **라이선스:** CC-BY (비상업·연구용) ✅
- **포커스:** 정보 검색 (IR) 품질 평가

**데이터셋 특성**
- 1.1M 쿼리, 8.8M 패시지 (v1.1 기준)
- 태스크: Passage Ranking, Document Ranking, QA
- Long-context 변형: 최근 연구에서 장문 컨텍스트 패시지 확장

**SOTA 현황**
- BM25: MRR@10 ~0.185
- Dense retrieval (DPR, E5): MRR@10 ~0.35+
- 최근 LLM reranker: MRR@10 ~0.40+

**MemKraft 적용 분석**
- MemKraft = 개인 대화 기억 시스템 → MS MARCO (일반 문서 IR)는 영역 불일치
- **단점:** 대화형 기억 vs 문서 검색은 평가 철학이 다름
- **적합 활용:** MemKraft retrieval 레이어의 baseline IR 성능 확인용

**차별화 가능성:** ⭐⭐
- 직접 비교보다 검색 엔진 성분 독립 검증에 활용

---

## 3. Top 3 채택 추천

### 🥇 1순위: LoCoMo

**추천 이유:**
1. **규모와 깊이 모두 충족** — 최대 35 세션, 300턴, 9K 토큰 → LongMemEval보다 훨씬 극단적인 장기 기억 테스트
2. **MemKraft 핵심 기능 직접 검증** — temporal reasoning, multi-hop recall, session decomposition
3. **커뮤니티 인지도** — ACL 2024, 59개 SOTA 항목, 활발한 후속 연구
4. **라이선스 안전** — Apache 2.0
5. **기존 하네스와 연계 용이** — harness.py 확장으로 LoCoMo QA 서브태스크 추가 가능

**구현 계획:**
```python
# benchmarks/locomo/harness_locomo.py (신규)
class LoCoMoHarness:
    def __init__(self, mk: MemKraft, ...):
        self.mk = mk
    
    def ingest_sessions(self, conversation: dict):
        """35 세션 전체 → log_event() 순차 ingest"""
        for session in conversation["sessions"]:
            for turn in session["turns"]:
                self.mk.log_event(
                    f"[{turn['speaker']}] {turn['text']}",
                    tags=f"session:{session['id']}",
                    importance="medium"
                )
    
    def evaluate_qa(self, question: str, gold: str) -> bool:
        context = self.mk.search(question, top_k=10)
        prediction = self.answer(context, question)
        return llm_judge(question, gold, prediction)
```

**예상 성과:** LoCoMo QA F1 기준 기존 RAG (~60%) 대비 10+ 포인트 우위 가능

---

### 🥈 2순위: MemoryBench (supermemoryai)

**추천 이유:**
1. **멀티-프레임워크 비교** — MemKraft vs Mem0 vs Zep을 단일 프레임워크에서 비교
2. **커뮤니티 등록 가능** — 공개 리더보드에 MemKraft 등록 → 마케팅 효과
3. **LongMemEval 포함** — 기존 벤치마크 재활용 가능
4. **구현 용이** — MIT 라이선스, TypeScript이나 Python HTTP adapter 가능

**구현 계획:**
```python
# benchmarks/memorybench/memkraft_provider.py (신규)
class MemKraftProvider:
    """MemoryBench Provider 인터페이스 구현"""
    
    def ingest(self, messages: list[dict]) -> None:
        for msg in messages:
            self.mk.log_event(msg["content"], tags=msg.get("role", "user"))
    
    def search(self, query: str, top_k: int = 10) -> list[str]:
        results = self.mk.search(query, top_k=top_k)
        return [r["content"] for r in results]
    
    def answer(self, query: str, context: list[str]) -> str:
        # Claude/GPT로 최종 답변 생성
        ...
```

**예상 성과:** 공개 리더보드 등록, MemKraft 커뮤니티 인지도 향상

---

### 🥉 3순위: PersonaMem (자체 설계)

**추천 이유:**
1. **benchmarks/personamem/ 폴더 이미 존재** — 기반 완비
2. **MemKraft 고유 기능 (persona-centric memory)** 검증
3. **기존 LME 천장 돌파** — 개인화 기억에서 새로운 도전 과제 제공
4. **관련 연구 풍부** — PersonaVLM, DeltaMem, AdaMem (arXiv 2026년 활발)

**구현 계획:**
```python
# benchmarks/personamem/personamem_harness.py (신규)
# 설계: 사용자 X가 10회 세션에 걸쳐 다양한 정보를 공유
# 평가: 세션 5, 10, 15 후 → 이전 공유 정보 recall 테스트
# 팩트 타입: 취미, 직업, 선호도, 가족 관계, 건강 정보

PERSONA_QA_TYPES = [
    "direct_recall",      # "내 강아지 이름이 뭐야?"
    "preference_recall",  # "내가 좋아하는 음식은?"
    "temporal_update",    # "나 직업 바꿨다고 했는데 기억해?"
    "cross_session",      # "지난 주에 말한 것과 오늘 말한 것 비교해봐"
]
```

**예상 성과:** MemKraft 차별화 스토리 — "개인화 기억 1위 시스템"

---

## 4. LLM Judge 자체화 계획

### 4.1 현재 상태 진단

```python
# 현재: benchmarks/longmemeval/llm_judge.py
_DEFAULT_JUDGE_MODEL = os.environ.get("JUDGE_MODEL", 
                        os.environ.get("MODEL", "claude-haiku-4-5"))

# 문제:
# 1. 단일 모델 judge → position bias, verbosity bias
# 2. API 비용: N=500 × 3runs = 1,500 API 호출
# 3. temperature=0도 완전 결정적이지 않음 (GPU 부동소수점 비결정성)
# 4. LITELLM_VHH_BASE_URL 미등록 → llm.vhh.sh 직접 접근 안됨
```

### 4.2 다중 Judge 합의 설계

```python
# benchmarks/shared/multi_judge.py (신규)
"""
다중 LLM Judge 합의 시스템

합의 방식:
- Majority Vote (3 judges): correct/incorrect 다수결
- Confidence Weighted: 각 judge의 응답 확률 가중 평균
- Cascade: 합의 불일치 시 상위 모델로 escalate
"""

import anthropic
import openai
from typing import Literal

JudgeResult = Literal["correct", "incorrect", "uncertain"]

JUDGE_POOL = [
    {"provider": "anthropic", "model": "claude-haiku-4-5",   "weight": 1.0},
    {"provider": "anthropic", "model": "claude-sonnet-4-5",  "weight": 1.5},  # tie-breaker
    {"provider": "openai",    "model": "gpt-4o-mini",        "weight": 1.0},
]

def multi_judge(question: str, gold: str, prediction: str, 
                mode: str = "majority") -> tuple[bool, float]:
    """
    Returns: (is_correct, confidence)
    mode: "majority" | "cascade" | "confidence_weighted"
    """
    votes = []
    for judge_config in JUDGE_POOL:
        result = single_judge(question, gold, prediction, **judge_config)
        votes.append((result, judge_config["weight"]))
    
    if mode == "majority":
        correct_weight = sum(w for r, w in votes if r == "correct")
        total_weight = sum(w for _, w in votes)
        is_correct = correct_weight / total_weight > 0.5
        confidence = correct_weight / total_weight
        return is_correct, confidence
    
    elif mode == "cascade":
        # 하위 judge 합의 → 불일치 시 상위 judge 사용
        primary_votes = [r for r, _ in votes[:2]]
        if all(v == primary_votes[0] for v in primary_votes):
            return primary_votes[0] == "correct", 1.0
        else:
            # Sonnet으로 최종 결정
            result = single_judge(question, gold, prediction,
                                  provider="anthropic", model="claude-sonnet-4-5")
            return result == "correct", 0.7
```

### 4.3 로컬 Judge 옵션 (비용 제로)

```python
# 로컬 모델 judge (API 비용 0, 재현성 향상)
LOCAL_JUDGE_OPTIONS = [
    # Option 1: Ollama (로컬 LLM)
    {"provider": "ollama", "model": "llama3.2:3b", "port": 11434},
    # Option 2: LiteLLM + 로컬 엔드포인트 (vhh.sh)
    {"provider": "litellm", "base_url": "https://llm.vhh.sh", "model": "minpeter/sonnet-4.6"},
]

# 환경변수 설정 필요:
# export LITELLM_VHH_BASE_URL="https://llm.vhh.sh"
# export LITELLM_VHH_API_KEY="..."
```

### 4.4 Judge 캐시 + 재현성

```python
# benchmarks/shared/judge_cache.py (신규)
import hashlib, json, sqlite3

class JudgeCache:
    """같은 (question, gold, prediction) 쌍 → 캐시 반환"""
    
    def __init__(self, db_path: str = "benchmarks/.judge_cache.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_schema()
    
    def _cache_key(self, q: str, g: str, p: str, model: str) -> str:
        return hashlib.sha256(f"{q}|{g}|{p}|{model}".encode()).hexdigest()
    
    def get(self, q, g, p, model) -> bool | None:
        key = self._cache_key(q, g, p, model)
        row = self.conn.execute(
            "SELECT result FROM cache WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None
    
    def set(self, q, g, p, model, result: bool):
        key = self._cache_key(q, g, p, model)
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
            (key, int(result), json.dumps({"q": q[:100]}))
        )
        self.conn.commit()
```

### 4.5 비용 추정

| Judge 방식 | N=500 기준 비용 | 재현성 | 권장 |
|-----------|---------------|--------|------|
| Claude Haiku 단일 | ~$0.15 | 낮음 | 현재 |
| Haiku + GPT-4o-mini 다수결 | ~$0.30 | 중간 | ✅ 단기 |
| vhh.sh 로컬 (LiteLLM) | ~$0 | 높음 | ✅ 장기 |
| Haiku + Sonnet cascade | ~$0.50 | 높음 | ✅ 고품질 평가 |

---

## 5. CI/CD 통합 전략

### 5.1 벤치마크 자동화 파이프라인

```yaml
# .github/workflows/benchmark.yml (신규)
name: MemKraft Benchmark Suite

on:
  push:
    branches: [main, feat/v2.1-roadmap]
  schedule:
    - cron: '0 2 * * 1'  # 매주 월요일 새벽 2시

jobs:
  benchmark-longmemeval:
    name: LongMemEval (oracle N=50)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run LME benchmark
        run: |
          cd benchmarks/longmemeval
          python run_smart.sh
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: lme-results-${{ github.sha }}
          path: benchmarks/longmemeval/results/

  benchmark-locomo:
    name: LoCoMo QA (N=10 conversations)
    runs-on: ubuntu-latest
    needs: []  # 병렬 실행
    steps:
      - uses: actions/checkout@v4
      - name: Run LoCoMo benchmark
        run: |
          cd benchmarks/locomo
          python run_locomo.py --subset qa --n_conv 10
```

### 5.2 결과 대시보드

```
benchmarks/
├── longmemeval/results/     ← 기존
├── locomo/results/          ← 신규
├── personamem/results/      ← 신규
├── memorybench/results/     ← 신규
└── LEADERBOARD.md           ← 자동 생성 (CI에서 업데이트)
```

```markdown
# MemKraft Benchmark Leaderboard

| Benchmark | Score | N | Date | Notes |
|-----------|-------|---|------|-------|
| LongMemEval oracle | 98.0% | 50 | 2026-04-22 | semantic majority v3 |
| LoCoMo QA | TBD | 10 | - | - |
| PersonaMem | TBD | - | - | - |
```

### 5.3 회귀 탐지

```python
# scripts/benchmark-regression-check.py
THRESHOLDS = {
    "longmemeval_oracle": 0.96,   # 98.0% - 2% 여유
    "locomo_qa_f1": 0.55,         # 인간 85% 기준 55% 목표
    "personamem_direct_recall": 0.80,
}

def check_regression(results: dict) -> list[str]:
    failures = []
    for bench, score in results.items():
        if score < THRESHOLDS.get(bench, 0):
            failures.append(f"🔴 REGRESSION: {bench} = {score:.1%} < {THRESHOLDS[bench]:.1%}")
    return failures
```

---

## 6. 6개월 로드맵

### Phase 1 (Month 1-2): LoCoMo 통합

**목표:** LoCoMo QA 서브태스크 완전 평가

**주요 작업:**
- [ ] `benchmarks/locomo/` 폴더 생성 및 데이터 다운로드
  - `huggingface-cli download snap-research/LoCoMo`
- [ ] `harness_locomo.py` 구현 (harness.py 패턴 재사용)
- [ ] 35 세션 ingest 최적화 (배치 처리, 진행 바)
- [ ] QA F1 평가 스크립트 구현
- [ ] 첫 번째 결과 리포트 (`LOCOMO_V1_REPORT.md`)

**성공 기준:**
- LoCoMo QA F1 > 55% (인간 85% 기준 RAG baseline 60% 대비 경쟁력)
- 기존 harness와 동일한 runner 인터페이스

**예상 비용:** API 비용 ~$5 (전체 10 conversations × 35 sessions)

---

### Phase 2 (Month 2-3): Multi-Judge 시스템

**목표:** LLM Judge 다원화 및 캐시 도입

**주요 작업:**
- [ ] `benchmarks/shared/multi_judge.py` 구현
- [ ] `benchmarks/shared/judge_cache.py` 구현 (SQLite)
- [ ] `LITELLM_VHH_BASE_URL` 환경변수 설정 및 vhh.sh judge 테스트
- [ ] LongMemEval에 multi-judge 적용 후 결과 비교
- [ ] judge 합의율 통계 (`judge_agreement_rate` 메트릭)

**성공 기준:**
- Judge 합의율 > 90% (Haiku vs GPT-4o-mini)
- 캐시 히트율 > 50% (동일 예측 재평가 방지)

---

### Phase 3 (Month 3-4): PersonaMem 설계 및 구현

**목표:** 자체 개인화 기억 벤치마크 완성

**주요 작업:**
- [ ] 페르소나 프로필 100개 생성 (GPT-4로 자동화)
- [ ] 10회 세션 시뮬레이션 데이터셋 구축
- [ ] QA 유형 4종 (direct_recall, preference, temporal_update, cross_session) 구현
- [ ] `benchmarks/personamem/harness_personamem.py` 구현
- [ ] 결과 공개 (HuggingFace Dataset 업로드)

**성공 기준:**
- N=100 페르소나 × 4 QA 유형 = 400 평가 문항
- Direct recall: > 85%

---

### Phase 4 (Month 4-5): MemoryBench 등록

**목표:** supermemoryai/memorybench 공개 리더보드에 MemKraft 등록

**주요 작업:**
- [ ] `benchmarks/memorybench/memkraft_provider.py` (TypeScript 또는 Python HTTP)
- [ ] Mem0, Zep와 비교 평가 실행
- [ ] PR 제출: github.com/supermemoryai/memorybench
- [ ] 비교 결과 블로그 포스트 초안

**성공 기준:**
- MemoryBench 공개 리더보드 MemKraft 등록
- LoCoMo 서브셋에서 Mem0 대비 +5% 이상

---

### Phase 5 (Month 5-6): 논문/기술 리포트 준비

**목표:** 다중 벤치마크 결과를 기반으로 기술 보고서 작성

**주요 작업:**
- [ ] `MEMKRAFT_BENCHMARK_REPORT_V21.md` 작성
  - LME 98.0%, LoCoMo QA F1, PersonaMem 결과
  - MemoryBench 비교 (Mem0, Zep, Supermemory)
  - LLM Judge 합의율, 재현성 분석
- [ ] arXiv 제출 검토 (기술 리포트 형태)
- [ ] HuggingFace Model Card 업데이트

---

### 월별 마일스톤 요약

```
Month 1: LoCoMo 데이터 다운로드 + harness 구현
Month 2: LoCoMo 첫 결과 + Multi-judge 시스템 완성
Month 3: PersonaMem 데이터셋 설계 완성
Month 4: PersonaMem 평가 완성 + MemoryBench 어댑터
Month 5: MemoryBench 등록 + 비교 결과
Month 6: 기술 리포트 완성 + 논문 제출 준비
```

---

## 7. 포지셔닝 전략

### 7.1 현재 MemKraft 포지션

```
LongMemEval oracle N=50: 98.0% (semantic majority vote)
비교:
- Zep (상업): 94.8% (DMR 기준)
- 일반 상업 챗봇: 70% 수준 (LME 논문 기준 30% 정확도 하락)
- 인간: ~100%
```

**문제:** "N=50 oracle에서 98%"는 과소평가될 수 있음. 전체 500 문항에서의 성능 미확인.

### 7.2 목표 포지션 (v2.1 이후)

| 영역 | 목표 | 근거 |
|------|------|------|
| **장기 대화 기억** | LME 전체 500문항 > 90% | oracle 98% → 전체는 낮을 가능성 |
| **초장기 기억 (35 세션)** | LoCoMo QA F1 > 65% | 인간 85%, SOTA RAG ~60% |
| **개인화 기억** | PersonaMem direct recall > 85% | 개인화 영역 독보적 |
| **검색 품질** | MTEB Retrieval top-20% | 기저 임베딩 성능 증명 |

### 7.3 차별화 스토리

**핵심 메시지:**
> "MemKraft는 단순 RAG가 아닙니다. 시간적 팩트, 엔티티 추적, 개인화 기억을 통합한 **비템포럴 메모리 시스템**으로, LongMemEval 98%, LoCoMo 65+%, PersonaMem 85+%를 동시 달성하는 유일한 라이브러리입니다."

**3단계 증거 체계:**
1. **LME 98%** — "기존 SOTA (Zep 94.8%) 초월"
2. **LoCoMo 65%+** — "35 세션, 300턴 초장기 기억도 처리"
3. **PersonaMem 85%+** — "개인화 기억 분야 선도"

### 7.4 경쟁사 대비 포지셔닝 맵

```
              개인화 강도
              ↑
PersonaMem    │ MemKraft (목표)
              │      ★
              │   Supermemory
              │
LoCoMo  ──────┼─────────────────→ 장기 기억 깊이
              │ Mem0
              │     Zep
              │
단순 RAG      │
              ↓
```

---

## 부록 A: 벤치마크 출처 URL 모음

| 벤치마크 | 논문 URL | GitHub/HF URL |
|---------|---------|-------------|
| LongMemEval | https://arxiv.org/abs/2410.10813 | https://github.com/xiaowu0162/LongMemEval |
| LoCoMo | https://arxiv.org/abs/2402.17753 | https://github.com/snap-research/LoCoMo |
| MemoryBench (supermemory) | - | https://github.com/supermemoryai/memorybench |
| MemoryBench (학술) | https://arxiv.org/abs/2510.17281 | - |
| LongBench v2 | https://arxiv.org/abs/2412.15204 | https://github.com/THUDM/LongBench |
| MTEB | https://arxiv.org/abs/2210.07316 | https://huggingface.co/spaces/mteb/leaderboard |
| Zep (SOTA 참고) | https://arxiv.org/abs/2501.13956 | https://github.com/getzep/zep |

---

## 부록 B: 즉시 실행 가능한 Quick Start

### LoCoMo 데이터 다운로드

```bash
cd /Users/gimseojun/memcraft/benchmarks
mkdir -p locomo/data

# HuggingFace에서 다운로드
pip install huggingface_hub
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='snap-research/LoCoMo',
    repo_type='dataset',
    local_dir='locomo/data'
)
print('LoCoMo downloaded!')
"
```

### Multi-judge 환경변수 설정

```bash
# ~/.zshrc에 추가
export LITELLM_VHH_BASE_URL="https://llm.vhh.sh"
# export LITELLM_VHH_API_KEY="..."  # 필요 시
export JUDGE_MODEL_SECONDARY="gpt-4o-mini"
export JUDGE_CONSENSUS_MODE="majority"  # majority | cascade | confidence
```

### PersonaMem 폴더 초기화

```bash
cd /Users/gimseojun/memcraft/benchmarks/personamem
mkdir -p data results
touch harness_personamem.py
touch README.md
echo "PersonaMem benchmark initialized"
```

---

*이 보고서는 MemKraft v2.1 로드맵 Task #5로 작성됨.*  
*작성자: MemKraft v2.1 리서치 서브에이전트*  
*날짜: 2026-04-26*
