"""Score upper bounds for top-k search pruning (v2.9.x).

Long queries (multi-KB cron prompts capped to a few hundred chars) often
contain one or two near-universal tokens (``2026``, ``hermes``...).  The
inverted-index candidate set then covers ~every document and ``search``
reads and scores the whole corpus, which took 20-80s on a 43k-doc store.

When only a top-k window is requested we can order candidates by a cheap
score *upper bound* computed from the in-memory TF map (no file read) and
stop once the bound of the next candidate is strictly below the current
k-th best distinct-entity score.  The bound covers every scoring term, so
the visible window, its scores and its order are identical to the full
scan.  Escape hatch: ``MEMKRAFT_DISABLE_TOPK_PRUNE=1``.
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional

_RECENCY_MAX = 0.05
# Scores are rounded to 2 decimals; keep a margin so rounding can't
# reorder a pruned doc above a kept one.
_ROUND_MARGIN = 0.011
PRUNE_MIN_CANDIDATES = 256


def prune_enabled() -> bool:
    return os.environ.get("MEMKRAFT_DISABLE_TOPK_PRUNE") != "1"


def score_upper_bound(
    *,
    md,
    query_lower: str,
    query_tokens: List[str],
    interior_tokens: List[str],
    lin_idf: Dict[str, float],
    log_idf: Dict[str, float],
    total_lin: float,
    doc_tf: Optional[Dict[str, int]],
) -> float:
    """Upper bound on ``search``'s final score for one candidate doc."""
    if doc_tf is None:
        return 2.0  # unknown body tokens: never prune
    filename_lower = md.stem.lower().replace("-", " ")
    if query_lower in filename_lower:
        return 2.0
    # Exact-substring match requires every interior query token to be a
    # body token (it is bordered by separators on both sides in the query,
    # hence in the content too).  If that holds we can't rule it out.
    if not interior_tokens or all(t in doc_tf for t in interior_tokens):
        return 2.0
    fname_tokens = None
    matched_lin = 0.0
    matched_log = 0.0
    for t in query_tokens:
        hit = t in doc_tf
        if not hit:
            if fname_tokens is None:
                from ._core_search_helpers import search_tokens

                fname_tokens = set(search_tokens(filename_lower))
            hit = t in fname_tokens
        if hit:
            matched_lin += lin_idf[t]
            matched_log += log_idf[t]
    total = total_lin or 1.0
    token_ub = matched_lin / total
    # Phrase bonus needs both bigram tokens in the body; heading bonus needs
    # ``query_lower`` inside a line, impossible once exact match is ruled out.
    phrase_ub = 0.0
    if len(query_tokens) >= 2:
        hits = sum(
            1
            for i in range(len(query_tokens) - 1)
            if query_tokens[i] in doc_tf and query_tokens[i + 1] in doc_tf
        )
        phrase_ub = 0.15 * hits / (len(query_tokens) - 1)
    # bm25 term per token < idf_log * (k1+1); normalised by sum(lin)*(k1+1).
    bm25_ub = min(1.0, matched_log / total)
    return min(1.0, 0.45 * token_ub + 0.30 * bm25_ub + phrase_ub + _RECENCY_MAX)


def build_idf_tables(query_tokens: List[str], doc_count: int, token_doc_freq: Dict[str, int]):
    lin: Dict[str, float] = {}
    log: Dict[str, float] = {}
    for t in query_tokens:
        df_lin = token_doc_freq.get(t, 1)
        lin[t] = max(0.1, (doc_count - df_lin + 0.5) / (df_lin + 0.5))
        n = token_doc_freq.get(t, 0)
        val = math.log(((doc_count - n + 0.5) / (n + 0.5)) + 1.0)
        log[t] = val if val > 0 else 0.0
    total_lin = 0.0
    for t in query_tokens:
        total_lin += lin[t]
    return lin, log, total_lin


def kth_best(best_by_stem: Dict[str, float], k: int) -> Optional[float]:
    if len(best_by_stem) < k:
        return None
    import heapq

    return heapq.nlargest(k, best_by_stem.values())[-1]


def can_stop(bound: float, kth: Optional[float]) -> bool:
    return kth is not None and bound + _ROUND_MARGIN < kth
