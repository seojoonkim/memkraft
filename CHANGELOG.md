# CHANGELOG

## [Unreleased] — v2.7.3

### Added
- **Local embedding retrieval** (1° LongMemEval lever — closes the
  zero-embedding gap that capped accuracy at ~90%, targeting parity
  with MemPalace 96.6% / OMEGA 95.4%). New module
  `memkraft.embedding` with a lazy SentenceTransformer loader,
  pure-Python cosine, and incremental on-disk index.
- New API on `MemKraft` (additive, keyword-only where it matters):
  - `mk.embed_text(text)` → `list[float]`
  - `mk.embed_batch(texts)` → `list[list[float]]`
  - `mk.search_semantic(query, top_k=10, *, min_score=0.0, auto_build=True)`
  - `mk.search_hybrid(query, top_k=10, *, alpha=0.5, k=60, date_hint=None)`
    — Reciprocal Rank Fusion of `search_smart` (BM25 family) and
    `search_semantic`. `alpha=0.0` ≡ BM25 only, `alpha=1.0` ≡
    semantic only. Falls back to pure BM25 when the optional extra
    is missing so existing pipelines never crash.
  - `mk.build_embeddings(force=False)` → stats dict
  - `mk.embedding_stats()`, `mk.embedding_clear()`
- Default model: `sentence-transformers/all-MiniLM-L6-v2`
  (~90 MB, 384-d). Override via `MEMKRAFT_EMBEDDING_MODEL` env var.

### Storage
- New `<base_dir>/.memkraft/embeddings/index.jsonl` — one JSON
  record per markdown file (`{file, model, dim, mtime, size, vec}`).
  Re-indexing is incremental: a file is re-encoded only when its
  mtime or size changes; deleted files are pruned automatically.

### Optional dependency
- `pip install 'memkraft[embedding]'` installs
  `sentence-transformers>=2.2,<6` and `numpy>=1.21`. Without it,
  `embed_text` / `search_semantic` raise `MemKraftEmbeddingError`
  with a clear install hint, and `search_hybrid` degrades to
  BM25-only.

### Fixed
- **`pref_context(entity)` no longer raises `TypeError`.** The
  `scenario` argument is now optional (`scenario: str = ""`); when
  omitted or empty, no scenario keyword filtering is applied and the
  call returns the top `max_prefs` current preferences ranked by
  strength. Previously, callers that just wanted "all current
  preferences for this entity" were forced to pass a dummy scenario
  string to avoid `missing 1 required positional argument: 'scenario'`.
- **`pref_set` auto-creates parent directories on a fresh `base_dir`.**
  The `preferences/` directory is now created with `parents=True`, so
  `MemKraft(base_dir=<never-initialized-path>).pref_set(...)` no longer
  raises `FileNotFoundError`. Mirrors the `parents=True` policy core.py
  already uses for `.memkraft/<inner>/`.

### Tests
- New cases in `tests/test_preference_v272.py`:
  - `test_pref_context_no_scenario` — `pref_context(entity)` returns a
    structured payload without raising.
  - `test_pref_context_empty_scenario` — `pref_context(entity, "")`
    matches the no-arg behaviour.
  - `test_pref_context_with_scenario_still_filters` — existing scenario
    keyword routing is unchanged (food scenario still ranks food).
  - `test_pref_set_creates_nested_dirs` — fresh deep `base_dir` works
    end-to-end.
  - `test_pref_set_existing_dir_idempotent` — second call into an
    already-created `preferences/` dir succeeds without error.

### Compatibility
- Additive only. Public signatures relaxed (`scenario` gains a default).
  Pre-v2.7.3 callers passing `scenario=` positionally or by keyword are
  unaffected.

## [2.7.2] — 2026-05-01

### Fixed
- **Critical — preference API was unreachable.** Since v2.1.0 the
  `PreferenceMixin` (defining `pref_set`, `pref_get`, `pref_context`,
  `pref_evolution`) was deliberately not registered on `MemKraft` to
  avoid clobbering `core._slugify` (which has CJK support). The mixin
  was never re-attached selectively, so all four methods silently
  resolved as `AttributeError` at call time. The PersonaMem 32k harness
  swallows those exceptions in `try/except` guards, so ingestion
  appeared to work but **no preferences were ever stored** — producing a
  -13.6pp accuracy regression vs hybrid baseline (memkraft 38% / hybrid
  44% / baseline 42% on n=50). Now `pref_set / pref_get / pref_evolution
  / pref_context` are explicitly attached to `_BaseMemKraft` while
  preserving `core._slugify` (CJK-aware) and the existing
  `pref_conflicts` / `pref_conflicts_all` aliases (no-arg, scans all
  entities).

### Tests
- New: `tests/test_preference_v272.py` — 10 tests covering method
  presence, set/get round-trip, chronological overwrite (closes
  previous open-ended pref), `pref_context` scenario routing,
  `pref_evolution` ordering, `pref_conflicts*` shape, Korean entity
  names (CJK `_slugify` regression guard), category filter, reason
  preservation, and empty-entity behaviour.
- Cumulative: **1220 passed, 3 skipped** (zero regressions; baseline 1210).

### Compatibility
- Additive only. Public signatures unchanged. No new dependencies.
  Pre-v2.7.2 callers who relied on `AttributeError` for these methods
  (none should) will now get real return values.

### Upgrade
```bash
pip install --upgrade memkraft
```

## [2.7.1] — 2026-05-01

### Added
- **ReasoningBank** — record agent reasoning trajectories (thought → action → outcome), distill them into success / failure patterns, and recall relevant past lessons before the next task. Six new methods on `MemKraft`:
  - `mk.trajectory_start(task_id, *, title="", tags="")` — begin a trajectory.
  - `mk.trajectory_log(task_id, step, *, thought="", action="", outcome="", metadata=None)` — append a step. Auto-starts the trajectory if missing.
  - `mk.trajectory_complete(task_id, *, status="success", lesson="", pattern_signature="", tags="")` — finish a trajectory and upsert its pattern bucket. Idempotent on duplicate (status, signature).
  - `mk.reasoning_recall(query, *, top_k=3, status="", min_score=0.0)` — retrieve completed trajectories most relevant to `query` via stopword-aware Jaccard over (title, lesson, tags, signature).
  - `mk.reasoning_patterns(*, status="", min_count=1, top_k=20)` — list patterns sorted by frequency.
  - `mk.trajectory_get(task_id)` — return the full reconstructed view (start + steps + complete).
- **Repeated-failure detection** — when the same `failure` signature occurs ≥ 2 times, MemKraft auto-emits a high-importance event via `log_event(..., tags="reasoning-bank,repeat-failure")` so retros and dashboards light up immediately.

### Storage
- All ReasoningBank data lives under `<base_dir>/.memkraft/`:
  - `trajectories/<task_id>.jsonl` — append-only, one JSON record per line, tolerant of corrupt lines.
  - `patterns.json` — atomic-rename writes, per-pattern caps (max 3 lessons / 50 task ids).
- Zero leakage into user-facing markdown — ReasoningBank is a meta layer, not a document corpus.

### Tests
- New: `tests/test_reasoning_bank.py` — 18 tests covering start/log/complete round-trip, signature determinism + degenerate fallback, status / min_score filters, pattern frequency ordering, repeat-failure event emission, idempotency on duplicate completes, corrupt-line tolerance, layout safety, list/string tag coercion, and path-traversal-safe task ids.
- Cumulative: **1210 passed, 3 skipped** (zero regressions; baseline 1192).

### Design
- Mixin pattern (`ReasoningBankMixin`), additive only, stdlib only, no signature changes elsewhere. Spec: `docs/REASONING_BANK_DESIGN.md`.

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.7.0] — 2026-05-01

### Added
- **Search result caching** — `search_v2()` and `search_smart()` now serve repeat queries from a thread-safe in-process LRU + TTL cache (`_SearchCache`, default capacity 256, TTL 300s). Zero breaking changes; opt-out per call via `cache=False`.
- **`cache_stats()`** — returns `{hits, misses, evictions, size, capacity, ttl_seconds, hit_rate, generation}`. Useful for monitoring cache effectiveness in long-running agents.
- **`cache_clear()`** — manual purge. Mostly for tests / benchmarks; mutations already auto-invalidate.
- **`cache_configure(capacity, ttl)`** — reconfigure cache at runtime. Existing entries preserved (capacity shrink evicts immediately).

### Changed
- `search_v2(query, ..., cache=True)` and `search_smart(query, ..., cache=True)` — new keyword-only parameter. Default `True`.
- Mutation methods (`update`, `track`, `fact_add`, `log_event`, `consolidate`, `consolidate_run`, `decision_record`, `incident_record`, `dream_cycle`) now bump an internal `_cache_generation_counter` after the original call returns. The counter is part of every cache key, so mutations automatically invalidate every cached entry without requiring an explicit purge. Bookkeeping is wrapped in a `try/except` so cache failures can never break a write.

### Performance (measured on synthetic 50-entity corpus, 100 calls per workload, Apple M-series)
- **Hot path** (10 repeated queries): cache OFF mean **6.59 ms** → cache ON mean **1.07 ms** — **6.14x speedup**, **+513% throughput** (152 → 931 qps).
- **Mixed workload** (50% repeat / 50% varied): cache OFF mean **6.08 ms** → cache ON mean **3.69 ms** — **1.65x speedup** (164 → 271 qps).
- **`search_smart` hot path**: cache OFF mean **5.69 ms** → cache ON mean **0.99 ms** — **5.76x speedup** (176 → 1012 qps).
- p50 cache hit latency: **0.14 ms** (vs ~5.5 ms uncached).
- Invalidation correctness: 100% miss rate when a mutation runs every 10 queries (verified end-to-end).
- Raw numbers: `benchmarks/v2.7.0-bench-result.json`.

### Tests
- New: `tests/test_search_cache.py` — 24 tests (LRU/TTL/thread-safety/invalidation/opt-out/configure).
- Cumulative: **1192 passed, 3 skipped** (zero regressions).

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.6.0] — 2026-04-30

### Added
- **`fact_type` (episodic / semantic / procedural)** — new keyword on `mk.fact_add(..., fact_type="episodic")`. Default is `semantic` (backward-compatible). The type is persisted as-is in frontmatter with guaranteed round-trip — a foundation for retrieval policies that will eventually treat fact kinds differently.
- **`auto_tier()`** — activity-based tier recommendation per entity. Combines `(recency, frequency, importance)` into a weighted score and returns a `core / recall / archival` candidate. `dry_run=True` is the default — it returns a result dict without touching files. Weights are overridable via `weights={...}`. Works on a single entity or as a full-store sweep.
- **Contradiction detection** — if the same `(entity, key)` is recorded with different values whose validity windows overlap, MemKraft now detects it automatically. Catches both naive concurrent writes and conflicts buried inside body text. Silent by design — it warns only, never blocks the write (preserves user workflow).
- **Counting question + 1-hop graph neighbor expansion** (`search.py`) — queries shaped like "how many / list all / in total" now fold in 1-hop graph neighbors. This catches cross-session counting where the entity is shared but keywords don’t overlap. Neighbor scores are damped (cap 0.50, factor 0.6) so they can never outrank a direct match. Cleanly no-ops when the graph mixin is absent.

### Tests
- New: `tests/test_new_260_features.py` — 15 cases (FactType 6 + AutoTier 5 + Contradiction 4) all passing.
- Cumulative: **1168 passed, 3 skipped** (zero regressions).

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.3.3] — 2026-04-27

### Added
- **Context Compression** — `compress_context()` 신규. 핵심 facts만 추출해 LLM 컨텍스트를 18K → 3~5K 까지 압축. 쿼리 토큰 오버랩 relevance 점수 + (entity, key) 기반 dedup + temporal 메타(`valid_from` / `recorded_at` / 본문 날짜) 가중치. 결정론적·idempotent — 같은 입력은 항상 같은 출력. `context_compress.py` 신규 모듈.
- **Re-ranking by Question Type** — `rerank_for_question_type()` 신규. 라우팅이 고른 전략 위에 질문 유형별(counting / knowledge_update / temporal_reasoning / preference / multi_session) 정밀 재정렬을 얹어 가장 유용한 근거를 컨텍스트 앞쪽으로 부유시킴. 보너스는 ≤ +0.30 으로 캡 — 강한 base score 가 깨지지 않음. `rerank.py` 신규 모듈.
- **Temporal Annotation** — `_annotate_temporal()` 신규. 결과에 `[YYYY-MM-DD ~ present]` 류 시간 태그를 명시적으로 부착해 LLM 이 "언제부터 유효한 사실인지" 즉시 식별. `confidence.py` 에 통합.
- **format_context_for_llm** — 통합 파이프라인. `rerank_for_question_type` → `_annotate_temporal` → `compress_context` → confidence 태깅 순으로 한 번에 처리. 단일 호출로 LLM-ready 컨텍스트 블록 생성. `confidence.py` 에 추가.

### Tests
- 신규: `test_context_boost.py` — 26 케이스 전부 통과 (compress_context 9 + rerank 11 + temporal_annotation 3 + format_context_for_llm 3).
- 누적: 1141 passed (회귀 0건. test_hierarchical 12 baseline failure 는 v2.2.0 부터 이어진 미통합 모듈, 본 릴리즈 무관).

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.3.2] — 2026-04-27

### Added
- **Counting Question 특화** — "how many" 질문 시 5-pass exhaustive sweep. `routing.py`에 6번째 질문 타입 (`counting`) 추가. 카운팅 질문은 단일 hit으로 끝내지 않고 모든 후보를 휩쓸어 누락된 인스턴스를 잡아냄. `multi_pass.py`에 counting-mode 분기. `tests/test_counting_question.py` 케이스 통과.
- **Confidence Threshold** — `search()` 결과에 `confidence` 필드(`high` / `medium` / `low`) 자동 부착. 암시적 표현 패턴 20개(EN+KO) 매칭으로 "probably" / "~인 것 같다" 같은 약한 단언 자동 분류. `confidence.py` 신규 모듈 + LLM 포맷터로 답변 신뢰도 가시화. `tests/test_confidence.py` 케이스 통과.
- **Temporal Chain** — temporal graph 기반 세션 간 이벤트 연결. 시간 윈도우 추출(`extract_temporal_window`) + recency-weighted 점수 합성. 같은 엔티티의 멀티-세션 이벤트를 시간순으로 자동 체이닝해 "지난주에 했던 그 미팅" 류 시간 의존 질의 정확도 향상. `temporal_chain.py` 신규 모듈. `tests/test_temporal_chain.py` 케이스 통과.

### Tests
- 신규: test_confidence.py + test_counting_question.py + test_temporal_chain.py + test_question_routing.py 총 97개 케이스 전부 통과
- 회귀 0건 (test_hierarchical.py 12 baseline failure는 v2.2.0부터 이어진 미통합 모듈, 본 릴리즈와 무관)

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.3.1] — 2026-04-26

### Patch
- **PyPI 재업로드** — 버전 메타데이터 정정을 위한 패치 릴리즈. 기능 변경 없음. v2.3.0과 코드 동일.

### Upgrade
```bash
pip install --upgrade memkraft
```

---

## [2.3.0] — 2026-04-26

### Added
- **BM25 Scoring** — Okapi BM25 (stdlib `math` only, zero deps). `search()`에 4번째 신호로 통합. TF 포화 (k1=1.5) + 길이 정규화 (b=0.75) + IDF. 기존 exact/fuzzy/graph 신호와 가중 합성. `tests/test_bm25.py` 케이스.
- **Reciprocal Rank Fusion (RRF)** — `rrf.py` 신규 모듈. RRF(k=60) 순위 기반 검색 융합. `search_multi()`의 기본 융합 전략으로 채택 (기존 가중 블렌딩 0.5·p1 + 0.3·p2 + 0.2·p3 대체 가능). 서로 다른 스케일의 점수를 안정적으로 결합. `tests/test_rrf.py` 케이스.
- **Causal Graph** — `graph.py`에 `graph_type` 컬럼 추가 (`entity` / `temporal` / `causal` / `semantic` 4종). `graph_causal_chain(entity, direction='backward'|'forward', max_hops=N)` API. 한/영/중 causal 패턴 (because, 때문에, 因为, 所以, leads to, 결국, 导致 등) 자동 추출. `tests/test_causal_graph.py` 케이스.
- **Memory Consolidation** — 4단계 수면 통합 (duplicate merge, stale close, orphan cleanup, observation generation). `consolidate(strategy='auto'|'aggressive', dry_run=False)` API. 잠자는 동안 중복 엔티티 병합·만료된 fact 자동 종료·고아 노드 정리·메타 관찰 생성. `consolidation.py` 신규 모듈. `tests/test_consolidation.py` 29개 케이스, 전부 통과.

### Tests
- 전체: **1050 passed, 3 skipped, 12 failed** (12 failed = test_hierarchical.py baseline, v2.2.0부터 이어진 미통합 모듈, v2.3.0 신규 기능과 무관)
- 신규: test_bm25.py + test_rrf.py + test_causal_graph.py + test_consolidation.py 총 100개 케이스 추가, 전부 통과
- 회귀 0건

### Upgrade
```bash
pip install --upgrade memkraft
```
Zero breaking changes. v2.2.x API 시그니처 100% 유지. `search()`/`search_multi()` 호출부 그대로 사용 가능.

---

## [2.2.0] — 2026-04-26

### Added
- **Knowledge Update Auto-Close** — `fact_add()`에서 같은 entity+key의 기존 open-ended fact를 자동 종료. `auto_close_stale=True` (기본값). CEO→CTO 전환 시 이전 role 자동 종료. `tests/test_knowledge_update.py` 20 케이스.
- **Multi-Pass Retrieval** — `search_multi(passes=3)`: Pass 1 (exact+fuzzy) → Pass 2 (graph expansion) → Pass 3 (temporal timeline). 0.5·p1 + 0.3·p2 + 0.2·p3 블렌딩. `tests/test_multi_pass.py` 21 케이스.
- **Question-Type Routing** — `search_smart_v2(query)`: 5가지 질문 유형 자동 분류 + 유형별 검색 전략 (single_session / multi_session / knowledge_update / temporal_reasoning / preference). `tests/test_question_routing.py` 32 케이스.

### Tests
- 전체: **950 passed, 3 skipped** (신규 회귀 0)
- 신규: test_knowledge_update.py (20), test_multi_pass.py (21), test_question_routing.py (32) — 총 73개
- 주의: test_hierarchical.py 12개 실패는 기존 baseline (별도 hierarchical 모듈 통합 미완료, v2.2.0 신규 기능과 무관)

### Upgrade
```bash
pip install --upgrade memkraft
```
Zero breaking changes. v2.1.x API 시그니처 100% 유지.

---

## [2.1.0] — 2026-04-26

### Added
- **Korean Graph Extraction** — `graph.py`에 한국어 관계 패턴 37개 추가 (14개 relation 타입). 조사(strip_josa) 처리, 2-char guard, 한국어 stopwords. `tests/test_korean_graph.py` 37 케이스.
- **Multimodal Memory** — `multimodal.py` (MultimodalMixin) 4개 API: `attach`, `attachments`, `detach`, `search_multimodal`. 확장자 기반 modality 자동 감지, 텍스트/코드 직접 읽기, 오디오/이미지 `transcribe_fn` 콜백. `tests/test_multimodal.py` 22 케이스.
- **Core Refactor Plan** — `docs/V21_CORE_REFACTOR_PLAN.md` (4,435줄 → 9개 모듈 분해 설계)
- **Multi-Agent v2 Spec** — `docs/V21_MULTIAGENT_V2_SPEC.md` (Model A: Shared base_dir + Namespace, 6개 API)
- **Benchmark Diversification** — `docs/V21_BENCHMARK_DIVERSIFICATION.md` (7개 벤치마크 분석, Top3: LoCoMo/MemoryBench/PersonaMem)

### Changed
- **CHANGELOG v1.1.1 + v2.0.0 섹션 복원** — 이전에 누락된 변경내역 추가 (watch/unwatch/schedule 3 API + graph 6 API)
- **Total public APIs: 25+ (기존 16 → graph 6 + multimodal 4 + lifecycle 3)**

### Tests
- 전체: **877 passed, 3 skipped** (회귀 0)
- 신규: test_korean_graph.py (37 cases), test_multimodal.py (22 cases)

### Upgrade
```bash
pip install --upgrade memkraft
```
Zero breaking changes. 모든 v2.0.x API 시그니처 유지.

---

## [2.0.0] — 2026-04-23

### Added

- **`GraphMixin`** (`src/memkraft/graph.py`) — SQLite-backed knowledge graph layer. Zero external dependencies (Python built-in `sqlite3`). Additive mixin; no changes to existing APIs.
- **`graph_node(node_id, node_type='entity', label=None, metadata=None)`** — Add or update a node in the graph. Upserts: re-calling with the same `node_id` updates attributes.
- **`graph_edge(from_id, relation, to_id, weight=1.0, valid_from=None, valid_until=None)`** — Add a directed, optionally time-scoped edge. Auto-creates missing nodes. Deduplicates exact (from, relation, to) triples.
- **`graph_neighbors(node_id, hops=2, relation=None)`** — BFS traversal up to N hops. Returns a list of path dicts with `path`, `depth`, `target`, `relation`, `text` fields. Optional `relation` filter.
- **`graph_search(query, top_k=5)`** — Natural-language → graph paths. Extracts capitalized entities from query, traverses via `graph_neighbors`, falls back to `search_precise` if graph results are sparse.
- **`graph_extract(text)`** — Pattern-based (no LLM) auto-extraction of entities and relations from free text. Returns `{nodes_added, edges_added}`.
- **`graph_stats()`** — Returns node/edge counts, node-type breakdown, and top-10 relations by frequency.
- **Connection pooling** — Single cached SQLite connection per instance for performance.
- **Hybrid search** — `graph_search` chains: graph (exact) → entity fallback → `search_precise` (document fallback).
- **Tests:** `tests/test_graph_mixin.py` (8 cases): `test_graph_node_basic`, `test_graph_edge_basic`, `test_graph_neighbors`, `test_graph_extract`, `test_graph_search`, `test_multihop_reasoning`, `test_graph_stats`, `test_no_duplicate_edges`.

### Motivation

Flat entity bags can't express *relationships*. GraphMixin adds a persistent, queryable knowledge graph so MemKraft can answer multi-hop questions (e.g. "Who does Simon work with at Hashed?") without an external graph database.

### Performance (AMB PersonaMem, 2026-04 Zeon)

- **PersonaMem 128k:** 56% (±8% LLM variance, retrieval-bound)
- **PersonaMem 1M:** 52% (±4% LLM variance)

### Total APIs: 25 (19 existing + 6 new graph APIs: `graph_node`, `graph_edge`, `graph_neighbors`, `graph_search`, `graph_extract`, `graph_stats`)

### Breaking Changes

None. All v1.1.1 APIs remain unchanged. `GraphMixin` is additive.

### Upgrade

```bash
pip install --upgrade memkraft
```

---

## [1.1.1] — 2026-04-23

### Added

- **`watch(path, on_change='flush', interval=300)`** — Start a background daemon thread that polls `path` every `interval` seconds. On modification, triggers `on_change`: `"flush"` (re-import file), `"compact"`, `"digest"`, or any callable `(changed_path: str)`. Supports both file and directory watching (`.md` files only for directories).
- **`unwatch()`** — Stop the background watcher thread started by `watch()`. Sets the internal `_watching` flag to `False`.
- **`schedule(pipeline, cron_expr)`** — Built-in cron scheduler. Runs an ordered list of memory-management actions (`"compact"` or any zero-arg callable) on a 5-field cron expression. Requires optional dep: `pip install "memkraft[schedule]"` (`apscheduler`).
- **`memkraft[schedule]` extra** — New optional dependency group added to `pyproject.toml`.
- **Tests:** `tests/test_v110_lifecycle.py` (6 new cases): `test_watch_starts_thread`, `test_unwatch_stops_flag`, `test_watch_callable_on_change`, `test_schedule_requires_apscheduler`, `test_schedule_invalid_cron`, `test_schedule_creates_scheduler`.

### Motivation

MemKraft 1.1.0 introduced self-managing lifecycle APIs (flush/compact/digest/health) but they still required explicit calls. v1.1.1 makes memory management truly autonomous: `watch()` triggers flush automatically when files change; `schedule()` runs compact nightly via cron — no manual intervention needed.

### Total APIs: 19 (16 existing + 3 new: `watch`, `unwatch`, `schedule`)

### Breaking Changes

None. All v1.1.0 APIs remain unchanged. `watch`, `unwatch`, `schedule` are additive.

### Upgrade

```bash
pip install --upgrade memkraft
# optional: cron scheduler support
pip install "memkraft[schedule]"
```

---

## [1.1.0] — 2026-04-23

### Added

- **`flush(source_path, strategy='auto')`** — Import external markdown file → MemKraft structured data. Supports `auto` (section-aware), `events` (list items), and `facts` (table rows) strategies.
- **`compact(max_chars=15000, dry_run=False)`** — Automatically move old/low-importance entities to archival tier. Supports `dry_run` preview mode.
- **`digest(output_path, max_chars=15000)`** — Render MemKraft state → MEMORY.md, always ≤ `max_chars`. Guaranteed no overflow.
- **`health()`** — Diagnose memory health: total size, tier distribution, entity count, recommendations, and status (`healthy` / `warning` / `critical`).
- **`LifecycleMixin`** (`src/memkraft/lifecycle.py`) — additive mixin; no changes to existing APIs.
- **Tests:** `tests/test_v110_lifecycle.py` (20 cases) covering flush/compact/digest/health with edge cases.

### Motivation

MEMORY.md grew to 153KB due to add-only pattern + broken nightly cleanup. MemKraft 1.1.0 makes this impossible: memory manages itself.

### Total APIs: 16 (12 existing + 4 new lifecycle APIs)

```bash
pip install --upgrade memkraft
```

All v1.0.3 APIs remain unchanged. Existing call sites are unaffected.

---

## [1.0.3] — 2026-04-23

### Added

- **`track_document(doc_id, content, chunk_size=500, chunk_overlap=50, entity_type="document", source="")`** — auto-chunking for long documents. Splits content into ~chunk_size-word overlapping chunks (BM25-style) and tracks each as `{doc_id}__c{idx}`. A parent entity is also tracked so callers can group chunk hits back to the source.
- **`search_precise(query, top_k=5, score_threshold=0.1)`** — precision-first search. Runs `search(fuzzy=False)` with a score threshold; falls back to `search(fuzzy=True)` with a relaxed threshold (`score_threshold * 0.5`) if the precision pass returns nothing.
- **`ChunkingMixin`** (`src/memkraft/chunking.py`) — additive mixin wired via `__init__.py`; no changes to `core.py` or existing signatures.
- **Tests:** `tests/test_v103_chunking.py` (13 cases) covering chunk math, overlap, parameter validation, search round-trip, threshold filtering, and fuzzy fallback.

### Performance (AMB PersonaMem pilot, 2026-04 Zeon)

- **PersonaMem 32k:** MemKraft **80%** vs BM25 70% (**+10pp**)
- **PersonaMem 128k:** MemKraft **75%** vs BM25 50% (**+25pp**)

### Upgrade

```bash
pip install --upgrade memkraft
```

All v1.0.2 APIs remain unchanged. Existing call sites are unaffected.

---

## [1.0.2] — 2026-04-22

### Added

- **LongMemEval benchmark harness** (`benchmarks/longmemeval/`):
  - `run.py` — oracle/s subset evaluation
  - `llm_judge.py` — LLM-as-judge scoring
  - `run_majority_vote.py` — 3-run semantic majority vote
  - Achieves **98.0%** on LongMemEval (LLM-judge, oracle 50, 3-run majority)
  - Surpasses prior SOTA: MemPalace (96.6%), MEMENTO/MS (90.8%)
- **SearchMixin APIs** (`src/memkraft/search.py`):
  - `search_v2`, `search_expand`, `search_temporal`, `search_ranked`, `search_smart`
- **`[bench]` optional dependency group** for benchmark reproduction (`pip install memkraft[bench]`)
- **README:** Added "Reproducing LongMemEval Results" section with full reproduction guide
- **Tests:** New `tests/test_v102_search.py` covering SearchMixin surface

### Fixed

- Benchmark harness: temporal questions excluded from aggregation detection (v3 fix)
- Benchmark harness: assistant content truncation at 1500 chars (v4 fix)
- Benchmark harness: full content sidecar for long assistant responses (v4.4 fix)

### Upgrade

```bash
pip install --upgrade memkraft
# or with benchmark extras:
pip install --upgrade 'memkraft[bench]'
```

No breaking changes. All new APIs are additive.

---

## [1.0.1] — 2026-04-21

### Polish & Bug Fixes

First patch after 1.0.0. Zero breaking changes; all fixes are additive
or opt-in.

- **README:** Added the hero "1.0 Self-Improvement Loop" quickstart
  block covering `prompt_register` → `prompt_eval` → `prompt_evidence`
  → `convergence_check`. Verified end-to-end.
- **`prompt_register`:** Now emits a `UserWarning` when `path` points to
  a non-existent file (silent typo fix). Pass `validate_path=True` to
  promote the warning to `FileNotFoundError` for stricter pipelines.
- **`prompt_eval`:** Rejects calls with *both* empty `scenarios` and
  empty `results` (`ValueError`) — recording an empty iteration polluted
  the ledger without analytical value.
- **`prompt_eval`:** Emits a `UserWarning` when a result references a
  scenario name not declared in `scenarios`, catching copy-paste drift
  that previously persisted as bad data.
- **`convergence_check`:** The `insufficient-iters` response now surfaces
  the iteration numbers that *were* found (previously always `[]`).
  When no iterations exist, `suggested_next` is `"first-iteration"`
  instead of the misleading `"patch-and-iterate"`.
- **Tests:** +10 (total 741 passing, 1 skipped).

### Upgrade

```bash
pip install --upgrade memkraft
```

## [1.0.0] — 2026-04-21

### Philosophy

> Bitemporal memory × empirical tuning: the first self-improvement ledger for AI agents.

Your agent's accountable past, in plain Markdown.

### New in 1.0.0

- **`prompt_register`** — Register any prompt/skill as a tracked entity with tier + metadata.
- **`prompt_eval`** — Record one empirical tuning iteration (scenarios + results) as a bitemporal decision + incident (on regression).
- **`prompt_evidence`** — Cite past tuning results via bitemporal decision search. As-of-then view, not a rewrite.
- **`convergence_check`** — Auto-judge mizchi-style convergence with decay-weighted pass-rate trend.

Together these four APIs close the loop that began in 0.5: **register → tune → recall → decide**, every step auditable and time-travelable in plain Markdown.

### Upgrade from 0.9.x

**Zero breaking changes.** All 0.9.x public APIs keep their exact signatures. Storage layout unchanged. See [MIGRATION.md](./MIGRATION.md).

```bash
pip install --upgrade memkraft
```

### Architecture

1.0.0 is an **integration release**, not a new-backend release.

- ✅ No new storage engines. Pure Markdown + frontmatter, as always.
- ✅ No new required dependencies. Core stays zero-dep.
- ✅ No LLM calls inside MemKraft. The ledger is data; the agent is the LLM.
- ✅ Every 0.9.x primitive now composes into the full self-improvement loop.

### API surface (total: 12 public methods)

| API | Since | Role |
|-----|-------|------|
| `track` | 0.5 | Start tracking an entity |
| `update` | 0.5 | Append information to an entity |
| `search` | 0.5 | Hybrid search (exact + IDF + fuzzy) |
| `tier_set` | 0.8 | Set tier: `core` / `recall` / `archival` |
| `fact_add` | 0.8 | Record a bitemporal fact |
| `log_event` | 0.8 | Log a timestamped event |
| `decision_record` | 0.9 | Capture a decision with rationale |
| `evidence_first` | 0.9 | Retrieve evidence before acting |
| `prompt_register` | **1.0** | Register a prompt/skill as an entity |
| `prompt_eval` | **1.0** | Record one tuning iteration |
| `prompt_evidence` | **1.0** | Cite past tuning results |
| `convergence_check` | **1.0** | Auto-judge convergence |

### Tests

731 passed, 1 skipped (same as 0.9.2a2 — 1.0.0 adds zero new test code; it's a stabilization + docs release).

### Deprecations

None.

---

## [0.8.4] - 2026-04-18

### Fixed
- `brief()` now returns text from MCP recall path (critical bug: existing entities reported as `found: False`) (#3)
- `track()` correctly returns `Path` matching `Optional[Path]` signature (#2)

### Improved
- Korean josa (조사) stripping with longest-match for complex particles (`에서`, `한테서`, `로서`) + 2-char guard for names like "이은" (#1)

### Tests
- 594 passed, 1 skipped (+21 from 0.8.3)

---

## [0.8.3] - 2026-04-17

### Added
- **`memkraft init --template <name>`** — 5 presets: `claude-code`, `cursor`, `mcp`, `minimal`, `rag`. Idempotent: existing files are preserved on re-run.
- **`memkraft templates list`** — browse available scaffolding templates.
- **`memkraft agents-hint --format json`** — structured output for CI/automation. All 6 targets emit a stable envelope with `{target, version, base_dir, content}`.
- **`memkraft doctor --fix [--dry-run] [--yes]`** — auto-repair missing `memory/` structure. **Create-only, never deletes.** Prompts for confirmation unless `--yes` or `--dry-run`.
- **`memkraft stats`** — workspace dashboard. `--export json|csv` + `--out <path>` for CI.
- **`memkraft mcp doctor`** / **`memkraft mcp test`** — production validation for the MCP server. `doctor` checks extras + entry point + tool schemas + Claude Desktop config location. `test` runs a remember→search→recall round-trip in a temp workspace.
- **`docs/mcp-setup.md`** — Claude Desktop + Cursor integration guide, including troubleshooting.

### Changed
- `pyproject.toml` gains richer `project.urls` (Documentation, Bug Tracker, Changelog).
- New package data: `templates_pkg/*.json` shipped in the wheel.

### Compatibility
- Fully backward-compatible with 0.8.2. All 538 existing tests still pass; 35 new tests added (573 total).

---

## [0.8.2] - 2026-04-17

### Added
- **`memkraft selfupdate`** — self-upgrade via pip when a newer release is on PyPI. `--dry-run` to check only.
- **`memkraft doctor --check-updates`** — doctor now optionally checks PyPI and reports 🟢 up-to-date / 🟡 update available / 🔴 PyPI unreachable.
- **GitHub Actions auto-release** (`.github/workflows/release.yml`) — push a `vX.Y.Z` tag and CI builds, verifies, uploads to PyPI, and cuts a GitHub Release.

### Docs
- README: new "Staying up to date" section.
- Maintainers: PyPI publishes require `PYPI_API_TOKEN` repo secret.

### Compatibility
- Fully backward-compatible with 0.8.1. No public API changes; all 515 existing tests pass.

---

## [0.8.1] - 2026-04-17

### Added
- **`mk.init()` now returns `{"created": [...], "exists": [...], "base_dir": "..."}`** — quickstart examples actually work.
- **`memkraft agents-hint <target>` CLI** — copy-paste integration snippets for 6 targets: `claude-code`, `openclaw`, `openai`, `cursor`, `mcp`, `langchain`. Also supports `--format json` and `--base-dir` overrides.
- **`examples/` folder** — drop-in AGENTS.md block, OpenAI function-calling example, 10-line RAG loop.
- **`python -m memkraft.mcp`** — MCP stdio server exposing `remember`, `search`, `recall`, `link`. Requires `pip install 'memkraft[mcp]'`.
- **`memkraft watch`** — filesystem auto-reindex. Requires `pip install 'memkraft[watch]'`.
- **`memkraft doctor`** — health check for install + memory structure, with 🟢/🟡/🔴 icons and suggested fixes.

### Fixed
- README Quick Start example using `mk.init()` previously produced no observable side effect besides printing; now returns a structured dict so tests and scripts can branch on it.

### Changed
- `pyproject.toml` gains `[project.optional-dependencies]`: `mcp`, `watch`, and `all`.
- Package now ships `src/memkraft/prompts/templates/*.md` as package-data and includes `examples/` in sdist via `MANIFEST.in`.

### Compatibility
- Fully backward-compatible with 0.8.0. All 492 existing tests still pass; 23 new tests added (515 total).

---

## [0.8.0] — 2026-04-16

### The Memory Foundation

0.8.0 establishes the four subsystems that every later release builds on:
bitemporal facts, tier-based attention, reversible decay, and a wiki-link
graph. All four ship zero-dep and fully backward-compatible with 0.7.x.

### Added

- **Bitemporal Fact Layer** (`src/memkraft/bitemporal.py`) — facts carry
  a `valid_from` + optional `valid_to`, so entity memory becomes a
  time-travel ledger. Inline Markdown markers in
  `memory/facts/<slug>.md` keep the store git-diffable.
  - APIs: `fact_add`, `fact_at`, `fact_history`, `fact_invalidate`,
    `fact_list`, `fact_keys`.
- **Memory Tier Labels + Working Set** (`src/memkraft/tiers.py`) — three
  tiers drive retrieval priority: `core` (always recalled), `recall`
  (recalled on relevance), `archival` (cold storage). `working_set`
  assembles `core` + recently-accessed `recall` entities into an
  attention budget.
  - APIs: `tier_set`, `tier_of`, `tier_list`, `tier_promote`,
    `tier_demote`, `tier_touch`, `working_set`.
- **Reversible Decay + Tombstone** (`src/memkraft/decay.py`) —
  exponential weight decay for long-unaccessed entities, with
  tombstones that move stale files to `.memkraft/tombstones/` but keep
  them restorable. Accessing an entity restores weight; deletion is
  never destructive.
  - APIs: `decay_apply`, `decay_list`, `decay_restore`, `decay_run`,
    `decay_tombstone`.
- **Cross-Entity Link Graph + Backlinks** (`src/memkraft/links.py`) —
  `[[Wiki Link]]` patterns in any markdown file are parsed into a
  bidirectional graph. The filesystem is the database; rebuild with
  `link_scan`.
  - APIs: `link_scan`, `link_backlinks`, `link_forward`, `link_graph`,
    `link_orphans`.

### API surface added in 0.8.0

| API | Role |
|-----|------|
| `fact_add` | Bitemporal fact with `valid_from` / `valid_to` |
| `fact_at` | Query facts as of a given timestamp |
| `fact_history` | Full history of a fact key |
| `tier_set` | Set retrieval tier (`core` / `recall` / `archival`) |
| `working_set` | Build attention budget from core + recent recall |
| `decay_apply` | Apply reversible exponential decay |
| `decay_tombstone` | Soft-delete to `.memkraft/tombstones/` |
| `link_scan` | Rebuild the `[[wiki-link]]` graph index |
| `link_backlinks` | Query incoming links for an entity |

### Tests

409 → **492 passed** (+83 new in `tests/test_v080_bitemporal.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.7.x. No storage migration required:
all bitemporal state lives in frontmatter + inline Markdown markers in
the same plain files introduced in 0.5. Zero new dependencies.

---

## [0.7.0] — 2026-04-15

### Multi-Agent Auto Integration

0.7.0 turns MemKraft from a single-agent memory into a substrate for
coordinated multi-agent workflows. Channel context, task continuity,
and agent working memory (introduced in 0.6) become interoperable:
tasks can be delegated, working memory can be handed off, and context
injection is now tunable per-call.

### Added

- **`channel_update` modes** — `set` / `append` / `merge` for flexible
  channel-context updates without clobbering prior state.
- **`task_delegate()`** — track agent-to-agent task delegation with
  full history (who delegated what, to whom, when).
- **`agent_handoff()`** — transfer working memory + context between
  agents as a first-class operation.
- **`channel_tasks()`** — query recent tasks per channel with status
  filtering (e.g. `pending`, `in_progress`, `completed`).
- **`task_cleanup()`** — auto-archive completed tasks older than N
  days, keeping the active task set lean.
- **`agent_inject()` enhancements** — new `max_history` and
  `include_completed_tasks` options to shape the injected context
  block per sub-agent.

### API surface added in 0.7.0

| API | Role |
|-----|------|
| `task_delegate` | Record agent → agent task delegation |
| `agent_handoff` | Transfer working memory between agents |
| `channel_tasks` | Query tasks by channel + status |
| `task_cleanup` | Archive old completed tasks |

### Tests

357 → **409 passed** (+52 new in `tests/test_v070_multiagent.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.6.x. Zero new dependencies; all
multi-agent state reuses the `.memkraft/channels/`, `.memkraft/tasks/`,
and `.memkraft/agents/` directories introduced in 0.6.

---

## [0.6.1] — 2026-04-15

### Added

- **`agent_inject()`** promoted into the 0.6 public API surface,
  merging channel + task + agent context into a single prompt block.
- Documentation tightened around the channel / task / agent trio.

### Tests

**357 passed** (same as 0.6.0 — patch release).

### Compatibility

Fully backward-compatible with 0.6.0. Zero-dependency maintained.

---

## [0.6.0] — 2026-04-15

### Channel Context + Task Continuity + Agent Working Memory

0.6.0 introduces the three substrates that 0.7's multi-agent features
build on:

1. **Channel Context** — persistent per-channel state (e.g. one
   Telegram chat, one Slack thread) in `.memkraft/channels/`.
2. **Task Continuity** — a lifecycle register for work units
   (`start` → `update` → `complete`) in `.memkraft/tasks/`.
3. **Agent Working Memory** — per-agent persistent scratchpad in
   `.memkraft/agents/`, injectable into sub-agent prompts.

### Added

- **`channel_save` / `channel_load` / `channel_update`** — per-channel
  context persistence.
- **`task_start` / `task_update` / `task_complete` / `task_history` /
  `task_list`** — full task lifecycle tracking.
- **`agent_save` / `agent_load`** — per-agent persistent working
  memory.
- **`agent_inject()`** — merges channel + task + agent context into a
  single prompt-ready block.
- **CLI** — `channel-save` / `channel-load`, `task-start` /
  `task-update` / `task-list`, `agent-save` / `agent-load` /
  `agent-inject`.

### API surface added in 0.6.0

| API | Role |
|-----|------|
| `channel_save` / `channel_load` / `channel_update` | Channel state |
| `task_start` / `task_update` / `task_complete` | Task lifecycle |
| `task_history` / `task_list` | Task queries |
| `agent_save` / `agent_load` | Agent working memory |
| `agent_inject` | Merge channel + task + agent context |

### Tests

328 → **357 passed** (+29 new in `tests/test_v054_context.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.5.x. Zero-dependency maintained.

> 0.5.4 and 0.5.5 shipped the same channel/task/agent features as
> pre-release iterations before the feature set was frozen and
> re-tagged as 0.6.0.

---

## [0.5.1] — 2026-04-14

### Robustness Pass

0.5.1 hardens the Snapshots & Time Travel engine introduced in 0.5.0
for multi-agent usage (OpenClaw 2026-04 compatibility).

### Fixed

- **Symlink safety** — `_all_md_files()` skips symlinks to prevent
  circular traversal.
- **Race-safe `stat()` calls** — `OSError` guards across
  `health_check`, `diff`, `retro`, `open_loops`, `build_index`,
  `summarize`, `snapshot_diff`.
- **Collision-free snapshot IDs** — snapshot IDs now include a
  microsecond + UUID suffix
  (`SNAP-YYYYMMDD-HHMMSS-xxxxxx`), eliminating collisions when
  multiple agents snapshot within the same second.
- **Dynamic version in manifests** — snapshot manifests read
  `__version__` at runtime instead of a hardcoded string.
- **Snapshot memory cap** — per-file embedding in `include_content`
  is capped at 1 MB to prevent memory exhaustion on large workspaces.
- **Timezone normalisation** — `time_travel` normalises timezone-aware
  ISO timestamps to naive for cross-platform datetime comparison.

### Tests

**328 passed** (unchanged — stabilisation release, PyPI-deployed).

### Compatibility

Fully backward-compatible with 0.5.0. Zero-dependency maintained.

> 0.5.2 and 0.5.3 shipped the same robustness fixes as pre-release
> iterations; the consolidated, PyPI-tagged release is **0.5.1**.

---

## [0.5.0] — 2026-04-14

### Memory Snapshots & Time Travel

The foundational release. 0.5.0 turns a MemKraft workspace into a
point-in-time–queryable ledger: every memory state can be snapshotted,
diffed, and searched as of any past moment.

### Added

- **`snapshot()`** — point-in-time memory snapshots covering the full
  `memory/` tree.
- **`snapshot_list()`** — enumerate all snapshots.
- **`snapshot_diff()`** — compare two snapshots (added / removed /
  modified entities).
- **`time_travel()`** — search past memory states as of a given
  timestamp.
- **`snapshot_entity()`** — per-entity evolution tracking across
  snapshots.

### API surface added in 0.5.0

| API | Role |
|-----|------|
| `snapshot` | Create a point-in-time snapshot |
| `snapshot_list` | List snapshots |
| `snapshot_diff` | Diff two snapshots |
| `snapshot_entity` | Per-entity evolution timeline |
| `time_travel` | Query memory as of a past timestamp |

### Tests

277 → **328 passed** (+51 new in `tests/test_v050_snapshots.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Architecture note

0.5.0 cemented the design choice that every subsequent release
inherits: **the filesystem is the database.** Snapshots are plain
Markdown + manifest files, diffable with `git`, portable across
agents, and readable without MemKraft installed.

---

## Pre-0.5 history

0.2 – 0.4 were exploratory releases (Goal-Weighted Reconstructive
Memory, Feedback Loop + Confidence Levels + Applicability Conditions,
Debug Hypothesis Tracking). They are preserved in git history
(`git log --grep='v0\.[234]'`) but are not part of the 1.0 API
contract. 0.5.0 is the first version whose APIs still exist in 1.0.
