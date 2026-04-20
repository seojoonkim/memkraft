"""Prompt Evidence — MemKraft v0.9.2 M2 alpha.

Part of the MemKraft 1.0.0 "Empirical Memory Loop" roadmap (M2).

``prompt_evidence`` lets a host agent cite its own past empirical tuning
results *before* dispatching another iteration. It is the pre-iteration
recall half of the loop; ``convergence_check`` is the post-iteration
stopping rule.

Design principles (see ``memory/memkraft-1.0-design-proposal-2026-04-20.md``):
- Additive only. ``core.py`` is NOT modified.
- Reuses 0.9.1 ``decision_search`` + ``incident_search`` + ``search``
  primitives. No new storage backend.
- Zero dependencies. Stdlib only.
- No LLM calls. Similarity is Jaccard on word tokens, stopwords-aware if
  ``self.stopwords`` is available.

Public API (alpha):

    mk.prompt_evidence(
        prompt_id,
        query=None,
        *,
        scenario=None,
        min_similarity=0.3,
        max_results=5,
        time_range_days=90,
    ) -> dict
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .storage.incident_storage import slugify


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_STOPWORDS: Set[str] = {
    # english minimal stopword set (stdlib-only, no NLTK)
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "by", "and", "or", "but",
    "if", "then", "than", "as", "from", "that", "this", "these", "those",
    "it", "its", "it's", "we", "they", "he", "she", "you", "i", "me",
    "do", "does", "did", "done", "have", "has", "had", "not", "no", "yes",
    "so", "up", "down", "out", "over", "under", "about", "into", "onto",
    "prompt", "eval", "iteration",  # anchor words present in every record
}

_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


def _tokenize(text: str, stopwords: Optional[Set[str]] = None) -> Set[str]:
    sw = stopwords if stopwords is not None else _DEFAULT_STOPWORDS
    tokens = _TOKEN_RE.findall((text or "").lower())
    return {t for t in tokens if t and t not in sw and len(t) > 1}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _containment(query: Set[str], doc: Set[str]) -> float:
    """How much of the *query* is present in the doc.

    For pre-iteration recall we care ``does this past record cover the
    terms I'm asking about?`` more than strict symmetric overlap — the
    doc can be much longer without penalty.
    """
    if not query:
        return 0.0
    return len(query & doc) / len(query)


def _parse_iter_from_tags(tags: List[str]) -> Optional[int]:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("iteration:"):
            try:
                return int(t.split(":", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


def _age_days(iso_ts: str) -> float:
    """Return age in days from now (local naive)."""
    if not iso_ts:
        return 1e9
    try:
        # tolerate both date and datetime strings
        s = str(iso_ts)
        if "T" in s:
            dt = datetime.fromisoformat(s.split(".")[0])
        else:
            dt = datetime.fromisoformat(s)
        delta = datetime.now() - dt.replace(tzinfo=None)
        return max(0.0, delta.total_seconds() / 86400.0)
    except Exception:
        return 1e9


def _normalise_prompt_id(prompt_id: str) -> str:
    if not prompt_id or not str(prompt_id).strip():
        raise ValueError("prompt_id must be a non-empty string")
    return slugify(str(prompt_id).strip(), max_len=80)


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class PromptEvidenceMixin:
    """Adds ``prompt_evidence`` to :class:`MemKraft`."""

    def prompt_evidence(
        self,
        prompt_id: str,
        query: Optional[str] = None,
        *,
        scenario: Optional[str] = None,
        min_similarity: float = 0.3,
        max_results: int = 5,
        time_range_days: Optional[int] = 90,
    ) -> Dict[str, Any]:
        """Cite past empirical tuning results for this prompt.

        Returns a dict with a ``results`` list of up to ``max_results``
        entries from decisions tagged ``prompt:{slug}``, ranked by
        ``similarity * recency_weight`` desc. See
        ``memory/memkraft-0.9.2-m2-spec-2026-04-20.md`` §1 for the full
        contract.
        """
        slug = _normalise_prompt_id(prompt_id)

        # Ensure the prompt is registered — mirrors prompt_eval's behaviour.
        live_path: Path = self.live_notes_dir / f"{slug}.md"  # type: ignore[attr-defined]
        if not live_path.exists():
            raise ValueError(
                f"prompt_id {prompt_id!r} is not registered — call "
                "mk.prompt_register(...) first"
            )

        if max_results is not None and max_results < 0:
            raise ValueError("max_results must be >= 0")
        if not (0.0 <= float(min_similarity) <= 1.0):
            raise ValueError("min_similarity must be in [0, 1]")

        effective_query = (query or "").strip()
        if not effective_query:
            # Fall back to the prompt id + live-note title so we always
            # have *something* to match against.
            effective_query = slug.replace("-", " ")

        stopwords = getattr(self, "stopwords", None)
        if isinstance(stopwords, dict):
            # core.py stores ``{lang: [words]}``; flatten.
            flat: Set[str] = set(_DEFAULT_STOPWORDS)
            for v in stopwords.values():
                if isinstance(v, list):
                    flat.update(str(x).lower() for x in v)
            stopwords_set: Optional[Set[str]] = flat
        elif isinstance(stopwords, (list, tuple, set)):
            stopwords_set = set(str(x).lower() for x in stopwords) | _DEFAULT_STOPWORDS
        else:
            stopwords_set = _DEFAULT_STOPWORDS

        q_tokens = _tokenize(effective_query, stopwords_set)

        # --- 1. fetch decisions tagged prompt:{slug} ------------------
        try:
            decisions = self.decision_search(  # type: ignore[attr-defined]
                query=None,
                tag=f"prompt:{slug}",
                limit=200,
            ) or []
        except Exception:
            decisions = []

        results: List[Dict[str, Any]] = []
        skipped_stale = 0
        skipped_lowsim = 0
        skipped_scenario = 0

        for d in decisions:
            decided_at = str(d.get("decided_at") or "")
            age = _age_days(decided_at)
            if time_range_days is not None and age > float(time_range_days):
                skipped_stale += 1
                continue

            title = str(d.get("title") or "")
            tags = list(d.get("tags") or [])
            tag_text = " ".join(tags)

            # Load full body for better similarity signal.
            body_text = ""
            try:
                detail = self.decision_get(d.get("id"))  # type: ignore[attr-defined]
                sections = detail.get("sections") or {}
                body_text = " ".join(
                    " ".join(v) for v in sections.values() if isinstance(v, list)
                )
            except Exception:
                body_text = ""

            haystack = f"{title} {tag_text} {body_text}"

            if scenario:
                if scenario.lower() not in haystack.lower():
                    skipped_scenario += 1
                    continue

            d_tokens = _tokenize(haystack, stopwords_set)
            # Use query containment (recall) as the primary score —
            # Jaccard kept as a tiebreaker-friendly secondary metric.
            sim = _containment(q_tokens, d_tokens)

            if sim < float(min_similarity):
                skipped_lowsim += 1
                continue

            if time_range_days is not None and time_range_days > 0:
                recency_weight = max(0.1, 1.0 - (age / float(time_range_days)))
            else:
                recency_weight = 1.0
            score = sim * recency_weight

            summary_text = title or (body_text[:140] if body_text else "")

            results.append(
                {
                    "_source": "decision",
                    "id": d.get("id"),
                    "iteration": _parse_iter_from_tags(tags),
                    "decided_at": decided_at,
                    "similarity": round(sim, 4),
                    "age_days": round(age, 2),
                    "score": round(score, 4),
                    "summary": summary_text,
                    "tags": tags,
                }
            )

        results.sort(key=lambda r: (r["score"], r["decided_at"]), reverse=True)
        top = results[: max_results] if max_results and max_results > 0 else results

        return {
            "query": effective_query,
            "prompt_id": slug,
            "scenario": scenario,
            "time_range_days": time_range_days,
            "min_similarity": float(min_similarity),
            "counts": {
                "decisions_matched": len(results),
                "decisions_total": len(decisions),
                "skipped_stale": skipped_stale,
                "skipped_low_similarity": skipped_lowsim,
                "skipped_scenario_mismatch": skipped_scenario,
            },
            "results": top,
        }
