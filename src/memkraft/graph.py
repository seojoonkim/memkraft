"""GraphMixin — SQLite-based graph layer for MemKraft (v2.0.0)

Zero external dependencies. Uses Python's built-in sqlite3.
Graph DB stored as a single file: {base_dir}/graph.db

API:
    mk.graph_node(id, node_type, label)     — add/update node
    mk.graph_edge(from_id, relation, to_id) — add edge
    mk.graph_neighbors(node_id, hops=1)     — BFS traversal
    mk.graph_search(query)                  — natural language → graph paths
    mk.graph_extract(text)                  — auto-extract entities+relations
    mk.graph_stats()                        — node/edge counts
"""
from __future__ import annotations

import os
import re
import sqlite3
import json
from datetime import datetime, timezone
from typing import Any, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT DEFAULT 'entity',
    label TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_id TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    valid_from TEXT,
    valid_until TEXT,
    created_at TEXT,
    graph_type TEXT DEFAULT 'entity'
);
CREATE INDEX IF NOT EXISTS idx_edges_graph_type ON edges(graph_type);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
"""

# 관계 패턴 (자동 추출용)
_RELATION_PATTERNS = [
    (r'\b(\w+)\s+works?\s+(?:at|for)\s+([A-Z]\w+)', 'works_at'),
    (r'\b(\w+)\s+worked\s+(?:at|for)\s+([A-Z]\w+)', 'works_at'),
    (r'\b(\w+)\s+lives?\s+in\s+([A-Z]\w+)', 'lives_in'),
    (r'\b(\w+)\s+moved\s+to\s+([A-Z]\w+)', 'lives_in'),
    (r'\b(\w+)\s+(?:likes?|loves?|enjoys?)\s+(\w+(?:\s+\w+)?)', 'likes'),
    (r'\b(\w+)\s+is\s+(?:a|an)\s+(\w+(?:\s+\w+)?)', 'is_a'),
    (r'\b(\w+)\s+(?:knows?|met)\s+([A-Z]\w+)', 'knows'),
    (r'\b(\w+)\s+(?:studied|studies)\s+(?:at|in)\s+([A-Z]\w+)', 'studied_at'),
    (r'\b(\w+)\s+(?:graduated|grad)\s+from\s+([A-Z]\w+)', 'graduated_from'),
    (r'\b(\w+)\s+(?:married|dating)\s+([A-Z]\w+)', 'partner_of'),
    (r'\b(\w+)\'s\s+(?:hobby|hobbies)\s+(?:is|are|include)\s+(\w+(?:\s+\w+)?)', 'hobby_is'),
    (r'\b(\w+)\s+(?:born|grew up)\s+in\s+([A-Z]\w+)', 'born_in'),
]

# ====================================================================
# Causal patterns (v2.3) — 인과 관계 자동 추출
# ====================================================================
# 매치 결과: (cause, effect) — 항상 cause --caused_by-- effect 가 아니라
# effect --caused_by--> cause 로 저장 ("effect는 cause 때문에 발생").
# resulted_in 은 반대 방향: cause --resulted_in--> effect.
#
# 패턴 형식: (regex, relation, direction)
#   direction="effect_caused_by_cause": group(1)=effect, group(2)=cause → effect --caused_by--> cause
#   direction="cause_resulted_in_effect": group(1)=cause, group(2)=effect → cause --resulted_in--> effect
#   direction="cause_caused_effect":     group(1)=cause, group(2)=effect → effect --caused_by--> cause
_CAUSAL_PATTERNS = [
    # 한국어 — "X 때문에/덕분에/으로 인해/탓에 Y" : X가 원인, Y가 결과
    # 매치 그룹: (1)=cause, (2)=effect
    (r'([가-힯\w]+)\s*(?:때문에|덕분에|으로\s*인해|로\s*인해|탓에)\s+([가-힯\w]+)', 'caused_by', 'cause_caused_effect'),
    # 중국어 — "X 导致/引起/造成 Y" : X가 원인, Y가 결과
    (r'([\u4e00-\u9fff\w]+)\s*(?:导致|引起|造成)\s*([\u4e00-\u9fff\w]+)', 'caused_by', 'cause_caused_effect'),
    # 영어 — "X caused Y" : X가 원인, Y가 결과
    (r'\b([A-Za-z][\w]*)\s+caused\s+([A-Za-z][\w]*)', 'caused_by', 'cause_caused_effect'),
    # 영어 — "X led to Y" : X가 원인, Y가 결과
    (r'\b([A-Za-z][\w]*)\s+led\s+to\s+([A-Za-z][\w]*)', 'caused_by', 'cause_caused_effect'),
    # 영어 — "X resulted in Y" : X가 원인, Y가 결과
    (r'\b([A-Za-z][\w]*)\s+resulted\s+in\s+([A-Za-z][\w]*)', 'resulted_in', 'cause_resulted_in_effect'),
]

_STOPWORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'who',
    'where', 'when', 'how', 'why', 'did', 'does', 'do', 'will',
    'would', 'could', 'should', 'have', 'has', 'had', 'be', 'been',
    'their', 'they', 'this', 'that', 'with', 'from', 'about',
}

# ====================================================================
# Korean (한국어) support — v2.1
# ====================================================================

_HANGUL_RE = re.compile(r'[\uac00-\ud7af]')

# 조사 (Josa) — 단어 끝에서 strip
# 긴 조사 먼저 (단일 조사 이전에 매치 우선하도록 길이 순 정렬)
_JOSA_LIST = [
    '으로서', '으로부터', '이라도', '이서부터',
    '한테', '에게', '로서', '에서', '으로',
    '까지', '부터', '마저', '조차', '밖에',
    '처럼', '만큼', '보다', '마다', '대로',
    '은', '는', '이', '가', '을', '를', '의', '에',
    '로', '와', '과', '도', '만', '랑', '이랑',
    '이나', '나', '며', '고', '이며',
]
# 긴 것 우선
_JOSA_LIST.sort(key=len, reverse=True)
_JOSA_PATTERN = re.compile(r'(' + '|'.join(_JOSA_LIST) + r')$')

# Korean stopwords (관계 추출 시 주어/목적어로 쓰는 게 아닌 게 안 되는 단어)
# 조사 자체도 포함 (strip 실패 시 폴백)
_KO_STOPWORDS = {
    '그', '이', '저', '그는', '그녀', '그들', '우리', '이것', '저것', '그것',
    '이분', '그분', '저분', '자신', '본인', '당신', '너', '나',
    '어디', '언제', '어떻게', '왜', '무엇', '뭐', '누구', '누가',
    '지금', '아까', '조금', '아주', '너무', '더', '더욱', '거의',
    '그래서', '하지만', '그러나', '또한', '그리고', '그런',
    '같은', '같이', '동일', '아니', '아니라',
    '합니다', '입니다', '있습니다', '했다', '한다', '하다',
    '되다', '됐다', '있다', '없다', '같다', '다르다',
    '아니다', '이다', '일', '해', '함', '됨',
    # 원소적 조사 어딩
    '은', '는', '이', '가', '을', '를', '의', '에', '에서',
}


def _strip_josa(word: str) -> str:
    """한국어 단어 끝의 조사 제거 (iterative).

    - 이중 조사 케이스 ("서준이는" = 서준+이+는) 처리: 변경 없을 때까지 반복 strip (max 4회)
    - 2-char guard: 매 단계에서 strip 결과 2자 미만이면 그 단계 거부 (원본 유지)
    - 한글이 아닌 단어는 원본 그대로
    """
    if not word:
        return word
    if not _HANGUL_RE.search(word):
        return word
    current = word
    for _ in range(4):
        m = _JOSA_PATTERN.search(current)
        if not m:
            break
        josa = m.group(1)
        stripped = current[: -len(josa)]
        # 2-char guard: 결과 2자 미만이면 멈춤 (이은 같은 이름 보호)
        if len(stripped) < 2:
            break
        if stripped == current:
            break
        current = stripped
    return current


# 한국어 관계 패턴 — 조사 포함 capture (graph_extract에서 _strip_josa로 정규화)
# 한글 단어 매치: [가-힯]+ (1글자 이상)
_KO_NAME = r'[가-힯]+'
_EN_OR_KO = r'[가-힯\w]+'

_KO_RELATION_PATTERNS = [
    # ====================================================================
    # 한국어 동사 활용 — 음절 융합 처리 위해 어간 변형 alternation 사용
    # ====================================================================

    # === works_at (소속/직장/직책) ===
    # 일하다 → 일한, 일해, 일했, 일하는
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에서\s*(?:일하|일한|일해|일했|일함)\w*', 'works_at'),
    # 근무하다
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에서\s*근무\w{{0,3}}', 'works_at'),
    # 다니다 → 다니, 다닌, 다녔, 다녀
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에\s*(?:다니|다닌|다녔|다녀|다님)\w*', 'works_at'),
    # 직책 명사
    (rf'({_KO_NAME})[은는이가]\s+({_KO_NAME})\s*(?:의\s*)?(?:CEO|대표(?!\s*적)|이사(?!\s*[하한했함])|팀장|개발자|디자이너|기획자|창업자|공동창업자|임원|디렉터|교수|연구원|매니저|파트너)', 'works_at'),
    (rf'({_KO_NAME})[의]\s+({_KO_NAME})\s+(?:CEO|대표|이사|팀장)', 'works_at'),

    # === lives_in (거주) ===
    # 살다 → 살, 산, 삽, 살아, 사는
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에\s*(?:살|산다|살아|살고|삽니다|살았|사는)\w*', 'lives_in'),
    # 거주하다
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에\s*거주\w{{0,3}}', 'lives_in'),
    # 이사하다
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME}?)\s*(?:으로|로)\s*이사\w{{0,3}}', 'lives_in'),

    # === likes (선호) ===
    # 좋아하다 → 좋아한, 좋아해, 좋아했, 좋아함
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*(?:좋아하|좋아한|좋아해|좋아했|좋아함|좋아함)\w*', 'likes'),
    # 사랑하다
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*(?:사랑하|사랑한|사랑해|사랑했|사랑함)\w*', 'likes'),
    # 즐기다 / 즐겨
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*(?:즐기|즐긴|즐겼|즐겨|즐김)\w*', 'likes'),
    # 선호하다
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*선호\w{{0,3}}', 'likes'),

    # === knows / met (알다 / 만났다) ===
    # 알다 → 안, 알, 알고, 알았, 안다, 아는
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*(?:안다|아는\s|알고\s|알았|알게)', 'knows'),
    # 만나다 → 만나, 만난, 만났, 만남
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*(?:만나|만난|만났|만남)\w*', 'knows'),
    # 친구/친한
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*(?:와|과)\s*(?:친한|친구|동료)', 'knows'),

    # === is_a (속성/직업) ===
    # X는 Y다 / X는 Y이다 / X는 Y입니다 / X는 Y이자 ...
    (rf'({_KO_NAME})[은는이가]\s+({_KO_NAME})(?:이다|다)(?:\.|$|\s)', 'is_a'),
    (rf'({_KO_NAME})[은는이가]\s+({_KO_NAME})\s*입니다', 'is_a'),
    (rf'({_KO_NAME})[은는이가]\s+({_KO_NAME})\s*이자\s', 'is_a'),

    # === partner_of (결혼/동료) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})(?:과|와)\s*(?:결혼하|결혼한|결혼했|결혼함)\w*', 'partner_of'),
    (rf'({_KO_NAME})(?:과|와)\s+({_KO_NAME})[은는이가]?\s*(?:결혼하|결혼한|결혼했|결혼함|부부)\w*', 'partner_of'),
    (rf'({_KO_NAME})[의]\s+(?:남편|아내|배우자|와이프)는\s+({_KO_NAME})', 'partner_of'),

    # === studied_at / graduated_from (학업) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에서\s*(?:공부하|공부한|공부했|공부함)\w*', 'studied_at'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[를을]?\s*(?:졸업하|졸업한|졸업했|졸업함)\w*', 'graduated_from'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*출신', 'graduated_from'),

    # === born_in (출생) ===
    # 태어나다 → 태어났, 태어난, 태어남
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에서\s*(?:태어났|태어난|태어남|태어나)\w*', 'born_in'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*출생', 'born_in'),

    # === hobby_is (취미) ===
    (rf'({_KO_NAME})[의]\s+취미는\s+({_KO_NAME})', 'hobby_is'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[을를]?\s*취미로', 'hobby_is'),

    # === located_in (장소) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*에\s*(?:위치하|위치한|위치했|위치함)\w*', 'located_in'),

    # === part_of (소속 관계) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[의]\s+(?:자회사|계열사|파트너사)', 'part_of'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})\s*소속', 'part_of'),

    # === founded (창업) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[를을]?\s*(?:창업하|창업한|창업했|창업함)\w*', 'founded'),
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[를을]?\s*(?:설립하|설립한|설립했|설립함)\w*', 'founded'),

    # === invested_in (투자) ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})[에]\s*(?:투자하|투자한|투자했|투자함)\w*', 'invested_in'),

    # === parent / family ===
    (rf'({_KO_NAME})[의]\s+(?:아버지|어머니|아빠|엄마|부친|모친)는\s+({_KO_NAME})', 'child_of'),
    (rf'({_KO_NAME})[의]\s+(?:아들|딸|자녀|아이|자식)는\s+({_KO_NAME})', 'parent_of'),

    # === works_with / colleague ===
    (rf'({_KO_NAME})[은는이가]?\s+({_KO_NAME})(?:과|와)\s+(?:함께\s+)?(?:일하|일한|일해|일했|일함)\w*', 'works_with'),

    # === advised_by ===
    (rf'({_KO_NAME})[의]\s+(?:멘토|스승|지도교수)는\s+({_KO_NAME})', 'advised_by'),

    # ====================================================================
    # 사전형(어근형) 패턴 — v2.4 추가
    # 동사 활용 없이 "~한다" 사전형만으로 매치
    # ====================================================================
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*좋아한다', 'likes'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})\s*에서\s*근무한다', 'works_at'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})\s*에\s*산다', 'lives_in'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})이다', 'is_a'),
    (rf'({_EN_OR_KO})[와과]\s+({_EN_OR_KO})[은는]?\s*친구이다', 'knows'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*공부한다', 'studied_at'),
    # v2.4.0 추가 사전형 패턴 (11개)
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[에에]\s*참여한다', 'participates_in'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*담당한다', 'manages'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*개발한다', 'develops'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*운영한다', 'operates'),
    (rf'({_KO_NAME})[은는이가]?\s+({_EN_OR_KO})[을를]\s*설립했다', 'founded'),
    (rf'({_KO_NAME})[은는이가]?\s+({_EN_OR_KO})[에에]\s*합류했다', 'joined'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*이끈다', 'leads'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})[을를]\s*대표한다', 'represents'),
    (rf'({_KO_NAME})[은는]\s+({_EN_OR_KO})\s*소속이다', 'part_of'),
]


# 긴 패턴이 먼저 매치되도록 길이 순 (패턴의 원본 길이 기준) 정렬은 별로 필요는 없음.
# 이유: re.finditer는 non-overlapping 매치, 여러 패턴을 순차 수행.



class GraphMixin:
    """SQLite graph layer — zero external deps, one file."""

    # ── internal ──────────────────────────────────────────────────────
    _graph_conn: Optional[sqlite3.Connection] = None
    _graph_db_path: Optional[str] = None

    def _graph_db(self) -> sqlite3.Connection:
        db_path = os.path.join(self.base_dir, "graph.db")
        if self._graph_conn is None or self._graph_db_path != db_path:
            if self._graph_conn is not None:
                try:
                    self._graph_conn.close()
                except Exception:
                    pass
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            # Pre-migration: 구버전 DB(graph_type 없는 edges 테이블) 대응.
            # _SCHEMA에 graph_type 관련 INDEX가 있어서, ALTER 먼저 해야 executescript가 깨지지 않음.
            self._migrate_edges_graph_type(conn)
            conn.executescript(_SCHEMA)
            conn.commit()
            self._graph_conn = conn
            self._graph_db_path = db_path
        return self._graph_conn

    def _migrate_edges_graph_type(self, conn: sqlite3.Connection) -> None:
        """Add graph_type column to existing edges tables (idempotent).

        v2.3+: edges.graph_type ∈ {entity, temporal, causal, semantic}.
        Old rows default to 'entity'.
        """
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(edges)").fetchall()]
            if "graph_type" not in cols:
                conn.execute("ALTER TABLE edges ADD COLUMN graph_type TEXT DEFAULT 'entity'")
                conn.execute("UPDATE edges SET graph_type='entity' WHERE graph_type IS NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_graph_type ON edges(graph_type)")
                conn.commit()
        except Exception:
            # 마이그레이션 실패 시에도 graph 자체는 동작해야 함
            pass

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── public API ────────────────────────────────────────────────────
    def graph_node(
        self,
        node_id: str,
        node_type: str = "entity",
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Add or update a node."""
        node_id = node_id.lower().strip()
        now = self._now()
        with self._graph_db() as conn:
            existing = conn.execute(
                "SELECT id FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE nodes SET node_type=?, label=?, metadata=?, updated_at=? WHERE id=?",
                    (node_type, label or node_id, json.dumps(metadata or {}), now, node_id),
                )
            else:
                conn.execute(
                    "INSERT INTO nodes VALUES (?,?,?,?,?,?)",
                    (node_id, node_type, label or node_id, json.dumps(metadata or {}), now, now),
                )

    def graph_edge(
        self,
        from_id: str,
        relation: str,
        to_id: str,
        weight: float = 1.0,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        *,
        graph_type: str = "entity",
    ) -> None:
        """Add an edge between two nodes. Auto-creates nodes if missing.

        graph_type: 'entity' | 'temporal' | 'causal' | 'semantic' (v2.3+).
        Default 'entity' preserves backward compatibility.
        """
        from_id = from_id.lower().strip()
        to_id = to_id.lower().strip()
        relation = relation.lower().strip()
        graph_type = (graph_type or "entity").lower().strip()
        # auto-create nodes
        self.graph_node(from_id)
        self.graph_node(to_id)
        now = self._now()
        with self._graph_db() as conn:
            # avoid exact duplicates (same triple + same graph_type)
            dup = conn.execute(
                "SELECT id FROM edges WHERE from_id=? AND relation=? AND to_id=? AND graph_type=?",
                (from_id, relation, to_id, graph_type),
            ).fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO edges(from_id,relation,to_id,weight,valid_from,valid_until,created_at,graph_type) VALUES(?,?,?,?,?,?,?,?)",
                    (from_id, relation, to_id, weight, valid_from, valid_until, now, graph_type),
                )

    def graph_neighbors(
        self,
        node_id: str,
        hops: int = 2,
        relation: Optional[str] = None,
    ) -> List[dict]:
        """BFS traversal up to N hops. Returns list of path dicts."""
        node_id = node_id.lower().strip()
        visited: set = set()
        frontier = [(node_id, 0, [])]
        results: List[dict] = []
        with self._graph_db() as conn:
            while frontier:
                cur, depth, path = frontier.pop(0)
                if cur in visited or depth > hops:
                    continue
                visited.add(cur)
                q = "SELECT from_id, relation, to_id FROM edges WHERE from_id=?"
                params: list = [cur]
                if relation:
                    q += " AND relation=?"
                    params.append(relation)
                rows = conn.execute(q, params).fetchall()
                for row in rows:
                    step = f"{row['from_id']} --{row['relation']}--> {row['to_id']}"
                    new_path = path + [step]
                    results.append(
                        {
                            "path": new_path,
                            "depth": depth + 1,
                            "target": row["to_id"],
                            "relation": row["relation"],
                            "text": step,
                        }
                    )
                    if depth + 1 < hops:
                        frontier.append((row["to_id"], depth + 1, new_path))
        return results

    def graph_search(self, query: str, top_k: int = 5) -> List[str]:
        """Natural language → graph paths.

        1. Extract entity names from query (capitalized words + Korean nouns)
        2. Traverse graph from each entity
        3. Return paths as natural language strings
        4. Fallback to search_precise if no graph results
        """
        # 엔티티 추출 (대문자 단어 NER)
        entities = re.findall(r"\b[A-Z][a-z]+\b", query)
        entities = [e for e in entities if e.lower() not in _STOPWORDS]

        # 한국어 단어 추출 (조사 strip 후 stopword 필터)
        ko_words = re.findall(r'[\uac00-\ud7af]+', query)
        for w in ko_words:
            stripped = _strip_josa(w)
            if stripped and stripped not in _KO_STOPWORDS and len(stripped) >= 2:
                entities.append(stripped)

        graph_results: List[str] = []
        seen: set = set()
        for entity in entities:
            paths = self.graph_neighbors(entity, hops=2)
            for p in paths:
                text = p["text"]
                if text not in seen:
                    seen.add(text)
                    graph_results.append(text)

        # fallback to vector-like search
        if len(graph_results) < top_k:
            try:
                fallback = self.search_precise(query, top_k=top_k - len(graph_results))
                for r in fallback or []:
                    t = str(r)
                    if t not in seen:
                        seen.add(t)
                        graph_results.append(t)
            except Exception:
                pass

        return graph_results[:top_k]

    def graph_causal_chain(
        self,
        event_id: str,
        direction: str = "backward",
        max_hops: int = 5,
    ) -> List[dict]:
        """Trace a causal chain from `event_id`.

        direction="backward": event ← caused_by ← cause ← root_cause
            (follow caused_by edges OUT from event)
        direction="forward":  event → resulted_in → effect → consequence
            (follow resulted_in edges OUT from event,
             plus reverse caused_by edges INTO event's causes' siblings)

        Returns list of dicts: [{id, relation, depth, graph_type, from_id, to_id}].
        Cycle-safe (visited set) and bounded by max_hops.
        """
        event_id = event_id.lower().strip()
        direction = (direction or "backward").lower().strip()
        if direction not in ("backward", "forward"):
            raise ValueError(f"direction must be 'backward' or 'forward', got {direction!r}")
        if max_hops < 1:
            return []

        results: List[dict] = []
        visited: set = {event_id}
        # frontier item: (current_node_id, depth)
        frontier: List[tuple] = [(event_id, 0)]

        with self._graph_db() as conn:
            while frontier:
                cur, depth = frontier.pop(0)
                if depth >= max_hops:
                    continue

                if direction == "backward":
                    # follow caused_by from cur (cur --caused_by--> cause)
                    rows = conn.execute(
                        "SELECT from_id, relation, to_id, graph_type FROM edges "
                        "WHERE from_id=? AND relation='caused_by' AND graph_type='causal'",
                        (cur,),
                    ).fetchall()
                    for row in rows:
                        nxt = row["to_id"]
                        results.append({
                            "id": nxt,
                            "from_id": row["from_id"],
                            "to_id": nxt,
                            "relation": row["relation"],
                            "depth": depth + 1,
                            "graph_type": row["graph_type"],
                        })
                        if nxt not in visited:
                            visited.add(nxt)
                            frontier.append((nxt, depth + 1))
                else:  # forward
                    # follow resulted_in from cur (cur --resulted_in--> effect)
                    # AND reverse caused_by INTO cur (effect --caused_by--> cur means cur caused effect)
                    rows_resulted = conn.execute(
                        "SELECT from_id, relation, to_id, graph_type FROM edges "
                        "WHERE from_id=? AND relation='resulted_in' AND graph_type='causal'",
                        (cur,),
                    ).fetchall()
                    rows_reverse = conn.execute(
                        "SELECT from_id, relation, to_id, graph_type FROM edges "
                        "WHERE to_id=? AND relation='caused_by' AND graph_type='causal'",
                        (cur,),
                    ).fetchall()
                    # resulted_in: nxt = to_id
                    for row in rows_resulted:
                        nxt = row["to_id"]
                        results.append({
                            "id": nxt,
                            "from_id": row["from_id"],
                            "to_id": nxt,
                            "relation": row["relation"],
                            "depth": depth + 1,
                            "graph_type": row["graph_type"],
                        })
                        if nxt not in visited:
                            visited.add(nxt)
                            frontier.append((nxt, depth + 1))
                    # reverse caused_by: nxt = from_id (the effect)
                    for row in rows_reverse:
                        nxt = row["from_id"]
                        results.append({
                            "id": nxt,
                            "from_id": row["from_id"],
                            "to_id": row["to_id"],
                            "relation": row["relation"],
                            "depth": depth + 1,
                            "graph_type": row["graph_type"],
                        })
                        if nxt not in visited:
                            visited.add(nxt)
                            frontier.append((nxt, depth + 1))

        return results

    def graph_extract(self, text: str) -> dict:
        """Auto-extract entities and relations from text.

        Pattern-based (no LLM required). Supports English + Korean.
        Returns dict with nodes and edges counts added.
        """
        nodes_added = 0
        edges_added = 0
        seen_edges: set = set()

        # English patterns
        for pattern, relation in _RELATION_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                subject = match.group(1).lower()
                obj = match.group(2).lower()
                if len(subject) < 2 or len(obj) < 2:
                    continue
                if subject in _STOPWORDS or obj in _STOPWORDS:
                    continue
                key = (subject, relation, obj)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                self.graph_node(subject)
                self.graph_node(obj)
                self.graph_edge(subject, relation, obj)
                nodes_added += 1
                edges_added += 1

        # Korean patterns
        for pattern, relation in _KO_RELATION_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    raw_subj = match.group(1)
                    raw_obj = match.group(2)
                except IndexError:
                    continue
                # 한국어 매치는 lower() 영향 없음. 영어 혼합 시 lower() 적용.
                subject = _strip_josa(raw_subj).lower()
                obj = _strip_josa(raw_obj).lower()
                # 길이 가드: 한글은 "문자 수" 기준, 그 외 "바이트 수" 대신 char count
                if len(subject) < 1 or len(obj) < 1:
                    continue
                # 영문은 2자 미만 컷, 한글은 1자도 인명일 수 있으므로 통과
                if not _HANGUL_RE.search(subject) and len(subject) < 2:
                    continue
                if not _HANGUL_RE.search(obj) and len(obj) < 2:
                    continue
                if subject in _STOPWORDS or obj in _STOPWORDS:
                    continue
                if subject in _KO_STOPWORDS or obj in _KO_STOPWORDS:
                    continue
                if subject == obj:
                    continue
                key = (subject, relation, obj)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                self.graph_node(subject)
                self.graph_node(obj)
                self.graph_edge(subject, relation, obj)
                nodes_added += 1
                edges_added += 1

        # Causal patterns (v2.3) — 인과 관계 자동 추출
        for pattern, relation, direction in _CAUSAL_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    raw_a = match.group(1)
                    raw_b = match.group(2)
                except IndexError:
                    continue
                a = _strip_josa(raw_a).lower().strip()
                b = _strip_josa(raw_b).lower().strip()
                if not a or not b or a == b:
                    continue
                # 길이 가드 (English/CJK 혼합)
                if not _HANGUL_RE.search(a) and not re.search(r'[\u4e00-\u9fff]', a) and len(a) < 2:
                    continue
                if not _HANGUL_RE.search(b) and not re.search(r'[\u4e00-\u9fff]', b) and len(b) < 2:
                    continue
                if a in _STOPWORDS or b in _STOPWORDS:
                    continue
                if a in _KO_STOPWORDS or b in _KO_STOPWORDS:
                    continue
                # direction 해석:
                #   cause_caused_effect: a=cause, b=effect → effect --caused_by--> cause
                #   cause_resulted_in_effect: a=cause, b=effect → cause --resulted_in--> effect
                if direction == "cause_caused_effect":
                    from_id, to_id = b, a  # effect --caused_by--> cause
                elif direction == "cause_resulted_in_effect":
                    from_id, to_id = a, b  # cause --resulted_in--> effect
                else:
                    from_id, to_id = a, b
                key = (from_id, relation, to_id, "causal")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                self.graph_node(from_id)
                self.graph_node(to_id)
                self.graph_edge(from_id, relation, to_id, graph_type="causal")
                nodes_added += 1
                edges_added += 1

        return {"nodes_added": nodes_added, "edges_added": edges_added}

    def graph_stats(self) -> dict:
        """Return node/edge counts."""
        with self._graph_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            e = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            types = conn.execute(
                "SELECT node_type, COUNT(*) as cnt FROM nodes GROUP BY node_type"
            ).fetchall()
            rels = conn.execute(
                "SELECT relation, COUNT(*) as cnt FROM edges GROUP BY relation ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
        return {
            "nodes": n,
            "edges": e,
            "node_types": {r["node_type"]: r["cnt"] for r in types},
            "top_relations": {r["relation"]: r["cnt"] for r in rels},
        }
