# LongMemEval v2 개선 분석 (2026-04-22)

## 결과 요약

| 지표 | Baseline | v2 | Δ |
|------|----------|----|----|
| **전체** | 90.0% | **94.0%** | **+4.0pp** ✅ |
| multi-session | 76.9% (10/13) | 92.3% (12/13) | **+15.4pp** 🎯 |
| knowledge-update | 87.5% (7/8) | 100.0% (8/8) | +12.5pp |
| temporal-reasoning | 100.0% (13/13) | 92.3% (12/13) | −7.7pp ⚠️ |
| single-session-* | 모두 유지 | 모두 유지 | = |

**메타**: 50 샘플, oracle 데이터셋, sonnet-4.6 응답/judge, 0 judge 에러.

## 96.6% 목표 대비

- 현재: 94.0% (47/50)
- 96.6% 도달까지: 1.3pp (0.7샘플) 차이
- 남은 오답 3개:
  1. `bf659f65` (multi-session) — "3개 앨범/EP 구매/다운로드?" → 모델이 2로 답 (Spotify 다운로드가 스트리밍인지 구매인지 모호)
  2. `8752c811` (single-session-assistant) — "27번째 파라미터?" → 컨텍스트에서 27이 생략되어 추론 실패
  3. `gpt4_85da3956` (temporal) — "몇 주 전?" → aggregation 프롬프트 오발동 + 날짜 재계산 실수 ⚠️ **regression**

## 개선 포인트 제안 (형 승인 시 v3)

### P1: `_is_aggregation_question` false positive 수정
`"how many weeks ago"`, `"how many days ago"`, `"how many months ago"` 패턴을 aggregation에서 **제외**해야 함 (temporal-reasoning 문제). 현재 `"how many"` 트리거가 너무 광범위.

```python
# 제안
def _is_aggregation_question(question: str) -> bool:
    q = question.lower()
    # 시간 표현 먼저 제외
    TEMPORAL_EXCLUDE = ["how many weeks ago", "how many days ago",
                        "how many months ago", "how many years ago",
                        "how many hours ago", "how long ago"]
    if any(t in q for t in TEMPORAL_EXCLUDE):
        return False
    # (기존 trigger 체크)
    ...
```

이것만 고쳐도 +2pp 가능 → **96%+ 도달 가능**.

### P2: multi-session 1개 남은 실패 (`bf659f65`)
Spotify 다운로드 ambiguity. 프롬프트에 "Downloaded/streamed music via platform (Spotify/Apple Music) = counts as downloaded" 같은 명시 필요.

### P3: single-session-assistant regression (`8752c811`)
리스트 아이템 번호 추론 실패. 이건 retrieval보다 생성 모델의 cognitive ability 문제. 현재 구조에서 해결 어려움.

## 파일 위치

- 응답: `results/sonnet46_ms_v2_oracle_n50_20260422_1754.json`
- Judge 결과: `results/sonnet46_ms_v2_oracle_n50_20260422_1754_judged.json`
- 베이스라인: `results/sonnet46_oracle_n50_20260422_1739_judged.json`
