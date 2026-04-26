"""v2.2 Question-Type Routing — additive, non-breaking.

Adds a single new public method ``search_smart_v2()`` plus five helpers to
MemKraft without touching ``core.py``, ``search.py``, or ``graph.py``.

Five question types (LongMemEval-aligned):
  * ``single_session``    — direct factual lookup ("when did X happen?")
  * ``multi_session``     — cross-session aggregation ("how often", "compare")
  * ``knowledge_update``  — current-state queries ("what is X *now*?")
  * ``temporal_reasoning``— ordering / before-after questions
  * ``preference``        — likes / dislikes / favourites

Each type is dispatched to a different retrieval strategy.  All strategies
fall back to ``self.search(query, fuzzy=True)`` when they return empty so
no caller ever ends up with zero results purely because of routing.

Design constraints honoured:
  * Does NOT modify or wrap existing ``search`` / ``search_smart``.
  * Does NOT modify ``graph.py``.
  * No external dependencies — pure stdlib.
  * Silent (no stdout side effects).
"""
from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Keyword tables — lowercase ASCII + Korean.  Order is significant: types
# checked earlier "win" when keywords overlap (e.g. a query containing both
# "now" and "compare" routes to ``multi_session`` because comparison wins).
# ---------------------------------------------------------------------------

_QUESTION_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Cross-session aggregation — checked first so "compare current X vs Y"
    # routes here rather than to ``knowledge_update``.
    "multi_session": (
        "compare", "comparison", "how often", "both", "all sessions",
        "across", "between", "versus", " vs ", " vs.", "frequency",
        "비교", "얼마나 자주", "전부", "모든", "각각",
    ),
    # Temporal ordering — before/after/timeline/sequence.  Must be checked
    # before ``knowledge_update`` because "before" / "after" can otherwise
    # be swallowed by the "current/now" pattern.
    "temporal_reasoning": (
        "before", "after", "first", "last", "timeline", "sequence",
        "earlier", "later", "previously", "in order",
        "전에", "나중에", "순서", "먼저", "뒤에", "이전", "이후",
    ),
    # Knowledge-update — current/latest state.
    "knowledge_update": (
        "current", "currently", "now", "today", "changed", "updated",
        "latest", "most recent", "still", "anymore",
        "현재", "지금", "바뀌", "최근", "요즘", "최신",
    ),
    # Preferences / likes.
    "preference": (
        "prefer", "preference", "like", "likes", "favorite", "favourite",
        "hate", "dislike", "love", "loves", "enjoy", "enjoys",
        "좋아", "싫어", "선호", "최애", "취향",
    ),
    # Single-session factual lookup — interrogative-only patterns.  Kept
    # last because every other type can also start with "what" / "when".
    "single_session": (
        "what did", "when did", "where did", "who said", "what was",
        "when was", "where was", "what time",
        "언제", "어디서", "어디에서", "뭐라고", "무엇을", "누가 말",
    ),
}

# Order in which buckets are tested — earlier wins.
_TYPE_ORDER: tuple[str, ...] = (
    "multi_session",
    "temporal_reasoning",
    "knowledge_update",
    "preference",
    "single_session",
)

# Used by ``_search_temporal_timeline`` to find dates in result content.
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Used by ``_search_preference`` to boost preference-flavoured snippets.
_PREFERENCE_BOOST_TOKENS: tuple[str, ...] = (
    "prefer", "favorite", "favourite", "like", "love", "enjoy",
    "hate", "dislike",
    "좋아", "싫어", "선호", "최애",
)


class RoutingMixin:
    """Question-type aware retrieval (v2.2)."""

    # ------------------------------------------------------------------
    # Internals — silent ``search`` runner reused by every strategy.
    # ------------------------------------------------------------------
    def _r22_run_search(self, query: str, fuzzy: bool = False) -> list[dict]:
        """Call ``self.search`` with stdout silenced; tolerate failures."""
        if not isinstance(query, str) or not query.strip():
            return []
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                out = self.search(query, fuzzy=fuzzy)
        except Exception:
            return []
        return out if isinstance(out, list) else []

    def _r22_merge(self, batches) -> list[dict]:
        """Dedupe by ``file`` keeping the max score per file."""
        merged: dict[str, dict] = {}
        for batch in batches:
            for r in batch or []:
                if not isinstance(r, dict):
                    continue
                fpath = r.get("file")
                if not fpath:
                    continue
                prev = merged.get(fpath)
                if prev is None or r.get("score", 0) > prev.get("score", 0):
                    merged[fpath] = r
        return sorted(
            merged.values(),
            key=lambda x: x.get("score", 0),
            reverse=True,
        )

    # ------------------------------------------------------------------
    # 1. Classifier
    # ------------------------------------------------------------------
    @staticmethod
    def _r22_kw_match(query_lower: str, kw: str) -> bool:
        """Match keyword against query with word boundaries for short Latin
        tokens, but allow plain substring search for multi-word phrases
        ("how often") and CJK keywords (no whitespace concept).
        """
        if not kw:
            return False
        # CJK keyword → simple substring (Korean has no space-delimited
        # word boundaries inside common phrases like '跸不').
        if any(ord(c) > 0x3000 for c in kw):
            return kw in query_lower
        # Multi-word phrase → substring is safe (whitespace is a guard).
        if " " in kw.strip():
            return kw in query_lower
        # Single Latin token → require word boundaries to avoid matches
        # like 'now' inside 'Knows', or 'like' inside 'unlike-something'.
        return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", query_lower) is not None

    def _classify_question(self, query: str) -> str:
        """Classify ``query`` into one of the five LongMemEval buckets.

        Returns ``'general'`` when no keyword matches (so callers can
        special-case a generic fallback).  Never raises.
        """
        if not isinstance(query, str) or not query.strip():
            return "general"
        q = query.lower()
        for q_type in _TYPE_ORDER:
            for kw in _QUESTION_TYPE_KEYWORDS[q_type]:
                if self._r22_kw_match(q, kw):
                    return q_type
        return "general"

    # ------------------------------------------------------------------
    # 2. Type-specific strategies
    # ------------------------------------------------------------------
    def _search_temporal_latest(self, query: str) -> list[dict]:
        """Prefer the most recently-updated documents.

        Strategy:
          1. Run ``search_expand`` (recall-favouring) to gather candidates.
          2. Re-score using each file's mtime — newer → higher.

        The mtime-based boost is small (≤0.20) so very strong textual
        matches still win over a slightly-newer but unrelated doc.
        """
        # search_expand provides better recall on "current/now" phrasing.
        expand = getattr(self, "search_expand", None)
        if callable(expand):
            base = expand(query, top_k=30, fuzzy=True)
        else:
            base = self._r22_run_search(query, fuzzy=True)

        if not base:
            return []

        # Compute mtime per file (best-effort).
        mtimes: list[float] = []
        for r in base:
            fpath = r.get("file") or ""
            mt = 0.0
            try:
                ap = self.base_dir / fpath if fpath else None
                if ap and ap.exists():
                    mt = ap.stat().st_mtime
            except Exception:
                mt = 0.0
            mtimes.append(mt)

        if not any(mtimes):
            # No usable mtimes — return the base ranking unchanged.
            return base

        max_mt = max(mtimes) or 1.0
        min_mt = min(m for m in mtimes if m > 0) if any(mtimes) else 0.0
        span = max(max_mt - min_mt, 1.0)

        boosted: list[dict] = []
        for r, mt in zip(base, mtimes):
            new = dict(r)
            score = float(new.get("score", 0) or 0)
            if mt > 0 and span > 0:
                # Linear in [0, 0.20]: most-recent file gets full 0.20.
                recency = (mt - min_mt) / span
                boost = 0.20 * recency
                new["score"] = round(min(1.0, score + boost), 3)
                new["_recency_boost"] = round(boost, 3)
            boosted.append(new)

        boosted.sort(key=lambda x: x.get("score", 0), reverse=True)
        return boosted

    def _search_temporal_timeline(self, query: str) -> list[dict]:
        """Sort hits by the earliest date mentioned in their content.

        Useful for "what came first / what came after" questions.  We
        keep the original score as a secondary sort key.
        """
        expand = getattr(self, "search_expand", None)
        if callable(expand):
            base = expand(query, top_k=30, fuzzy=True)
        else:
            base = self._r22_run_search(query, fuzzy=True)

        if not base:
            return []

        annotated: list[tuple[datetime | None, float, dict]] = []
        for r in base:
            fpath = r.get("file") or ""
            content = r.get("snippet", "") or ""
            try:
                ap = self.base_dir / fpath if fpath else None
                if ap and ap.exists():
                    content = ap.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

            earliest: datetime | None = None
            for m in _DATE_RE.findall(content):
                try:
                    d = datetime.strptime(m, "%Y-%m-%d")
                except ValueError:
                    continue
                if earliest is None or d < earliest:
                    earliest = d

            new = dict(r)
            if earliest is not None:
                new["_timeline_date"] = earliest.strftime("%Y-%m-%d")
            annotated.append((earliest, float(new.get("score", 0) or 0), new))

        # Sort: dated docs first (chronological), undated last (by score).
        dated = [(d, s, r) for d, s, r in annotated if d is not None]
        undated = [(d, s, r) for d, s, r in annotated if d is None]
        dated.sort(key=lambda x: (x[0], -x[1]))
        undated.sort(key=lambda x: -x[1])

        return [r for _, _, r in dated] + [r for _, _, r in undated]

    def _search_preference(self, query: str) -> list[dict]:
        """Boost results whose content mentions like/dislike/preference."""
        expand = getattr(self, "search_expand", None)
        if callable(expand):
            base = expand(query, top_k=30, fuzzy=True)
        else:
            base = self._r22_run_search(query, fuzzy=True)

        if not base:
            return []

        boosted: list[dict] = []
        for r in base:
            new = dict(r)
            score = float(new.get("score", 0) or 0)
            fpath = new.get("file") or ""
            content = (new.get("snippet", "") or "").lower()
            try:
                ap = self.base_dir / fpath if fpath else None
                if ap and ap.exists():
                    content = ap.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                pass

            hits = sum(1 for tok in _PREFERENCE_BOOST_TOKENS if tok in content)
            if hits:
                # Cap at +0.25 (5 hits @ 0.05 each) to avoid runaway scores.
                boost = min(0.25, 0.05 * hits)
                new["score"] = round(min(1.0, score + boost), 3)
                new["_preference_boost"] = round(boost, 3)
            boosted.append(new)

        boosted.sort(key=lambda x: x.get("score", 0), reverse=True)
        return boosted

    def _search_multi_session(self, query: str) -> list[dict]:
        """Aggregate hits across multiple keyword variants.

        Prefers ``search_multi(passes=3)`` (v2.2 multi-pass retrieval)
        when available — it already does graph expansion + bitemporal
        fact aggregation, which is exactly what cross-session questions
        need.  Falls back to a keyword-variant merge when ``search_multi``
        is missing (older builds / mixin disabled in tests).
        """
        smulti = getattr(self, "search_multi", None)
        if callable(smulti):
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    out = smulti(query, top_k=30, passes=3)
                if isinstance(out, list) and out:
                    return out
            except Exception:
                pass  # fall through to variant merge

        batches: list[list[dict]] = [self._r22_run_search(query, fuzzy=True)]

        # Pull the keyword variants used by SearchMixin if available.
        gen_variants = getattr(self, "_v102_keyword_variants", None)
        variants: list[str] = []
        if callable(gen_variants):
            try:
                variants = gen_variants(query) or []
            except Exception:
                variants = []
        for v in variants:
            batches.append(self._r22_run_search(v, fuzzy=True))

        return self._r22_merge(batches)

    # ------------------------------------------------------------------
    # 3. Public dispatcher
    # ------------------------------------------------------------------
    def search_smart_v2(
        self,
        query: str,
        top_k: int = 5,
    ) -> dict:
        """Question-type aware retrieval.

        Returns a dict with three keys:
          * ``question_type`` — one of ``single_session``,
            ``multi_session``, ``knowledge_update``, ``temporal_reasoning``,
            ``preference``, or ``general`` (no keyword matched).
          * ``results`` — at most ``top_k`` result dicts (same shape as
            ``self.search`` returns).
          * ``strategy`` — a short string describing the retrieval path
            actually taken (useful for debugging and offline eval).

        Behaviour:
          * Empty / whitespace-only queries → ``{'question_type': 'general',
            'results': [], 'strategy': 'empty_query'}``.
          * Falls back to ``self.search(query, fuzzy=True)`` when the
            primary strategy returns nothing.
        """
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 5

        if not isinstance(query, str) or not query.strip():
            return {
                "question_type": "general",
                "results": [],
                "strategy": "empty_query",
            }

        q_type = self._classify_question(query)
        results: list[dict] = []
        strategy = ""

        if q_type == "single_session":
            # Precision-first: exact-match search, then a fuzzy pass for
            # graceful degradation on near-miss phrasings.
            exact = self._r22_run_search(query, fuzzy=False)
            fuzzy = self._r22_run_search(query, fuzzy=True)
            results = self._r22_merge([exact, fuzzy])
            strategy = "exact-then-fuzzy (single_session)"

        elif q_type == "multi_session":
            results = self._search_multi_session(query)
            strategy = "multi-variant aggregation (multi_session)"

        elif q_type == "knowledge_update":
            results = self._search_temporal_latest(query)
            strategy = "recency-boosted (knowledge_update)"

        elif q_type == "temporal_reasoning":
            results = self._search_temporal_timeline(query)
            strategy = "chronological sort (temporal_reasoning)"

        elif q_type == "preference":
            results = self._search_preference(query)
            strategy = "preference-keyword boost (preference)"

        else:  # general fallback
            results = self._r22_run_search(query, fuzzy=True)
            strategy = "fuzzy fallback (general)"

        # Universal fallback so we never starve the caller silently.
        if not results:
            fb = self._r22_run_search(query, fuzzy=True)
            if fb:
                results = fb
                strategy = strategy + " → fuzzy fallback"

        return {
            "question_type": q_type,
            "results": results[:top_k],
            "strategy": strategy,
        }
