# MemKraft v2.8 Refactor Debug Report — 2026-05-10

> Verifier: Zeon subagent (`memkraft-v28-debug`)
> Branch: `refactor/v2.8-comprehensive`
> Scope: validate WS-A (helper extraction) + WS-B (regex precompile) + WS-C (LRU read cache) + corpus index cache without regression; fix `__version__` mismatch.

---

## 1. 사전 상태

| 항목 | 값 |
|------|----|
| `python -c "import memkraft; print(memkraft.__version__)"` (작업 시작 시) | `2.7.4` |
| `pip show memkraft` Version (egg-info, stale) | `2.7.6` |
| `pyproject.toml` version (작업 시작 시) | `2.7.4` |
| Editable install location | `/Users/gimseojun/memcraft` ✅ |
| Branch | `refactor/v2.8-comprehensive` |
| `core.py` | 3,941 lines |
| Helper modules present | `_core_detection_helpers.py`, `_core_lifecycle_helpers.py`, `_core_search_helpers.py` ✅ |

### 검증한 4 커밋 (HEAD → 31ba … )
```
8bfa7d6 perf(v2.8): corpus index cache for smart_search hot path
aab7775 refactor(v2.8): extract helper modules from core.py (WS-A)
519090f perf(v2.8): bounded LRU file-read cache (WS-C)
39db959 perf(v2.8): precompile hot-path regex (WS-B)
31ab35a test(v2.8): expand search/mutation regression tests (WS-F safety net)  ← v2.7.x 끝선 (회귀망)
```

### 작업 시작 시 `git status`
```
?? IDEAS-PERFORMANCE-2026-05-04.md   (pre-existing untracked, scope 외)
```

### 버전 불일치 진단
- `pyproject.toml` = 2.7.4
- `src/memkraft/__init__.py` = 2.7.4
- `src/memkraft.egg-info/PKG-INFO` = 2.7.6 ← **stale build metadata** (이전 빌드 잔재)
- pip 가 보는 버전(2.7.6)은 egg-info 메타에서 옴. 코드 자체는 2.7.4.

---

## 2. 테스트 실행 결과

### 1차 (수정 전)
```
$ python3 -m pytest tests/
1300 passed, 3 skipped, 36 warnings in 40.92s
```
**0 failed, 0 errors.** WS-A/B/C + corpus_index_cache 4개 커밋이 회귀 없이 통과.

`-x` 옵션으로 stop-on-first 검증도 동일 결과.

### 2차 (버전 2.8.0a1 시도)
```
FAILED tests/test_v102_search.py::TestBackwardCompat::test_version_bumped
ValueError: invalid literal for int() with base 10: '0a1'
1 failed, 1299 passed, 3 skipped
```
**원인:** `test_v102_search.py:245` 가 `__version__.split(".")[:3]` 의 각 요소를 그대로 `int()` 캐스팅. PEP 440 pre-release 표기(`2.8.0a1`)는 토큰이 `["2","8","0a1"]`이라 파싱 실패.
**판단:** 테스트 자체 수정은 task 경계상 ❌ → semantic version 문자열로 fallback (`2.8.0`).

### 3차 (최종, 버전 2.8.0)
```
$ python3 -m pytest tests/
1300 passed, 3 skipped, 36 warnings in 33.85s
```
**0 failed, 0 errors.** 검증 완료.

| 메트릭 | 값 |
|------|---|
| Passed | **1300** |
| Skipped | 3 (모두 `s` 마크 — 환경 의존, 신규 회귀 아님) |
| Failed | **0** |
| Errors | **0** |
| 총 시간 | 33.85s |

---

## 3. 성능 벤치마크 (refactor/v2.8 vs v2.7.5 baseline)

`benchmarks/search_cache_bench.py` 동일 워크로드. baseline = `benchmarks/v2.7.5-bench-result.json` (커밋된 측정치). v2.8 측정치는 `benchmarks/v2.8.0-current-bench-result.json` 으로 저장.

### 워크로드별 mean latency / throughput

| Workload | v2.7.5 mean | v2.8 mean | Δ (ms) | v2.7.5 QPS | v2.8 QPS | Δ (QPS) |
|----------|------------:|----------:|------:|----------:|---------:|--------:|
| repeat_cache_off | 5.692 ms | 5.430 ms | **−4.6%** | 175.7 | 184.2 | **+4.8%** |
| repeat_cache_on | 0.974 ms | 0.938 ms | −3.7% | 1026.9 | 1065.6 | +3.8% |
| mixed_cache_off | 4.456 ms | 4.326 ms | −2.9% | 224.4 | 231.2 | +3.0% |
| mixed_cache_on | 2.857 ms | 2.724 ms | −4.7% | 350.0 | 367.1 | +4.9% |
| smart_repeat_cache_off | 4.676 ms | 4.577 ms | −2.1% | 213.8 | 218.5 | +2.2% |
| smart_repeat_cache_on | 0.876 ms | 0.850 ms | −3.0% | 1141.9 | 1176.3 | +3.0% |
| invalidation | 5.382 ms | 5.330 ms | −1.0% | 185.8 | 187.6 | +1.0% |

### Speedup ratios (cache-on / cache-off)

| 지표 | v2.7.5 | v2.8 |
|------|-------:|-----:|
| repeat_mean_speedup_x | 5.84× | 5.79× |
| repeat_throughput_gain_pct | +484.5% | +478.5% |
| mixed_mean_speedup_x | 1.56× | 1.59× |
| smart_mean_speedup_x | 5.34× | 5.38× |

### 해석
- **모든 워크로드가 v2.7.5 대비 1~5% latency 감소 / QPS 증가.** 회귀는 없음.
- 특히 `repeat_cache_off` 같은 cold path 도 −4.6% — WS-B(regex precompile) + WS-C(file-read LRU) 가 cache miss 경로에서도 작용함을 시사.
- cache-on / cache-off 비율 (speedup_x) 은 거의 동일 — corpus index cache 가 기존 search cache 와 직교적으로 동작하며 hit-rate 패턴을 깨뜨리지 않음.
- `mixed_mean_speedup_x` 만 1.56→1.59 로 약간 개선 (mixed = 50% repeat + 50% varied; varied 쪽 cold cost 가 줄어든 효과로 추정).

> ⚠️ 단일 머신 단일 런 결과 — n=100/워크로드. 5%는 노이즈 범위 안일 수 있어, "회귀 없음" 만 강하게 주장하고 "유의미 개선"은 multi-run + CI 측정으로 재확인 권장.

---

## 4. 발견한 버그 / 이슈

### 4.1. (해결) `__version__` 메타 stale
- 코드 = 2.7.4, egg-info = 2.7.6. pip / `memkraft doctor` 가 다른 버전을 보고하는 원인.
- 해결: 합의된 v2.8 라인으로 단일화 (`2.8.0`).

### 4.2. (해결 — fallback 처리) `test_v102_search.py` 가 PEP 440 prerelease 미지원
- `test_version_bumped` 가 `__version__.split(".")[:3]` → `int()` 캐스팅 → `2.8.0a1` 같은 표준 alpha 표기 거부.
- 본 task 경계로 테스트 파일은 미수정. 대신 release version `2.8.0` 사용.
- **권장**: 다음 정비 사이클에서 테스트를 `packaging.version.Version` 기반으로 교체하면 RC/alpha 워크플로우 살아남.

### 4.3. (해결되지 않음 — 보고만) `IDEAS-PERFORMANCE-2026-05-04.md` 가 untracked
- 본 작업 이전부터 untracked. `IDEAS` 파일이라 .gitignore 또는 의도적 미커밋 가능성. 본 task 스코프 ❌.

### 4.4. (관찰) helper 모듈 호출 경로 동작 정상
- `_core_detection_helpers`, `_core_lifecycle_helpers`, `_core_search_helpers` import 가 1300개 테스트 통과. WS-A 추출이 클린.
- core.py 가 3,941 줄로 유지 — task 설명("3941줄 예상") 과 일치. 아직 큰 모놀리스지만 helper 추출이 의미있는 첫 분리 단계.

---

## 5. 수정한 것

| 파일 | 변경 |
|------|-----|
| `pyproject.toml` | `version = "2.7.4"` → `"2.8.0"` |
| `src/memkraft/__init__.py` | `__version__ = "2.7.4"` → `"2.8.0"` |
| `benchmarks/v2.8.0-current-bench-result.json` | (신규, artifact) v2.8 벤치 결과 저장 |

`git diff --stat` (tracked):
```
 pyproject.toml           | 2 +-
 src/memkraft/__init__.py | 2 +-
 2 files changed, 2 insertions(+), 2 deletions(-)
```

`git status` (전체):
```
 M pyproject.toml
 M src/memkraft/__init__.py
?? IDEAS-PERFORMANCE-2026-05-04.md         (pre-existing, out of scope)
?? benchmarks/v2.8.0-current-bench-result.json  (신규 artifact)
```

> `git push`, PyPI 배포, main 머지 — 모두 ❌ (task 경계 준수).

---

## 6. 권장 다음 액션 (형 결정 필요)

### A. 버전 전략 (즉시 결정 필요)
- 현재 브랜치 `__version__ = "2.8.0"`. 형 의도가 alpha 였으면 `2.8.0a1` 로 다시 내리되, **그 전에 `test_v102_search.py:245` 를 PEP 440 호환으로 패치 필요** (이번 task 에서는 경계 때문에 미수행).
- 옵션 1: 그대로 `2.8.0` (이번 PR 머지 후 정식 릴리스 노선).
- 옵션 2: `2.8.0a1` 로 내리고 `tests/test_v102_search.py` 의 version 파서를 `packaging.version.Version` 으로 교체.

### B. 벤치 신뢰도
- 단일 런 결과라 5% 차이는 노이즈일 수 있음. CI 에서 N=5 run × median 으로 측정하면 v2.8 회귀-없음 주장이 더 단단해짐.
- `benchmarks/v2.7.5-bench-result.json` 처럼 `v2.8.0-bench-result.json` 으로 정식 commit 할지 형 결정.

### C. core.py 모놀리스
- 3,941 줄. 이번 WS-A 가 helper 추출로 첫 분해를 했지만, 추가 모듈 분리(예: `_core_pref.py`, `_core_decay.py` 등) 는 다음 사이클에 별도 위임 추천. 현재 mixin 구조 + helper 모듈이 공존하는 상태라 일관성 있게 정리할 시점.

### D. 배포 시그널
- 1300/1303 테스트 통과 + 회귀 없음 + 성능 동등하거나 약간 개선 → **머지 가능 상태**로 판단. push/PyPI/main 머지는 형 손에 맡김.

### E. IDEAS-PERFORMANCE-2026-05-04.md
- untracked 상태. 형 의도 확인 후 commit 또는 .gitignore 또는 그대로 두기 결정 필요.

---

## 부록: 빠른 재현
```bash
cd /Users/gimseojun/memcraft
git status              # 위 출력 확인
python3 -m pytest tests/ -q                   # 1300 passed, 3 skipped
python3 benchmarks/search_cache_bench.py      # /tmp/v2.7.0-bench-result.json (실제로는 v2.8 측정치)
python3 -c "import memkraft; print(memkraft.__version__)"   # 2.8.0
```
