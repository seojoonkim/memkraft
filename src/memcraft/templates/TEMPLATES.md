# Compiled Truth + Timeline 페이지 템플릿

모든 memory/entities/ 및 memory/originals/ 페이지는 이 이중층 구조를 따른다.

## 인물 페이지 템플릿

```markdown
# {이름}

## Executive Summary
{한 문단. 어떻게 알게 됐는지, 왜 중요한지}

## State
- **역할:** {현재 직함/역할}
- **소속:** {기업/조직}
- **관계:** {형과의 관계}
- **키 컨텍스트:** {가장 중요한 최근 사실}

## What They Believe
{세계관, 원칙, 고집}

## What They're Building
{현재 프로젝트, 최근 출시, 다음 단계}

## Assessment
{강점, 약점, 종합 판단}

## Trajectory
{상승 / 정체 / 전환 / 하락}

## Open Threads
- [ ] {미해결 이슈} [Source: ...]

## See Also
- [[관련 인물]] | [[관련 기업]] | [[관련 프로젝트]]

---

## Timeline

- **YYYY-MM-DD** | {무슨 일} [Source: {누가}, {채널}, {날짜시간}]
- **YYYY-MM-DD** | {무슨 일} [Source: {누가}, {채널}, {날짜시간}]
```

## 기업 페이지 템플릿

```markdown
# {기업명}

## Executive Summary
{한 문단. 어떤 기업인지, 왤 중요한지}

## State
- **산업:** {산업}
- **단계:** {Seed/Series A/...}
- **핵심 지표:** {매출/사용자 등}

## Assessment
{강점, 약점, 종합 판단}

## Open Threads
- [ ] {미해결 이슈} [Source: ...]

## See Also
- [[관련 인물]] | [[관련 프로젝트]]

---

## Timeline

- **YYYY-MM-DD** | {무슨 일} [Source: {누가}, {채널}, {날짜시간}]
```

## 결정 페이지 템플릿

```markdown
# {결정명}

## Executive Summary
{한 문단. 무엇을 결정했는지}

## Decision
- **결정:** {결정 내용}
- **이유:** {왜 이렇게 결정했는지}
- **대안:** {고려했지만 선택하지 않은 것}
- **만료:** {재검토 필요 시점}

## See Also
- [[관련 항목]]

---

## Timeline

- **YYYY-MM-DD** | 결정 [Source: {누가}, {채널}, {날짜시간}]
```

## Source Attribution 규칙

**모든 팩트에 [Source: ...] 인용 필수.** 이는 제안이 아닌 강제 규칙.

### 형식
`[Source: {누가}, {채널/컨텍스트}, {날짜} {시간} {시간대}]`

### 예시
| 카테고리 | 예시 |
|---------|------|
| 직접 발언 | `[Source: Simon, Telegram DM, 2026-04-11 11:29 KST]` |
| 회의 | `[Source: Meeting "팀미팅", 2026-04-10 14:00 KST]` |
| 트윗 | `[Source: X/@handle, topic, 2026-04-10](https://x.com/handle/status/...)` |
| 이메일 | `[Source: email from 이름, 제목, 2026-04-10 09:30 KST]` |
| API | `[Source: API enrichment, 2026-04-11]` |
| 웹 | `[Source: 웹사이트명, 2026-04-11](https://...)` |

### 출처 계층 (충돌 시)
1. 형 직접 발언 (최고 권위)
2. 1차 소스 (회의, 이메일, 직접 대화)
3. API enrichment
4. 웹 검색 결과
5. 소셜 미디어

충돌 시: 한쪽을 자동 선택하지 말고 양쪽 모두 인용과 함께 모순 명시.
