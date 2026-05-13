# MemKraft v2.7.4 → 성능 부스트 아이디어 모음
> 작성일: 2026-05-04 | Baseline: v2.7.5 (corpus_index 패치 포함)
> 의존성 정책: **기본 zero-dep**, optional extras `[embedding/mcp/watch/bench/schedule]`

---

## 현재 벤치 기준 (v2.7.5 baseline)

| 시나리오 | 평균 레이턴시 | QPS | 비고 |
|----------|-------------|-----|------|
| repeat, cache OFF | 4.8 ms | ~208 | v2.7.1 대비 -2.0 ms (1.39x) |
| repeat, cache ON  | 0.18 ms | ~5560 | warm LRU hit |
| mixed, cache OFF  | ~5.3 ms | ~188 | cold path 포함 |

---

## 💡 성능 개선 아이디어 10+

---

### #1 — RapidFuzz 로 `SequenceMatcher` 전면 교체

- **카테고리:** fuzzy
- **현재 상태:** `core.py:21` `from difflib import SequenceMatcher`; 총 6개 호출지점 (core.py:669, 1223, 1229, 2006, 2816; graph.py 관련 없음). 순수 Python O(n·m) 구현.
- **개선안:**
  - `pip install rapidfuzz` (optional dep `[fuzzy]` extra 또는 기본 extra에 포함)
  - `from rapidfuzz.fuzz import ratio` → `ratio(a, b) / 100` 형태로 직접 치환
  - `SequenceMatcher(None, a, b).ratio()` → `rapidfuzz.fuzz.ratio(a, b) / 100`
  - 배치 비교 시 `rapidfuzz.process.cdist` 활용 (numpy array 반환, 병렬)
- **예상 효과:** `.ratio()` 단일 호출 **10~100x** 속도 향상. fuzzy-heavy 검색 경로(1223, 1229) 기준 cold path ~1~2 ms 절감. `mixed_cache_off` → 3~4 ms 대.
- **구현 난이도:** Easy | **0.5일**
- **리스크:** `rapidfuzz`는 C 확장 — PyPy 비호환 가능성 낮음. 점수 범위 0~100 → 0~1.0 스케일 변환 주의 (실수 하면 threshold 오동작).
- **참조:** [RapidFuzz GitHub](https://github.com/maxbachmann/RapidFuzz)
- **ROI 점수: 9/10**

---

### #2 — W-TinyLFU 캐시로 LRU 교체

- **카테고리:** 캐시
- **현재 상태:** `cache.py` LRU + TTL, capacity=256, ttl=300s. 실측 mixed workload hit rate **~40%**.
- **개선안:**
  - `pip install theine-py` (또는 `cachetools` TLRUCache — 차선책)
  - `theine` 의 `Cache("TinyLFU", maxsize=256)` 로 교체
  - long-tail 엔티티 키(빈도 낮지만 반복 접근)를 frequency sketch 가 자동 보호
  - TTL은 그대로 유지, 프리퀀시 어드미션 게이트만 추가
- **예상 효과:** hit rate **40% → 50~60%** (+10~20 pp). warm QPS 5560 → 6500~7000 추정. cold path miss 비율 감소로 mixed QPS +15~25%.
- **구현 난이도:** Easy | **0.5일**
- **리스크:** `theine-py`가 Rust wheel 필요 → 아키텍처 빌드 실패 시 `cachetools` fallback. 캐시 key serialization이 기존과 동일해야 함.
- **참조:** Caffeine W-TinyLFU 논문 + [theine-py](https://github.com/Yiling-J/theine-python)
- **ROI 점수: 8/10**

---

### #3 — BM25S 라이브러리 통합 (optional dep)

- **카테고리:** BM25 / 인덱싱
- **현재 상태:** `_corpus_index.py` (v2.7.5 신규) — 자체 BM25 구현. 매쿼리 IDF/TF 재계산은 해결됐지만 numpy sparse eager scoring 패스가 없음.
- **개선안:**
  - `pip install bm25s` (optional extra `[bm25s]`)
  - `BM25S.index(corpus)` → eager matrix 빌드, 이후 `BM25S.retrieve(query, k=20)` — numpy sparse dot
  - 현재 corpus_index 캐시와 병행: `bm25s` corpus가 이미 인덱스에 있으면 `BM25S.retrieve` 경로 사용
  - fallback: `bm25s` 미설치 시 기존 경로
- **예상 효과:** rank-bm25 대비 최대 **500x** 이론치. 현재 자체 BM25 대비 **5~50x** 현실적 추정. `repeat_cache_off` 4.8 ms → 1~2 ms.
- **구현 난이도:** Medium | **1.5일**
- **리스크:** eager 인덱스 빌드 시 메모리 +20~50 MB (문서 수 비례). 증분 업데이트(새 엔티티 추가)시 재빌드 필요 — 더티 플래그 + 배치 reindex 필요.
- **참조:** [bm25s GitHub](https://github.com/xhluca/bm25s), arxiv:2407.03618
- **ROI 점수: 8/10**

---

### #4 — 그래프 정규식 모듈 수준 사전 컴파일 + 캐시

- **카테고리:** 그래프 / 빌드
- **현재 상태:** `graph.py:57~230` `_RELATION_PATTERNS` (12개), `_CAUSAL_PATTERNS` (5개), `_KO_RELATION_PATTERNS` (37개+) — raw string tuple로 저장. `extract_relations()` 호출 시마다 `re.finditer(pattern, text, ...)` 에서 내부적으로 `re.compile` 발생 (CPython re 캐시는 512개지만 플래그 포함 키라 오염 가능).
- **개선안:**
  - 모듈 로드 시 한 번: `_COMPILED_EN = [(re.compile(p, re.IGNORECASE), rel) for p, rel in _RELATION_PATTERNS]`
  - `_COMPILED_KO`, `_COMPILED_CAUSAL` 동일하게 사전 컴파일
  - `extract_relations()` 내부 루프를 compiled 버전으로 교체
  - `_JOSA_PATTERN` 은 이미 컴파일됨 (line 123) — 나머지 불일치 해소
- **예상 효과:** `extract_relations()` 호출당 정규식 컴파일 오버헤드 제거. 패턴 54+ 개 기준 **0.2~0.5 ms** 절감 per call. 그래프 heavy 워크로드에서 체감.
- **구현 난이도:** Easy | **0.3일**
- **리스크:** 없음 (순수 내부 최적화, API 변경 없음).
- **참조:** CPython `re` 모듈 캐시 한계 (512 entries, LRU), Python docs
- **ROI 점수: 7/10**

---

### #5 — Lazy Import 분리 (CLI cold start 단축)

- **카테고리:** 빌드
- **현재 상태:** `core.py` 3941 lines — `import` 시 sentence-transformers, torch, watchdog, schedule 등 heavy 의존성이 조건 없이 로드됨. CLI 첫 호출 100~200 ms 소요 추정.
- **개선안:**
  - `embedding.py` 내 `SentenceTransformer` import를 함수 내부 lazy: `def embed_text(...):\n    from sentence_transformers import ...`
  - `core.py` 상단 `import watchdog`, `import schedule` → optional guard: `try: import watchdog ...`
  - `TYPE_CHECKING` 블록으로 타입 힌트용 import 분리
  - `importlib.import_module` 동적 로드 패턴 적용 (watchdog, schedule)
- **예상 효과:** CLI 첫 호출 **50~100 ms 단축**. `python3 -c "import memkraft"` 타임 ~30% 감소.
- **구현 난이도:** Easy | **0.5일**
- **리스크:** 런타임 `ImportError`가 사용 시점으로 지연됨 — 사용자가 설치 누락을 늦게 발견할 수 있음. `doctor()` 커맨드에서 사전 체크 권장.
- **참조:** Python 공식 lazy import 패턴, PEP 562
- **ROI 점수: 7/10**

---

### #6 — ONNX Runtime 임베딩 추론 (CPU 2~4x)

- **카테고리:** 임베딩
- **현재 상태:** `embedding.py:209` `embed_text()` — `sentence-transformers` + PyTorch CPU 추론. MiniLM 기준 single-thread ~50~80 ms/call.
- **개선안:**
  - `pip install optimum[onnxruntime]` + `optimum.exporters.onnx` 로 MiniLM ONNX 변환 (1회)
  - `from optimum.onnxruntime import ORTModelForFeatureExtraction` 로 대체
  - optional extra `[embedding-onnx]` 로 분리, torch 미설치 환경에서도 동작
  - 배치 추론 지원: `embed_batch(texts: list[str])` 공개 API 추가
- **예상 효과:** CPU 추론 **2~4x** 향상. 50~80 ms → 15~30 ms. 배치 16개 기준 throughput 3~5x.
- **구현 난이도:** Medium | **2일**
- **리스크:** ONNX 모델 파일 배포 방식 결정 필요 (허깅페이스 hub 자동 다운 vs 번들). 모델 버전 고정 안 하면 재현성 문제.
- **참조:** [Optimum ONNX](https://huggingface.co/docs/optimum/onnxruntime/overview), sentence-transformers ONNX export
- **ROI 점수: 6/10**

---

### #7 — Matryoshka Embedding (MRL) 다차원 인덱스

- **카테고리:** 임베딩 / 인덱싱
- **현재 상태:** 단일 고정 차원 임베딩 (768 또는 384). ANN/벡터 검색 시 전체 차원 사용.
- **개선안:**
  - MRL 지원 모델 (예: `nomic-embed-text-v1.5`, `mxbai-embed-large`) 채택
  - 첫 단계: 64/128차원 cheap coarse 검색 → 후보 top-100 추출
  - 두 번째 단계: 전체 차원(768) re-ranking
  - `embedding.py` 에 `embed_text(text, dims=768)` → `dims` 파라미터 추가
- **예상 효과:** coarse pass (64d) 기준 벡터 연산 **12x 감소** (768→64). end-to-end ANN 레이턴시 **2~3x** 향상, recall@10 ≥ 98% 유지.
- **구현 난이도:** Medium | **2일**
- **리스크:** MRL 모델로 마이그레이션 시 기존 임베딩 인덱스 재생성 필요. 기존 사용자 호환성 파괴 위험 — 마이그레이션 스크립트 필수.
- **참조:** Matryoshka Representation Learning (Kusupati et al. 2022), nomic-embed-text-v1.5
- **ROI 점수: 6/10**

---

### #8 — 검색 결과 LRU에 Generation 기반 Invalidation

- **카테고리:** 캐시
- **현재 상태:** `cache.py` TTL 기반 만료 (300s). 엔티티가 업데이트되어도 캐시가 TTL 전에 stale 결과 반환.
- **개선안:**
  - `MemKraft` 클래스에 `_generation: int = 0` 카운터 추가
  - `update()`, `track()`, `fact_add()` 등 write 경로에서 `_generation += 1`
  - 캐시 키에 `generation` 포함: `cache_key = f"{query}:g{self._generation}"`
  - TTL 300s → 600s로 늘려도 정확도 유지 (generation 변경 시 자동 무효화)
- **예상 효과:** TTL 연장으로 warm hit rate +5~10 pp. stale read 0%. write 후 즉각 정확성 보장.
- **구현 난이도:** Easy | **0.5일**
- **리스크:** generation 카운터가 프로세스 재시작 시 리셋됨 — 다중 프로세스 환경에서 stale 가능. 단일 프로세스 CLI 용도엔 문제없음.
- **참조:** [Caffeine invalidation strategies](https://github.com/ben-manes/caffeine/wiki), Redis keyspace notifications 유사 패턴
- **ROI 점수: 7/10**

---

### #9 — sqlite-vec FTS5 + RRF Hybrid 인덱스 (1만 doc+ 시나리오)

- **카테고리:** 인덱싱 / BM25
- **현재 상태:** 파일 기반 in-memory 인덱스. 엔티티 수 증가 시 선형 스캔 비용 O(n).
- **개선안:**
  - `pip install sqlite-vec` (optional extra `[vec]`)
  - SQLite FTS5 테이블로 엔티티 텍스트 인덱싱 (BM25 내장)
  - `sqlite-vec` extension으로 벡터 컬럼 추가
  - FTS5 BM25 score + 벡터 cosine similarity → RRF fusion (`1/(k+r_bm25) + 1/(k+r_vec)`, k=60)
  - 단일 `.db` 파일 — zero-server, 이식성 유지
- **예상 효과:** 10K 엔티티 기준 검색 **10~30x** 향상 (파일 스캔 제거). RRF 융합으로 recall +5~10%.
- **구현 난이도:** Hard | **4일**
- **리스크:** 파일 기반 메모리 ↔ SQLite 이중 관리. 마이그레이션 경로 복잡. 소규모(< 1K 엔티티) 환경에서는 오히려 overhead.
- **참조:** [sqlite-vec](https://github.com/asg017/sqlite-vec), SQLite FTS5 docs
- **ROI 점수: 5/10** (대규모 전용)

---

### #10 — 검색 경로 프로파일링 + 핫패스 분기 최적화

- **카테고리:** 빌드 / 인덱싱
- **현재 상태:** `_smart_search()` (core.py:1093) — exact → BM25 → fuzzy → embedding 순 폭포수. 각 단계가 항상 실행됨. 짧은 쿼리(1~2 토큰)에도 full BM25 + fuzzy 실행.
- **개선안:**
  - 쿼리 길이/타입에 따른 조기 종료: `len(tokens) == 1` + exact match → 즉시 반환
  - 신뢰도 임계값 (score ≥ 0.9) 달성 시 다음 단계 스킵
  - `SearchPlan` 객체 도입: 쿼리 특성 분석 후 어떤 단계 실행할지 사전 결정
  - cProfile + `bench/` 디렉토리로 핫패스 측정 자동화
- **예상 효과:** 단순 exact-match 쿼리 **2~5x** 향상 (BM25/fuzzy 건너뜀). 평균 `repeat_cache_off` 4.8 ms → 2~3 ms.
- **구현 난이도:** Medium | **1.5일**
- **리스크:** 조기 종료 로직이 recall을 떨어뜨릴 수 있음 — 임계값 튜닝 테스트 필요.
- **참조:** Elasticsearch "early termination" 전략, Lucene Top-K
- **ROI 점수: 8/10**

---

### #11 — Tantivy-py Fulltext Engine (선택적 Rust 백엔드)

- **카테고리:** BM25 / 인덱싱
- **현재 상태:** 자체 Python BM25 구현 — 멀티코어 활용 없음, GIL 제한.
- **개선안:**
  - `pip install tantivy` (optional extra `[tantivy]`)
  - `tantivy.SchemaBuilder` → 스키마 정의, `tantivy.Index` 생성
  - `mk search` 시 tantivy 인덱스 hit → score 반환
  - 기존 BM25 경로는 `tantivy` 미설치 시 자동 fallback
- **예상 효과:** Lucene급 성능. Python BM25 대비 **20~100x**. 멀티코어 병렬 색인.
- **구현 난이도:** Hard | **3일**
- **리스크:** Rust wheel 빌드 환경 의존. `tantivy` 인덱스 포맷이 메모리 파일 구조와 이질적 — 동기화 레이어 필요.
- **참조:** [tantivy-py](https://github.com/quickwit-oss/tantivy-py), MIT license
- **ROI 점수: 5/10** (대형 배포 전용)

---

### #12 — 배치 `embed_batch()` + 비동기 임베딩 파이프라인

- **카테고리:** 임베딩
- **현재 상태:** `embedding.py:209` `embed_text()` — 단건 동기 처리. `update()` 여러 번 호출 시 순차 추론.
- **개선안:**
  - `embed_batch(texts: list[str], batch_size: int = 32) -> np.ndarray` 공개 API
  - `asyncio` 기반 `aembed_text()` / `aembed_batch()` 비동기 래퍼
  - `track()` bulk 시나리오에서 배치 큐잉: 32개 쌓이면 한 번에 추론
  - `sentence-transformers` `.encode(batch)` 내부 배치 처리 활용
- **예상 효과:** 배치 32 기준 throughput **5~10x** 향상. 순차 단건 50ms × 32 = 1600ms → 배치 160ms.
- **구현 난이도:** Medium | **1일**
- **리스크:** 배치 큐잉 → latency vs throughput tradeoff. 실시간 단건 쿼리에는 효과 없음.
- **참조:** sentence-transformers `.encode(sentences, batch_size=32)`, asyncio.Queue pattern
- **ROI 점수: 6/10**

---

## 📊 TOP 3 우선순위 표

| 순위 | 아이디어 | 카테고리 | 예상 효과 | 난이도 | 작업일 | ROI |
|------|---------|---------|---------|-------|-------|-----|
| 🥇 **1** | **#1 RapidFuzz SequenceMatcher 교체** | fuzzy | cold path -1~2 ms (10~100x) | Easy | 0.5d | **9/10** |
| 🥈 **2** | **#10 핫패스 분기 최적화** | 빌드 | repeat_cache_off 4.8→2~3 ms (2~5x) | Medium | 1.5d | **8/10** |
| 🥉 **3** | **#3 BM25S 라이브러리 통합** | BM25 | repeat_cache_off → 1~2 ms (5~50x) | Medium | 1.5d | **8/10** |

> **단기 빠른 승리 (이번 주):** #1 (0.5d) + #4 정규식 사전 컴파일 (0.3d) + #8 Generation Invalidation (0.5d) — **합산 1.3일, 체감 개선 즉각**.
> **다음 스프린트:** #10 핫패스 분기 + #3 BM25S (3일).

---

## ✅ v2.7.5 Already Done

| 패치 | 내용 | 효과 |
|------|------|------|
| `_corpus_index.py` 신규 | `_smart_search()` 매쿼리 BM25 IDF/TF 재계산 → corpus_index 캐시 | cold 1.39x, warm 5~6x (0.18ms/5560 qps) |

---

*MemKraft v2.7.4 설치 확인 (런타임) + v2.7.5 corpus_index 패치 설치 확인 후 작성됨.*
*참조 벤치: repeat_cache_off mean 4.8ms / repeat_cache_on 0.18ms 5560qps / mixed_cache_off 5.3ms 188qps*
