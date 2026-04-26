"""v2.5 — Re-ranking by question type.

Adds a per-question-type re-ranker on top of the existing
multi-pass / RRF / confidence pipeline.  The intent is *fine-grained*
ordering nudges, not a wholesale re-score: the routing layer already
picks the right strategy, this module just makes sure the most
useful evidence floats to the top of the LLM's context window for
each question class.

Design:
  * Pure stdlib, additive, no signature changes.
  * Idempotent — re-running on already-ranked results is stable.
  * Bonuses are bounded (≤ +0.30) so a strong base score still wins
    over a weak hit that happens to match a heuristic.
  * Question-type strings match the routing module's labels:
    ``counting``, ``knowledge_update``, ``temporal_reasoning``,
    ``preference``, ``multi_session``, ``single_session``, ``general``.

Public API:
  * ``MemKraft.rerank_for_question_type(results, question_type) -> list[dict]``
  * ``MemKraft._rerank_bonus(result, question_type) -> float`` (helper).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Pattern banks.  Compiled once at import time; case-insensitive where it
# makes sense.  Korean patterns are added inline.
# ---------------------------------------------------------------------------
_ACQUISITION_RE = re.compile(
    r"\b(?:acquired|bought|got|received|purchased|picked\s+up|added|"
    r"obtained|grabbed|brought|adopted)\b",
    re.IGNORECASE,
)
_ACQUISITION_KO = re.compile(r"(샀|구매|받았|입양|들였|구입|얻었|장만)")

_PREF_RE = re.compile(
    r"\b(?:prefer|preferred|prefers|like|likes|liked|love|loves|loved|"
    r"enjoy|enjoys|enjoyed|favorite|favourite|hate|hates|dislike|dislikes)\b",
    re.IGNORECASE,
)
_PREF_KO = re.compile(r"(좋아|싫어|선호|취향|최애)")

_BEFORE_AFTER_RE = re.compile(
    r"\b(?:before|after|earlier|later|previously|first|last|then|next|"
    r"prior\s+to|following)\b",
    re.IGNORECASE,
)
_BEFORE_AFTER_KO = re.compile(r"(전에|이전|이후|나중|먼저|뒤에|순서)")

_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _coerce_score(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _haystack(result: dict) -> str:
    return f"{(result.get('match') or '')}\n{(result.get('snippet') or '')}"


def _has_temporal_metadata(result: dict) -> bool:
    for k in ("valid_from", "valid_until", "recorded_at", "asserted_at",
              "date", "timestamp"):
        if result.get(k):
            return True
    return False


def _is_open_ended(result: dict) -> bool:
    """True when fact has no explicit ``valid_until`` (still in effect)."""
    if not isinstance(result, dict):
        return False
    if not result.get("valid_from"):
        return False
    vu = result.get("valid_until")
    if vu in (None, "", "present", "PRESENT"):
        return True
    return False


# ---------------------------------------------------------------------------
# Bonus functions per question type.  Each returns a float in [-0.20, 0.30].
# ---------------------------------------------------------------------------
def _bonus_counting(r: dict) -> float:
    text = _haystack(r)
    bonus = 0.0
    if _ACQUISITION_RE.search(text) or _ACQUISITION_KO.search(text):
        bonus += 0.30
    # Implicit-acquisition phrasing → small *penalty* so confirmed
    # acquisitions outrank "thinking of buying".
    if r.get("_implicit_acquisition"):
        bonus -= 0.15
    return bonus


def _bonus_knowledge_update(r: dict) -> float:
    bonus = 0.0
    if _is_open_ended(r):
        bonus += 0.20
    elif _has_temporal_metadata(r):
        bonus += 0.10
    text = _haystack(r)
    if re.search(r"\b(?:current|currently|now|today|latest|most\s+recent)\b",
                 text, re.IGNORECASE):
        bonus += 0.05
    return bonus


def _bonus_temporal_reasoning(r: dict) -> float:
    text = _haystack(r)
    bonus = 0.0
    if _BEFORE_AFTER_RE.search(text) or _BEFORE_AFTER_KO.search(text):
        bonus += 0.15
    if _DATE_RE.search(text):
        bonus += 0.10
    elif _YEAR_RE.search(text):
        bonus += 0.05
    if _has_temporal_metadata(r):
        bonus += 0.05
    return bonus


def _bonus_preference(r: dict) -> float:
    text = _haystack(r)
    if _PREF_RE.search(text) or _PREF_KO.search(text):
        return 0.25
    return 0.0


def _bonus_multi_session(r: dict, all_sessions: set[str]) -> float:
    """Slight bonus for facts coming from sessions other than the dominant one."""
    sess = r.get("session") or r.get("session_id") or r.get("source")
    if not sess:
        return 0.0
    # When facts span ≥3 sessions, reward each non-dominant one a touch
    # so the LLM sees a representative spread instead of 5 hits from
    # the same chat.
    if len(all_sessions) >= 3:
        return 0.05
    return 0.0


# ---------------------------------------------------------------------------
# Sort keys — secondary signals for tie-breaking after bonus + base score.
# ---------------------------------------------------------------------------
def _temporal_sort_key(r: dict) -> str:
    """Return a sortable date string ('' if none).  Newer-first ordering uses
    the negation handled at the call site."""
    for k in ("valid_from", "recorded_at", "asserted_at", "date", "timestamp"):
        v = r.get(k)
        if isinstance(v, str) and v:
            return v
    text = _haystack(r)
    m = _DATE_RE.search(text)
    if m:
        return m.group(0)
    m = _YEAR_RE.search(text)
    if m:
        return m.group(0) + "-01-01"
    return ""


# ---------------------------------------------------------------------------
# Mixin.
# ---------------------------------------------------------------------------
class RerankMixin:
    """v2.5 question-type-aware re-ranker."""

    # Public helper for tests / introspection.
    def _rerank_bonus(self, result: dict, question_type: str) -> float:
        if not isinstance(result, dict):
            return 0.0
        qt = (question_type or "").strip().lower()
        if qt == "counting":
            return _bonus_counting(result)
        if qt == "knowledge_update":
            return _bonus_knowledge_update(result)
        if qt == "temporal_reasoning":
            return _bonus_temporal_reasoning(result)
        if qt == "preference":
            return _bonus_preference(result)
        if qt == "multi_session":
            # Sessions set unknown at single-row level — caller-side
            # bonus is computed in ``rerank_for_question_type``.
            return 0.0
        return 0.0

    def rerank_for_question_type(
        self,
        results: Iterable[dict] | None,
        question_type: str | None,
    ) -> list[dict]:
        """Return a new list of results re-sorted for ``question_type``.

        Never raises.  Empty input → ``[]``.  Unknown question type →
        results returned in their original order (still as a fresh list).
        """
        if not results:
            return []
        rows = [r for r in results if isinstance(r, dict)]
        if not rows:
            return []
        qt = (question_type or "").strip().lower()
        if qt in ("", "general", "single_session"):
            return list(rows)

        # Multi-session needs a global view of session diversity.
        all_sessions: set[str] = set()
        if qt == "multi_session":
            for r in rows:
                sess = r.get("session") or r.get("session_id") or r.get("source")
                if sess:
                    all_sessions.add(str(sess))

        # Score each row.
        scored: list[tuple[float, int, dict]] = []
        for idx, r in enumerate(rows):
            base = _coerce_score(r.get("score"))
            bonus = self._rerank_bonus(r, qt)
            if qt == "multi_session":
                bonus += _bonus_multi_session(r, all_sessions)
            # Stash the bonus on the row so downstream consumers (and
            # tests) can inspect it.  Doesn't replace ``score``.
            r["_rerank_bonus"] = round(bonus, 4)
            r["_rerank_for"] = qt
            scored.append((base + bonus, idx, r))

        # For temporal_reasoning we apply a stable secondary sort by date
        # *after* the bonus-adjusted score.  Newer first; rows without
        # any date signal sort last.
        if qt == "temporal_reasoning":
            def _tr_key(t: tuple[float, int, dict]) -> tuple[float, int, str]:
                score_neg = -t[0]
                date = _temporal_sort_key(t[2])
                # Empty date ranks last among ties; non-empty dates sort
                # newest-first by negating via lexical complement.
                if date:
                    # Lexical-desc: pad to 10 chars then invert.  Simpler:
                    # use ord-by-ord complement via a translated string.
                    # We use ``-int(...)`` on YYYYMMDD when possible.
                    digits = date.replace("-", "")
                    try:
                        return (score_neg, 0, -int(digits))  # type: ignore[return-value]
                    except ValueError:
                        return (score_neg, 0, date)  # type: ignore[return-value]
                return (score_neg, 1, "")
            scored.sort(key=_tr_key)
        else:
            scored.sort(key=lambda t: (-t[0], t[1]))

        return [r for _, _, r in scored]
