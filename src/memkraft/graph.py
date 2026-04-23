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
    created_at TEXT
);
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

_STOPWORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'who',
    'where', 'when', 'how', 'why', 'did', 'does', 'do', 'will',
    'would', 'could', 'should', 'have', 'has', 'had', 'be', 'been',
    'their', 'they', 'this', 'that', 'with', 'from', 'about',
}


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
            conn.executescript(_SCHEMA)
            conn.commit()
            self._graph_conn = conn
            self._graph_db_path = db_path
        return self._graph_conn

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
    ) -> None:
        """Add an edge between two nodes. Auto-creates nodes if missing."""
        from_id = from_id.lower().strip()
        to_id = to_id.lower().strip()
        relation = relation.lower().strip()
        # auto-create nodes
        self.graph_node(from_id)
        self.graph_node(to_id)
        now = self._now()
        with self._graph_db() as conn:
            # avoid exact duplicates
            dup = conn.execute(
                "SELECT id FROM edges WHERE from_id=? AND relation=? AND to_id=?",
                (from_id, relation, to_id),
            ).fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO edges(from_id,relation,to_id,weight,valid_from,valid_until,created_at) VALUES(?,?,?,?,?,?,?)",
                    (from_id, relation, to_id, weight, valid_from, valid_until, now),
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

        1. Extract entity names from query (capitalized words)
        2. Traverse graph from each entity
        3. Return paths as natural language strings
        4. Fallback to search_precise if no graph results
        """
        # 엔티티 추출 (대문자 단어 NER)
        entities = re.findall(r"\b[A-Z][a-z]+\b", query)
        entities = [e for e in entities if e.lower() not in _STOPWORDS]

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

    def graph_extract(self, text: str) -> dict:
        """Auto-extract entities and relations from text.

        Pattern-based (no LLM required).
        Returns dict with nodes and edges counts added.
        """
        nodes_added = 0
        edges_added = 0

        for pattern, relation in _RELATION_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                subject = match.group(1).lower()
                obj = match.group(2).lower()
                # skip very short / stopword matches
                if len(subject) < 2 or len(obj) < 2:
                    continue
                if subject in _STOPWORDS or obj in _STOPWORDS:
                    continue
                self.graph_node(subject)
                self.graph_node(obj)
                self.graph_edge(subject, relation, obj)
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
