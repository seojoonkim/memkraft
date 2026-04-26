"""PreferenceGraphSyncMixin — B1 (v2.1.0+)

Bridges PreferenceMixin (markdown-based bitemporal prefs) with
GraphMixin (SQLite entity graph). The hypothesis (memkraft-v2-improvement-roadmap-2026-04-25):

  PreferenceMixin already carries 86% of the +3.72pp gain via
  `acknowledge_latest_user_preferences` and `generalize_to_new_scenarios`.
  But `preference_reasons` (0pp) and `novel_suggestion` (+0.43pp) lag
  because there is no entity-relation reasoning surface.

Solution: project every closed preference into the graph as
  (entity) --[{key}_{positive|negative}]--> (value) edges, plus
  (value) --[because_of]--> (reason) when reasons exist. Then the
graph can answer "why" / "what else" questions through neighbor
traversal — without altering existing preference behaviour.

Design constraints:
  * additive only — no existing function signatures change
  * idempotent — re-syncing the same preference is safe (graph_edge
    deduplicates exact (from, relation, to) triples)
  * graceful — works whether or not GraphMixin is mixed in
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _slugify_node(name: str) -> str:
    """Stable, lowercase, dash-separated id usable as a graph node."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def _is_clean_node_value(value: str, max_tokens: int = 8) -> bool:
    """Is this preference value a short, clean noun-phrase suitable for
    a negative graph edge?

    B2.5 (2026-04-25) — PreferenceMixin's adapter often stores narrative
    sentences as preference values (e.g. "the user mentioned in passing
    that they sometimes find it tiring to attend large social
    gatherings, especially after work"). Projecting those as graph nodes
    creates 50+ token slugs that the validator then matches on a single
    common token, blowing up false-positive contradictions.

    This gate keeps only short, noun-phrase-like values for negative
    edges. Positive edges are still permitted unconditionally for now.
    """
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    # too long even before tokenizing → almost always narrative noise
    if len(v) > 60:
        return False
    tokens = v.split()
    if len(tokens) > max_tokens:
        return False
    # sentence-shaped (multiple punctuation marks)
    if v.count('.') >= 2 or v.count(',') >= 2:
        return False
    return True


def _polarity_from_pref(pref: Dict[str, Any]) -> str:
    """Infer positive/negative from preference key/value heuristics.

    PreferenceMixin doesn't store an explicit polarity field (its
    contract is bitemporal closure, not sign), so we derive one:

      - keys starting with `dislike`, `not_`, `avoid`, `discontinued`
        → negative
      - values like "no", "none", "never" → negative
      - otherwise → positive
    """
    key = (pref.get("key") or "").lower()
    val = (pref.get("value") or "").lower().strip()

    neg_key_prefixes = ("dislike", "not_", "avoid", "discontinued",
                        "persona_dislike", "hate", "anti")
    if any(key.startswith(p) for p in neg_key_prefixes):
        return "negative"
    if val in {"no", "none", "never", "n/a", "false"}:
        return "negative"
    return "positive"


class PreferenceGraphSyncMixin:
    """Bridge: PreferenceMixin → GraphMixin.

    Provides two new methods (no overrides):
      - sync_preference_to_graph(entity, preference)
      - sync_all_preferences_to_graph(entity)
      - reason_preference_via_graph(entity, query, max_hops=2)
    """

    # ── single-pref sync ──────────────────────────────────────────────
    def sync_preference_to_graph(
        self,
        entity: str,
        preference: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Project one preference dict into the graph.

        Args:
            entity: persona/user identifier (e.g. "persona_42" or "Simon")
            preference: dict shaped like PreferenceMixin._parse_preferences
                output: {key, value, valid_from, valid_to, recorded,
                         strength, category, [reason]}

        Returns:
            dict with edges_added count and the node ids touched.
        """
        if not hasattr(self, "graph_edge"):
            return {"edges_added": 0, "skipped": "no GraphMixin"}

        key = preference.get("key")
        value = preference.get("value")
        if not entity or not key or not value:
            return {"edges_added": 0, "skipped": "missing entity/key/value"}

        entity_id = _slugify_node(entity)
        # Use the value as the target node so neighbor queries can pivot
        # (e.g. all users who like `pizza`).
        value_id = _slugify_node(str(value))
        category = (preference.get("category") or "general").lower()
        polarity = _polarity_from_pref(preference)
        strength = float(preference.get("strength", 1.0) or 1.0)

        # Relation encodes both the preference key and polarity, so a
        # single edge captures "Simon likes pizza" vs "Simon dislikes
        # pizza". `_close_preference`'s temporal markers are projected
        # onto valid_from/valid_until.
        rel_base = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_") or "prefers"
        # If the key already encodes negativity (dislike_food, not_*, avoid_*),
        # don't double-negate by re-prefixing `not_`.
        if polarity == "negative" and not any(
            rel_base.startswith(p) for p in ("dislike", "not_", "avoid", "discontinued", "hate", "anti")
        ):
            relation = f"not_{rel_base}"
        else:
            relation = rel_base

        edges_added = 0
        # B2.5 quality gate (negative edges only): drop narrative-shaped
        # values so the validator's graph signal stays trustworthy.
        if polarity == "negative" and not _is_clean_node_value(str(value)):
            return {
                "edges_added": 0,
                "skipped": "low_quality_negative_value",
                "polarity": polarity,
            }
        try:
            self.graph_edge(
                from_id=entity_id,
                relation=relation,
                to_id=value_id,
                weight=strength,
                valid_from=preference.get("valid_from"),
                valid_until=preference.get("valid_to"),
            )
            edges_added += 1
        except Exception:
            return {"edges_added": 0, "skipped": "graph_edge failed"}

        # Promote category as a typed edge so traversals can filter.
        if category and category != "general":
            try:
                self.graph_edge(
                    from_id=value_id,
                    relation="category",
                    to_id=_slugify_node(category),
                )
                edges_added += 1
            except Exception:
                pass

        # Reason: link value → reason via `because_of` (used by
        # reason_preference_via_graph below).
        reason = preference.get("reason")
        if reason:
            try:
                self.graph_edge(
                    from_id=value_id,
                    relation="because_of",
                    to_id=_slugify_node(str(reason)),
                )
                edges_added += 1
            except Exception:
                pass

        return {
            "edges_added": edges_added,
            "entity": entity_id,
            "relation": relation,
            "target": value_id,
            "polarity": polarity,
        }

    # ── bulk sync ─────────────────────────────────────────────────────
    def sync_all_preferences_to_graph(
        self,
        entity: str,
        include_closed: bool = True,
    ) -> Dict[str, Any]:
        """Project every stored preference for `entity` into the graph.

        By default also includes historically-closed preferences so the
        graph can answer "what did they used to like" — caller can opt
        out by passing include_closed=False.
        """
        if not hasattr(self, "pref_get"):
            return {"edges_added": 0, "synced": 0, "skipped": "no PreferenceMixin"}

        # pref_get with at_time=None defaults to "currently open"; to
        # also include closed preferences we read the file directly via
        # PreferenceMixin's internal parser.
        prefs: List[Dict[str, Any]]
        if include_closed:
            from pathlib import Path
            slug = self._slugify(entity)
            pref_file = Path(self.base_dir) / "preferences" / f"{slug}.md"
            if pref_file.exists():
                prefs = self._parse_preferences(pref_file)
            else:
                prefs = []
        else:
            prefs = self.pref_get(entity)

        edges_added = 0
        synced = 0
        low_quality_skipped = 0
        for p in prefs:
            r = self.sync_preference_to_graph(entity, p)
            edges_added += r.get("edges_added", 0)
            if r.get("edges_added", 0) > 0:
                synced += 1
            elif r.get("skipped") == "low_quality_negative_value":
                low_quality_skipped += 1
        return {
            "entity": entity,
            "synced": synced,
            "edges_added": edges_added,
            "total_prefs": len(prefs),
            "graph_low_quality_skipped": low_quality_skipped,
        }

    # ── graph-based preference reasoning ──────────────────────────────
    def reason_preference_via_graph(
        self,
        entity: str,
        query: str,
        max_hops: int = 2,
    ) -> List[Dict[str, Any]]:
        """Answer "why does X like Y" / "what would X also like" via graph.

        Strategy:
          1. extract candidate value tokens from query (lowercased,
             stopwords stripped)
          2. for each candidate, traverse from the entity node looking
             for paths that pass through the candidate
          3. return enriched paths with relation + because_of reasons

        This is intentionally lightweight — it complements pref_get,
        not replaces it.
        """
        if not hasattr(self, "graph_neighbors"):
            return []

        entity_id = _slugify_node(entity)
        # tokens to look for in target nodes
        q = (query or "").lower()
        tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2]
        token_set = set(tokens)

        paths = self.graph_neighbors(entity_id, hops=max_hops)
        if not paths:
            return []

        results: List[Dict[str, Any]] = []
        for p in paths:
            target = p.get("target", "")
            if not token_set or any(t in target for t in token_set):
                # Look up because_of edges from this target to surface
                # reasons (single hop, cheap).
                reasons: List[str] = []
                try:
                    reason_paths = self.graph_neighbors(
                        target, hops=1, relation="because_of"
                    )
                    reasons = [rp["target"] for rp in reason_paths]
                except Exception:
                    pass
                results.append({
                    "path": p.get("path", []),
                    "target": target,
                    "relation": p.get("relation"),
                    "depth": p.get("depth"),
                    "reasons": reasons,
                })

        # Deduplicate by (target, relation) keeping shortest path.
        seen: Dict = {}
        for r in results:
            k = (r["target"], r["relation"])
            if k not in seen or r["depth"] < seen[k]["depth"]:
                seen[k] = r
        return list(seen.values())
