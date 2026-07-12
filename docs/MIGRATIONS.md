# MemKraft Migrations

이 문서는 릴리스 간 저장소 스키마 마이그레이션의 **단일 기준(source of truth)**이다. 루트의 `MIGRATION.md`(1.0.0 시대 사용자 안내)와 달리, 이 문서는 `.memkraft/` sidecar 스키마와 CLI 계약을 다루는 운영 문서다. 각 릴리스 PR은 이 문서의 마이그레이션 표를 갱신해야 머지할 수 있다.

관련 문서: [2026-07-08 refined roadmap](plans/2026-07-08-memkraft-v3-fable5-refined-roadmap.md) §10.1, [2.13 micro-slices](plans/2026-07-08-memkraft-2.13-micro-slices.md)

---

## 1. 원칙

1. **마이그레이션은 3단계다: dry-run → apply → rollback.** dry-run 없는 apply는 없고, rollback 경로 없는 apply도 없다.
2. **사용자 markdown은 절대 마이그레이션 대상이 아니다.** 마이그레이션은 `.memkraft/` sidecar와 파생 캐시만 만진다. (roadmap §10.6 불변식)
3. **파생 캐시는 마이그레이션하지 않고 버린다.** 재생성 가능한 캐시(예: 2.14의 `compiled_truth.jsonl`)는 버전 간 포맷이 바뀌면 삭제 후 재생성이 기본이다.
4. **자동 암묵 마이그레이션 금지.** 라이브러리 import나 `search` 호출이 조용히 디스크 포맷을 바꾸는 일은 없다. 포맷 변경은 오직 `memkraft migrate --apply`를 통해서만 일어난다. 단, **신규 파일의 lazy 생성**(예: 첫 `remember_candidate`가 `candidates.jsonl`을 만드는 것)은 마이그레이션이 아니라 정상 동작이다.
5. **reader는 한 버전 뒤까지 관용적이다.** 2.13 reader는 envelope 없는 legacy 레코드를 읽을 수 있어야 한다(read-compat N-1). writer는 항상 최신 스키마로만 쓴다.

## 2. 마이그레이션 표

| from_version | to_version | automatic? | command | rollback | data_loss_risk |
|---|---|---|---|---|---|
| 2.12.x | 2.13.0 | no (doctor가 안내) | `memkraft migrate --base-dir <dir> --to 2.13 --apply` | backup dir 복원 | none expected |
| 2.13.x | 3.0 | no (additive/read-compatible) | 파생 뷰 재구축; 별도 schema rewrite 없음 | 2.13 binary + sidecar backup 복원 | preview 데이터만 재생성 필요 |

### 2.1 — 2.12.x → 2.13.0 상세

2.13은 스토리지 계약 v1(레코드 엔벨로프 `{id, schema_version: 1, created_at, tombstone, provenance_id?}`)을 도입하고 신규 sidecar 두 개를 추가한다.

**변경 내용:**

| 대상 | 2.12.x | 2.13.0 | 마이그레이션 동작 |
|---|---|---|---|
| `<base_dir>/.memkraft/candidates.jsonl` | 없음 | envelope v1 | 신규 — 마이그레이션 불필요(lazy 생성) |
| `<base_dir>/.memkraft/last_interactions.jsonl` + 스냅샷 `last_interactions.json` | 없음 | envelope v1 append 로그 + 주기 스냅샷 | 신규 — 마이그레이션 불필요(lazy 생성) |
| 기존 `.memkraft/` 레코드 (provenance 등) | envelope 없음 | envelope 없는 legacy로 read-compat 유지 | **재작성하지 않음.** reader가 legacy를 관용 처리. `migrate`는 `.memkraft/meta.json`에 `storage_schema: 1` 마킹만 수행 |
| 사용자 markdown (`memory/` 등) | — | — | 불변. 마이그레이션이 읽기만 하고 쓰지 않음 |

즉 2.12→2.13은 **구조적으로 additive**다. `migrate --apply`의 실제 작업은 (a) 디렉터리 구조 검증, (b) `.memkraft/meta.json`에 `storage_schema` 스탬프, (c) corrupt line 사전 검출 보고이며, 레코드 재작성은 없다. 그럼에도 dry-run/backup 규약은 동일하게 적용한다 — 이번에 규약을 세워야 2.14(파생 뷰)에서 진짜로 필요할 때 이미 검증된 경로가 있다.

### 2.2 — 2.13.x → 3.0

3.0은 2.13 envelope v1을 유지하는 additive 전환이다. 먼저 `.memkraft/`를 백업하고 기존 2.13 reader/search smoke를 기록한다. import/search가 자동 마이그레이션을 하지 않으며 전용 3.0 schema rewrite도 없다.

- store/event: 기존 markdown과 2.13 sidecar는 그대로 읽힌다. 신규 canonical 원본은 `.memkraft/events.jsonl`에 source/provenance와 함께 append된다.
- truth/sleep: `.memkraft/compiled_truth.jsonl`은 파생 캐시이므로 삭제 후 `compile_truth(dry_run=False)` 또는 `sleep(dry_run=False)`로 재구축한다. sleep journal은 append-only이며 재실행은 멱등이다.
- context/outcome: `compile_context` usage와 `report_outcome` 레코드는 preview sidecar다. 기존 기억의 의미를 바꾸지 않으며 2.13은 이를 무시할 수 있다.
- API compatibility: `append_event`, `compile_truth`, `current_truth`, `sleep`, `forget`, `compile_context`, `report_outcome`가 3.0 stable 이름이다. legacy alias는 경고 후 전달되며 3.x에서 제거되지 않는다.
- preview caveats: candidate/resolver/session/context/outcome의 additive 필드와 ranking은 minor release에서 조정될 수 있다. 이를 영속 비즈니스 스키마로 복제하지 않는다.

**검증:** 백업 → 3.0 설치 → `compile_truth` dry-run → lifecycle Gym gate → 기존 search smoke 순으로 수행한다. source 없는 event가 있으면 apply하지 말고 입력을 수정한다.

**rollback:** 프로세스를 중지하고 3.0에서 쓴 `.memkraft/`를 감사용으로 보존한 뒤 사전 백업을 복원하고 2.13.x를 재설치한다. 2.13은 신규 preview 파일을 무시하지만, 3.0에서 추가한 canonical event를 유지해야 한다면 downgrade 전에 JSONL을 별도 export한다. 사용자 markdown은 복원 대상이 아니다. `compiled_truth.jsonl`, context usage, outcomes 같은 파생/preview 파일은 삭제해도 canonical event가 손실되지 않는다.

## 3. CLI 계약

### 3.1 `memkraft doctor --migrations`

```bash
memkraft doctor --base-dir <dir> --migrations
```

- 기존 `doctor`(설치·구조 헬스체크, `src/memkraft/cli.py`의 서브커맨드)에 `--migrations` 플래그를 추가한다.
- **읽기 전용.** 파일시스템 변경 0바이트를 보장한다 (`doctor --fix`와 조합 불가 — 조합 시 구조화 에러).
- 출력: 현재 감지된 `storage_schema` 버전, 설치된 패키지 버전, 필요한 마이그레이션 목록, 권장 커맨드 한 줄.
- 종료 코드: `0` = 마이그레이션 불필요, `2` = 마이그레이션 필요(pending), `1` = 검사 자체 실패.

### 3.2 `memkraft migrate`

```bash
memkraft migrate --base-dir <dir> --to 2.13 --dry-run
memkraft migrate --base-dir <dir> --to 2.13 --apply
memkraft migrate --base-dir <dir> --to 2.13 --apply --no-backup
```

- `--dry-run`과 `--apply`는 상호 배타이며 **둘 중 하나는 필수**(기본값 없음 — 무인자 실행은 usage 에러).
- `--dry-run`: 수행할 작업 계획을 JSON으로 stdout에 출력. 파일시스템 변경 0바이트.
- `--apply`: 적용 전 `<base_dir>/.memkraft-backup-<from>-<to>-<UTC timestamp>/`에 `.memkraft/` 전체를 복사한다. 백업 생략은 명시적 `--no-backup`으로만 가능.
- `--to`는 마이그레이션 표에 있는 타깃만 수용. 알 수 없는 타깃은 구조화 에러 + 비제로 종료.
- 이미 타깃 스키마인 디렉터리에 대한 `--apply`는 no-op 성공(멱등).
- 출력 JSON 스키마(gym 게이트 출력 규약과 동일 계열):

```json
{
  "from_schema": 0,
  "to_schema": 1,
  "mode": "dry_run",
  "planned_actions": [{"kind": "stamp_meta", "path": ".memkraft/meta.json"}],
  "backup_dir": null,
  "warnings": []
}
```

### 3.3 Rollback

```bash
# 자동화된 rollback 커맨드는 2.13 범위 밖. 절차는 수동이며 문서화된 계약이다:
rm -rf <base_dir>/.memkraft
cp -R <base_dir>/.memkraft-backup-2.12-2.13-<ts> <base_dir>/.memkraft
pipx install memkraft==2.12.0   # 또는 pip install memkraft==2.12.0
```

- 백업 디렉터리는 사용자가 명시적으로 삭제하기 전까지 `migrate`가 건드리지 않는다.
- 롤백 후 2.12.x 바이너리로 `doctor`가 통과해야 한다 — 이것이 §4의 테스트 게이트에 포함된다.

## 4. 테스트 게이트

2.13 릴리스 전 아래 전부가 자동 테스트로 존재하고 그린이어야 한다.

| 게이트 | 임계값 |
|---|---|
| dry-run 무변경 | 2.12 fixture 디렉터리에서 `migrate --dry-run` 전후 트리 해시 동일 (변경 0바이트) |
| apply 무회귀 | `migrate --apply` 후 동일 쿼리 세트에 대한 `search` 결과가 2.12 fixture 기준과 동일 (recall 회귀 0.00) |
| 백업 강제 | `--apply`는 백업 디렉터리 생성 또는 명시적 `--no-backup` 없이는 실패; 백업 내용 == 적용 전 `.memkraft/` (해시 비교) |
| 멱등성 | `--apply` 2회 연속 실행 시 2회차는 no-op, 파일 변경 0바이트 |
| 롤백 왕복 | apply → 백업 복원 → 2.12 reader로 읽기 성공 + `search` 결과 원상 |
| doctor 정합 | 마이그레이션 전 `doctor --migrations` 종료 코드 2, 적용 후 0 |
| corrupt 입력 | corrupt line이 섞인 fixture에서 dry-run이 해당 라인을 `warnings`로 보고하고 비파괴적으로 완료 |

## 5. 릴리스 절차 연동

- 마이그레이션이 포함된 릴리스는 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)의 wheel smoke 단계에서 **2.12 fixture 디렉터리에 대한 doctor/migrate 왕복**을 추가로 수행한다.
- 이 표에 행이 없는 버전 조합은 "마이그레이션 없음(additive only)"을 의미하며, CHANGELOG에 그 사실을 명시한다.
