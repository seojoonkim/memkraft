"""v1.0.2 Search enhancements — additive, non-breaking.

Adds three new methods to MemKraft without touching core.py:

- ``search_v2(query, top_k=20, expand_query=False, fuzzy=False)`` —
  thin wrapper over the core ``search`` that supports top_k limiting
  and optional query expansion (keyword-only variants) for better
  recall on natural-language questions.

- ``search_expand(query, top_k=20, fuzzy=False)`` — convenience alias
  for ``search_v2(query, expand_query=True)``.

- ``search_temporal(query, date_hint=None, top_k=20, fuzzy=False)`` —
  same as ``search_v2`` but boosts results whose content contains the
  given date hint (YYYY-MM-DD) or a nearby date.  Falls back to
  ``search_v2`` when no hint is provided.

Design constraints honoured:
  * Does NOT modify core.py or the existing ``search`` signature.
  * Builds on public primitives only (``self.search`` + simple I/O
    helpers already exposed by MemKraft).
  * Silent by default — no stdout noise (unlike the legacy ``search``).
"""
from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List


# Lightweight multilingual stopword set.  Intentionally small — stop
# list is used only to *generate additional* query variants, the
# original query is always tried first and kept in the merged pool.
_STOPWORDS: set[str] = {
    # English — interrogative / function / filler
    "how", "what", "when", "where", "which", "who", "why", "whom", "whose",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "do", "did", "does", "doing", "done",
    "have", "had", "has", "having",
    "i", "my", "me", "mine", "myself",
    "you", "your", "yours", "yourself", "yourselves",
    "we", "our", "ours", "ourselves",
    "they", "their", "theirs", "them", "themselves",
    "he", "she", "his", "her", "hers", "him", "it", "its",
    "this", "that", "these", "those",
    "to", "of", "in", "on", "at", "for", "with", "about", "from", "by",
    "as", "if", "or", "and", "but", "not", "no", "yes",
    "can", "could", "would", "will", "should", "may", "might", "must", "shall",
    "there", "here", "then", "than", "so", "too", "very", "much", "many",
    "some", "any", "all", "every", "each", "few", "more", "most", "other",
    "ago", "since", "before", "after", "during", "while", "until", "till",
    "regularly", "currently", "recently", "often", "still", "already",
    "remind", "tell", "remember", "know", "knew", "think", "thought",
    "please", "also", "just", "now", "only", "ever", "never",
    "last", "first", "next", "previous",
    # Korean — common particles / endings
    "이", "가", "은", "는", "을", "를", "의", "에", "에서", "로", "으로",
    "와", "과", "도", "만", "까지", "부터", "에게", "한테",
    "했다", "한다", "해요", "합니다", "입니다", "있다", "없다", "같다",
    "그리고", "그러나", "그런데", "하지만", "또한", "또는", "혹은",
}


class SearchMixin:
    """v1.0.2 additive search API.  Attach via ``__init__.py`` mixin loop."""

    # ------------------------------------------------------------------
    # v2.4.0 helpers — decay reset + dedup
    # ------------------------------------------------------------------
    def _reset_decay(self, results: list[dict]) -> None:
        """Touch last_accessed for every hit so decay score stays fresh."""
        now_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in results:
            fpath = r.get("file") if isinstance(r, dict) else None
            if fpath and hasattr(self, "_touch_last_accessed"):
                try:
                    self._touch_last_accessed(fpath, now_str)
                except Exception:
                    pass

    @staticmethod
    def _dedup_by_key(results: list[dict]) -> list[dict]:
        """Deduplicate results by entity key, keeping the highest score."""
        seen: dict[str, dict] = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            key = r.get("match") or r.get("file") or ""
            if key in seen:
                if r.get("score", 0) > seen[key].get("score", 0):
                    seen[key] = r
            else:
                seen[key] = r
        return sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _v102_keyword_variants(self, query: str, max_variants: int = 3) -> list[str]:
        """Derive keyword-only variants of ``query`` for recall expansion.

        Returns up to ``max_variants`` additional queries, never
        including the original query itself.  Variants are constructed
        from non-stopword tokens (length >= 3) to capture the topical
        content of the question.
        """
        if not query or not query.strip():
            return []

        # Grab alphanumeric tokens (preserve apostrophes / hyphens mid-word)
        tokens = re.findall(r"[\w][\w'\-]*", query.lower(), flags=re.UNICODE)
        keywords: list[str] = []
        seen: set[str] = set()
        for t in tokens:
            if len(t) < 3:
                continue
            if t in _STOPWORDS:
                continue
            if t in seen:
                continue
            seen.add(t)
            keywords.append(t)

        variants: list[str] = []
        if not keywords:
            return variants

        # Variant 1: all meaningful keywords joined (up to 6 — captures
        # the topical skeleton of the question without single-token
        # noise).
        joined = " ".join(keywords[:6])
        if joined and joined != query.lower():
            variants.append(joined)

        # Variant 2: top 3 keywords — captures short phrases like
        # "sugar factory icon", "summer nights".
        if len(keywords) >= 3:
            top = " ".join(keywords[:3])
            if top not in variants and top != query.lower():
                variants.append(top)

        # Deliberately NOT adding single-token variants: empirically
        # they lower precision on natural-language questions by
        # matching unrelated sessions.  Callers that want aggressive
        # recall can pass their own keywords directly.

        return variants[:max_variants]

    def _v102_run_search(self, query: str, fuzzy: bool = False) -> list[dict]:
        """Run the core ``search`` while suppressing its stdout side
        effects.  Returns the result list verbatim (may be empty)."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                # ``self.search`` here is the legacy core method —
                # SearchMixin is attached AFTER the class exists, so
                # the base ``search`` is still reachable as the class
                # method defined in core.py.
                out = self.search(query, fuzzy=fuzzy)
        except Exception:
            return []
        return out if isinstance(out, list) else []

    def _v102_merge(self, batches: Iterable[list[dict]]) -> list[dict]:
        """Merge multiple result batches, keeping the max score per file."""
        merged: dict[str, dict] = {}
        for batch in batches:
            for r in batch:
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
    # Public API (v1.0.2)
    # ------------------------------------------------------------------
    def search_v2(
        self,
        query: str,
        top_k: int = 20,
        expand_query: bool = False,
        fuzzy: bool = False,
    ) -> list[dict]:
        """Enhanced search with top_k limiting and optional expansion.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum number of results to return (default 20, vs 10 in
            the legacy ``search``).
        expand_query:
            When True, also runs keyword-only variants of the query
            and merges the result sets (keeping the max score per
            file).  Improves recall on verbose / conversational
            questions at a small latency cost.
        fuzzy:
            Forwarded to the underlying ``search``.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 20

        batches: list[list[dict]] = [self._v102_run_search(query, fuzzy=fuzzy)]
        if expand_query:
            for variant in self._v102_keyword_variants(query):
                batches.append(self._v102_run_search(variant, fuzzy=fuzzy))

        merged = self._v102_merge(batches)
        result = merged[:top_k]
        self._reset_decay(result)
        return result

    def search_expand(
        self,
        query: str,
        top_k: int = 20,
        fuzzy: bool = False,
    ) -> list[dict]:
        """Convenience: ``search_v2(query, top_k, expand_query=True)``."""
        return self.search_v2(query, top_k=top_k, expand_query=True, fuzzy=fuzzy)

    # ------------------------------------------------------------------
    # v1.0.2 Phase 2 — Score-based ranking + per-query-type strategy
    # ------------------------------------------------------------------
    _TEMPORAL_KW = (
        "when", "date", "year", "month", "week", "day", "days",
        "how long", "ago", "since", "before", "after",
        "언제", "며칠", "얼마나",
    )
    _PREFERENCE_KW = (
        "favorite", "prefer", "like", "enjoy", "love", "hate", "best",
        "suggest", "recommend",
        "좋아하", "선호", "추천",
    )
    _COUNT_KW = (
        "how many", "count", "number of", "total",
        # v2.6.x — extra count-like phrasings that benefit from
        # multi-session aggregation (was being mis-routed to ``fact``
        # before, which suppressed query expansion).
        "how often", "how frequent", "how frequently",
        "list all", "each of",
        "in total", "altogether", "combined", "overall",
        "몇", "얼마", "몇 번", "몇번", "총",
    )

    def _v102_classify(self, query: str) -> str:
        """Classify a natural-language query into a retrieval strategy bucket.

        Returns one of: ``temporal``, ``preference``, ``count``, ``fact``.
        Heuristic — never raises.
        """
        if not isinstance(query, str) or not query.strip():
            return "fact"
        q = query.lower()
        if any(kw in q for kw in self._COUNT_KW):
            return "count"
        if any(kw in q for kw in self._TEMPORAL_KW):
            return "temporal"
        if any(kw in q for kw in self._PREFERENCE_KW):
            return "preference"
        return "fact"

    def search_ranked(
        self,
        query: str,
        top_k: int = 20,
        min_score: float = 0.0,
        fuzzy: bool = False,
    ) -> list[dict]:
        """Core search with an explicit score floor.

        Unlike ``search_expand`` this does NOT fire keyword variants —
        the goal is precision, not recall.  Results below ``min_score``
        are dropped *only* when at least one result clears the floor;
        otherwise the original result list is returned verbatim so the
        caller never ends up with an empty hand when the corpus is
        small (e.g. LongMemEval oracle with 1-3 sessions total).
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 20

        base = self._v102_run_search(query, fuzzy=fuzzy)
        base.sort(key=lambda x: x.get("score", 0), reverse=True)

        if min_score <= 0:
            result = base[:top_k]
            self._reset_decay(result)
            return result

        above = [r for r in base if r.get("score", 0) >= min_score]
        if above:
            result = above[:top_k]
            self._reset_decay(result)
            return result
        # Do not starve the caller — small corpora may have all scores
        # below an aggressive floor.
        result = base[:top_k]
        self._reset_decay(result)
        return result

    def search_smart(
        self,
        query: str,
        top_k: int = 20,
        date_hint: str | None = None,
        fuzzy: bool = False,
    ) -> list[dict]:
        """Strategy-dispatch search.

        - **temporal** questions → ``search_temporal`` (date-aware)
        - **count**/multi-session questions → ``search_expand`` with a
          larger ``top_k`` to capture every relevant session
        - **preference** questions → ``search_expand`` (recall matters
          more than precision when the target is a style, not a fact)
        - **fact** questions → ``search_ranked`` (precision-first, no
          variant expansion to avoid topic drift)
        """
        strategy = self._v102_classify(query)
        if strategy == "temporal":
            return self.search_temporal(
                query, date_hint=date_hint, top_k=top_k, fuzzy=fuzzy
            )
        if strategy == "count":
            # Multi-item questions benefit from wider recall.
            # v2.6.x: also fold in 1-hop graph neighbors of any entities
            # the user query mentions (when the graph mixin is
            # available) — counting questions often span sessions that
            # share an entity but not a keyword with the question.
            base = self.search_expand(query, top_k=max(top_k, 30), fuzzy=fuzzy)
            try:
                neighbor_results = self._count_neighbor_expansion(
                    query, top_k=top_k
                )
            except Exception:
                neighbor_results = []
            if neighbor_results:
                # Merge while keeping max score per file.
                merged = self._v102_merge([base, neighbor_results])
                return merged[: max(top_k, 30)][:top_k]
            return base[:top_k]
        if strategy == "preference":
            return self.search_expand(query, top_k=top_k, fuzzy=fuzzy)
        # fact
        return self.search_ranked(query, top_k=top_k, min_score=0.0, fuzzy=fuzzy)

    # ------------------------------------------------------------------
    # v2.6.x — Counting-question neighbor expansion (graph 1-hop)
    # ------------------------------------------------------------------
    def _count_neighbor_expansion(
        self,
        query: str,
        top_k: int = 20,
    ) -> list[dict]:
        """For counting questions, broaden recall by asking the graph
        for files that mention any 1-hop neighbor of an entity touched
        by ``query``. Best-effort — returns ``[]`` when the graph mixin
        is missing or no entities can be resolved.

        Each returned dict mirrors ``search_v2`` output
        (``{file, score, snippet}``). Scores are deliberately small
        (0.05 floor) so neighbor hits never out-rank direct keyword
        matches; they only fill gaps when a session shares an entity
        but not a literal keyword with the question.
        """
        if not query or not query.strip():
            return []

        # Pull candidate entity names from the query (alpha tokens >=3 chars,
        # not stopwords). Cheap heuristic — the graph layer will
        # silently ignore unknown nodes.
        tokens = re.findall(r"[\w][\w'\-]*", query.lower(), flags=re.UNICODE)
        candidates: list[str] = []
        seen: set[str] = set()
        for t in tokens:
            if len(t) < 3 or t in _STOPWORDS or t in seen:
                continue
            seen.add(t)
            candidates.append(t)
        if not candidates:
            return []

        neighbors: list[str] = []
        graph_neighbors = getattr(self, "graph_neighbors", None)
        if not callable(graph_neighbors):
            return []
        for cand in candidates[:5]:
            try:
                paths = graph_neighbors(cand, hops=1) or []
            except Exception:
                continue
            for p in paths:
                # graph_neighbors returns a list of paths; each path is
                # a list of (src, rel, dst) edges. Pull the dst names.
                if isinstance(p, (list, tuple)):
                    for edge in p:
                        if isinstance(edge, (list, tuple)) and len(edge) >= 3:
                            dst = str(edge[2]) if edge[2] else ""
                            if dst and dst.lower() not in seen and len(dst) >= 3:
                                neighbors.append(dst)
                                seen.add(dst.lower())
        if not neighbors:
            return []

        merged: dict[str, dict] = {}
        for n in neighbors[:8]:
            try:
                hits = self.search_v2(n, top_k=max(top_k, 10), expand_query=False)
            except Exception:
                continue
            for r in hits or []:
                if not isinstance(r, dict):
                    continue
                f = r.get("file")
                if not f:
                    continue
                # Cap neighbor score so it never out-ranks a direct keyword hit.
                damped = min(0.50, max(0.05, float(r.get("score", 0)) * 0.6))
                prev = merged.get(f)
                if prev is None or damped > prev.get("score", 0):
                    new_r = dict(r)
                    new_r["score"] = damped
                    new_r["_neighbor_expansion"] = True
                    merged[f] = new_r
        return sorted(
            merged.values(), key=lambda x: x.get("score", 0), reverse=True
        )[:top_k]

    # ------------------------------------------------------------------
    # v2.5.0 — Multi-query RRF fusion + Context budget check
    # ------------------------------------------------------------------

    def search_multi_query(
        self,
        queries: List[str],
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Multi-query RRF fusion search.

        Runs each query through ``search_smart`` and fuses the results
        using Reciprocal Rank Fusion (RRF).  Useful when a single
        phrasing may miss relevant results — multiple formulations
        increase recall without sacrificing precision (RRF handles
        the ranking).

        Parameters
        ----------
        queries:
            List of query strings.
        top_k:
            Maximum number of results to return (default 10).

        Returns
        -------
        list[dict]
            Deduplicated, RRF-ranked results.
        """
        if not queries:
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 10

        batches: List[List[Dict[str, Any]]] = []
        for q in queries:
            if isinstance(q, str) and q.strip():
                batches.append(self.search_smart(q, top_k=top_k))

        if not batches:
            return []

        if len(batches) == 1:
            return batches[0][:top_k]

        # Use RRF fusion (self._rrf_fusion from RRFMixin)
        if hasattr(self, "_rrf_fusion"):
            fused = self._rrf_fusion(*batches)
        else:
            # Fallback: simple score-max merge
            fused = self._v102_merge(batches)

        return fused[:top_k]

    @staticmethod
    def context_budget_check(
        results: List[Dict[str, Any]],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        """Check whether search results fit within a token budget.

        Estimates total tokens (chars / 4 ≈ tokens) for all result
        snippets.  If the total exceeds ``max_tokens``, truncates the
        list to fit.

        Parameters
        ----------
        results:
            List of search result dicts (each may have ``snippet``
            and/or ``file`` keys).
        max_tokens:
            Maximum allowed tokens (default 4000).

        Returns
        -------
        dict
            ``{"total_tokens": N, "over_budget": bool,
              "truncated_results": [...]}``
        """
        if not results:
            return {"total_tokens": 0, "over_budget": False, "truncated_results": []}

        def _estimate_tokens(text: str) -> int:
            """Estimate token count from character count (chars / 4)."""
            return max(1, len(text) // 4) if text else 0

        truncated: List[Dict[str, Any]] = []
        total_tokens = 0
        over_budget = False

        for r in results:
            # Combine snippet + file path for estimation
            snippet = r.get("snippet", "") or ""
            file_path = r.get("file", "") or ""
            match = r.get("match", "") or ""
            text = f"{match} {snippet} {file_path}".strip()

            est = _estimate_tokens(text)

            if total_tokens + est > max_tokens:
                over_budget = True
                # Still add if we haven't added anything yet
                if not truncated:
                    truncated.append(r)
                break

            total_tokens += est
            truncated.append(r)

        return {
            "total_tokens": total_tokens,
            "over_budget": over_budget,
            "truncated_results": truncated,
        }

    def search_temporal(
        self,
        query: str,
        date_hint: str | None = None,
        top_k: int = 20,
        fuzzy: bool = False,
        window_days: int = 30,
    ) -> list[dict]:
        """Search with an optional date hint.

        Results whose content (or filename) contains ``date_hint`` or a
        date within ``window_days`` of it get a boost in the returned
        score.  When ``date_hint`` is None or malformed, behaves
        exactly like ``search_v2(..., expand_query=True)``.
        """
        base = self.search_v2(
            query,
            top_k=max(top_k * 2, top_k),
            expand_query=True,
            fuzzy=fuzzy,
        )
        if not date_hint:
            return base[:top_k]

        try:
            hint_dt = datetime.strptime(date_hint[:10], "%Y-%m-%d")
        except ValueError:
            return base[:top_k]

        date_re = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
        boosted: list[dict] = []
        for r in base:
            score = float(r.get("score", 0) or 0)
            fpath = r.get("file", "") or ""
            snippet = r.get("snippet", "") or ""

            # Try to read a larger chunk for date detection
            content_for_date = f"{fpath}\n{snippet}"
            try:
                abs_path = self.base_dir / fpath if fpath else None
                if abs_path and abs_path.exists():
                    content_for_date = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

            boost = 0.0
            if date_hint[:10] in content_for_date:
                boost = 0.15
            else:
                # Look for any date within window_days of the hint
                for m in date_re.findall(content_for_date):
                    try:
                        d = datetime.strptime(m, "%Y-%m-%d")
                    except ValueError:
                        continue
                    delta = abs((d - hint_dt).days)
                    if delta <= window_days:
                        # Linear falloff: closer → bigger boost
                        boost = max(boost, 0.10 * (1 - delta / max(window_days, 1)))

            new = dict(r)
            new["score"] = round(min(1.0, score + boost), 3)
            if boost:
                new["_temporal_boost"] = round(boost, 3)
            boosted.append(new)

        boosted.sort(key=lambda x: x.get("score", 0), reverse=True)
        result = boosted[:top_k]
        self._reset_decay(result)
        return result
