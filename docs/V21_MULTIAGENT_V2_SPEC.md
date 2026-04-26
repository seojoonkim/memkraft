# MemKraft v2.1 — Multi-Agent Shared Memory v2 Specification

**Branch:** `feat/v2.1-roadmap`  
**Status:** DRAFT  
**Author:** Zeon (sub-agent, 2026-04-26)  
**Reviewed-by:** (pending)  
**Base version:** MemKraft 2.0.0  
**Target version:** 2.1.0  

---

## 목차

1. [동기 (Why This Matters)](#1-동기)
2. [v0.7~v2.0 한계 분석](#2-v07v20-한계-분석)
3. [공유 모델 4가지 비교](#3-공유-모델-4가지-비교)
4. [추천 아키텍처](#4-추천-아키텍처)
5. [API 설계](#5-api-설계)
6. [Conflict Resolution](#6-conflict-resolution)
7. [Storage 레이아웃](#7-storage-레이아웃)
8. [5남매 실사용 시나리오 3개](#8-5남매-실사용-시나리오-3개)
9. [Migration 가이드](#9-migration-가이드)
10. [보안 & 접근 제어](#10-보안--접근-제어)
11. [구현 로드맵](#11-구현-로드맵)

---

## 1. 동기

### 현재 상황

MemKraft 2.0.0은 **단일 에이전트** 전제로 설계됨:

```
MemKraft(base_dir="/Users/gimseojun/сlawd/memory")
    → 단일 파일시스템 루트
    → 단일 에이전트 관점
    → 에이전트 간 메모리 경계 없음
```

5남매 운영 환경(제온/시온/미온/사노/+1)에서 이 구조는 다음 문제를 야기:

1. **에이전트 A가 쓴 메모리를 에이전트 B가 오염**  
   → 예: 시온이 "Simon은 투자자" 업데이트 → 제온이 "Simon = 투자자"로 읽어 오판
   
2. **동시 쓰기 시 파일 충돌** (no locking)  
   → 두 에이전트가 같은 entity 파일을 동시에 write → last-write-wins, 데이터 소실
   
3. **어떤 에이전트가 무엇을 썼는지 audit 불가**  
   → 잘못된 메모리가 생겼을 때 추적 불가능

### 목표

- **격리성**: 에이전트별 private namespace
- **공유성**: 선택적으로 공유 가능한 공유 namespace
- **일관성**: Conflict를 bitemporal 기록으로 보존
- **감사 가능성**: 모든 쓰기에 agent_id + timestamp 기록
- **하위 호환성**: 기존 `MemKraft(base_dir=...)` 코드 변경 불필요

---

## 2. v0.7~v2.0 한계 분석

### 한계 시나리오 1: 동시 Entity 업데이트 — 데이터 소실

```python
# 제온이 Simon 엔티티 업데이트 (t=0)
zeon_mk = MemKraft("/Users/gimseojun/сlawd/memory")
zeon_mk.update("Simon", "Hashed CEO, blockchain expert", source="DM")

# 시온이 같은 파일 동시 업데이트 (t=0.05s)
sion_mk = MemKraft("/Users/gimseojun/сlawd/memory")
sion_mk.update("Simon", "투자자 미팅 완료", source="groupchat")

# 결과: 제온 업데이트 소실 또는 시온 업데이트 소실
# v2.0은 파일 쓰기에 atomic lock 없음
```

**영향도**: HIGH — 비트템포럴 기록조차 손실될 수 있음

### 한계 시나리오 2: 에이전트 메모리 오염 — 역할 혼선

```python
# 미온(디자인 담당)이 개발 관련 메모리를 공유 영역에 작성
mion_mk = MemKraft("/Users/gimseojun/сlawd/memory")
mion_mk.track("VibeKai", entity_type="project")
mion_mk.update("VibeKai", "디자인 시스템 완료, 색상: #3B82F6", source="design-review")

# 제온(개발 담당)이 같은 엔티티 조회
zeon_mk = MemKraft("/Users/gimseojun/сlawd/memory")
results = zeon_mk.search("VibeKai deployment status")
# → 디자인 메모리가 개발 컨텍스트에 혼입, 관련없는 결과 오염
```

**영향도**: MEDIUM — 검색 품질 저하, 에이전트 판단 오염

### 한계 시나리오 3: Audit 불가 — 잘못된 메모리 소스 추적 불가

```python
# 어느 에이전트가 이 사실을 썼는지 알 수 없음
results = mk.search("Simon role")
# → "Simon = 투자자 (Source: DM)"
# → 어떤 에이전트가 언제? 알 수 없음 → rollback 불가
```

**현재 코드 한계:**
```python
# core.py:3353 — agent_save는 에이전트 working memory만 저장
def agent_save(self, agent_id: str, working_memory: Dict[str, Any]) -> Path:
    # entity 파일에는 agent_id가 기록되지 않음
    # 오직 Source 문자열만 있고, 이는 자유형식
```

**영향도**: HIGH — 잘못된 사실이 생기면 디버깅 불가, 신뢰 문제

### 추가 한계: channel 공유 시 에이전트 scope 없음

```python
# v0.7 channel은 단순 key-value 저장 → 누가 읽을 수 있는지 제어 불가
mk.channel_save("dm-simon", {"summary": "..."})
# → 모든 에이전트가 읽기/쓰기 가능
# → read-only 에이전트 개념 없음
```

---

## 3. 공유 모델 4가지 비교

| 항목 | **A) Shared base_dir + namespace** | **B) 각자 base_dir + sync** | **C) Central server** | **D) Read-only mounts** |
|------|-----------------------------------|-----------------------------|----------------------|------------------------|
| **구조** | 단일 디렉토리 + `ns/` 서브폴더 | 에이전트별 독립 디렉토리 | SQLite/Redis 서버 | symlink 또는 하드링크 |
| **격리** | ✅ namespace로 논리 격리 | ✅ 물리 격리 | ✅ row-level 격리 | ⚠️ 읽기만 격리 |
| **공유** | ✅ `shared/` 폴더 | ⚠️ sync 필요 (rsync/watchdog) | ✅ 쿼리로 공유 | ❌ 공유 쓰기 불가 |
| **동시성** | ⚠️ filelock 필요 | ✅ 독립 쓰기 | ✅ DB 트랜잭션 | ✅ 읽기 전용 |
| **audit** | ✅ namespace + 메타 | ⚠️ sync log 필요 | ✅ DB row 기록 | ❌ 추적 어려움 |
| **하위 호환** | ✅ base_dir 동일 | ❌ 경로 재설정 필요 | ❌ API 대규모 변경 | ⚠️ 제한적 |
| **복잡도** | 낮음 | 중간 | 높음 | 낮음 (기능 제한) |
| **zero-dependency 유지** | ✅ | ✅ | ❌ (서버 필요) | ✅ |
| **bitemporal 호환** | ✅ | ⚠️ sync 시 timestamp 충돌 | ✅ | ⚠️ |
| **적합 환경** | 로컬 단일 머신 | 분산 머신 | 팀 서버 환경 | 읽기 전용 참조 |

**MemKraft 철학 (zero-dependency, 파일시스템 기반)과 5남매 운영 환경(단일 Mac mini) 기준으로 평가.**

---

## 4. 추천 아키텍처

### ✅ 모델 A: Shared base_dir + Namespace 계층

**선택 이유:**

1. **zero-dependency 유지**: 외부 서버/DB 없이 파일시스템만 사용
2. **기존 코드 하위 호환**: `MemKraft(base_dir=...)` 시그니처 변경 없음
3. **단일 머신 최적**: 5남매는 같은 Mac mini에서 실행 — 네트워크 sync 불필요
4. **bitemporal과 자연스럽게 연동**: 파일 기반 메타데이터에 `agent_id` 추가만으로 구현
5. **점진적 도입**: `namespace="shared"` 기본값 → 기존 코드 0 변경

### 물리 구조

```
/Users/gimseojun/сlawd/memory/           ← base_dir (변경 없음)
├── .memkraft/
│   ├── agents/                           ← 기존 agent working memory
│   ├── channels/
│   ├── tasks/
│   └── namespaces/                       ← 🆕 v2.1 추가
│       ├── _registry.json                ← namespace 목록 + 권한
│       └── audit.jsonl                   ← 전체 쓰기 audit log
├── entities/                             ← 기존 (shared namespace 기본)
├── ns/                                   ← 🆕 namespace 루트
│   ├── zeon/                             ← 제온 private namespace
│   │   ├── entities/
│   │   ├── facts/
│   │   └── daily/
│   ├── sion/                             ← 시온 private namespace
│   │   ├── entities/
│   │   └── facts/
│   ├── mion/
│   ├── sano/
│   └── shared/                           ← 공유 namespace (읽기/쓰기)
│       ├── entities/
│       ├── facts/
│       └── channels/
```

### 핵심 원칙

```
private write  → ns/{agent_id}/
shared write   → ns/shared/ (권한 있는 에이전트만)
legacy read    → base_dir/ (기존 파일, 항상 readable)
legacy write   → ns/shared/ 로 리다이렉트 (default)
```

---

## 5. API 설계

### 5.1 NamespacedMemKraft — 기본 클래스 확장

```python
from memkraft import MemKraft

class NamespacedMemKraft(MemKraft):
    """
    v2.1 Multi-Agent Shared Memory 확장.
    
    기존 MemKraft를 상속, namespace 계층 추가.
    하위 호환: namespace 미지정 시 "shared" 기본값 (기존 동작 유지)
    """
    
    def __init__(
        self,
        base_dir: str,
        agent_id: str,                       # 🆕 필수: 에이전트 식별자
        namespace: str = "shared",            # 🆕 기본 namespace
        read_namespaces: list[str] = None,    # 🆕 읽기 가능한 추가 namespace 목록
    ) -> None:
        """
        Args:
            base_dir: 기존 메모리 루트 경로 (변경 없음)
            agent_id: 에이전트 식별자 ("zeon", "sion", "mion", "sano")
            namespace: 기본 쓰기 namespace. "private" → ns/{agent_id}/
            read_namespaces: 추가로 읽을 namespace 목록 (기본: ["shared", "private"])
        
        Examples:
            # 제온 - 개인 namespace에 쓰고, shared도 읽기
            mk = NamespacedMemKraft(
                base_dir="/Users/gimseojun/сlawd/memory",
                agent_id="zeon",
                namespace="private",
                read_namespaces=["shared"]
            )
            
            # 기존 코드 하위 호환 (namespace="shared", 기존 동작)
            mk = MemKraft(base_dir="/Users/gimseojun/сlawd/memory")
        """
        super().__init__(base_dir)
        self.agent_id = agent_id
        self.namespace = namespace
        self.read_namespaces = read_namespaces or ["shared", "private"]
        self._ns_root = Path(base_dir) / "ns"
        self._audit_log = Path(base_dir) / ".memkraft" / "namespaces" / "audit.jsonl"
        self._ensure_namespace_dirs()
```

### 5.2 `namespace_create` — namespace 생성 및 권한 설정

```python
def namespace_create(
    self,
    name: str,
    access: dict[str, str] = None,
    description: str = ""
) -> dict:
    """
    새 namespace를 생성하고 권한 레지스트리에 등록.
    
    Args:
        name: namespace 이름 ("zeon", "shared", "project-x")
        access: 에이전트별 권한 맵 {"zeon": "rw", "sion": "r", "*": "r"}
                - "rw": 읽기+쓰기
                - "r": 읽기만
                - None/없음: 접근 불가
        description: namespace 설명
    
    Returns:
        {"name": str, "path": str, "access": dict, "created_at": str}
    
    Examples:
        # 공유 프로젝트 namespace
        mk.namespace_create(
            "project-vibekai",
            access={"zeon": "rw", "sion": "rw", "*": "r"},
            description="VibeKai 프로젝트 공유 메모리"
        )
        
        # 제온 전용 private namespace
        mk.namespace_create(
            "zeon-private",
            access={"zeon": "rw"},
            description="제온 개인 메모리"
        )
    
    Raises:
        NamespaceExistsError: 이미 존재하는 namespace
        PermissionError: namespace_create 권한 없는 에이전트
    """
```

### 5.3 `namespace_share` — 메모리를 다른 namespace로 공유

```python
def namespace_share(
    self,
    entity_name: str,
    source_namespace: str,
    target_namespace: str,
    mode: str = "link",           # "link" | "copy" | "sync"
    read_only: bool = True,
    valid_until: str = None,      # ISO datetime, 없으면 영구
    shared_by: str = None         # 기본: self.agent_id
) -> dict:
    """
    특정 엔티티를 다른 namespace와 공유.
    
    Args:
        entity_name: 공유할 엔티티 이름 (예: "Simon", "VibeKai")
        source_namespace: 원본 namespace
        target_namespace: 공유 대상 namespace
        mode:
            - "link": symlink (동기화됨, 원본 변경 즉시 반영)
            - "copy": 독립 복사본 (스냅샷)
            - "sync": 양방향 동기화 (conflict resolution 활성화)
        read_only: 대상 namespace에서 쓰기 금지 여부
        valid_until: 공유 만료 시간 (None = 영구)
        shared_by: 공유 주체 에이전트 ID
    
    Returns:
        {
            "entity": str,
            "source": str,
            "target": str,
            "mode": str,
            "link_path": str,
            "shared_at": str,
            "shared_by": str,
            "valid_until": str | None
        }
    
    Examples:
        # 제온이 Simon 엔티티를 shared namespace에 링크
        mk.namespace_share(
            "Simon",
            source_namespace="zeon",
            target_namespace="shared",
            mode="link",
            read_only=False
        )
        
        # 특정 기간만 공유
        mk.namespace_share(
            "VibeKai",
            source_namespace="zeon",
            target_namespace="sion",
            mode="copy",
            valid_until="2026-05-01T00:00:00"
        )
    """
```

### 5.4 `namespace_sync` — namespace 간 동기화

```python
def namespace_sync(
    self,
    source_namespace: str,
    target_namespace: str,
    entities: list[str] = None,   # None = 전체
    conflict_strategy: str = "bitemporal",  # "bitemporal" | "newest" | "source-wins"
    dry_run: bool = False
) -> dict:
    """
    두 namespace 간 메모리 동기화.
    
    conflict_strategy:
        - "bitemporal": 두 버전 모두 보존 (bitemporal 타임라인에 기록)
        - "newest": 최신 timestamp 승리
        - "source-wins": source_namespace 우선
    
    Args:
        source_namespace: 동기화 소스
        target_namespace: 동기화 대상
        entities: 동기화할 엔티티 목록 (None = 전체)
        conflict_strategy: 충돌 해결 전략
        dry_run: 실제 쓰기 없이 충돌 미리 보기
    
    Returns:
        {
            "synced": int,           # 동기화된 엔티티 수
            "conflicts": list[dict], # 감지된 충돌 목록
            "skipped": int,          # 권한 없어 건너뜀
            "dry_run": bool
        }
    
    Examples:
        # shared → zeon 동기화 (bitemporal 보존)
        result = mk.namespace_sync(
            "shared", "zeon",
            conflict_strategy="bitemporal",
            dry_run=True
        )
        print(f"충돌 {len(result['conflicts'])}건 예상")
    """
```

### 5.5 `namespace_audit` — 쓰기 감사 로그 조회

```python
def namespace_audit(
    self,
    namespace: str = None,        # None = 전체
    agent_id: str = None,         # 특정 에이전트 필터
    entity_name: str = None,      # 특정 엔티티 필터
    from_dt: str = None,          # ISO datetime
    to_dt: str = None,
    limit: int = 50,
    action: str = None            # "write" | "delete" | "share" | "sync"
) -> list[dict]:
    """
    namespace 쓰기 감사 로그 조회.
    
    모든 쓰기 작업은 audit.jsonl에 자동 기록됨:
    {
        "ts": "2026-04-26T11:17:00+09:00",
        "agent_id": "zeon",
        "namespace": "shared",
        "action": "write",
        "entity": "Simon",
        "fact": "Hashed CEO confirmed in press release",
        "source": "web_fetch",
        "session_id": "agent:zeon:telegram:..."
    }
    
    Returns:
        list of audit entries (최신순)
    
    Examples:
        # 제온이 shared에 쓴 모든 기록
        logs = mk.namespace_audit(
            namespace="shared",
            agent_id="zeon",
            limit=20
        )
        
        # Simon 엔티티 관련 모든 변경
        logs = mk.namespace_audit(entity_name="Simon")
        
        # 특정 시간대 감사
        logs = mk.namespace_audit(
            from_dt="2026-04-25T00:00:00",
            to_dt="2026-04-26T00:00:00"
        )
    """
```

### 5.6 `namespace_list` — namespace 목록 조회

```python
def namespace_list(
    self,
    include_stats: bool = False
) -> list[dict]:
    """
    현재 에이전트가 접근 가능한 namespace 목록.
    
    Returns:
        [
            {
                "name": "zeon",
                "access": "rw",
                "entity_count": 42,
                "last_write": "2026-04-26T11:00:00",
                "size_kb": 128
            },
            ...
        ]
    """
```

### 5.7 기존 API에 agent_id 투명 주입

기존 `update()`, `track()`, `fact_add()` 등 모든 쓰기 API는 namespace 컨텍스트를 자동으로 반영:

```python
# 기존 코드 (변경 없음)
mk.update("Simon", "Hashed CEO", source="DM")

# 내부 동작 (v2.1에서 자동)
# → 현재 namespace(ns/{agent_id}/ 또는 ns/shared/)에 쓰기
# → audit.jsonl에 {agent_id, namespace, action, entity, ts} 기록
# → filelock 획득 후 원자적 쓰기
```

---

## 6. Conflict Resolution

### 6.1 Bitemporal 기반 Conflict 보존 원칙

MemKraft 2.0의 bitemporal 레이어를 namespace conflict에 확장:

```
비트템포럴 2축:
  - valid_time: 사실이 실세계에서 참인 기간
  - transaction_time: DB(파일)에 기록된 기간

Conflict: 같은 entity+attribute에 대해
  동일 valid_time 구간에 서로 다른 값이 존재
```

### 6.2 Conflict 감지 — 3단계 파이프라인

```
Stage 1: Hash 비교 (빠름)
  → 두 namespace의 entity 파일 hash 비교
  → 동일 → no conflict
  → 다름 → Stage 2

Stage 2: Fact-level diff
  → 각 bullet fact를 파싱
  → 의미론적 대립 감지 (_is_opposing 재사용)
  → 대립 없음 → merge 가능
  → 대립 있음 → Stage 3

Stage 3: Bitemporal 기록
  → 두 fact 모두 보존
  → [CONFLICT:agent_a vs agent_b] 태그
  → audit.jsonl에 conflict 이벤트 기록
  → CONFLICTS.md 업데이트
```

### 6.3 Conflict Resolution 전략

```python
# 전략 1: bitemporal (기본, 권장)
# → 두 버전 모두 보존, 나중에 human/dream이 해결
{
    "valid_from": "2026-04-01",
    "value": "Hashed CEO",
    "agent": "zeon",
    "conflict_with": {
        "value": "ex-Hashed CEO",
        "agent": "sion",
        "valid_from": "2026-04-25"
    }
}

# 전략 2: newest (자동 해결, 빠름)
# → transaction_time 기준 최신 값 채택
# → 오래된 값은 archive로 이동

# 전략 3: agent-priority (설정 기반)
# → namespace_create() 시 priority 설정
mk.namespace_create("shared", priority={"zeon": 1, "sion": 2})
# → priority 낮은 값이 이김 (1 > 2)
```

### 6.4 Filelock — 동시 쓰기 방지

```python
# 구현: Python 표준 라이브러리 기반 (zero-dependency 유지)
import fcntl
import tempfile

class _FileLock:
    """
    POSIX fcntl 기반 advisory lock.
    zero-dependency: fcntl은 Python 표준 라이브러리.
    Windows: msvcrt.locking 폴백.
    """
    
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd = None
    
    def __enter__(self):
        self._fd = open(self.lock_path, "w")
        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self
    
    def __exit__(self, *args):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()

# 사용 예
with _FileLock(entity_path.with_suffix(".lock")):
    content = entity_path.read_text()
    # ... modify ...
    entity_path.write_text(new_content)
```

### 6.5 Audit Log 포맷

```jsonl
{"ts":"2026-04-26T11:17:00+09:00","agent_id":"zeon","namespace":"shared","action":"write","entity":"Simon","fact":"Hashed CEO confirmed","source":"DM","session":"agent:zeon:telegram:46291309","conflict":false}
{"ts":"2026-04-26T11:18:00+09:00","agent_id":"sion","namespace":"shared","action":"write","entity":"Simon","fact":"ex-Hashed CEO rumor","source":"web","session":"agent:sion:groupchat","conflict":true,"conflict_with":"zeon@2026-04-26T11:17:00"}
{"ts":"2026-04-26T11:19:00+09:00","agent_id":"zeon","namespace":"zeon","action":"share","entity":"Simon","target_ns":"shared","mode":"link","session":"agent:zeon:telegram:46291309","conflict":false}
```

---

## 7. Storage 레이아웃

### 7.1 전체 디렉토리 구조

```
{base_dir}/
├── .memkraft/
│   ├── agents/                          ← 기존: agent working memory JSON
│   │   ├── zeon.json
│   │   └── sion.json
│   ├── channels/                        ← 기존: channel context JSON
│   ├── tasks/                           ← 기존: task records
│   │   └── archive/
│   └── namespaces/                      ← 🆕 v2.1
│       ├── _registry.json               ← namespace 메타 + 권한
│       ├── audit.jsonl                  ← 전체 쓰기 감사 로그
│       └── locks/                       ← 임시 lock 파일
│           ├── zeon__Simon.lock
│           └── shared__VibeKai.lock
│
├── ns/                                  ← 🆕 namespace 루트
│   ├── zeon/                            ← 제온 private namespace
│   │   ├── entities/
│   │   │   └── simon.md
│   │   ├── facts/
│   │   └── daily/
│   ├── sion/                            ← 시온 private namespace
│   │   └── entities/
│   ├── mion/
│   ├── sano/
│   └── shared/                          ← 공유 namespace
│       ├── entities/
│       │   └── simon.md                 ← 공유된 Simon 엔티티
│       ├── facts/
│       └── channels/
│
├── entities/                            ← 기존 (legacy, 하위 호환)
├── facts/
├── daily/
└── CONFLICTS.md                         ← 기존 + namespace 정보 추가
```

### 7.2 `_registry.json` 포맷

```json
{
  "version": "2.1.0",
  "namespaces": {
    "shared": {
      "description": "5남매 공유 메모리",
      "access": {"*": "rw"},
      "priority": {},
      "created_at": "2026-04-26T11:00:00+09:00",
      "created_by": "zeon"
    },
    "zeon": {
      "description": "제온 private namespace",
      "access": {"zeon": "rw", "shared": "r"},
      "priority": {"zeon": 1},
      "created_at": "2026-04-26T11:00:00+09:00",
      "created_by": "zeon"
    },
    "sion": {
      "description": "시온 private namespace",
      "access": {"sion": "rw"},
      "priority": {"sion": 1},
      "created_at": "2026-04-26T11:00:00+09:00",
      "created_by": "sion"
    }
  },
  "shares": [
    {
      "entity": "Simon",
      "source_ns": "zeon",
      "target_ns": "shared",
      "mode": "link",
      "read_only": false,
      "shared_at": "2026-04-26T11:10:00+09:00",
      "shared_by": "zeon",
      "valid_until": null
    }
  ]
}
```

### 7.3 Entity 파일 헤더 — agent 메타 추가

```markdown
---
name: Simon
entity_type: person
namespace: shared
last_updated: 2026-04-26T11:17:00+09:00
last_updated_by: zeon            ← 🆕
sources: [DM, web_fetch]
tier: core
---

# Simon

## Core Facts
- Hashed CEO (2020~현재) [Source: press | Agent: zeon | 2026-04-26]
- 범우주적 아이디어 프로젝트 창시자 [Source: DM | Agent: zeon | 2026-04-20]

## Timeline
...
```

---

## 8. 5남매 실사용 시나리오 3개

### 시나리오 1: 5남매 단톡방 — Simon 인물 메모리 충돌 방지

**배경**: Simon이 5남매 단톡방에서 새로운 투자 건을 언급. 제온과 시온이 동시에 Simon 엔티티를 업데이트하려 함.

```python
# 제온 (개발/인프라 담당)
zeon_mk = NamespacedMemKraft(
    base_dir="/Users/gimseojun/сlawd/memory",
    agent_id="zeon",
    namespace="private",
    read_namespaces=["shared"]
)

# Simon 엔티티를 private에 업데이트
zeon_mk.update("Simon", 
    "Hashed, 새 DeFi 프로토콜 투자 검토 중 (2026-04-26 단톡방 언급)", 
    source="groupchat"
)

# 필요시 shared로 promote
zeon_mk.namespace_share("Simon", "zeon", "shared", mode="link")

# -----------------------------------------------

# 시온 (콘텐츠/글쓰기 담당) - 동시에 같은 엔티티 업데이트
sion_mk = NamespacedMemKraft(
    base_dir="/Users/gimseojun/сlawd/memory",
    agent_id="sion",
    namespace="private"
)

# 시온은 자신의 private에 씀 → 충돌 없음
sion_mk.update("Simon",
    "Simon이 NFT 아트 콜라보 가능성 언급 (2026-04-26 단톡방)",
    source="groupchat"
)

# -----------------------------------------------

# 나중에 shared 동기화 — bitemporal으로 두 사실 모두 보존
result = zeon_mk.namespace_sync(
    "zeon", "shared",
    conflict_strategy="bitemporal"
)

result = sion_mk.namespace_sync(
    "sion", "shared",
    conflict_strategy="bitemporal"
)
# → shared/entities/simon.md에 두 fact 모두 기록, 출처(agent) 명시
# → 충돌 시 [CONFLICT:zeon vs sion] 태그 + CONFLICTS.md 업데이트
```

**효과:**
- 동시 쓰기 → lock으로 보호
- 각 에이전트 관점의 사실이 모두 보존
- 나중에 Simon이 직접 확인 → dream() 시 자동 해결 가능

---

### 시나리오 2: 형 인물 메모리 — 에이전트별 뷰 관리

**배경**: Simon(형)에 대해 에이전트마다 다른 측면을 알고 있음. 통합 뷰와 에이전트별 전문 뷰가 공존해야 함.

```python
# 공유 namespace에서 Simon 기본 정보 (모든 에이전트 공통)
shared_mk = NamespacedMemKraft(
    base_dir=MEMORY_DIR,
    agent_id="zeon",
    namespace="shared"
)
shared_mk.track("Simon", entity_type="person", source="identity")
shared_mk.update("Simon", "김서준, Hashed CEO, @simonkim_nft", source="USER.md")

# 제온만 아는 기술 관련 정보
zeon_mk = NamespacedMemKraft(MEMORY_DIR, agent_id="zeon", namespace="private")
zeon_mk.update("Simon", "Mac mini M4 Pro 사용, 인프라 직접 관여", source="DM-dev")

# 미온만 아는 디자인 취향
mion_mk = NamespacedMemKraft(MEMORY_DIR, agent_id="mion", namespace="private")
mion_mk.update("Simon", "다크 테마 선호, 애니메이션 좋아함 (카레카노 레퍼런스)", source="DM-design")

# 에이전트가 Simon 관련 질문 받을 때 — 다중 namespace 병합 검색
zeon_mk_multi = NamespacedMemKraft(
    MEMORY_DIR, agent_id="zeon", namespace="private",
    read_namespaces=["private", "shared"]   # private + shared 모두 검색
)
results = zeon_mk_multi.search("Simon 기술 스택")
# → zeon/private: "Mac mini M4 Pro" + shared: "Hashed CEO" 병합 반환
# → sion/private 내용은 포함 안 됨 (read_namespaces에 없음)

# audit으로 Simon 관련 전체 변경 추적
logs = shared_mk.namespace_audit(entity_name="Simon", limit=20)
# → 누가 언제 무엇을 업데이트했는지 전체 이력
```

**효과:**
- 에이전트별 전문 메모리 분리 (역할 혼선 방지)
- 공통 정보는 shared에서 일관성 유지
- 검색 시 read_namespaces 설정으로 필요한 범위만 병합

---

### 시나리오 3: VibeKai 프로젝트 상태 — 크로스 에이전트 작업 추적

**배경**: VibeKai 배포 작업이 제온(개발) → 시온(문서) → 미온(디자인)으로 이어짐. 각 에이전트가 동일 프로젝트의 다른 aspect를 관리.

```python
PROJECT_NS = "project-vibekai"
MEMORY_DIR = "/Users/gimseojun/сlawd/memory"

# 프로젝트 namespace 생성 (제온이 초기화)
zeon_mk = NamespacedMemKraft(MEMORY_DIR, agent_id="zeon")
zeon_mk.namespace_create(
    PROJECT_NS,
    access={"zeon": "rw", "sion": "rw", "mion": "rw", "*": "r"},
    description="VibeKai 프로젝트 공유 작업 공간"
)

# 제온: 배포 상태 기록
zeon_proj = NamespacedMemKraft(MEMORY_DIR, agent_id="zeon", namespace=PROJECT_NS)
zeon_proj.channel_save("vibekai-deploy", {
    "status": "in_progress",
    "version": "v1.2.0",
    "env": "production",
    "deploy_started": "2026-04-26T11:00:00"
})
zeon_proj.task_start("deploy-v1.2", "VibeKai v1.2 배포", agent="zeon")
zeon_proj.task_update("deploy-v1.2", "in_progress", "Vercel 빌드 완료, DNS 전파 중")

# 시온: 릴리즈 노트 작성 후 업데이트
sion_proj = NamespacedMemKraft(MEMORY_DIR, agent_id="sion", namespace=PROJECT_NS)
sion_proj.channel_update("vibekai-deploy", "release_notes", 
    "## v1.2.0\n- 바이브코딩 모드 추가\n- 성능 30% 개선",
    mode="set"
)
# → audit.jsonl: {agent:"sion", action:"write", channel:"vibekai-deploy", ...}

# 미온: 디자인 변경사항 기록
mion_proj = NamespacedMemKraft(MEMORY_DIR, agent_id="mion", namespace=PROJECT_NS)
mion_proj.channel_update("vibekai-deploy", "design_changes",
    ["새 온보딩 화면", "다크 모드 토글"],
    mode="append"
)

# 제온: 전체 배포 상태 조회 (모든 에이전트 기여 통합)
deploy_state = zeon_proj.channel_load("vibekai-deploy")
# → {status, version, release_notes(sion), design_changes(mion), deploy_started}

# 완료 후 핸드오프 + task 위임
zeon_proj.task_delegate("deploy-v1.2", "zeon", "sion", "문서 최종 검토 부탁")

# 프로젝트 namespace audit
project_audit = zeon_proj.namespace_audit(namespace=PROJECT_NS)
# → 제온/시온/미온 모든 활동 타임라인으로 조회
# → 형에게 보고 시 전체 이력 제공 가능

# 배포 완료 후 cleanup
zeon_proj.task_complete("deploy-v1.2", "VibeKai v1.2 배포 완료, Vercel ● Ready")
zeon_proj.namespace_sync(PROJECT_NS, "shared",  # shared에도 최종 상태 반영
    entities=["vibekai-deploy"],
    conflict_strategy="source-wins"
)
```

**효과:**
- 3개 에이전트가 같은 프로젝트 채널에 동시 기여 가능
- 각 기여의 출처(agent, timestamp) 명확히 기록
- 형에게 보고 시 전체 타임라인 단일 audit으로 제공

---

## 9. Migration 가이드

### 9.1 기존 코드 — 변경 없음 (0 breaking change)

```python
# v2.0 코드 (그대로 동작)
mk = MemKraft(base_dir="/Users/gimseojun/сlawd/memory")
mk.update("Simon", "Hashed CEO", source="DM")
```

내부적으로:
- `agent_id` 없으면 `"anonymous"` 처리
- 쓰기 경로: `ns/shared/` 대신 기존 `entities/` 유지
- audit: 선택적 (기본 off, `MEMKRAFT_AUDIT=1` 환경변수로 활성화)

### 9.2 점진적 이행 경로

```
Phase 0 (현재):
  └─ MemKraft(base_dir) — 단일 namespace

Phase 1 (v2.1.0):
  └─ NamespacedMemKraft(base_dir, agent_id) 도입
  └─ 기존 entities/ → ns/shared/ 로 자동 마이그레이션 스크립트
  └─ audit.jsonl 기록 시작

Phase 2 (v2.1.x):
  └─ namespace_share, namespace_sync 안정화
  └─ Conflict resolution UI (dream() 통합)
  └─ 5남매 각자 private namespace 셋업

Phase 3 (v2.2.0):
  └─ namespace_create API 공개
  └─ 프로젝트별 namespace 지원
  └─ 감사 대시보드 (memkraft audit 커맨드)
```

### 9.3 마이그레이션 스크립트 (예시)

```bash
# 기존 entities/ → ns/shared/entities/ 이관
python3 -m memkraft migrate --target-namespace=shared --dry-run
python3 -m memkraft migrate --target-namespace=shared

# 이관 후 검증
python3 -m memkraft doctor --check-namespaces
```

### 9.4 환경변수 설정

```bash
# ~/.zshrc
export MEMKRAFT_AGENT_ID="zeon"           # 기본 에이전트 ID
export MEMKRAFT_NAMESPACE="private"        # 기본 쓰기 namespace
export MEMKRAFT_AUDIT=1                    # audit.jsonl 활성화
export MEMKRAFT_LOCK_TIMEOUT=5             # filelock 타임아웃 (초)
```

---

## 10. 보안 & 접근 제어

### 10.1 접근 제어 모델

```
에이전트별 namespace 권한:
  - "rw": 읽기 + 쓰기 (entity 생성/수정/삭제)
  - "r":  읽기만 (search, agent_inject 가능)
  - None: 접근 불가 (namespace 자체가 안 보임)

namespace 생성 권한:
  - 기본: 모든 에이전트 가능
  - 제한 모드: MEMKRAFT_NS_CREATE_POLICY=restricted 시 admin 에이전트만
```

### 10.2 데이터 격리 원칙

```
1. Private namespace는 해당 에이전트만 기본 쓰기 가능
2. shared namespace는 명시적 access 설정 필요
3. namespace_share()는 원본 소유자만 호출 가능
4. audit.jsonl은 append-only (삭제/수정 불가)
5. lock 파일은 pid + timestamp로 stale lock 자동 해제
```

### 10.3 민감 정보 처리

```python
# 민감 엔티티 — private namespace + 암호화 (Phase 3 예정)
zeon_mk.track("Simon-private-keys", entity_type="credentials",
               namespace="zeon",    # private
               encrypted=True)      # Phase 3 추가 예정

# audit에서 민감 필드 마스킹
# → fact 내용 일부 마스킹 (MEMKRAFT_AUDIT_MASK_PATTERNS 환경변수)
```

### 10.4 Prompt Injection 방어

멀티에이전트 환경에서 shared namespace 오염을 통한 prompt injection 시도 가능:

```
공격 시나리오:
  악성 에이전트 → shared namespace에 오염된 entity 쓰기
  → 다른 에이전트가 search 후 그 내용을 프롬프트에 포함
  → 실행 흐름 오염

방어:
  1. shared namespace 쓰기 시 prompt-guard 스캔 (옵션)
  2. entity 내용에 지시어 패턴 감지 → [SUSPICIOUS] 태그
  3. audit.jsonl로 오염 추적 + 롤백
  4. agent_inject() 시 shared 내용에 `[External]` 명시
```

---

## 11. 구현 로드맵

### Phase 1 MVP (v2.1.0-alpha, 2주)

| 태스크 | 담당 | 우선순위 |
|--------|------|---------|
| `NamespacedMemKraft` 기본 클래스 | 제온 | P0 |
| `_FileLock` (fcntl) 구현 | 제온 | P0 |
| `namespace_create` + `_registry.json` | 제온 | P0 |
| audit.jsonl 기록 (모든 write에 투명 주입) | 제온 | P0 |
| `namespace_audit` 조회 API | 제온 | P1 |
| 기존 entity writer에 `agent_id` 메타 추가 | 제온 | P1 |
| Migration 스크립트 | 제온 | P1 |
| Tests (pytest) | 제온 | P0 |

### Phase 2 (v2.1.0, 1주)

| 태스크 | 담당 | 우선순위 |
|--------|------|---------|
| `namespace_share` (link/copy 모드) | 제온 | P1 |
| `namespace_sync` + conflict detection | 제온 | P1 |
| dream() 통합 (conflict 자동 해결) | 제온 | P2 |
| `memkraft audit` CLI 커맨드 | 제온 | P2 |

### Phase 3 (v2.2.0, 추후)

| 태스크 | 담당 | 우선순위 |
|--------|------|---------|
| 프로젝트별 namespace | TBD | P2 |
| 감사 대시보드 (웹 UI) | TBD | P3 |
| 암호화 namespace | TBD | P3 |
| 분산 머신 지원 (rsync 백엔드) | TBD | P3 |

---

## 부록: 현재 v2.0 API와 v2.1 API 대조표

| v2.0 API | v2.1 대응 | 변경 여부 |
|----------|----------|---------|
| `MemKraft(base_dir)` | `NamespacedMemKraft(base_dir, agent_id)` | 선택적 확장 |
| `mk.update(name, info)` | 동일 (namespace 자동 반영) | ✅ 하위 호환 |
| `mk.track(name)` | 동일 | ✅ 하위 호환 |
| `mk.search(query)` | 동일 (read_namespaces 병합) | ✅ 하위 호환 |
| `mk.agent_save(agent_id, mem)` | 동일 | ✅ 하위 호환 |
| `mk.agent_handoff(from, to)` | 동일 + audit 기록 | ✅ 하위 호환 |
| `mk.channel_save(ch, data)` | 동일 (namespace 반영) | ✅ 하위 호환 |
| `mk.task_delegate(task, from, to)` | 동일 + audit 기록 | ✅ 하위 호환 |
| — | `mk.namespace_create(name, access)` | 🆕 |
| — | `mk.namespace_share(entity, src, tgt)` | 🆕 |
| — | `mk.namespace_sync(src, tgt)` | 🆕 |
| — | `mk.namespace_audit(...)` | 🆕 |
| — | `mk.namespace_list()` | 🆕 |

---

*이 문서는 MemKraft v2.1 개발의 설계 기준 문서입니다.*  
*구현 PR: `feat/v2.1-multiagent-shared-memory` (예정)*  
*질문/수정: Zeon에게 DM 또는 GitHub Issue*
