"""v2.2 — Multi-Pass Retrieval.

Adds ``search_multi(query, top_k=5, passes=3)`` to MemKraft as an
additive mixin. Combines three retrieval passes for higher accuracy:

  Pass 1 — Exact + entity match (existing ``search_v2``).
  Pass 2 — Graph expansion (neighbors of top hits via ``graph_neighbors``).
  Pass 3 — Temporal timeline (``fact_history`` with recency weighting).

Final score is a weighted blend:
    final = 0.5 * p1 + 0.3 * p2 + 0.2 * p3

Constraints honoured:
  * Does NOT modify ``core.py``, ``graph.py``, ``bitemporal.py`` or
    the existing ``search``/``search_v2`` APIs — purely additive.
  * Uses only public mixin primitives already attached to MemKraft:
    ``search_v2``, ``graph_neighbors``, ``fact_history``.
  * No external dependencies (stdlib only).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable


# Same minimal stopword set used by SearchMixin / GraphMixin so entity
# extraction stays consistent across passes.
_STOPWORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "do", "did", "does", "have", "had", "has",
    "i", "you", "we", "they", "he", "she", "it",
    "my", "your", "our", "their", "his", "her", "its",
    "this", "that", "these", "those",
    "to", "of", "in", "on", "at", "for", "with", "from", "by", "as",
    "and", "or", "but", "not", "if", "so", "than", "then",
    "how", "what", "when", "where", "why", "who", "which",
    "session", "messages", "user", "assistant", "date",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _extract_entities_from_text(text: str, *, max_entities: int = 6) -> list[str]:
    """Extract candidate entity names (capitalized + Korean) from text."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()

    # English: capitalized words (not at sentence start ideally, but we
    # accept any capitalized token >=3 chars that isn't a stopword)
    for m in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text):
        low = m.lower()
        if low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(low)
        if len(out) >= max_entities:
            return out

    # Korean tokens (>=2 chars)
    for m in re.findall(r"[\uac00-\ud7af]{2,}", text):
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
        if len(out) >= max_entities:
            return out

    return out


def _entity_from_filename(stem: str) -> str | None:
    """Treat first space-separated token of the file stem as an entity hint."""
    if not stem:
        return None
    cleaned = stem.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return None
    first = cleaned.split()[0].lower()
    if first in _STOPWORDS or len(first) < 2:
        return None
    return first


class MultiPassMixin:
    """v2.2 additive multi-pass retrieval API."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mp_pass1(self, query: str, top_k: int) -> list[dict]:
        """Pass 1 — Exact + entity match (delegates to ``search_v2``).

        Returns enriched dicts of the form:
            {file, score, match, snippet, _p1_score}
        """
        try:
            base = self.search_v2(query, top_k=max(top_k * 2, 1), fuzzy=True)
        except Exception:
            base = []
        out: list[dict] = []
        for r in base or []:
            if not isinstance(r, dict):
                continue
            sc = float(r.get("score", 0) or 0)
            out.append(
                {
                    "file": r.get("file"),
                    "match": r.get("match", ""),
                    "snippet": r.get("snippet", ""),
                    "score": sc,
                    "_p1_score": sc,
                }
            )
        return out

    def _mp_pass2(
        self,
        query: str,
        seed_entities: list[str],
        top_k: int,
    ) -> tuple[list[dict], list[str]]:
        """Pass 2 — Graph expansion.

        For each seed entity, walk ``graph_neighbors(entity, hops=1)``
        and score neighbours by IDF-weighted overlap with ``query``
        tokens.  Returns (results, expanded_entity_list).
        """
        if not seed_entities:
            return [], []
        if not hasattr(self, "graph_neighbors"):
            return [], []

        # Pre-tokenise the query for relevance scoring.
        try:
            q_tokens = list(self._search_tokens(query.lower())) if query else []
        except Exception:
            q_tokens = re.findall(r"\w+", (query or "").lower())
        q_tokens = [t for t in q_tokens if t not in _STOPWORDS and len(t) >= 2]
        q_set = set(q_tokens)

        seen_targets: set[str] = set()
        expanded: list[str] = []
        results: list[dict] = []

        for entity in seed_entities:
            try:
                paths = self.graph_neighbors(entity, hops=1) or []
            except Exception:
                continue
            for p in paths:
                target = (p.get("target") or "").strip().lower()
                relation = p.get("relation") or ""
                step_text = p.get("text") or ""
                if not target or target in seen_targets:
                    continue
                seen_targets.add(target)
                expanded.append(target)

                # Relevance: token overlap between (target + relation) and query.
                hay = f"{target} {relation}".lower()
                hay_tokens = set(re.findall(r"\w+", hay))
                overlap = len(q_set & hay_tokens)
                if q_set:
                    overlap_ratio = overlap / max(len(q_set), 1)
                else:
                    overlap_ratio = 0.0

                # Base score from being a 1-hop neighbour of a seed; bump
                # with overlap so query-relevant neighbours rise.
                pass2_score = round(min(1.0, 0.4 + 0.6 * overlap_ratio), 3)

                results.append(
                    {
                        "file": None,
                        "match": target,
                        "snippet": step_text,
                        "score": pass2_score,
                        "_p2_score": pass2_score,
                        "_neighbor_of": entity,
                        "_relation": relation,
                    }
                )

        # Limit pass 2 to a reasonable pool size.
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[: max(top_k * 3, top_k)], expanded

    def _mp_pass3(
        self,
        entities: Iterable[str],
        top_k: int,
    ) -> list[dict]:
        """Pass 3 — Temporal timeline via ``fact_history``.

        Collects every fact for each entity and applies a recency
        weight (newer ``recorded_at`` → higher score).  This is what
        makes knowledge-update questions ("what is X *now*?") work.
        """
        if not entities:
            return []
        if not hasattr(self, "fact_history"):
            return []

        seen_keys: set[tuple[str, str, str]] = set()
        rows: list[tuple[dict, datetime | None]] = []

        for ent in entities:
            ent = (ent or "").strip()
            if not ent:
                continue
            try:
                facts = self.fact_history(ent) or []
            except Exception:
                continue
            for f in facts:
                key = f.get("key", "")
                value = f.get("value", "")
                rec = f.get("recorded_at") or ""
                vf = f.get("valid_from") or ""
                vt = f.get("valid_to") or ""
                dedup = (ent, key, value)
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)

                rec_dt: datetime | None = None
                if rec:
                    for fmt in (
                        "%Y-%m-%dT%H:%M:%S.%fZ",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d",
                    ):
                        try:
                            rec_dt = datetime.strptime(rec[: len(fmt) + 6], fmt)
                            break
                        except (ValueError, IndexError):
                            continue

                snippet = f"{key}={value}"
                if vf or vt:
                    snippet += f" (valid {vf or '…'}…{vt or 'now'})"
                rows.append(
                    (
                        {
                            "file": None,
                            "match": ent,
                            "snippet": snippet,
                            "_entity": ent,
                            "_key": key,
                            "_value": value,
                            "_recorded_at": rec,
                            "_valid_from": vf,
                            "_valid_to": vt,
                            # is_open = current/active fact (no valid_to)
                            "_is_open": not vt,
                        },
                        rec_dt,
                    )
                )

        if not rows:
            return []

        # Recency weighting — newer recorded_at gets a higher score
        # within [0.3, 1.0].  When recorded_at can't be parsed we
        # default to the floor so the fact still surfaces.
        valid_dts = [d for _, d in rows if d is not None]
        results: list[dict] = []
        if valid_dts:
            newest = max(valid_dts)
            oldest = min(valid_dts)
            span_days = max((newest - oldest).days, 1)
            for row, dt in rows:
                if dt is None:
                    rec_score = 0.3
                else:
                    age_days = (newest - dt).days
                    rec_score = round(0.3 + 0.7 * (1 - age_days / span_days), 3)
                # Open (currently-valid) facts get a small bump — the
                # whole point of bitemporal retrieval is "what is true now".
                if row.get("_is_open"):
                    rec_score = min(1.0, rec_score + 0.1)
                row["score"] = rec_score
                row["_p3_score"] = rec_score
                results.append(row)
        else:
            for row, _dt in rows:
                rec_score = 0.4 if row.get("_is_open") else 0.3
                row["score"] = rec_score
                row["_p3_score"] = rec_score
                results.append(row)

        # Sort newest-first and cap at a generous multiple of top_k.
        results.sort(key=lambda x: x.get("_recorded_at", ""), reverse=True)
        return results[: max(top_k * 3, top_k)]

    def _mp_blend(
        self,
        pass1: list[dict],
        pass2: list[dict],
        pass3: list[dict],
    ) -> list[dict]:
        """Combine the three pass outputs into a single ranked list.

        Dedup key prefers ``file`` (filesystem hit) when present, else
        ``("entity", match)`` for graph/temporal hits.  Per-pass scores
        are preserved and a blended ``score`` is written.
        """
        merged: dict[tuple, dict] = {}

        def _key_for(r: dict) -> tuple:
            f = r.get("file")
            if f:
                return ("file", f)
            # Pass 3 (temporal) hits: dedup per (entity, key, value) so
            # that distinct historical facts about the same entity stay
            # separate (e.g. role=junior vs role=senior).
            if r.get("_entity") is not None or r.get("_key") is not None:
                return (
                    "fact",
                    (r.get("_entity") or r.get("match") or "").lower(),
                    r.get("_key", ""),
                    r.get("_value", ""),
                )
            # Pass 2 (graph) hits: dedup per (entity, relation) so two
            # different relations on the same target stay distinct.
            if r.get("_relation") is not None:
                return (
                    "graph",
                    (r.get("match") or "").lower(),
                    r.get("_relation", ""),
                    r.get("_neighbor_of", ""),
                )
            return ("entity", (r.get("match") or "").lower())

        def _absorb(r: dict, p1: float = 0.0, p2: float = 0.0, p3: float = 0.0) -> None:
            k = _key_for(r)
            existing = merged.get(k)
            if existing is None:
                payload = dict(r)
                payload["_p1_score"] = max(payload.get("_p1_score", 0.0), p1)
                payload["_p2_score"] = max(payload.get("_p2_score", 0.0), p2)
                payload["_p3_score"] = max(payload.get("_p3_score", 0.0), p3)
                payload["source_passes"] = sorted(
                    {n for n, s in [(1, p1), (2, p2), (3, p3)] if s > 0}
                )
                merged[k] = payload
            else:
                existing["_p1_score"] = max(existing.get("_p1_score", 0.0), p1)
                existing["_p2_score"] = max(existing.get("_p2_score", 0.0), p2)
                existing["_p3_score"] = max(existing.get("_p3_score", 0.0), p3)
                # Prefer richer snippet/file when arriving from another pass.
                if not existing.get("snippet") and r.get("snippet"):
                    existing["snippet"] = r["snippet"]
                if not existing.get("file") and r.get("file"):
                    existing["file"] = r["file"]
                merged_passes = set(existing.get("source_passes", []))
                merged_passes.update(
                    {n for n, s in [(1, p1), (2, p2), (3, p3)] if s > 0}
                )
                existing["source_passes"] = sorted(merged_passes)

        for r in pass1:
            _absorb(r, p1=float(r.get("_p1_score", r.get("score", 0)) or 0))
        for r in pass2:
            _absorb(r, p2=float(r.get("_p2_score", r.get("score", 0)) or 0))
        for r in pass3:
            _absorb(r, p3=float(r.get("_p3_score", r.get("score", 0)) or 0))

        out: list[dict] = []
        for r in merged.values():
            p1 = float(r.get("_p1_score", 0) or 0)
            p2 = float(r.get("_p2_score", 0) or 0)
            p3 = float(r.get("_p3_score", 0) or 0)
            blended = round(0.5 * p1 + 0.3 * p2 + 0.2 * p3, 4)
            r["score"] = blended
            r["pass_scores"] = {
                "p1": round(p1, 4),
                "p2": round(p2, 4),
                "p3": round(p3, 4),
            }
            out.append(r)

        out.sort(key=lambda x: x.get("score", 0), reverse=True)
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search_multi(
        self,
        query: str,
        top_k: int = 5,
        passes: int = 3,
    ) -> list[dict]:
        """Multi-pass retrieval for higher accuracy.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum results to return (default 5).
        passes:
            How many retrieval passes to run (1, 2, or 3).
            * 1 — Exact / token match only (Pass 1).
            * 2 — Pass 1 + graph neighbour expansion (Pass 2).
            * 3 — Pass 1 + 2 + bitemporal fact timeline (Pass 3).
            Out-of-range values are clamped to [1, 3].

        Returns
        -------
        list[dict]
            Ranked, deduplicated hits.  Each entry contains:

            * ``score`` — blended score (0.5·p1 + 0.3·p2 + 0.2·p3)
            * ``pass_scores`` — per-pass component scores
            * ``source_passes`` — which passes contributed
            * ``file`` — markdown file path (if a filesystem hit)
            * ``match`` — best textual handle for the hit
            * ``snippet`` — context snippet
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 5
        if not isinstance(passes, int) or passes < 1:
            passes = 1
        passes = min(passes, 3)

        # Pass 1 (always)
        pass1 = self._mp_pass1(query, top_k=top_k)

        # Seed entity list — used for both Pass 2 and Pass 3.
        seed_entities: list[str] = []
        seen_seed: set[str] = set()
        for r in pass1[: max(top_k, 5)]:
            for cand in (
                _entity_from_filename(r.get("match", "")),
                *_extract_entities_from_text(r.get("snippet", "")),
            ):
                if cand and cand not in seen_seed:
                    seen_seed.add(cand)
                    seed_entities.append(cand)
        # Also pull any explicit entity tokens from the query itself —
        # essential when Pass 1 misses (Pass 2 graph hop saves us).
        for cand in _extract_entities_from_text(query):
            if cand not in seen_seed:
                seen_seed.add(cand)
                seed_entities.append(cand)

        pass2: list[dict] = []
        expanded_entities: list[str] = []
        if passes >= 2:
            pass2, expanded_entities = self._mp_pass2(query, seed_entities, top_k=top_k)

        pass3: list[dict] = []
        if passes >= 3:
            timeline_entities = list({*seed_entities, *expanded_entities})
            pass3 = self._mp_pass3(timeline_entities, top_k=top_k)

        merged = self._mp_blend(pass1, pass2, pass3)
        return merged[:top_k]
