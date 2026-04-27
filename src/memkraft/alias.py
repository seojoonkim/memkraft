"""AliasMixin — Entity alias support for MemKraft (v2.4)

Zero external dependencies. Uses Python's built-in sqlite3.
Alias DB stored as a table in graph.db.

API:
    mk.alias_add(canonical, aliases=["서준", "서준이"])  — add aliases
    mk.alias_resolve(name) → canonical name              — resolve alias
    mk.alias_list(canonical) → list of aliases            — list aliases
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional


_ALIAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS aliases (
    canonical TEXT NOT NULL,
    alias TEXT NOT NULL UNIQUE,
    created_at TEXT,
    PRIMARY KEY (alias)
);
CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON aliases(canonical);
"""


class AliasMixin:
    """Entity alias resolution — map aliases to canonical names."""

    _alias_conn: Optional[sqlite3.Connection] = None
    _alias_db_path: Optional[str] = None

    def _alias_db(self) -> sqlite3.Connection:
        """Get or create the alias DB connection (shares graph.db)."""
        db_path = os.path.join(self.base_dir, "graph.db")
        if self._alias_conn is None or self._alias_db_path != db_path:
            if self._alias_conn is not None:
                try:
                    self._alias_conn.close()
                except Exception:
                    pass
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(_ALIAS_SCHEMA)
            conn.commit()
            self._alias_conn = conn
            self._alias_db_path = db_path
        return self._alias_conn

    def alias_add(self, canonical: str, aliases: List[str]) -> int:
        """Add aliases for a canonical entity name.

        Args:
            canonical: The canonical (primary) entity name.
            aliases: List of alias strings to map to canonical.

        Returns:
            Number of aliases actually inserted (duplicates skipped).
        """
        if not canonical or not canonical.strip():
            return 0
        canonical = canonical.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        conn = self._alias_db()
        added = 0
        for alias in aliases:
            if not alias or not alias.strip():
                continue
            alias = alias.strip().lower()
            if alias == canonical:
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (canonical, alias, created_at) VALUES (?, ?, ?)",
                    (canonical, alias, now),
                )
                added += 1
            except sqlite3.IntegrityError:
                pass  # duplicate alias
        conn.commit()
        return added

    def alias_resolve(self, name: str) -> str:
        """Resolve an alias to its canonical name.

        If the name is not a known alias, returns the name itself (lowercased).
        """
        if not name or not name.strip():
            return name
        name = name.strip().lower()
        conn = self._alias_db()
        row = conn.execute(
            "SELECT canonical FROM aliases WHERE alias=?", (name,)
        ).fetchone()
        if row:
            return row["canonical"]
        # Also check if the name IS a canonical name (return as-is)
        row = conn.execute(
            "SELECT canonical FROM aliases WHERE canonical=?", (name,)
        ).fetchone()
        if row:
            return row["canonical"]
        return name

    def alias_list(self, canonical: str) -> List[str]:
        """List all aliases for a canonical entity name.

        Returns:
            List of alias strings (does NOT include the canonical name itself).
        """
        if not canonical or not canonical.strip():
            return []
        canonical = canonical.strip().lower()
        conn = self._alias_db()
        rows = conn.execute(
            "SELECT alias FROM aliases WHERE canonical=? ORDER BY alias",
            (canonical,),
        ).fetchall()
        return [row["alias"] for row in rows]

    def alias_all(self) -> dict:
        """Return all alias mappings as {canonical: [aliases]}."""
        conn = self._alias_db()
        rows = conn.execute(
            "SELECT canonical, alias FROM aliases ORDER BY canonical, alias"
        ).fetchall()
        result: dict = {}
        for row in rows:
            canon = row["canonical"]
            if canon not in result:
                result[canon] = []
            result[canon].append(row["alias"])
        return result

    def alias_remove(self, alias: str) -> bool:
        """Remove a specific alias. Returns True if removed."""
        if not alias or not alias.strip():
            return False
        alias = alias.strip().lower()
        conn = self._alias_db()
        cursor = conn.execute("DELETE FROM aliases WHERE alias=?", (alias,))
        conn.commit()
        return cursor.rowcount > 0

    # ── v2.4.0 convenience wrappers ─────────────────────────────
    def alias_set(self, alias: str, canonical: str) -> int:
        """Map a single alias to its canonical name.

        Thin wrapper around ``alias_add`` for the common one-at-a-time case::

            mk.alias_set("서준", "김서준")

        Returns 1 if inserted, 0 if duplicate or invalid.
        """
        return self.alias_add(canonical, [alias])

    def alias_get(self, name: str) -> str:
        """Resolve *name* to its canonical form.

        Returns the canonical name if *name* is a known alias,
        otherwise returns *name* itself (lowercased)::

            mk.alias_get("서준")  # → "김서준"
            mk.alias_get("김서준")  # → "김서준"
            mk.alias_get("unknown")  # → "unknown"
        """
        return self.alias_resolve(name)
