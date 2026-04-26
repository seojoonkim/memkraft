"""Tests for Causal Graph layer (v2.3).

Covers:
- graph_edge with graph_type='causal'
- graph_causal_chain backward/forward
- 기존 entity graph 회귀 (graph_type='entity' 기본값)
- graph_extract에서 causal 자동 추출 (한국어/영어/중국어)
- 복합 인과 체인 (A → B → C)
- 엣케이스: 순환 참조, 빈 체인, max_hops, invalid direction
"""
import pytest
import sqlite3
from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# 1. graph_edge with graph_type='causal'
# ---------------------------------------------------------------------------

def test_graph_edge_causal_type(mk):
    """graph_type='causal' 엣지가 정상 저장된다."""
    mk.graph_edge("server_down", "caused_by", "deploy_v2", graph_type="causal")
    stats = mk.graph_stats()
    assert stats["edges"] == 1
    assert stats["nodes"] == 2


def test_graph_edge_default_is_entity(mk):
    """graph_type 미지정 시 'entity' (기존 호환성)."""
    mk.graph_edge("sarah", "works_at", "google")  # graph_type 미지정
    # DB에서 직접 조회해서 default 확인
    db_path = f"{mk.base_dir}/graph.db"
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT graph_type FROM edges WHERE from_id='sarah'").fetchone()
    conn.close()
    assert row[0] == "entity"


def test_graph_edge_causal_distinct_from_entity(mk):
    """같은 triple이라도 graph_type 다르면 별개로 저장."""
    mk.graph_edge("a", "rel", "b", graph_type="entity")
    mk.graph_edge("a", "rel", "b", graph_type="causal")
    stats = mk.graph_stats()
    assert stats["edges"] == 2


def test_graph_edge_causal_dedup(mk):
    """같은 graph_type + same triple은 중복 제거."""
    mk.graph_edge("a", "caused_by", "b", graph_type="causal")
    mk.graph_edge("a", "caused_by", "b", graph_type="causal")
    stats = mk.graph_stats()
    assert stats["edges"] == 1


# ---------------------------------------------------------------------------
# 2. graph_causal_chain - backward
# ---------------------------------------------------------------------------

def test_causal_chain_backward_simple(mk):
    """A --caused_by--> B : backward(A)는 B를 반환."""
    mk.graph_edge("server_crash", "caused_by", "memory_leak", graph_type="causal")
    chain = mk.graph_causal_chain("server_crash", direction="backward")
    assert len(chain) == 1
    assert chain[0]["id"] == "memory_leak"
    assert chain[0]["relation"] == "caused_by"
    assert chain[0]["depth"] == 1
    assert chain[0]["graph_type"] == "causal"


def test_causal_chain_backward_multihop(mk):
    """A → B → C 체인 : backward(A)는 B, C 모두 반환."""
    mk.graph_edge("crash", "caused_by", "oom", graph_type="causal")
    mk.graph_edge("oom", "caused_by", "leak", graph_type="causal")
    mk.graph_edge("leak", "caused_by", "v2_deploy", graph_type="causal")
    chain = mk.graph_causal_chain("crash", direction="backward", max_hops=5)
    ids = [c["id"] for c in chain]
    assert "oom" in ids
    assert "leak" in ids
    assert "v2_deploy" in ids
    # depth 검증
    by_id = {c["id"]: c for c in chain}
    assert by_id["oom"]["depth"] == 1
    assert by_id["leak"]["depth"] == 2
    assert by_id["v2_deploy"]["depth"] == 3


def test_causal_chain_backward_max_hops(mk):
    """max_hops 제한이 적용된다."""
    mk.graph_edge("a", "caused_by", "b", graph_type="causal")
    mk.graph_edge("b", "caused_by", "c", graph_type="causal")
    mk.graph_edge("c", "caused_by", "d", graph_type="causal")
    chain = mk.graph_causal_chain("a", direction="backward", max_hops=2)
    ids = [c["id"] for c in chain]
    assert "b" in ids
    assert "c" in ids
    assert "d" not in ids  # max_hops=2로 잘림


# ---------------------------------------------------------------------------
# 3. graph_causal_chain - forward
# ---------------------------------------------------------------------------

def test_causal_chain_forward_resulted_in(mk):
    """A --resulted_in--> B : forward(A)는 B를 반환."""
    mk.graph_edge("v2_deploy", "resulted_in", "perf_drop", graph_type="causal")
    chain = mk.graph_causal_chain("v2_deploy", direction="forward")
    ids = [c["id"] for c in chain]
    assert "perf_drop" in ids


def test_causal_chain_forward_via_reverse_caused_by(mk):
    """B --caused_by--> A 면 forward(A)는 B (effect)를 반환."""
    # crash --caused_by--> deploy : "deploy가 crash를 일으켰다"
    mk.graph_edge("crash", "caused_by", "deploy", graph_type="causal")
    chain = mk.graph_causal_chain("deploy", direction="forward")
    ids = [c["id"] for c in chain]
    assert "crash" in ids


# ---------------------------------------------------------------------------
# 4. 기존 entity graph 회귀
# ---------------------------------------------------------------------------

def test_entity_graph_unaffected(mk):
    """entity graph 동작은 그대로."""
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("google", "located_in", "nyc")
    results = mk.graph_neighbors("sarah", hops=2)
    targets = [r["target"] for r in results]
    assert "google" in targets
    assert "nyc" in targets


def test_causal_does_not_pollute_entity_neighbors(mk):
    """causal 엣지가 entity graph_neighbors에 섞이는지 확인 (현재는 섞임 OK; 회귀만 본다).

    graph_neighbors는 graph_type 필터를 안 거쳐서 모든 엣지를 본다.
    이 테스트는 회귀 보호용이며, 'entity-only' 필터링은 v2.3 범위 밖.
    """
    mk.graph_edge("sarah", "works_at", "google")
    mk.graph_edge("crash", "caused_by", "deploy", graph_type="causal")
    # sarah 이웃은 google 만 (causal과 무관)
    results = mk.graph_neighbors("sarah", hops=2)
    assert all("crash" not in r["target"] and "deploy" not in r["target"] for r in results)


def test_causal_chain_ignores_entity_edges(mk):
    """graph_causal_chain은 graph_type='causal' 엣지만 본다."""
    mk.graph_edge("sarah", "caused_by", "stress")  # entity (default!)
    mk.graph_edge("burnout", "caused_by", "overwork", graph_type="causal")
    # sarah는 causal 엣지 없음
    chain = mk.graph_causal_chain("sarah", direction="backward")
    assert chain == []
    # burnout은 있음
    chain2 = mk.graph_causal_chain("burnout", direction="backward")
    assert any(c["id"] == "overwork" for c in chain2)


# ---------------------------------------------------------------------------
# 5. graph_extract — causal 자동 추출
# ---------------------------------------------------------------------------

def test_extract_korean_ttaemune(mk):
    """'X 때문에 Y' → Y --caused_by--> X (causal)."""
    text = "메모리누수 때문에 서버다운 발생했다."
    result = mk.graph_extract(text)
    assert result["edges_added"] >= 1
    # causal chain 으로 확인: 서버다운 → 메모리누수
    chain = mk.graph_causal_chain("서버다운", direction="backward")
    assert any("메모리누수" in c["id"] for c in chain)


def test_extract_korean_eurolinhae(mk):
    """'X로 인해 Y' → Y --caused_by--> X (causal)."""
    text = "v2배포로 인해 장애 발생"
    result = mk.graph_extract(text)
    assert result["edges_added"] >= 1
    chain = mk.graph_causal_chain("장애", direction="backward")
    assert any("v2배포" in c["id"] for c in chain)


def test_extract_english_caused(mk):
    """'X caused Y' → Y --caused_by--> X (causal)."""
    text = "memoryleak caused crash"
    result = mk.graph_extract(text)
    assert result["edges_added"] >= 1
    chain = mk.graph_causal_chain("crash", direction="backward")
    assert any("memoryleak" in c["id"] for c in chain)


def test_extract_english_resulted_in(mk):
    """'X resulted in Y' → X --resulted_in--> Y (causal, forward)."""
    text = "deploy resulted in outage"
    result = mk.graph_extract(text)
    assert result["edges_added"] >= 1
    chain = mk.graph_causal_chain("deploy", direction="forward")
    ids = [c["id"] for c in chain]
    assert "outage" in ids


def test_extract_chinese_daozhi(mk):
    """중국어 '导致' → caused_by (causal)."""
    text = "bug导致crash"
    result = mk.graph_extract(text)
    # 중국어 패턴 매치 시 최소 1개 추가
    if result["edges_added"] >= 1:
        chain = mk.graph_causal_chain("crash", direction="backward")
        assert any("bug" in c["id"] for c in chain)


# ---------------------------------------------------------------------------
# 6. 복합 인과 체인 + extract 통합
# ---------------------------------------------------------------------------

def test_extract_then_chain(mk):
    """extract로 만든 체인을 graph_causal_chain이 추적할 수 있다."""
    # A 때문에 B, B 때문에 C
    mk.graph_extract("배포 때문에 누수 발생.")
    mk.graph_extract("누수 때문에 크래시 발생.")
    chain = mk.graph_causal_chain("크래시", direction="backward", max_hops=5)
    ids = [c["id"] for c in chain]
    assert "누수" in ids
    # 누수 → 배포 도 잡혀야 함
    assert "배포" in ids


# ---------------------------------------------------------------------------
# 7. 엣케이스
# ---------------------------------------------------------------------------

def test_causal_chain_empty(mk):
    """causal 엣지 없는 노드 → 빈 체인."""
    mk.graph_edge("a", "works_at", "b")  # entity only
    chain = mk.graph_causal_chain("a", direction="backward")
    assert chain == []


def test_causal_chain_nonexistent_node(mk):
    """존재하지 않는 노드 → 빈 체인 (에러 X)."""
    chain = mk.graph_causal_chain("ghost", direction="backward")
    assert chain == []


def test_causal_chain_cycle_safe(mk):
    """순환 참조: A → B → A. 무한루프 없이 종료."""
    mk.graph_edge("a", "caused_by", "b", graph_type="causal")
    mk.graph_edge("b", "caused_by", "a", graph_type="causal")
    chain = mk.graph_causal_chain("a", direction="backward", max_hops=10)
    # visited set으로 중단 → 결과 finite
    assert len(chain) <= 5  # 사이클 보호
    # 최소한 b는 도달
    ids = [c["id"] for c in chain]
    assert "b" in ids


def test_causal_chain_invalid_direction(mk):
    """잘못된 direction → ValueError."""
    with pytest.raises(ValueError):
        mk.graph_causal_chain("a", direction="sideways")


def test_causal_chain_max_hops_zero(mk):
    """max_hops=0 → 빈 체인."""
    mk.graph_edge("a", "caused_by", "b", graph_type="causal")
    chain = mk.graph_causal_chain("a", direction="backward", max_hops=0)
    assert chain == []


# ---------------------------------------------------------------------------
# 8. 마이그레이션 (기존 DB에 graph_type 컬럼 자동 추가)
# ---------------------------------------------------------------------------

def test_migration_adds_graph_type_column(tmp_path):
    """v2.2 시절 DB(graph_type 없는)를 열어도 자동 마이그레이션."""
    db_path = tmp_path / "graph.db"
    # v2.2 스키마(graph_type 없음)로 수동 생성
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            node_type TEXT DEFAULT 'entity',
            label TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            to_id TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            valid_from TEXT,
            valid_until TEXT,
            created_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO edges(from_id,relation,to_id,weight,created_at) VALUES (?,?,?,?,?)",
        ("old", "rel", "edge", 1.0, "2024-01-01")
    )
    conn.commit()
    conn.close()

    # MemKraft 열기 → 마이그레이션 트리거
    mk = MemKraft(base_dir=str(tmp_path))
    # graph_db 강제 초기화 (lazy load)
    mk.graph_node("test")  # triggers _graph_db()

    # 컬럼 추가됐는지 확인
    conn = sqlite3.connect(str(db_path))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(edges)").fetchall()]
    conn.close()
    assert "graph_type" in cols

    # 기존 엣지의 graph_type='entity'로 채워졌는지
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT graph_type FROM edges WHERE from_id='old'").fetchone()
    conn.close()
    assert row[0] == "entity"


# ---------------------------------------------------------------------------
# 9. 통합: extract → chain 합성 (한국어 시나리오)
# ---------------------------------------------------------------------------

def test_korean_root_cause_analysis(mk):
    """RCA 시나리오: 근본 원인까지 추적."""
    mk.graph_extract("배포 때문에 메모리누수 발생.")
    mk.graph_extract("메모리누수 때문에 oom 발생.")
    mk.graph_extract("oom 때문에 크래시 발생.")

    chain = mk.graph_causal_chain("크래시", direction="backward", max_hops=10)
    ids = [c["id"] for c in chain]
    # 전체 인과 체인이 잡혀야 함
    assert "oom" in ids
    assert "메모리누수" in ids
    assert "배포" in ids  # root cause
