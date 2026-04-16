# MemKraft 0.8.0+ 아이디어 (2026-04-17 리서치)

> **리서치 대상:** mem0 v3, Letta, Zep/Graphiti, Cognee, MemPalace, Memento, Brain Framework, Hindsight, Rowboat, LCM
> **철학:** "너무 베낀 것처럼은 말고 적용할 부분은 적용" (형 지시)
> **제약:** zero-dependency (stdlib only), 기존 API 시그니처 유지, 409 tests 통과
> **현재 코어 규모:** `core.py` 4,361 lines, 메서드 ~120개, v0.7.0 multi-agent integration 완료

---

## 🎯 Top 5 추천 (우선순위 순)

### 🥇 #1. Bitemporal Fact Layer — 양시간축 사실 추적

**한 줄 요약:** "언제 실제로 그랬는지(valid_time)"와 "언제 기록했는지(record_time)"를 분리한다. Graphiti/Memento가 증명한, **지금 가장 뜨거운 메모리 기법**.

**핵심 가치:**
- 현재 MemKraft의 Timeline은 "언제 기록했는지"만 안다 → "2월에 CEO였다가 3월에 바뀐 걸 4월에 알았다" 같은 시차 사실 표현 불가
- Bitemporal 추가하면 시간 추론 정확도 +10pp 이상 (Memento 벤치마크 기준, 67% → 89.5%)

**경쟁자 대비 차별점:**
- Graphiti(Zep)는 그래프 DB에 저장 → zero-dep 포기
- **우리는 Markdown bullet에 인라인 마커만 추가** (`[valid: 2026-02~2026-03]`)
- 사람이 읽어도 이해되고, git diff로 변경 추적 가능 → **Graphiti보다 투명, Markdown보다 똑똑**

**예상 API 시그니처:**
```python
mk.update(
    "Simon Kim",
    info="Role: CEO of Hashed",
    valid_from="2022-01",
    valid_until=None,        # 현재진행형
    recorded_at="2026-04-17", # default: 오늘
    source="linkedin"
)
# → Timeline에 저장: "- Role: CEO of Hashed [valid: 2022-01~now, recorded: 2026-04-17] (linkedin)"

mk.as_of("Simon Kim", date="2022-06-01")  # 당시 상태 재구성
mk.fact_history("Simon Kim", field="Role")  # Role 필드만 시간순
```

**zero-dep 가능 여부:** ✅ — Markdown 라인 파싱 + 정규식, 새 의존성 0
**구현 난이도:** Medium (기존 `update`/`_append_fact`에 메타데이터 레이어 추가)
**예상 테스트 개수:** 25~30 (valid/recorded 파싱, as_of 재구성, fact_history 정렬, backward-compat)

---

### 🥈 #2. ADD-Only Hybrid Retrieval — 단일 패스 다중신호 검색

**한 줄 요약:** mem0 v3의 핵심 혁신을 MemKraft식으로 재해석. **UPDATE/DELETE 없이 쌓기만 하고**, 검색 시 semantic(grep+fuzzy) + BM25(우리는 token-freq) + entity-match를 **병렬 점수 합산**해서 찾는다.

**핵심 가치:**
- 현재 `search(fuzzy=True)`는 단일 신호(토큰 매칭) → 재현율은 높지만 정밀도 낮음
- 3신호 fusion으로 정밀도↑: mem0 v3가 LoCoMo 71.4 → 91.6 (+20pp), LongMemEval 67.8 → 93.4 (+26pp)
- **ADD-only 철학**은 이미 MemKraft가 부분 채택 중 (Timeline append 위주) → 공식화하면 자연스러움

**경쟁자 대비 차별점:**
- mem0 v3는 vector DB + spaCy 필수 (`pip install mem0ai[nlp]`)
- **우리는 stdlib만으로 BM25 구현** (term frequency + IDF, 파이썬 `collections.Counter`로 충분)
- Entity match는 기존 `_detect_regex` 재활용 → 신규 코드 최소화

**예상 API 시그니처:**
```python
mk.search(
    "hashed CEO 투자",
    mode="hybrid",      # 기본은 "fuzzy" 유지 (backward-compat)
    weights={"token": 0.4, "bm25": 0.4, "entity": 0.2},
    top_k=10,
)
# 내부적으로:
#  - token_score: 기존 fuzzy 로직
#  - bm25_score: stdlib BM25 (raw term freq + log IDF)
#  - entity_score: 쿼리 내 엔티티와 doc 엔티티 겹침
# 세 점수를 min-max 정규화 후 가중합 → 재랭킹
```

**zero-dep 가능 여부:** ✅ — BM25는 수식 10줄, stdlib `math.log`로 끝
**구현 난이도:** Medium-Large (기존 `search` 리팩터 + 신규 scoring 모듈)
**예상 테스트 개수:** 35~40 (각 신호 개별 + fusion + backward-compat + edge cases)

---

### 🥉 #3. Memory Tier Labels + Working Set — 명시적 3단 메모리

**한 줄 요약:** Letta의 `core/recall/archival` 3단 구조를 **Markdown frontmatter 한 줄로** 구현. 여기에 **현재 세션 working set**을 더해 agent inject 시 core만 자동 포함.

**핵심 가치:**
- 현재 `agent_inject`는 최근 N개를 무차별 포함 → 정말 중요한 것과 일시적인 것 구분 불가
- Tier 라벨로 **컨텍스트 윈도우 예산 최적화** (core는 항상 포함, recall은 필요 시, archival은 명시적 호출만)

**경쟁자 대비 차별점:**
- Letta는 클라우드 API에 묶임, self-edit이 블랙박스
- **우리는 YAML frontmatter 한 줄** (`tier: core`) — 사람도 보고 수정 가능
- `working_set`은 channel_context의 일부로 저장 → 기존 multi-agent 구조와 자연 연결

**예상 API 시그니처:**
```python
mk.promote("Simon Kim", tier="core")          # archival → recall → core
mk.demote("Old Project X", tier="archival")
mk.tier_of("Simon Kim")                        # "core"
mk.working_set(channel_id="agent:zeon:main")   # 현재 세션의 hot entities 리스트

mk.agent_inject(
    agent_id="zeon",
    tier_budget={"core": "all", "recall": 5, "archival": 0},  # NEW
)
```

**zero-dep 가능 여부:** ✅ — YAML frontmatter는 수동 파싱 (기존에도 하고 있음)
**구현 난이도:** Small-Medium (frontmatter 읽기/쓰기 + `agent_inject` 필터 추가)
**예상 테스트 개수:** 15~20

---

### 🏅 #4. Memory Decay Preview + Reversible Decay — 망각의 투명화

**한 줄 요약:** Dream Cycle의 `decay()`가 뭘 지울 건지 **미리 보여주고**, 지운 것도 **7일간 tombstone으로 복구 가능**. 현재 `dream(dry_run=True)`보다 세분화된 안전장치.

**핵심 가치:**
- 형이 "어제 dream cycle 돌았는데 뭐 지웠지?" 물었을 때 답 못 함 → 불안감
- 경쟁자 대부분 decay가 블랙박스 (Letta, mem0 모두) → **투명한 decay가 우리 USP**
- snapshot 시스템(v0.5.0)과 결합하면 거의 공짜로 구현

**경쟁자 대비 차별점:**
- 아무도 안 하고 있음 — **우리만의 고유 기능**
- "Debugging is memory"를 decay에도 적용: 왜 지웠는지 이유까지 기록

**예상 API 시그니처:**
```python
mk.decay_preview(days=90)
# → [
#     {"path": "...", "reason": "no access 120d", "size": 1234, "last_read": "..."},
#     ...
#   ]

mk.decay(days=90, dry_run=False, tombstone_days=7)
# → 삭제 대신 .memkraft/tombstone/YYYY-MM-DD/ 로 이동
# → 7일 후 자동 완전삭제

mk.undecay(path_or_name)   # 복구
mk.tombstone_list()         # 현재 복구 가능한 항목들
```

**zero-dep 가능 여부:** ✅ — 파일 이동 + JSON 로그만 필요
**구현 난이도:** Small (기존 `decay()`에 tombstone 레이어 추가)
**예상 테스트 개수:** 15~20

---

### 🎖️ #5. Cross-Entity Link Graph + Backlinks — 저비용 그래프 메모리

**한 줄 요약:** Markdown의 `[[wiki-link]]`를 1급 시민으로 승격. **Cognee/Zep의 관계 그래프를 stdlib만으로** 구현. 각 엔티티 페이지에 "이 엔티티를 언급한 곳" 자동 섹션 생성.

**핵심 가치:**
- 현재 엔티티 페이지들은 섬처럼 고립 → "Simon과 Hashed의 관계" 같은 질문 답 어려움
- Zettelkasten 원칙: **연결이 곧 지식**
- 이미 `AGENTS.md`에 `[[wiki-link]] 패턴 권장`이 있음 → 자연스러운 다음 단계

**경쟁자 대비 차별점:**
- Cognee/Zep은 그래프 DB (Neo4j/Kuzu) → 무거움
- **우리는 Markdown 링크 인덱싱** — 파일 시스템이 DB, git diff가 change log
- `links()` 메서드는 이미 v0.x에 존재 → 양방향(backlinks)만 추가하면 완성

**예상 API 시그니처:**
```python
mk.links("Simon Kim")
# → {
#     "outgoing": ["Hashed", "MemKraft", "Seoul"],
#     "incoming": ["Simon Sinek (disambig)", ...],  # 이번 버전 신규
#   }

mk.graph(
    root="Simon Kim",
    max_hops=2,
    as_mermaid=True,  # 옵션: Mermaid 다이어그램 문자열 반환
)
# → "graph LR\n  Simon_Kim --> Hashed\n  ..."

mk.auto_link(entity="Simon Kim", dry_run=True)
# → 다른 페이지들에서 Simon Kim 언급 찾아 [[Simon Kim]]으로 자동 변환 제안
```

**zero-dep 가능 여부:** ✅ — 정규식으로 `[[...]]` 찾고 dict 만들면 끝
**구현 난이도:** Medium (인덱싱은 쉬우나 `auto_link`의 오탐 방지가 트리키)
**예상 테스트 개수:** 20~25

---

## 📚 경쟁자 분석

| 프로젝트 | ⭐ | 주요 기능 (2026 기준) | 우리가 가져올 것 | 이유 |
|---|---|---|---|---|
| **mem0 v3** ([github](https://github.com/mem0ai/mem0)) | 25K+ | ADD-only + multi-signal retrieval (semantic/BM25/entity fusion), LongMemEval 93.4 | **Hybrid retrieval (#2)** | 벤치마크로 증명된 기법, 우리는 zero-dep 버전으로 |
| **Letta** ([github](https://github.com/letta-ai/letta)) | 15K+ | Memory blocks (human/persona), tiered memory, GPT-5.2 기반 | **Tier labels (#3)** | core/recall/archival은 단순하면서 강력 |
| **Zep/Graphiti** ([github](https://github.com/getzep/zep)) | 3K+ | Temporal KG with `valid_at`/`invalid_at`, sub-200ms | **Bitemporal (#1)** | 시간축 사실 추적이 2026 트렌드 |
| **Cognee** ([github](https://github.com/topoteretes/cognee)) | 3K+ | `remember/recall/forget/improve` 4-op, ontology grounding | **Reversible decay (#4) 철학** | forget이 1급 API인 게 신선 |
| **MemPalace** | 30K+ (신규) | AAAK 30x compression, 170-token startup | **(2nd tier) Ultra compression** | 검증 후 dream cycle에 적용 |
| **Memento** | 신규 | Bitemporal KG, LongMemEval 90.8% | **Bitemporal (#1) 검증 근거** | Zep의 길을 논문으로 증명 |
| **GBrain** | 2K+ | Compiled Truth + Timeline | 이미 흡수 완료 (현재 MemKraft 기반) | — |
| **Rowboat** | 1K+ | Live tracking, meeting brief | 이미 흡수 (Zeon 쪽 스크립트 有) | — |
| **Hindsight** (Anthropic) | — | Local-first (Ollama), retain/recall/reflect | **Memory Tier (#3) 영감** | 로컬 퍼스트 철학 공유 |
| **LCM** (ArXiv) | 신규 | DAG 기반 페이지드 가상 메모리 | **(2nd tier) DAG 의존성 추적** | 진보적, 실전 검증은 미흡 |

---

## 🧪 2026 기술 트렌드 요약

1. **Bitemporal 시간 모델이 표준화 중** (Zep/Graphiti, Memento)
   → 우리: #1로 채택, Markdown 인라인 마커로 투명성 유지

2. **ADD-only + Fusion retrieval** (mem0 v3, Weaviate)
   → UPDATE/DELETE의 복잡성 vs 단순 누적 + 똑똑한 검색의 승리
   → 우리: #2로 채택, stdlib BM25로 zero-dep 유지

3. **Explicit memory tiers** (Letta의 MemGPT 논문이 정설화)
   → core/recall/archival이 사실상 업계 표준 용어
   → 우리: #3으로 채택, frontmatter 1줄로 구현

4. **Forgetting as first-class operation** (Cognee `forget`, Zep decay)
   → 망각도 기억만큼 중요, 하지만 **투명한 망각**이 차별점
   → 우리: #4로 채택, tombstone으로 복구 가능

5. **Graph 없는 그래프 메모리** (Obsidian, LogSeq 영감)
   → 파일 시스템 + wiki-link가 경량 그래프 DB 역할
   → 우리: #5로 채택, 이미 wiki-link 습관 있음

6. **Context Engineering > Prompt Engineering** (Zep 마케팅 문구)
   → 메모리는 이제 "어떻게 쌓느냐"보다 "어떻게 어셈블해서 넣느냐"가 관건
   → 우리: `agent_inject`의 tier_budget 파라미터(#3)로 대응

---

## 🎨 2nd Tier (추가 검토 아이디어)

| # | 이름 | 한 줄 설명 |
|---|---|---|
| 6 | **Scene Trace Metadata** | 사실에 상황 맥락(where/when/mood) 메타 추가 — Letta_AI 보고 +20pp |
| 7 | **AAAK-style Ultra Compression** | Dream Cycle에 극한 압축 적용, 세션 startup을 수백→100토큰대로 |
| 8 | **SQLite Artifact Layer (optional)** | Markdown 위에 옵션 SQLite 인덱스 — `--index` 플래그 켰을 때만, zero-dep 유지 |
| 9 | **Hippocampal Ranking** | working→hippocampal→long-term 자동 승격 (Brain Framework) |
| 10 | **Fact Confidence Calibration** | 기존 confidence 필드에 bayesian 업데이트 (같은 출처 반복 등장 시 상향) |
| 11 | **Conflict Graph View** | `detect_conflicts` 결과를 시간축 다이어그램(mermaid)으로 시각화 |
| 12 | **CID (Content ID) 기반 무결성** | 각 블록 해시 — 변조 탐지 + 분산 공유 기반 |
| 13 | **Session Replay** | snapshot들을 순차 재생해서 "어떻게 기억이 진화했나" 타임랩스 |
| 14 | **Fact Provenance Chain** | 한 사실이 어떤 원본→추출→업데이트 거쳤는지 체인 기록 |
| 15 | **LongMemEval 벤치마크 하네스** | 공식 벤치 실행 스크립트 번들링 → README에 점수 게시 |

---

## ⚠️ 하지 말아야 할 것

- ❌ **Vector DB 통합** (Chroma, Qdrant 등) — zero-dep 원칙 깨짐, mem0와 동일해짐
- ❌ **그래프 DB 통합** (Neo4j, Kuzu) — Zep과 동일해짐, 우리 경량성 희생
- ❌ **LLM 필수화** — "no API keys"가 README 1급 홍보 문구, 절대 깨지 말 것
- ❌ **기존 API 시그니처 breaking change** — v0.7.0의 409 tests는 성역
- ❌ **경쟁자 100% 복제** — 특히 mem0 Memory 클래스 mimicry는 금물 (라이센스+차별화)
- ⚠️ **async 전환** — 매력적이지만 기존 동기 API와 충돌, 0.9.0 이후 별도 검토
- ⚠️ **자동 임베딩** — sentence-transformers 깔아야 함, 무겁다. 대안: stdlib BM25(#2)로 대체

---

## 🗺️ 로드맵 제안

### 📍 v0.8.0 (단기, 1-2주) — "시간과 검색"
- ✅ **#1 Bitemporal Fact Layer** (25~30 tests)
- ✅ **#2 Hybrid Retrieval** (35~40 tests)
- → 총 409 + 65 = ~475 tests
- → 킬러 스토리: "MemKraft now speaks time. And searches like mem0, without the dependencies."

### 📍 v0.9.0 (중기, 1개월) — "구조와 안전"
- ✅ **#3 Memory Tiers + Working Set** (15~20 tests)
- ✅ **#4 Reversible Decay** (15~20 tests)
- ✅ **#5 Link Graph + Backlinks** (20~25 tests)
- → 총 ~530 tests
- → 킬러 스토리: "Tier-aware, reversibly forgetful, and graph-linked — all in Markdown."

### 📍 v1.0.0 (장기, 2-3개월) — "증명과 통합"
- 🎯 LongMemEval 벤치마크 공식 측정 + README 게재 (#15)
- 🎯 Scene Trace (#6) + Confidence Calibration (#10) 통합
- 🎯 AAAK 기법 검증 후 선택적 적용 (#7) — 단, 사람이 읽을 수 있어야
- 🎯 v1.0 매니페스토: "Zero-dep memory that matches mem0 on LoCoMo within 5pp, runs offline, diffs in git."

---

## 💬 결정 포인트 (형이 골라줄 것)

1. **v0.8.0 두 기능(#1, #2) 동시 vs 하나씩**
   → 동시 추천: 시간축 + 검색은 시너지 (시간대별 검색 등)

2. **Tier 라벨(#3)을 v0.8에 끼워넣을지**
   → 형이 "5남매 agent_inject 더 똑똑하게" 원하면 #3 먼저

3. **benchmark harness(#15)가 먼저냐 기능이 먼저냐**
   → 현재 포지션(30K star MemPalace와 경쟁) 생각하면 **벤치 숫자가 더 급할 수도**
   → 제안: v0.8과 병행, 코드 기능 + 벤치 점수 동시 릴리즈

---

*작성: Zeon 🌌 | 2026-04-17 | 소스: GitHub README (mem0, letta, zep, cognee) + `memory/memkraft-ideas/2026-04-16.md` + 내부 `IMPROVEMENT_IDEAS.md`*
