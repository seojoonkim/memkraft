# External Benchmark Adapter — Feasibility Spike

**목적:** 3.0 릴리스 노트에 외부 벤치마크 (정확도, p95) 표를 공표하려면(roadmap §3.4, §7.8), 라이선스·데이터 형식·실행 비용이 릴리스 막판 리스크가 되기 전에 검증되어야 한다. 이 spike는 2.13 기간 중 수행하며, 산출물은 이 README + 20문항 smoke adapter다. **이 디렉터리는 spike 산출물이며, 여기서의 수치는 어디에도 공표하지 않는다** — 공표 자격은 3.0 게이트를 통과한 수치만 갖는다.

관련: [refined roadmap](../../docs/plans/2026-07-08-memkraft-v3-fable5-refined-roadmap.md) §10.4, 기존 하네스 `benchmarks/longmemeval/`, `benchmarks/personamem/`

---

## 1. 후보 데이터셋

| 후보 | 형식 | 리포 내 현황 | 라이선스/데이터 리스크 |
|---|---|---|---|
| **LongMemEval** (Wu et al., ICLR 2025) | 멀티세션 대화 + 질문/정답. `longmemeval_s`(~115k tok), `oracle`(정답 세션만) | **이미 있음**: `benchmarks/longmemeval/data/longmemeval_s.json`, `longmemeval_oracle.json` + 자체 하네스(`harness.py`, LLM judge 포함) | 코드/데이터 라이선스 재확인 필요(spike 태스크 S1). 평가에 LLM judge가 필요한 문항 유형(abstention, temporal reasoning)이 있어 **결정적 채점 가능한 서브셋 선별** 필요. 데이터 자체를 wheel/repo에 재배포하지 않고 다운로드 스크립트로 취득하는 형태 검토 |
| **LoCoMo** (Snap Research) | 초장기 멀티세션 대화(평균 300+ 턴) + QA/이벤트 요약 태스크 | 없음 | Snap Research 배포 라이선스 확인 필수 — **비상업/연구 한정 조항 가능성**이 주요 리스크(S1에서 확정). 대화가 LLM 생성 페르소나 기반이라 한국어 커버리지 없음 |
| **PersonaMem** | 페르소나 일관성 장기 대화 | **이미 있음**: `benchmarks/personamem/` 하네스 + 다수 결과 | 기존 하네스가 외부 LLM 백엔드에 결합 — adapter 형식으로 재정리 대상이지 신규 취득 대상 아님 |

**spike 우선순위:** LongMemEval 먼저(데이터가 이미 로컬에 있고 oracle 변형으로 결정적 채점이 상대적으로 쉬움) → LoCoMo는 라이선스 확정 전 데이터 취득 보류.

## 2. 리스크 목록 (spike가 답해야 할 질문)

1. **라이선스 (S1):** 각 데이터셋의 (a) 데이터 재배포 가능 여부, (b) 상업적 사용 조항, (c) 파생 fixture(발췌 20문항) 재배포 가능 여부. **결론이 "재배포 불가"면**: repo에는 다운로드 스크립트 + 체크섬만 커밋하고 fixture는 로컬 생성.
2. **채점기 의존성:** LLM judge 필요 문항은 게이트에 부적합(비결정적, 비용). exact-match/normalized-string-match로 채점 가능한 문항 유형만 게이트 후보. judge 필요 유형은 참고 지표로 격하.
3. **실행 비용:** 20문항 smoke의 실행 시간(콜드/웜)과 필요 디스크. 목표: CI에서 돌릴 수 있게 **smoke 전체 5분 이내, 외부 네트워크 0회**(데이터 사전 취득 후).
4. **형식 안정성:** 데이터셋 버전 고정(체크섬). 업스트림 변경이 게이트를 조용히 바꾸지 않게.

## 3. Smoke adapter 형태 (20문항)

adapter는 "외부 데이터셋 → MemKraft ingest → 질의 → 채점"의 얇은 변환 계층이다. **MemKraft 코어를 데이터셋에 맞추지 않는다** — 맞춰지지 않는 부분이 바로 spike의 발견이다.

```text
benchmarks/external/
  README.md                  # 이 문서
  adapter_base.py            # (spike) 공통 인터페이스
  longmemeval_smoke.py       # (spike) 첫 구현
  fixtures/
    longmemeval_smoke_20.json   # 라이선스 허용 시에만 커밋; 불가 시 생성 스크립트만
```

**공통 인터페이스(계약):**

```python
class ExternalAdapter:
    name: str                # "longmemeval_smoke"
    dataset_version: str     # 체크섬 또는 릴리스 태그

    def load_cases(self) -> list[Case]:
        """Case = {case_id, sessions: [{session_id, turns: [text...]}],
                   question, expected, match: "exact"|"normalized"|"contains"}"""

    def ingest(self, mk, case) -> None:
        """세션 순서대로 remember/remember_candidate 호출. LLM 없음."""

    def answer(self, mk, case) -> str:
        """search(2.13) 또는 compile_context(2.15+) 결과에서 결정적 추출.
        생성 모델 미사용 — 검색이 정답 스팬을 노출하는지만 측정."""

    def score(self, predicted, expected, match) -> bool: ...
```

**20문항 선별 기준:** LongMemEval `oracle`에서 (a) 결정적 채점 가능(single-answer, exact/normalized match), (b) 문항 유형 분포 유지(single-session-user, multi-session, temporal 등 유형별 ≥ 2문항), (c) 고정 시드로 선별·`case_id` 목록을 이 디렉터리에 커밋(재현 가능).

**출력:** Gym 게이트 JSON 규약과 동일 계열 — `{scenario: "external_longmemeval_smoke", dataset_version, thresholds, observed: {accuracy, p50_ms, p95_ms}, per_case: [...], pass}`.

## 4. 지표

| 지표 | 정의 | smoke 단계 목표 |
|---|---|---|
| `accuracy` | score 통과 문항 / 20 | 목표치 없음 — **측정 자체가 산출물.** 게이트 숫자는 spike 결과를 보고 2.15/3.0 계획에서 정한다 |
| `p50_ms` / `p95_ms` | `answer()` 호출 지연(ingest 제외), 콜드 1회 + 웜 20회 | 기록만 |
| `ingest_s` | 케이스당 ingest 시간 | 기록만 (sleep/컴팩션 비용 예측용) |
| 실행 환경 | 머신 스펙, Python 버전, MemKraft 커밋 | 결과 JSON에 필수 기록 |

정확도와 지연은 항상 같은 표에 기록한다(roadmap §2 교훈 1).

## 5. 비-목표

- **리더보드 등재/공표 수치 생산.** smoke는 실행 가능성 검증이지 성능 주장이 아니다.
- **LLM judge 통합.** 결정적 채점 가능 문항만. judge 기반 전체 실행은 기존 `benchmarks/longmemeval/` 하네스의 일이며 이 adapter의 범위 밖.
- **전체 데이터셋 실행.** 20문항 smoke만. 풀 서브셋(50문항, 3.0 게이트)은 spike 결과가 나온 뒤 별도 계획.
- **데이터셋별 튜닝.** adapter에서 MemKraft 파라미터를 데이터셋에 맞게 조정하지 않는다 — 기본값 그대로의 성적이 정보다.
- **한국어 커버리지 해결.** 외부 벤치는 전부 영어다. 한/영 혼합 적대 세트는 내부 fixture(roadmap §7.1)가 담당하고, 여기서 재발명하지 않는다.

## 6. Spike 태스크 (2.13 기간 중, 순서대로)

- **S1:** LongMemEval·LoCoMo 라이선스 원문 확인, 이 README §1 표의 "확인 필요"를 확정 결론으로 교체. 재배포 가능 여부에 따라 fixture 커밋 전략 결정.
- **S2:** `adapter_base.py` 인터페이스 + `longmemeval_smoke.py` 구현 (기존 `benchmarks/longmemeval/data/` 재사용, 신규 다운로드 없음).
- **S3:** 20문항 fixture 선별(고정 시드) + 1회 실행, 결과 JSON을 이 디렉터리에 커밋.
- **S4:** 실행 시간/비용을 §4 표 기준으로 기록하고, 3.0 게이트 문항 수(50)와 임계값 초안을 roadmap 이슈로 제출.

**성공 판정:** S1~S4 완료 + "3.0 외부 벤치 게이트가 현실적인가"에 대한 yes/no/조건부 답. **no라도 spike는 성공이다** — 그 경우 3.0 게이트를 "외부 형식 adapter + 공개 fixture subset + 결과표"로 조정한다(roadmap §10.4).
