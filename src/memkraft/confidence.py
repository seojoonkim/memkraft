"""v2.4 — Confidence threshold + implicit-acquisition pattern detection.

Adds a ``confidence`` field (``"high"`` / ``"medium"`` / ``"low"``) to every
search result and lifts implicit-acquisition phrases ("thinking of getting",
"considering buying", "살까") into low-confidence hits so multi-session
counting questions don't silently miss them.

Design constraints honoured:
  * Does NOT change the signatures of ``search_v2``, ``search_expand``,
    ``search_ranked``, ``search_smart``, ``search_temporal``, or
    ``search_multi`` — only the result dicts grow a new ``confidence``
    key (and the existing ``score`` stays untouched).
  * Does NOT touch ``core.py`` or ``graph.py``.
  * Public API additions:
      - ``MemKraft._classify_confidence(score, snippet, query) -> str``
      - ``MemKraft._attach_confidence(results, query) -> list[dict]``
      - ``MemKraft._has_implicit_acquisition(text) -> bool``
      - ``MemKraft.format_results_for_llm(results, *, include_low=True)``
  * stdlib only.

Mechanics:
  * ``confidence`` is computed from the *existing* ``score`` plus a
    pattern-match check against the snippet.  No new IO, no extra
    search passes.
  * When a result's snippet contains an *implicit acquisition* phrase
    (configurable ``_IMPLICIT_PATTERNS``), the result is forced to
    ``"low"`` confidence and tagged ``_implicit_acquisition=True`` so
    callers that want to filter them are still able to.
  * ``search_v2`` and ``search_multi`` are wrapped so every list they
    return passes through ``_attach_confidence``.  The wrap is applied
    once at import time inside ``ConfidenceMixin``.
"""
from __future__ import annotations

import functools
import re
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# Implicit-acquisition language — these phrases describe an *intent* to
# acquire / buy / get something, not a confirmed acquisition.  When they
# appear in a snippet we still want to surface the hit (recall ↑) but
# the LLM should be told it's low-confidence evidence so it doesn't
# count an "I'm thinking of getting an orchid" message as a confirmed
# plant ownership.
_IMPLICIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # English — verb phrases
    re.compile(r"\bthinking\s+of\s+(?:getting|buying|purchasing|picking\s+up|grabbing)\b", re.IGNORECASE),
    re.compile(r"\bthinking\s+about\s+(?:getting|buying|purchasing)\b", re.IGNORECASE),
    re.compile(r"\bconsidering\s+(?:getting|buying|purchasing|picking\s+up)\b", re.IGNORECASE),
    re.compile(r"\bmight\s+(?:get|buy|purchase|pick\s+up|grab)\b", re.IGNORECASE),
    re.compile(r"\bmaybe\s+(?:get|buy|purchase|i'?ll\s+get|i'?ll\s+buy)\b", re.IGNORECASE),
    re.compile(r"\bplanning\s+to\s+(?:get|buy|purchase|pick\s+up)\b", re.IGNORECASE),
    re.compile(r"\bplan\s+to\s+(?:get|buy|purchase)\b", re.IGNORECASE),
    re.compile(r"\b(?:would|could)\s+(?:like\s+to|love\s+to)\s+(?:get|buy|own|have)\b", re.IGNORECASE),
    re.compile(r"\blooking\s+(?:into|at)\s+(?:getting|buying|a\s+\w+)", re.IGNORECASE),
    re.compile(r"\bdebating\s+(?:getting|buying|whether)", re.IGNORECASE),
    re.compile(r"\bmaybe\s+i'?ll\s+(?:get|buy|grab|pick\s+up)", re.IGNORECASE),
    re.compile(r"\b(?:i'?m|i\s+am)\s+thinking\s+about\b", re.IGNORECASE),
    re.compile(r"\bon\s+the\s+fence\s+about\s+(?:getting|buying)\b", re.IGNORECASE),
    # Korean — 살까 / 생각 중 / 고려 중 / 사고 싶 / 살지 고민
    re.compile(r"살까"),
    re.compile(r"살지\s*고민"),
    re.compile(r"생각\s*중"),
    re.compile(r"고려\s*중"),
    re.compile(r"고민\s*중"),
    re.compile(r"사고\s*싶"),
    re.compile(r"구입\s*(?:할까|고려)"),
)


# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------
# Tuned so results coming out of the existing search/multi-pass blend
# fall into sensible buckets.  Raw ``search`` scores in MemKraft are in
# [0, 1] with most matched files landing in [0.3, 0.9].
_HIGH_THRESHOLD: float = 0.7
_MEDIUM_THRESHOLD: float = 0.4


def _coerce_score(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _has_implicit_acquisition(text: str) -> bool:
    """True iff ``text`` contains any pattern from ``_IMPLICIT_PATTERNS``."""
    if not text or not isinstance(text, str):
        return False
    for pat in _IMPLICIT_PATTERNS:
        if pat.search(text):
            return True
    return False


def _classify_confidence(
    score: float,
    snippet: str,
    *,
    fuzzy_only: bool = False,
    implicit_hit: bool | None = None,
) -> str:
    """Map (score, snippet, fuzzy?) → ``"high"`` / ``"medium"`` / ``"low"``.

    Rules (in order):
      1. Implicit-acquisition language → ``"low"`` (recall, not confirmation).
      2. ``score > _HIGH_THRESHOLD`` and not fuzzy-only → ``"high"``.
      3. ``_MEDIUM_THRESHOLD <= score <= _HIGH_THRESHOLD`` → ``"medium"``.
      4. Otherwise → ``"low"``.
    """
    s = _coerce_score(score)
    if implicit_hit is None:
        implicit_hit = _has_implicit_acquisition(snippet or "")
    if implicit_hit:
        return "low"
    if fuzzy_only:
        # Fuzzy-only matches are never "high" — they can be medium at best.
        return "medium" if s >= _MEDIUM_THRESHOLD else "low"
    if s > _HIGH_THRESHOLD:
        return "high"
    if s >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _attach_confidence(results: list[dict] | None, query: str = "") -> list[dict]:
    """Mutate-and-return ``results`` so every entry gains a ``confidence`` field.

    Safe on ``None`` / non-list inputs (returns ``[]`` / passes through).
    Idempotent — re-running on already-classified results is a no-op
    unless an entry's ``snippet`` or ``score`` changed.
    """
    if not results or not isinstance(results, list):
        return results if isinstance(results, list) else []

    for r in results:
        if not isinstance(r, dict):
            continue
        snippet = r.get("snippet") or ""
        match_text = r.get("match") or ""
        # Pull together everything that *could* contain implicit phrasing.
        haystack = f"{match_text}\n{snippet}"
        implicit = _has_implicit_acquisition(haystack)
        score = _coerce_score(r.get("score"))
        # Only graph-pass-2 / temporal-pass-3 hits typically lack a
        # snippet match; treat their score as authoritative.
        fuzzy_only = bool(r.get("_fuzzy_only"))
        conf = _classify_confidence(
            score, haystack, fuzzy_only=fuzzy_only, implicit_hit=implicit
        )
        r["confidence"] = conf
        if implicit:
            r["_implicit_acquisition"] = True
            # Make it easy for downstream LLM formatters to mark these.
            r.setdefault("confidence_reason", "implicit_acquisition_phrase")
    return results


# ---------------------------------------------------------------------------
# LLM formatting helper
# ---------------------------------------------------------------------------
def _format_results_for_llm(
    results: Iterable[dict] | None,
    *,
    include_low: bool = True,
    max_snippet_chars: int = 220,
) -> str:
    """Render search results as a confidence-tagged plain-text block.

    Output shape:

        [high confidence] peace lily — acquired from nursery
        [medium confidence] succulent collection — added 3 plants
        --- low confidence (potential / inferred) ---
        [low confidence] orchid — thinking of getting fertilizer (might indicate acquisition intent)

    The low-confidence section header only appears when at least one
    low-confidence hit is being included.  When ``include_low=False``
    low-confidence rows are dropped entirely.
    """
    if not results:
        return ""

    high_med: list[str] = []
    low: list[str] = []

    for r in results:
        if not isinstance(r, dict):
            continue
        conf = r.get("confidence") or "low"
        match = (r.get("match") or "").strip()
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > max_snippet_chars:
            snippet = snippet[: max_snippet_chars - 1].rstrip() + "…"
        # File path provides a useful fallback identifier.
        file = (r.get("file") or "").strip()
        head = match or file or "(unnamed)"
        line_body = f"{head} — {snippet}" if snippet else head

        if conf == "high":
            high_med.append(f"[high confidence] {line_body}")
        elif conf == "medium":
            high_med.append(f"[medium confidence] {line_body}")
        else:
            if not include_low:
                continue
            tag = "[low confidence]"
            if r.get("_implicit_acquisition"):
                line_body += " (might indicate acquisition intent)"
            low.append(f"{tag} {line_body}")

    parts: list[str] = list(high_med)
    if low:
        parts.append("--- low confidence (potential / inferred) ---")
        parts.extend(low)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Mixin — wraps existing public methods so the ``confidence`` field
# appears automatically without touching their signatures.
# ---------------------------------------------------------------------------
class ConfidenceMixin:
    """v2.4 confidence + implicit-acquisition handling.

    Attached via ``__init__.py`` mixin loop.  The mixin:
      * Exposes ``_classify_confidence`` / ``_attach_confidence`` /
        ``_has_implicit_acquisition`` / ``format_results_for_llm`` on
        every MemKraft instance.
      * Wraps ``search_v2`` and ``search_multi`` (when present) so
        their return values gain the ``confidence`` field.
      * Adds ``search_with_confidence(query, …, include_low=True)`` —
        a convenience that runs ``search_multi`` (or ``search_v2`` as
        fallback) and returns the ranked list with low-confidence
        hits either kept or stripped per the flag.
    """

    # ------------------------------------------------------------------
    # Static-style helpers exposed as bound methods for convenience.
    # ------------------------------------------------------------------
    def _has_implicit_acquisition(self, text: str) -> bool:  # noqa: D401
        return _has_implicit_acquisition(text)

    def _classify_confidence(
        self,
        score: float,
        snippet: str = "",
        *,
        fuzzy_only: bool = False,
    ) -> str:
        return _classify_confidence(score, snippet, fuzzy_only=fuzzy_only)

    def _attach_confidence(
        self,
        results: list[dict] | None,
        query: str = "",
    ) -> list[dict]:
        return _attach_confidence(results, query)

    def format_results_for_llm(
        self,
        results: list[dict] | None,
        *,
        include_low: bool = True,
        max_snippet_chars: int = 220,
    ) -> str:
        return _format_results_for_llm(
            results,
            include_low=include_low,
            max_snippet_chars=max_snippet_chars,
        )

    # ------------------------------------------------------------------
    # v2.5 — Temporal annotation + integrated context formatter.
    # ------------------------------------------------------------------
    def _annotate_temporal(self, result: dict) -> dict:
        """Tag a result's snippet with ``[valid_from ~ valid_until]``.

        Returns the *same* dict (mutates in place) for ergonomic
        chaining.  Idempotent — if the snippet already begins with a
        ``[... ~ ...]`` tag the function leaves it alone.
        """
        if not isinstance(result, dict):
            return result
        snippet = result.get("snippet") or ""
        if isinstance(snippet, str) and snippet.lstrip().startswith("["):
            # Already annotated — bail out (idempotency).
            existing = snippet.lstrip()
            if " ~ " in existing.split("]", 1)[0]:
                return result
        valid_from = result.get("valid_from")
        valid_until = result.get("valid_until")
        recorded_at = result.get("recorded_at") or result.get("asserted_at")
        if not (valid_from or recorded_at):
            return result
        # Build the temporal tag.
        vu = valid_until if valid_until else "present"
        if valid_from:
            if recorded_at and recorded_at != valid_from:
                tag = f"[recorded: {recorded_at}, valid: {valid_from} ~ {vu}]"
            else:
                tag = f"[{valid_from} ~ {vu}]"
        else:
            tag = f"[recorded: {recorded_at}]"
        result["_temporal_tag"] = tag
        if snippet:
            result["snippet"] = f"{tag} {snippet}".strip()
        else:
            result["snippet"] = tag
        return result

    def format_context_for_llm(
        self,
        results: list[dict] | None,
        query: str = "",
        question_type: str | None = None,
        *,
        max_chars: int = 5000,
        max_lines: int | None = None,
        max_snippet_chars: int = 220,
        include_low: bool = True,
    ) -> str:
        """v2.5 integrated context formatter.

        Pipeline:
          1. Re-rank by question type (uses ``RerankMixin`` if attached).
          2. Annotate each result with its temporal range.
          3. Compress into ``max_chars`` (uses ``ContextCompressMixin`` if
             attached) — picks the highest-priority unique facts.
          4. Render with confidence tags (low-conf section is preserved
             when ``include_low=True``).

        Falls back gracefully when any optional mixin is missing — the
        method always returns a non-``None`` string.
        """
        if not results:
            return ""

        rows: list[dict] = [r for r in results if isinstance(r, dict)]
        if not rows:
            return ""

        # 1. Re-rank.
        rerank = getattr(self, "rerank_for_question_type", None)
        if callable(rerank) and question_type:
            try:
                rows = rerank(rows, question_type)
            except Exception:
                pass  # never let re-ranking break formatting.

        # 2. Temporal annotation.
        for r in rows:
            self._annotate_temporal(r)

        # 3. Compression.
        compress_select = getattr(self, "_compress_select", None)
        if callable(compress_select):
            try:
                rows = compress_select(
                    rows,
                    query=query,
                    max_chars=max_chars,
                    max_lines=max_lines,
                    max_line_chars=max_snippet_chars,
                )
            except Exception:
                pass

        # 4. Render with confidence tags.
        text = _format_results_for_llm(
            rows,
            include_low=include_low,
            max_snippet_chars=max_snippet_chars,
        )
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

    # ------------------------------------------------------------------
    # Convenience entry point that always emits confidence labels.
    # ------------------------------------------------------------------
    def search_with_confidence(
        self,
        query: str,
        top_k: int = 10,
        *,
        include_low: bool = True,
        passes: int = 3,
    ) -> list[dict]:
        """Run multi-pass search (or v2 fallback) with confidence labels.

        Parameters
        ----------
        query, top_k, passes:
            Forwarded to ``search_multi`` when available.
        include_low:
            When False, low-confidence hits (including implicit-
            acquisition matches) are filtered out before returning.
            When True (default), every hit is returned with its
            ``confidence`` label intact and counting / multi-session
            questions get the recall boost.
        """
        smulti = getattr(self, "search_multi", None)
        if callable(smulti):
            try:
                results = smulti(query, top_k=max(top_k * 2, top_k), passes=passes)
            except Exception:
                results = []
        else:
            sv2 = getattr(self, "search_v2", None)
            results = sv2(query, top_k=max(top_k * 2, top_k)) if callable(sv2) else []

        results = _attach_confidence(results or [], query=query)
        if not include_low:
            results = [r for r in results if r.get("confidence") != "low"]
        return results[:top_k]


# ---------------------------------------------------------------------------
# Wrap helpers — applied from __init__ after the base class is ready.
# ---------------------------------------------------------------------------
def _wrap_with_confidence(method: Callable[..., Any]) -> Callable[..., Any]:
    """Return a wrapped version of ``method`` that post-processes lists.

    Uses ``functools.wraps`` so ``inspect.signature`` keeps reporting
    the original parameter list (callers and tests that introspect the
    method shouldn't notice the wrap).
    """

    @functools.wraps(method)
    def _wrapped(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = method(self, *args, **kwargs)
        if isinstance(out, list):
            query = ""
            if args and isinstance(args[0], str):
                query = args[0]
            elif "query" in kwargs and isinstance(kwargs["query"], str):
                query = kwargs["query"]
            return _attach_confidence(out, query=query)
        return out

    _wrapped.__wrapped_with_confidence__ = True  # type: ignore[attr-defined]
    return _wrapped


def install_confidence_wrappers(target_cls: type) -> None:
    """Wrap public search methods on ``target_cls`` to attach confidence.

    Idempotent — checks ``__wrapped_with_confidence__`` to avoid
    double-wrapping when the module is reloaded (test harnesses).
    """
    for name in ("search_v2", "search_multi", "search_expand", "search_ranked", "search_temporal"):
        method = getattr(target_cls, name, None)
        if method is None:
            continue
        if getattr(method, "__wrapped_with_confidence__", False):
            continue
        setattr(target_cls, name, _wrap_with_confidence(method))
