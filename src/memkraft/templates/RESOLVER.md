# RESOLVER.md — 메모리 분류 결정 트리

새 정보가 들어올 때 어디에 저장할지 결정하는 규칙.
**모든 새 페이지 생성 전 반드시 이 파일을 먼저 읽을 것.**

## 결정 트리

```
1. 이 정보의 주체는 무엇인가?
   │
   ├─ 사람 → 2a
   ├─ 기업/조직 → 2b
   ├─ 프로젝트/서비스 → 2c
   ├─ 개념/프레임워크 → 2d
   ├─ 형의 고유 사고 → 2e
   ├─ 결정/정책 → 2f
   ├─ 사건/회의 → 2g
   ├─ 일일 기록 → 2h
   └─ 미분류 → memory/inbox/
   
2a. 사람
   ├─ 형(Simon)과 직접 관련 → memory/entities/{slug}.md
   ├─ 5남매 관련 → shared-context/
   └─ 일반 인물 → memory/entities/{slug}.md
   
2b. 기업/조직
   ├─ 형의 포트폴리오/파트너 → memory/entities/{slug}.md
   └─ 일반 → memory/entities/{slug}.md
   
2c. 프로젝트/서비스
   ├─ 활성 프로젝트 → memory/projects.md (요약) + memory/tasks/{project}.md (상세)
   └─ 아카이브 → memory/archive/

2d. 개념/프레임워크
   ├─ 형이 창작 → memory/originals/{slug}.md
   ├─ 세계 개념 → memory/entities/{slug}.md
   └─ 5남매 운영 개념 → compound/skills/

2e. 형의 고유 사고
   └─ memory/originals/{slug}.md (항상 여기)

2f. 결정/정책
   ├─ 중요 결정 → memory/decisions/{slug}.md
   └─ 운영 정책 → compound/policy.json 또는 AGENTS.md

2g. 사건/회의
   ├─ 회의록 → memory/meetings/{date}-{slug}.md
   └─ 일일 기록 → memory/{YYYY-MM-DD}.md

2h. 일일 기록
   └─ memory/{YYYY-MM-DD}.md
```

## 중복 판별 규칙
1. 기존 페이지 검색: `memory_search` → `memory_get`으로 내용 확인
2. 동일 주체 기존 페이지 있으면 → 새로 만들지 말고 기존 것 업데이트
3. 두 디렉토리에 같은 항목이 있으면 → 주 디렉토리에 병합, 다른 쪽은 See Also로 링크

## 모호성 해결
| 충돌 시나리오 | 해결 |
|-------------|------|
| 사람이자 기업 설립자 | people/ 우선, 기업 페이지는 See Also |
| 개념이자 형의 original | original/ 우선, 개념 페이지는 See Also |
| 프로젝트가 기업과 동일 | projects/ 우선, 기업은 entities/에서 See Also |
| 결정이 프로젝트와 관련 | decisions/ 우선, projects/는 See Also |

## inbox 규칙
- 분류 불명확 → `memory/inbox/`에 임시 저장
- inbox 아이템은 48시간 내 분류 완료 (distill 스크립트 또는 수동)
- inbox 누적 = 스키마 업데이트 필요 신호

## MECE 원칙
- 모든 지식은 정확히 하나의 **주 홈** 디렉토리에 속함
- 크로스 레퍼런스는 허용 (See Also), 중복 페이지는 금지
- 디렉토리당 README.md에 "무엇이 들어가는지/아닌지" 명시
