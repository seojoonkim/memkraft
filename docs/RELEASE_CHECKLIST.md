# MemKraft Release Checklist

모든 릴리스(마이너/패치)는 이 체크리스트를 위에서 아래로 수행한다. 2.12.0 릴리스에서 수동으로 검증했던 절차를 고정한 것이며, 항목을 건너뛰려면 릴리스 노트에 사유를 남긴다. 체크리스트는 **로컬 검증 → 빌드 검증 → 설치 검증 → 배포 검증 → 사후/롤백** 순서다.

관련 문서: [refined roadmap](plans/2026-07-08-memkraft-v3-fable5-refined-roadmap.md) §10.5, [MIGRATIONS.md](MIGRATIONS.md), [THREAT_MODEL.md](THREAT_MODEL.md)

---

## 0. 사전 조건

- [ ] `main` 클린 (`git status` 무변경, 릴리스 대상 커밋이 push됨)
- [ ] `CHANGELOG.md`에 이번 버전 섹션 작성 완료 (마이그레이션 유무 명시 — [MIGRATIONS.md](MIGRATIONS.md) §5)
- [ ] `pyproject.toml`의 `version`이 릴리스 버전과 일치
- [ ] [THREAT_MODEL.md](THREAT_MODEL.md) §2 매트릭스에서 이번 릴리스에 "게이트 그린 필수"인 위협의 게이트가 전부 그린

## 1. 로컬 테스트

```bash
cd /path/to/memkraft
PYTHONPATH=src python3 -m pytest -q
```

- [ ] 전체 테스트 통과 (skip은 허용, fail/error 0건)

## 2. Memory Gym 게이트

```bash
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario search_recall --gate --out /tmp/memkraft-search.json
```

- [ ] `search_recall` 게이트 통과 (종료 코드 0, 출력 JSON의 `pass: true`)
- [ ] 이번 릴리스에서 추가된 시나리오가 있으면 각각 `--gate`로 실행, 전부 통과
- [ ] 게이트 출력 JSON을 `docs/bench/baselines/<version>.json`으로 커밋 (다음 릴리스의 기준점 — roadmap §7.6)
- [ ] 직전 baseline 대비: recall 회귀 0.00, 지연 회귀 +15% 이내

## 3. 빌드와 검사

```bash
rm -rf dist/ build/
python3 -m build
python3 -m twine check dist/*
```

- [ ] sdist + wheel 생성 확인 (`dist/memkraft-<version>.tar.gz`, `dist/memkraft-<version>-py3-none-any.whl`)
- [ ] `twine check` PASSED (README 렌더 포함)
- [ ] wheel 내용 검사: `unzip -l dist/*.whl`로 `templates/`, `stopwords.json`, `templates_pkg/` 등 package-data 포함 확인, 테스트/벤치마크/개인 데이터 미포함 확인

## 4. Fresh wheel smoke (격리 venv)

```bash
rm -rf /tmp/memkraft-smoke /tmp/memkraft-smoke-memory
python3 -m venv /tmp/memkraft-smoke
/tmp/memkraft-smoke/bin/pip install dist/*.whl
/tmp/memkraft-smoke/bin/memkraft --version
/tmp/memkraft-smoke/bin/memkraft doctor --base-dir /tmp/memkraft-smoke-memory
```

- [ ] `--version`이 릴리스 버전 출력
- [ ] `doctor` 통과 (fresh 디렉터리에서)
- [ ] 최소 왕복: smoke venv에서 `remember` → `search`가 방금 저장한 항목을 반환
- [ ] **repo 밖 디렉터리에서 실행** (개발 트리의 `src/`가 우연히 import되지 않음을 보장 — `python3 -c "import memkraft; print(memkraft.__file__)"`가 venv 경로를 가리키는지 확인)
- [ ] 마이그레이션이 포함된 릴리스라면: 이전 버전 fixture 디렉터리에 대해 `doctor --migrations` → `migrate --dry-run` → `migrate --apply` 왕복 수행 ([MIGRATIONS.md](MIGRATIONS.md) §4)

## 5. Hermes provider smoke

Hermes 통합은 **설치된 패키지만으로** 동작해야 한다. `source_path`(개발 트리 경로 주입) 없이 검증한다.

- [ ] `source_path` 설정 없이 installed package import 성공
- [ ] `HERMES_HOME` 아래에서 remember/search smoke 왕복 성공
- [ ] profile-local memory path가 의도한 위치에 생성되는지 확인 (홈 디렉터리 오염 없음)

## 6. 배포

```bash
python3 -m twine upload dist/*
```

- [ ] PyPI 업로드 성공
- [ ] `pip index versions memkraft` 또는 PyPI 웹에서 새 버전 노출 확인 (전파에 수 분 걸릴 수 있음)
- [ ] **PyPI로부터** fresh 설치 재검증:

```bash
rm -rf /tmp/memkraft-pypi-smoke
python3 -m venv /tmp/memkraft-pypi-smoke
/tmp/memkraft-pypi-smoke/bin/pip install memkraft==<version>
/tmp/memkraft-pypi-smoke/bin/memkraft --version
```

- [ ] git tag + GitHub release:

```bash
git tag v<version>
git push origin v<version>
gh release create v<version> --title "MemKraft <version>" --notes-file <릴리스 노트>
```

- [ ] GitHub release 노트가 CHANGELOG 섹션과 일치, 마이그레이션/기지 이슈(known issues) 명시
- [ ] (3.0부터) 릴리스 노트에 외부 벤치마크 (정확도, p95) 표 포함 — roadmap §7.8

## 7. 사후 확인 (배포 후 24h 이내)

- [ ] `pipx install memkraft` 클린 머신/클린 pipx 환경에서 동작 확인
- [ ] GitHub issues / 다운스트림(Hermes) 회귀 보고 모니터링

## 8. Rollback / Yank / Patch 정책

문제가 발견됐을 때의 판단 기준. **PyPI는 파일 교체가 불가능하다** — 같은 버전 재업로드는 없고, 선택지는 yank 또는 patch뿐이다.

### Yank 기준 (즉시)

다음 중 하나면 `twine`이 아닌 PyPI 웹 UI(또는 API)에서 해당 릴리스를 **yank**한다:

- 설치 자체가 실패 (import error, 패키징 누락)
- 데이터 손상/유실을 일으키는 결함 (사용자 `.memkraft/` 또는 markdown 파손)
- 비밀 유출성 결함 (T1 방어 우회로 저장/export에 비밀 노출)

Yank 후 절차: GitHub release에 경고 문구 추가 → 원인 수정 → patch 버전으로 재릴리스(이 체크리스트 전체 재수행). yank는 기존 pin 사용자를 깨지 않으면서 신규 설치만 차단한다 — 삭제(delete)는 쓰지 않는다.

### Patch cut 기준 (yank 없이)

- 기능 회귀지만 데이터 안전에는 무해 → 다음 patch 버전으로 수정. 심각도에 따라 24h(주요 경로 회귀) 또는 다음 정기 릴리스.
- Gym 게이트로 잡혔어야 할 회귀라면: patch에 **해당 회귀의 fixture 추가가 필수** — 같은 구멍으로 두 번 떨어지지 않는다.

### GitHub release 노트 수정 기준

- 코드 재배포가 필요 없는 문서 오류/누락 → release note만 수정하고 수정 이력을 노트 하단에 명시.
- 사용자 행동이 필요한 사실(마이그레이션 필요, known issue) 누락 → 노트 수정 + 저장소 CHANGELOG에도 반영 커밋.

### 사용자 데이터 rollback

- 패키지 rollback(`pip install memkraft==<prev>`)과 데이터 rollback은 별개다. 마이그레이션이 적용된 디렉터리의 복원은 [MIGRATIONS.md](MIGRATIONS.md) §3.3 절차를 따른다.
