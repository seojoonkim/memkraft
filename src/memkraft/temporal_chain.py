"""v2.3+ — Multi-Session Temporal Chain.

Adds session-bridging context retrieval by walking temporal-typed edges
in the graph within a query-derived time window.

Public mixin methods (all additive, no breaking changes):
  * ``_is_multi_session_query(query)`` — heuristic detector
  * ``_extract_time_window(query, now=None)`` — (start_dt, end_dt) or None
  * ``_get_temporal_chain(query, top_k=5, now=None)`` — list[dict] of
    facts/edges within the window, ranked by recency.

Used by ``MultiPassMixin.search_multi`` to inject cross-session context
into the fusion blend when the query looks multi-session.

Constraints honoured:
  * Does NOT modify ``graph.py`` or ``search.py``.
  * Reads temporal edges directly via the existing ``_graph_db()``
    connection (no new tables, no schema changes).
  * Pure stdlib — no external deps.
  * Silent.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional


# Keywords that strongly suggest the user is asking about events
# spanning multiple past sessions / time windows.
# Order matters loosely: any single match flips the flag.
_MULTI_SESSION_TEMPORAL_KEYWORDS: tuple[str, ...] = (
    # English — relative windows
    "last month", "last week", "last year", "last quarter", "last few days",
    "this month", "this week", "this year", "this quarter",
    "past month", "past week", "past year", "past few days",
    "recently", "lately",
    # English — multi-session aggregation cues
    "how many", "how often", "how frequently",
    "across sessions", "between sessions", "all sessions",
    "compare", "comparison", "over time", "throughout",
    # Korean — relative windows + aggregation
    "지난달", "지난 달", "지난주", "지난 주", "작년", "지난해",
    "이번달", "이번 달", "이번주", "이번 주", "올해",
    "최근", "요즘",
    "얼마나 자주", "얼마나 많이", "몇 번", "몇번",
)


# Map relative-time phrases → timedelta (days). Longest phrases first
# so "last month" wins over "last".
_RELATIVE_WINDOWS: tuple[tuple[str, int], ...] = (
    # English (longest first)
    ("last quarter", 90),
    ("past quarter", 90),
    ("this quarter", 90),
    ("last year", 365),
    ("past year", 365),
    ("this year", 365),
    ("last month", 30),
    ("past month", 30),
    ("this month", 30),
    ("last few days", 7),
    ("past few days", 7),
    ("last week", 7),
    ("past week", 7),
    ("this week", 7),
    ("recently", 30),
    ("lately", 30),
    # Korean
    ("지난달", 30),
    ("지난 달", 30),
    ("이번달", 30),
    ("이번 달", 30),
    ("지난주", 7),
    ("지난 주", 7),
    ("이번주", 7),
    ("이번 주", 7),
    ("작년", 365),
    ("지난해", 365),
    ("올해", 365),
    ("최근", 30),
    ("요즘", 30),
)


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse a few common ISO-ish date strings → naive UTC datetime."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # Strip a trailing Z (UTC) so strptime succeeds.
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s[: len(fmt) + 6 if "%f" in fmt else len(fmt)], fmt)
        except (ValueError, IndexError):
            continue
    # Fallback: try fromisoformat
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


class TemporalChainMixin:
    """Multi-session temporal-graph traversal."""

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def _is_multi_session_query(self, query: str) -> bool:
        """Return True if the query looks like a multi-session / temporal
        aggregation question.

        Heuristic — case-insensitive substring match against
        ``_MULTI_SESSION_TEMPORAL_KEYWORDS``.  Also matches the
        "how many ... in <timeframe>" pattern.
        """
        if not isinstance(query, str) or not query.strip():
            return False
        q = query.lower()
        for kw in _MULTI_SESSION_TEMPORAL_KEYWORDS:
            if kw in q:
                return True
        # Pattern: "how many X (verb)? in (the)? past/last N days/weeks/months"
        if re.search(
            r"\b(how\s+many|how\s+often|얼마나)\b.*\b("
            r"in\s+(?:the\s+)?(?:past|last)\s+\d+\s+(?:day|week|month|year)s?|"
            r"지난\s*\d+\s*(?:일|주|개월|년)"
            r")\b",
            q,
        ):
            return True
        return False

    # ------------------------------------------------------------------
    # Time-window extraction
    # ------------------------------------------------------------------
    def _extract_time_window(
        self,
        query: str,
        now: Optional[datetime] = None,
    ) -> Optional[tuple[datetime, datetime]]:
        """Extract a (start_dt, end_dt) window from natural-language
        temporal phrases in ``query``.

        Returns ``None`` when no recognisable temporal phrase is found.
        ``end_dt`` is always ``now`` (or the supplied ``now``); the
        window is anchored to the present and reaches back ``N`` days.
        """
        if not isinstance(query, str) or not query.strip():
            return None
        if now is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        q = query.lower()

        # Numeric pattern: "in (the)? past/last N (day|week|month|year)s?"
        m = re.search(
            r"\b(?:in\s+(?:the\s+)?)?(?:past|last)\s+(\d+)\s+(day|week|month|year)s?\b",
            q,
        )
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            mult = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
            days = max(1, n * mult)
            return (now - timedelta(days=days), now)

        # Korean numeric: "지난 N (일|주|개월|년)"
        m = re.search(r"지난\s*(\d+)\s*(일|주|개월|년)", q)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            mult = {"일": 1, "주": 7, "개월": 30, "년": 365}[unit]
            days = max(1, n * mult)
            return (now - timedelta(days=days), now)

        # Phrase lookup (longest first via _RELATIVE_WINDOWS ordering).
        for phrase, days in _RELATIVE_WINDOWS:
            if phrase in q:
                return (now - timedelta(days=days), now)

        return None

    # ------------------------------------------------------------------
    # Temporal-chain retrieval
    # ------------------------------------------------------------------
    def _get_temporal_chain(
        self,
        query: str,
        top_k: int = 10,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Walk ``graph_type='temporal'`` edges whose ``valid_from``
        (or ``created_at``, when ``valid_from`` is missing) falls within
        the query-derived time window, then collect any bitemporal
        facts attached to those edges' endpoints.

        Returns a list of dicts compatible with the ``search_multi``
        result shape:

            {
                "file":   None,
                "match":  <node_id>,
                "snippet": "<from> --<rel>--> <to> @ <when>",
                "score":  <recency-weighted, 0..1>,
                "_p_temporal_score": <same>,
                "_temporal_edge": True,
                "_from": ..., "_to": ..., "_relation": ...,
                "_valid_from": ..., "_valid_until": ...,
            }

        When the graph layer is unavailable, returns an empty list.
        """
        if not hasattr(self, "_graph_db"):
            return []
        window = self._extract_time_window(query, now=now)
        if window is None:
            return []
        start_dt, end_dt = window
        start_iso = start_dt.isoformat(timespec="seconds")
        end_iso = end_dt.isoformat(timespec="seconds")

        rows: list[dict] = []
        try:
            with self._graph_db() as conn:
                cur = conn.execute(
                    "SELECT from_id, relation, to_id, weight, valid_from, "
                    "       valid_until, created_at, graph_type "
                    "FROM edges "
                    "WHERE graph_type='temporal' "
                    "  AND ("
                    "        (valid_from IS NOT NULL AND valid_from BETWEEN ? AND ?)"
                    "     OR (valid_from IS NULL AND created_at BETWEEN ? AND ?)"
                    "  )",
                    (start_iso, end_iso, start_iso, end_iso),
                )
                fetched = cur.fetchall()
        except Exception:
            return []

        if not fetched:
            return []

        # Recency weighting — newer edges get higher score.
        # Anchor: the youngest edge in the result set is 1.0, the oldest 0.3.
        edge_dts: list[datetime] = []
        edge_records: list[tuple[Any, datetime]] = []
        for row in fetched:
            when_str = row["valid_from"] or row["created_at"] or ""
            when_dt = _parse_iso(when_str) or start_dt
            edge_dts.append(when_dt)
            edge_records.append((row, when_dt))

        if edge_dts:
            newest = max(edge_dts)
            oldest = min(edge_dts)
            span_days = max((newest - oldest).days, 1)
        else:
            newest = end_dt
            oldest = start_dt
            span_days = 1

        seen_keys: set[tuple[str, str, str]] = set()
        for row, when_dt in edge_records:
            from_id = row["from_id"]
            relation = row["relation"]
            to_id = row["to_id"]
            valid_from = row["valid_from"] or ""
            valid_until = row["valid_until"] or ""
            key = (from_id, relation, to_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            age_days = (newest - when_dt).days
            if span_days <= 0:
                rec_score = 1.0
            else:
                rec_score = round(0.3 + 0.7 * (1 - age_days / span_days), 3)
            rec_score = max(0.3, min(1.0, rec_score))
            when_label = valid_from or row["created_at"] or ""
            snippet = f"{from_id} --{relation}--> {to_id}"
            if when_label:
                snippet += f" @ {when_label}"
            rows.append(
                {
                    "file": None,
                    "match": from_id,
                    "snippet": snippet,
                    "score": rec_score,
                    "_p_temporal_score": rec_score,
                    "_temporal_edge": True,
                    "_from": from_id,
                    "_to": to_id,
                    "_relation": relation,
                    "_valid_from": valid_from,
                    "_valid_until": valid_until,
                }
            )

        # Optionally enrich with facts attached to involved nodes —
        # this is what bridges separate sessions: a temporal edge
        # "deploy_v1 --before--> deploy_v2" pulls in any bitemporal
        # facts about deploy_v1 / deploy_v2.
        if hasattr(self, "fact_history"):
            seen_facts: set[tuple[str, str, str]] = set()
            involved: list[str] = []
            seen_nodes: set[str] = set()
            for r in rows:
                for n in (r["_from"], r["_to"]):
                    if n and n not in seen_nodes:
                        seen_nodes.add(n)
                        involved.append(n)
            for ent in involved:
                try:
                    facts = self.fact_history(ent) or []
                except Exception:
                    facts = []
                for f in facts:
                    fkey = (ent, f.get("key", ""), str(f.get("value", "")))
                    if fkey in seen_facts:
                        continue
                    seen_facts.add(fkey)
                    # Only include facts whose recorded_at is within the window.
                    rec_str = f.get("recorded_at") or f.get("valid_from") or ""
                    rec_dt = _parse_iso(rec_str)
                    if rec_dt is not None and not (
                        start_dt <= rec_dt <= end_dt
                    ):
                        continue
                    if rec_dt is None:
                        # Without a parsable timestamp we keep it but
                        # discount its score so it doesn't outrank
                        # actually-dated edges.
                        score = 0.3
                    else:
                        age_days = (newest - rec_dt).days
                        score = max(
                            0.3,
                            round(0.3 + 0.7 * (1 - age_days / span_days), 3),
                        )
                    snippet = f"{f.get('key','')}={f.get('value','')}"
                    if rec_str:
                        snippet += f" (@ {rec_str})"
                    rows.append(
                        {
                            "file": None,
                            "match": ent,
                            "snippet": snippet,
                            "score": score,
                            "_p_temporal_score": score,
                            "_temporal_edge": False,
                            "_temporal_fact": True,
                            "_entity": ent,
                            "_key": f.get("key", ""),
                            "_value": f.get("value", ""),
                            "_recorded_at": rec_str,
                        }
                    )

        rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        return rows[: max(top_k, 1)]
