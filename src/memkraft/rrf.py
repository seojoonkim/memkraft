"""v2.3 — Reciprocal Rank Fusion (RRF).

Adds rank-based result fusion to MemKraft as an additive mixin.

RRF formula (Cormack et al., 2009):

    RRF_score(d) = Σ  1 / (k + rank_i(d))
                   i

where ``rank_i(d)`` is the 1-based rank of document ``d`` in result list
``i`` (∞ if absent) and ``k`` is a smoothing constant (60 by default,
the value validated in the original paper and widely adopted by
search-fusion systems such as Elasticsearch and Vespa).

Why RRF?
  * **Score-scale agnostic.**  Different retrieval passes (exact match,
    BM25-ish IDF, fuzzy edit-distance, graph-hop, temporal) produce
    scores on incompatible scales.  Weighted blends (e.g.
    ``0.5·p1 + 0.3·p2 + 0.2·p3``) require manual tuning every time a
    new pass is added.  RRF only needs the *order*.
  * **Robust to pass dropouts.**  If one pass returns nothing, RRF
    degrades gracefully — it just contributes zero.  Weighted blends
    can over-weight a single noisy pass.
  * **Simple + parameter-light.**  One global constant (``k``) instead
    of per-pass weights.

Constraints honoured:
  * Does **NOT** modify ``core.py``, ``graph.py``, ``bitemporal.py``,
    or any existing public method signature.
  * Pure Python stdlib — no external dependencies.
  * Additive mixin: registered in ``__init__.py`` alongside the other
    v2.x mixins.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Default RRF constant
# ---------------------------------------------------------------------------
RRF_K_DEFAULT = 60
"""Standard RRF smoothing constant from Cormack et al. (2009).

Smaller values (e.g. ``k=10``) give the top-1 of each list very high
weight; larger values (e.g. ``k=100``) flatten contributions across
deeper ranks.  ``60`` is a good middle ground and the de-facto
industry default."""


# ---------------------------------------------------------------------------
# Default key extraction for MemKraft result dicts
# ---------------------------------------------------------------------------
def _default_dedup_key(r: dict) -> tuple:
    """Identify a "document" across heterogeneous MemKraft result shapes.

    Mirrors the dedup logic already used by ``MultiPassMixin._mp_blend``
    so RRF and weighted-blend produce comparable membership.
    """
    if not isinstance(r, dict):
        # Non-dict items get a unique-ish key based on identity so they
        # never collide.  Practically this should not happen for
        # MemKraft results, but we stay defensive.
        return ("opaque", id(r))

    f = r.get("file")
    if f:
        return ("file", f)

    # Bitemporal / fact_history hits
    if r.get("_entity") is not None or r.get("_key") is not None:
        return (
            "fact",
            (r.get("_entity") or r.get("match") or "").lower(),
            r.get("_key", ""),
            r.get("_value", ""),
        )

    # Graph hits
    if r.get("_relation") is not None:
        return (
            "graph",
            (r.get("match") or "").lower(),
            r.get("_relation", ""),
            r.get("_neighbor_of", ""),
        )

    # Plain entity hits
    return ("entity", (r.get("match") or "").lower())


# ---------------------------------------------------------------------------
# Pure RRF function (also exposed as a module-level helper for testing)
# ---------------------------------------------------------------------------
def rrf_fuse(
    *result_lists: list[dict],
    k: int = RRF_K_DEFAULT,
    key_fn: Callable[[dict], Any] | None = None,
) -> list[dict]:
    """Fuse multiple ranked result lists via Reciprocal Rank Fusion.

    Parameters
    ----------
    *result_lists:
        One or more lists of result dicts.  Each list is **assumed
        already sorted by relevance descending** — RRF only consults
        the position (rank), not the underlying score.
    k:
        Smoothing constant (default 60).  Must be > 0.
    key_fn:
        Custom key function for deduplication.  Defaults to
        ``_default_dedup_key`` which handles MemKraft's heterogeneous
        result shapes.

    Returns
    -------
    list[dict]
        Deduplicated, RRF-ranked results.  Each entry is a *copy* of
        the first occurrence of that document in the input lists, with
        the following fields added/overwritten:

        * ``rrf_score`` — the fused score (float, monotonically
          decreasing).
        * ``rrf_ranks`` — list of 1-based ranks per input list, with
          ``None`` for lists where the doc was absent.
        * ``score`` — overwritten with ``rrf_score`` so downstream
          callers that sort by ``score`` continue to work.

    Notes
    -----
    * Empty / missing lists contribute nothing.  Calling with zero
      lists returns ``[]``.
    * Stable for identical RRF scores: documents that tie keep the
      order of first appearance across the input lists.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k!r}")

    if key_fn is None:
        key_fn = _default_dedup_key

    if not result_lists:
        return []

    n_lists = len(result_lists)

    # Track (score, ranks-per-list, payload, first-seen-order) per key.
    fused: dict[Any, dict[str, Any]] = {}
    insertion_order = 0

    for list_idx, lst in enumerate(result_lists):
        if not lst:
            continue
        for rank, item in enumerate(lst, start=1):  # 1-based rank
            try:
                key = key_fn(item)
            except Exception:
                # Defensive — never let a malformed dict break RRF.
                continue

            entry = fused.get(key)
            contribution = 1.0 / (k + rank)

            if entry is None:
                ranks = [None] * n_lists
                ranks[list_idx] = rank
                payload = dict(item) if isinstance(item, dict) else {"value": item}
                fused[key] = {
                    "score": contribution,
                    "ranks": ranks,
                    "payload": payload,
                    "_order": insertion_order,
                }
                insertion_order += 1
            else:
                # Only count the *best* (lowest) rank per (key, list).
                # This protects against duplicate entries within a
                # single input list.
                prev = entry["ranks"][list_idx]
                if prev is None or rank < prev:
                    if prev is not None:
                        # Subtract the old contribution from this list;
                        # we'll add the better one below.
                        entry["score"] -= 1.0 / (k + prev)
                    entry["ranks"][list_idx] = rank
                    entry["score"] += contribution

                # Merge richer fields from later occurrences (snippet,
                # file, etc.) without clobbering earlier values.
                if isinstance(item, dict):
                    pl = entry["payload"]
                    for fld in ("snippet", "file", "match", "match_type"):
                        if not pl.get(fld) and item.get(fld):
                            pl[fld] = item[fld]

    # Sort by score desc, then by first-seen order for stability.
    ordered = sorted(
        fused.values(),
        key=lambda e: (-e["score"], e["_order"]),
    )

    out: list[dict] = []
    for entry in ordered:
        payload = entry["payload"]
        payload["rrf_score"] = round(entry["score"], 6)
        payload["rrf_ranks"] = list(entry["ranks"])
        # Overwrite ``score`` so existing downstream sorts work.
        payload["score"] = payload["rrf_score"]
        out.append(payload)

    return out


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------
class RRFMixin:
    """v2.3 additive RRF API."""

    # Class-level default — instances may override via attribute set,
    # but normally you pass ``k=`` to the methods directly.
    RRF_K = RRF_K_DEFAULT

    # ------------------------------------------------------------------
    # Internal helper used by other mixins (notably MultiPassMixin)
    # ------------------------------------------------------------------
    def _rrf_fusion(
        self,
        *result_lists: list[dict],
        k: int = RRF_K_DEFAULT,
        key_fn: Callable[[dict], Any] | None = None,
    ) -> list[dict]:
        """Instance-method shim around :func:`rrf_fuse`.

        See module-level :func:`rrf_fuse` for the full contract.  This
        thin wrapper exists so that other mixins can call
        ``self._rrf_fusion(...)`` and unit tests can patch the method
        per-instance.
        """
        return rrf_fuse(*result_lists, k=k, key_fn=key_fn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search_rrf(
        self,
        query: str,
        top_k: int = 10,
        fuzzy: bool = False,
        k: int = RRF_K_DEFAULT,
    ) -> list[dict]:
        """RRF-fused recall-friendly search.

        Combines the original query with keyword-only variants
        (produced by ``SearchMixin._v102_keyword_variants``) and fuses
        the result lists via Reciprocal Rank Fusion.

        Parameters
        ----------
        query:
            Natural-language query.
        top_k:
            Maximum number of results to return (default 10).
        fuzzy:
            Forwarded to the underlying ``search`` calls.
        k:
            RRF smoothing constant (default 60).

        Returns
        -------
        list[dict]
            RRF-fused, deduplicated results sorted by ``rrf_score``
            desc.  Each entry has the ``rrf_score`` and ``rrf_ranks``
            fields described in :func:`rrf_fuse`.

        Notes
        -----
        Falls back gracefully when SearchMixin helpers are unavailable
        (e.g. minimal MemKraft test instance): returns the bare
        ``self.search(query, fuzzy=fuzzy)`` result truncated to
        ``top_k``.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            top_k = 10

        run = getattr(self, "_v102_run_search", None)
        variants_fn = getattr(self, "_v102_keyword_variants", None)

        if run is None or variants_fn is None:
            # SearchMixin not attached — degrade to plain search.
            try:
                bare = self.search(query, fuzzy=fuzzy)
            except Exception:
                bare = []
            return (bare or [])[:top_k]

        batches: list[list[dict]] = [run(query, fuzzy=fuzzy)]
        for variant in variants_fn(query):
            batches.append(run(variant, fuzzy=fuzzy))

        # Drop empties so RRF doesn't waste cycles on them.
        batches = [b for b in batches if b]

        fused = self._rrf_fusion(*batches, k=k)
        return fused[:top_k]
