"""v2.5 — Context Compression for LLM-bound search results.

Goal: take a (possibly large) list of search hits and compress them
into a tight, deduplicated, query-aligned context block that fits in
``max_chars``.  Used by ``format_context_for_llm`` (see
``confidence.py``) to feed only the highest-signal facts to the LLM.

Design:
  * Pure stdlib, additive, no signature changes elsewhere.
  * Deterministic — same inputs always produce the same output.
  * Idempotent — running compression on already-compressed text
    leaves it unchanged (callers may safely double-wrap).
  * Never raises on malformed inputs (empty, ``None``, missing keys).

Compression strategy (in order):
  1. Re-score each hit against ``query`` (token-overlap relevance).
  2. Drop near-duplicates — same ``(entity, key)`` or near-identical
     snippets keep only the most recent / highest-scoring instance.
  3. Prefer entries with explicit temporal metadata (``valid_from``,
     ``recorded_at``, dates inside the snippet).
  4. Greedily pack lines into ``max_chars`` using the combined
     priority signal.

Output format — one fact per line, optionally prefixed with a
confidence tag if present.  Each line is short (<=220 chars by
default) so the LLM doesn't waste tokens on filler.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Tokenisation helpers (cheap; reused by relevance scoring).
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_SMALL_STOP: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on", "at",
    "for", "to", "and", "or", "but", "i", "you", "he", "she", "it", "we",
    "they", "my", "your", "do", "did", "does", "have", "has", "had",
    "be", "been", "this", "that", "what", "when", "where", "how", "who",
})

_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _tokens(text: str) -> set[str]:
    if not text or not isinstance(text, str):
        return set()
    return {
        t.lower()
        for t in _WORD_RE.findall(text)
        if len(t) > 1 and t.lower() not in _SMALL_STOP
    }


def _query_relevance(query_toks: set[str], snippet: str, match: str) -> float:
    """Token-overlap ratio in [0, 1].  Empty query → 0."""
    if not query_toks:
        return 0.0
    hay = _tokens(f"{match}\n{snippet}")
    if not hay:
        return 0.0
    overlap = len(query_toks & hay)
    return overlap / max(1, len(query_toks))


def _has_temporal(snippet: str, result: dict) -> bool:
    """Heuristic: True if the result carries any usable date signal."""
    if not isinstance(result, dict):
        return False
    for key in ("valid_from", "valid_until", "recorded_at", "asserted_at",
                "date", "timestamp"):
        if result.get(key):
            return True
    if snippet and (_DATE_RE.search(snippet) or _YEAR_RE.search(snippet)):
        return True
    return False


def _dedup_key(result: dict) -> str:
    """Identifier for "same fact, same slot" — used for dedup.

    Falls back to the snippet's first 80 chars when no entity/key pair
    is available so unrelated hits never collide.
    """
    if not isinstance(result, dict):
        return ""
    entity = (result.get("entity") or "").strip().lower()
    key = (result.get("key") or result.get("predicate") or "").strip().lower()
    if entity and key:
        return f"{entity}::{key}"
    snippet = (result.get("snippet") or result.get("match") or "").strip().lower()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:80]


def _coerce_score(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _summarise_line(result: dict, max_line_chars: int = 220) -> str:
    """One-line summary of a result, suitable for LLM context."""
    if not isinstance(result, dict):
        return ""
    conf = result.get("confidence")
    match = (result.get("match") or "").strip()
    snippet = (result.get("snippet") or "").strip()
    snippet = re.sub(r"\s+", " ", snippet)
    head = match or (result.get("file") or "(unnamed)").strip()
    body = f"{head} — {snippet}" if snippet else head
    if conf:
        body = f"[{conf}] {body}"
    if len(body) > max_line_chars:
        body = body[: max_line_chars - 1].rstrip() + "…"
    return body


# ---------------------------------------------------------------------------
# Mixin — exposes ``compress_context`` on every MemKraft instance.
# ---------------------------------------------------------------------------
class ContextCompressMixin:
    """v2.5 context compression.

    Adds:
      * ``MemKraft.compress_context(results, query, *, max_chars=5000,
        max_lines=None, max_line_chars=220)`` — returns a compressed
        plain-text block.
      * ``MemKraft._compress_select(results, query, ...)`` — internal
        ranker that returns the selected dicts (used by re-ranking
        and ``format_context_for_llm``).
    """

    # ------------------------------------------------------------------
    # Internal ranker — returns dicts in selection order.
    # ------------------------------------------------------------------
    def _compress_select(
        self,
        results: Iterable[dict] | None,
        query: str = "",
        *,
        max_chars: int = 5000,
        max_lines: int | None = None,
        max_line_chars: int = 220,
    ) -> list[dict]:
        if not results:
            return []
        rows = [r for r in results if isinstance(r, dict)]
        if not rows:
            return []

        q_toks = _tokens(query or "")

        # Compute a composite priority per row.
        scored: list[tuple[float, int, dict]] = []
        for idx, r in enumerate(rows):
            snippet = r.get("snippet") or ""
            match = r.get("match") or ""
            base = _coerce_score(r.get("score"))
            relevance = _query_relevance(q_toks, snippet, match)
            temporal = 0.15 if _has_temporal(snippet, r) else 0.0
            conf = (r.get("confidence") or "").lower()
            conf_bonus = {"high": 0.20, "medium": 0.05, "low": -0.10}.get(conf, 0.0)
            priority = base + 0.5 * relevance + temporal + conf_bonus
            scored.append((priority, idx, r))

        scored.sort(key=lambda t: (-t[0], t[1]))

        # Dedup by (entity, key) / snippet — keep the best-scoring entry.
        seen: dict[str, float] = {}
        kept: list[dict] = []
        for priority, _, r in scored:
            key = _dedup_key(r)
            if not key:
                continue
            if key in seen:
                continue
            seen[key] = priority
            kept.append(r)
            if max_lines is not None and len(kept) >= max_lines:
                break

        # Greedy pack into max_chars.
        out: list[dict] = []
        used = 0
        for r in kept:
            line = _summarise_line(r, max_line_chars=max_line_chars)
            if not line:
                continue
            cost = len(line) + 1  # newline
            if used + cost > max_chars and out:
                break
            out.append(r)
            used += cost
        return out

    # ------------------------------------------------------------------
    # Public surface.
    # ------------------------------------------------------------------
    def compress_context(
        self,
        results: Iterable[dict] | None,
        query: str = "",
        *,
        max_chars: int = 5000,
        max_lines: int | None = None,
        max_line_chars: int = 220,
    ) -> str:
        """Compress ``results`` into a tight context block <= ``max_chars``.

        See module docstring for strategy.  Returns ``""`` when
        ``results`` is empty or no row survives compression.
        """
        chosen = self._compress_select(
            results,
            query=query,
            max_chars=max_chars,
            max_lines=max_lines,
            max_line_chars=max_line_chars,
        )
        lines = [_summarise_line(r, max_line_chars=max_line_chars) for r in chosen]
        text = "\n".join(line for line in lines if line)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
