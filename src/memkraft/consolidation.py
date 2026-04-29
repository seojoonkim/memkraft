"""
MemKraft v2.3 — Memory Consolidation (수면 통합 / Sleep Consolidation)

Inspired by:
    - Complementary Learning Systems (CLS) theory: hippocampus → neocortex
      consolidation during sleep, where redundant traces are merged and
      stable patterns crystallize.
    - SimpleMem's semantic lossless compression.

This mixin adds an offline ``consolidate()`` API that runs entirely with
pattern matching (no LLM calls, no external dependencies). It is meant to
be invoked during idle time — e.g. via :py:meth:`schedule` with a cron
expression like ``"0 4 * * *"`` (4am every night).

Four consolidation stages:
    1. Duplicate Fact Merge    — same entity+key+value → keep newest
    2. Stale Fact Close        — open-ended facts older than threshold
    3. Orphan Cleanup          — graph nodes with no edges & no entity file
    4. Observation Generation  — 1-line summary per entity → observations/

The API is *additive* — it never modifies :py:meth:`compact`, the graph
schema, or any existing behaviour. Use ``dry_run=True`` to preview.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .bitemporal import _LINE_RE, _now_iso, format_line, parse_line

# Module-level logger for contradiction warnings (Stage 5). The host
# application configures handlers; this just emits structured warnings
# whenever a contradictory fact is detected during consolidation.
_logger = logging.getLogger("memkraft.consolidation")


# Heuristic: assume ~4 chars per token. Used only for estimation.
_CHARS_PER_TOKEN = 4


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def _days_since(iso_date: str) -> Optional[int]:
    """Return number of days since ``iso_date`` (YYYY-MM-DD or full ISO).

    Returns ``None`` if the value is unparseable.
    """
    if not iso_date:
        return None
    try:
        # accept 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM[:SS]'
        head = iso_date.split("T", 1)[0]
        d = _dt.date.fromisoformat(head)
        return (_dt.date.today() - d).days
    except Exception:
        return None


class ConsolidationMixin:
    """Memory consolidation — offline cleanup without LLM calls.

    Mixin for :class:`MemKraft`. Requires ``self.base_dir``,
    :py:meth:`_facts_dir`, :py:meth:`_fact_file`, :py:meth:`_slugify`,
    and (optionally) :py:meth:`_graph_db` to be available.
    """

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def consolidate(
        self,
        strategy: str = "auto",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Consolidate memory. Call during idle time.

        Parameters
        ----------
        strategy:
            ``"auto"`` (default) — runs all four stages with default
            thresholds (stale = 365 days).
            ``"aggressive"`` — same stages but stale threshold = 180 days
            and observations include more facts per entity.
        dry_run:
            If ``True``, report what would change without writing anything.

        Returns
        -------
        dict
            ``{
                "duplicates_merged": int,
                "stale_closed": int,
                "orphans_removed": int,
                "observations_generated": int,
                "tokens_saved_estimate": int,
                "details": list[str],
                "dry_run": bool,
                "strategy": str,
            }``
        """
        if strategy not in ("auto", "aggressive"):
            raise ValueError(
                f"Unknown strategy: {strategy!r}. Use 'auto' or 'aggressive'."
            )

        stale_days = 365 if strategy == "auto" else 180
        max_obs_facts = 5 if strategy == "auto" else 8

        details: List[str] = []
        chars_saved = 0

        # Stage 1
        merged, c1 = self._consolidate_duplicates(dry_run=dry_run, details=details)
        chars_saved += c1

        # Stage 2
        closed, c2 = self._consolidate_stale(
            stale_days=stale_days, dry_run=dry_run, details=details
        )
        chars_saved += c2

        # Stage 3
        orphans, c3 = self._consolidate_orphans(dry_run=dry_run, details=details)
        chars_saved += c3

        # Stage 4
        observations = self._consolidate_observations(
            max_facts=max_obs_facts, dry_run=dry_run, details=details
        )

        # Stage 5
        contradictions, c5 = self._consolidate_contradictions(
            dry_run=dry_run, details=details
        )
        chars_saved += c5

        return {
            "duplicates_merged": merged,
            "stale_closed": closed,
            "orphans_removed": orphans,
            "observations_generated": observations,
            "contradictions_detected": contradictions,
            "tokens_saved_estimate": chars_saved // _CHARS_PER_TOKEN,
            "chars_saved": chars_saved,
            "details": details,
            "dry_run": dry_run,
            "strategy": strategy,
        }

    # ------------------------------------------------------------------
    # Stage 1 — Duplicate Fact Merge
    # ------------------------------------------------------------------

    def _consolidate_duplicates(
        self,
        *,
        dry_run: bool,
        details: List[str],
    ) -> Tuple[int, int]:
        """Merge duplicate facts.

        For each entity fact file, group lines by ``(key, value)``. If a
        group has >1 facts, keep only the one with the most recent
        ``recorded_at``; treat valid_to=None (open-ended) as canonical
        when tied.
        """
        facts_dir = self._safe_facts_dir()
        if facts_dir is None:
            return 0, 0

        merged_total = 0
        chars_saved = 0

        for fact_file in sorted(facts_dir.glob("*.md")):
            try:
                original_text = fact_file.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = original_text.splitlines()
            # Index facts: { (key, value): [(line_idx, parsed_dict), ...] }
            buckets: Dict[Tuple[str, str], List[Tuple[int, Dict[str, Any]]]] = {}
            for idx, line in enumerate(lines):
                parsed = parse_line(line)
                if parsed is None:
                    continue
                bucket_key = (parsed["key"], parsed["value"])
                buckets.setdefault(bucket_key, []).append((idx, parsed))

            # Decide which line indices to drop
            drop: set = set()
            for bucket_key, items in buckets.items():
                if len(items) <= 1:
                    continue
                # Sort by (open-ended-preferred, recorded_at desc)
                items_sorted = sorted(
                    items,
                    key=lambda it: (
                        # open-ended (None valid_to) wins ties
                        0 if it[1]["valid_to"] is None else 1,
                        # recorded_at descending
                        _neg_iso(it[1]["recorded_at"]),
                    ),
                )
                keep_idx = items_sorted[0][0]
                for idx, _ in items_sorted[1:]:
                    drop.add(idx)
                    chars_saved += len(lines[idx]) + 1  # +1 for newline
                    merged_total += 1

                if drop and details is not None:
                    details.append(
                        f"merge: {fact_file.stem} {bucket_key[0]}={bucket_key[1]!r} "
                        f"({len(items)} duplicates → 1)"
                    )

            if drop and not dry_run:
                new_lines = [ln for i, ln in enumerate(lines) if i not in drop]
                new_text = "\n".join(new_lines)
                if not new_text.endswith("\n"):
                    new_text += "\n"
                fact_file.write_text(new_text, encoding="utf-8")

        return merged_total, chars_saved

    # ------------------------------------------------------------------
    # Stage 2 — Stale Fact Close
    # ------------------------------------------------------------------

    def _consolidate_stale(
        self,
        *,
        stale_days: int,
        dry_run: bool,
        details: List[str],
    ) -> Tuple[int, int]:
        """Close open-ended facts whose ``valid_from`` is older than
        ``stale_days`` days.

        Closes them by setting ``valid_to`` to today. Facts with no
        ``valid_from`` (unknown start) are NOT touched — they could be
        evergreen truths or recently learned old knowledge.
        """
        facts_dir = self._safe_facts_dir()
        if facts_dir is None:
            return 0, 0

        closed_total = 0
        # 'closing' a fact replaces an open marker with a closed one.
        # Net char delta is approximately 0 (open ``..)`` vs closed ``..date]``).
        # We don't credit chars_saved here.
        chars_saved = 0
        today = _today_iso()
        now_rec = _now_iso()

        for fact_file in sorted(facts_dir.glob("*.md")):
            try:
                original_text = fact_file.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = original_text.splitlines()
            new_lines: List[str] = []
            file_changed = False

            for line in lines:
                parsed = parse_line(line)
                if parsed is None or parsed["valid_to"] is not None:
                    new_lines.append(line)
                    continue

                vf = parsed["valid_from"]
                if not vf:
                    new_lines.append(line)
                    continue

                age = _days_since(vf)
                if age is None or age < stale_days:
                    new_lines.append(line)
                    continue

                # Close it.
                new_line = format_line(
                    parsed["key"],
                    parsed["value"],
                    vf,
                    today,
                    now_rec,
                )
                new_lines.append(new_line)
                file_changed = True
                closed_total += 1
                if details is not None:
                    details.append(
                        f"stale-close: {fact_file.stem} "
                        f"{parsed['key']}={parsed['value']!r} "
                        f"(valid_from={vf}, age={age}d)"
                    )

            if file_changed and not dry_run:
                new_text = "\n".join(new_lines)
                if not new_text.endswith("\n"):
                    new_text += "\n"
                fact_file.write_text(new_text, encoding="utf-8")

        return closed_total, chars_saved

    # ------------------------------------------------------------------
    # Stage 3 — Orphan Cleanup
    # ------------------------------------------------------------------

    def _consolidate_orphans(
        self,
        *,
        dry_run: bool,
        details: List[str],
    ) -> Tuple[int, int]:
        """Remove graph nodes that have no edges AND no backing entity file.

        Safety net: if an entity markdown file exists for the node, it is
        preserved even when the graph has no edges (the file may be the
        sole source of truth and edges may simply not have been built yet).
        """
        # Lazy access — graph mixin may or may not be present.
        get_db = getattr(self, "_graph_db", None)
        if not callable(get_db):
            return 0, 0

        try:
            conn = get_db()
        except Exception:
            return 0, 0

        # Collect candidate orphan node ids.
        try:
            orphan_rows = conn.execute(
                """
                SELECT n.id FROM nodes n
                WHERE NOT EXISTS (
                    SELECT 1 FROM edges e WHERE e.from_id = n.id OR e.to_id = n.id
                )
                """
            ).fetchall()
        except Exception:
            return 0, 0

        orphan_ids = [r["id"] if hasattr(r, "keys") else r[0] for r in orphan_rows]
        if not orphan_ids:
            return 0, 0

        # Filter out ids that have a backing entity / live-note file.
        to_delete: List[str] = []
        chars_saved = 0
        for nid in orphan_ids:
            if self._has_backing_file(nid):
                continue
            to_delete.append(nid)
            chars_saved += 80  # rough estimate per node row

        if not to_delete:
            return 0, 0

        if details is not None:
            for nid in to_delete:
                details.append(f"orphan-remove: graph node {nid!r}")

        if not dry_run:
            try:
                placeholders = ",".join(["?"] * len(to_delete))
                conn.execute(
                    f"DELETE FROM nodes WHERE id IN ({placeholders})",
                    to_delete,
                )
                conn.commit()
            except Exception:
                # Best-effort; never raise from consolidate.
                return 0, chars_saved

        return len(to_delete), chars_saved

    # ------------------------------------------------------------------
    # Stage 4 — Observation Generation
    # ------------------------------------------------------------------

    def _consolidate_observations(
        self,
        *,
        max_facts: int,
        dry_run: bool,
        details: List[str],
    ) -> int:
        """Generate one-line observation summaries per entity.

        For each ``facts/<slug>.md`` file we render a tiny summary like::

            Simon: role=CEO of Hashed; lives_in=Seoul; likes=Bitcoin

        The summary uses currently-valid (open-ended or current-date)
        facts only, prioritizing those most-recently recorded. Files are
        written to ``{base_dir}/observations/{slug}.txt`` so a future
        ``search()`` upgrade can prefer them.
        """
        facts_dir = self._safe_facts_dir()
        if facts_dir is None:
            return 0

        obs_dir = Path(self.base_dir) / "observations"
        if not dry_run:
            obs_dir.mkdir(parents=True, exist_ok=True)

        generated = 0
        today = _today_iso()

        for fact_file in sorted(facts_dir.glob("*.md")):
            try:
                text = fact_file.read_text(encoding="utf-8")
            except Exception:
                continue

            entity_label = self._extract_entity_label(text, fact_file.stem)
            facts = []
            for line in text.splitlines():
                parsed = parse_line(line)
                if parsed is None:
                    continue
                # currently-valid only
                if parsed["valid_to"] is not None and parsed["valid_to"] < today:
                    continue
                facts.append(parsed)

            if not facts:
                continue

            # Most recently recorded first.
            facts.sort(key=lambda f: f["recorded_at"] or "", reverse=True)

            # De-dup keys: keep first occurrence (most recent).
            seen_keys: set = set()
            picked: List[Dict[str, Any]] = []
            for f in facts:
                if f["key"] in seen_keys:
                    continue
                seen_keys.add(f["key"])
                picked.append(f)
                if len(picked) >= max_facts:
                    break

            parts = [f"{f['key']}={f['value']}" for f in picked]
            observation = f"{entity_label}: " + "; ".join(parts)

            slug = fact_file.stem
            out_path = obs_dir / f"{slug}.txt"

            if not dry_run:
                try:
                    out_path.write_text(observation + "\n", encoding="utf-8")
                except Exception:
                    continue

            generated += 1
            if details is not None:
                details.append(f"observation: {slug} ({len(picked)} facts)")

        return generated

    # ------------------------------------------------------------------
    # Stage 5 — Contradiction Detection
    # ------------------------------------------------------------------

    def _consolidate_contradictions(
        self,
        *,
        dry_run: bool,
        details: List[str],
    ) -> Tuple[int, int]:
        """Detect contradictions in fact files.

        A contradiction is when the same entity has the same ``key`` mapped
        to different ``value``’s and the facts overlap in valid_time (both
        are currently valid, or their intervals intersect).

        For each contradiction:
        - The newest fact (by ``recorded_at``) is kept as canonical.
        - A warning is logged.
        - The older conflicting fact’s ``valid_to`` is set to the newer
          fact’s ``valid_from`` (or today) to close the conflict, unless
          ``dry_run`` is True.

        Returns ``(contradiction_count, chars_saved)``.
        """
        facts_dir = self._safe_facts_dir()
        if facts_dir is None:
            return 0, 0

        today = _today_iso()
        now_rec = _now_iso()
        total_contradictions = 0
        chars_saved = 0

        for fact_file in sorted(facts_dir.glob("*.md")):
            try:
                text = fact_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Group facts by key
            key_facts: Dict[str, List[Dict[str, Any]]] = {}
            for line in text.splitlines():
                parsed = parse_line(line)
                if parsed is None:
                    continue
                key_facts.setdefault(parsed["key"], []).append(parsed)

            # Find contradictions: same key, different value, overlapping validity
            for key, facts in key_facts.items():
                if len(facts) < 2:
                    continue

                # Compare all pairs
                seen_pairs: set = set()
                for i, f1 in enumerate(facts):
                    for j, f2 in enumerate(facts):
                        if j <= i:
                            continue
                        # Different value?
                        if f1["value"] == f2["value"]:
                            continue
                        # Overlapping validity?
                        if not self._facts_overlap(f1, f2, today):
                            continue
                        pair_key = (
                            min(f1["value"], f2["value"]),
                            max(f1["value"], f2["value"]),
                            f1.get("valid_from", ""),
                            f2.get("valid_from", ""),
                        )
                        if pair_key in seen_pairs:
                            continue
                        seen_pairs.add(pair_key)

                        total_contradictions += 1

                        # Newest wins — sort by recorded_at
                        newer = f1 if (f1["recorded_at"] or "") >= (f2["recorded_at"] or "") else f2
                        older = f2 if newer is f1 else f1

                        msg = (
                            f"contradiction: {fact_file.stem} "
                            f"{key}={older['value']!r} vs {newer['value']!r} "
                            f"(newer recorded at {newer['recorded_at']})"
                        )
                        if details is not None:
                            details.append(msg)
                        # Always emit a warning log so callers running
                        # consolidate() outside dream-cycle still see the
                        # conflict surface.
                        _logger.warning("⚠️ %s", msg)

                        if not dry_run:
                            # Close the older fact: set valid_to to
                            # newer's valid_from or today.
                            close_at = newer.get("valid_from") or today
                            if older["valid_to"] is None:
                                self._close_contradiction(
                                    fact_file, older, close_at, now_rec
                                )
                                # Replaced open interval with closed.
                                chars_saved += 20

        return total_contradictions, chars_saved

    @staticmethod
    def _facts_overlap(
        f1: Dict[str, Any],
        f2: Dict[str, Any],
        today: str,
    ) -> bool:
        """Check whether two facts’ validity intervals overlap.

        Handles open-ended intervals (valid_to=None = ongoing).
        """
        # Effective start: use valid_from or treat as −∞
        start1 = f1.get("valid_from") or "0000"
        start2 = f2.get("valid_from") or "0000"
        # Effective end: use valid_to or treat as +∞ (today for ongoing)
        end1 = f1.get("valid_to") or "9999"
        end2 = f2.get("valid_to") or "9999"

        # Standard interval overlap: start1 <= end2 AND start2 <= end1
        return start1 <= end2 and start2 <= end1

    def _close_contradiction(
        self,
        fact_file: Path,
        older_fact: Dict[str, Any],
        close_at: str,
        recorded_at: str,
    ) -> None:
        """Close an older contradictory fact by setting its valid_to."""
        try:
            text = fact_file.read_text(encoding="utf-8")
        except Exception:
            return

        lines = text.splitlines()
        new_lines = []
        changed = False

        for line in lines:
            parsed = parse_line(line)
            if (
                parsed is not None
                and parsed["key"] == older_fact["key"]
                and parsed["value"] == older_fact["value"]
                and parsed["valid_from"] == older_fact.get("valid_from")
                and parsed["recorded_at"] == older_fact.get("recorded_at")
                and parsed["valid_to"] is None
            ):
                new_line = format_line(
                    parsed["key"],
                    parsed["value"],
                    parsed["valid_from"],
                    close_at,
                    recorded_at,
                    fact_type=parsed.get("type"),
                )
                new_lines.append(new_line)
                changed = True
            else:
                new_lines.append(line)

        if changed:
            new_text = "\n".join(new_lines)
            if not new_text.endswith("\n"):
                new_text += "\n"
            fact_file.write_text(new_text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _count_duplicate_facts(self) -> int:
        """Count how many fact lines are duplicates of another fact line.

        A duplicate is defined as: same entity (= same fact file) +
        same key + same value, repeated more than once. Used by
        :py:meth:`health` to recommend ``consolidate()``.
        """
        facts_dir = self._safe_facts_dir()
        if facts_dir is None:
            return 0
        total = 0
        for fact_file in facts_dir.glob("*.md"):
            try:
                text = fact_file.read_text(encoding="utf-8")
            except Exception:
                continue
            seen: Dict[Tuple[str, str], int] = {}
            for line in text.splitlines():
                parsed = parse_line(line)
                if parsed is None:
                    continue
                key = (parsed["key"], parsed["value"])
                seen[key] = seen.get(key, 0) + 1
            for count in seen.values():
                if count > 1:
                    total += count - 1
        return total

    def _safe_facts_dir(self) -> Optional[Path]:
        """Return the facts directory if it exists, else ``None``."""
        # Try mixin helper first (creates if missing — but we want to read,
        # not create, so don't auto-create when empty).
        try:
            base = Path(self.base_dir) / "facts"
        except Exception:
            return None
        if not base.exists():
            return None
        return base

    def _has_backing_file(self, node_id: str) -> bool:
        """Check whether a graph node has a backing markdown file."""
        if not node_id:
            return False
        candidate_names = {node_id, node_id.replace(" ", "-"), node_id.replace(" ", "_")}
        # Try slugified version too if available.
        slugify = getattr(self, "_slugify", None)
        if callable(slugify):
            try:
                candidate_names.add(slugify(node_id))
            except Exception:
                pass

        roots = [
            Path(self.base_dir) / "facts",
            Path(self.base_dir) / "entities",
            Path(self.base_dir) / "live-notes",
        ]
        for root in roots:
            if not root.exists():
                continue
            for name in candidate_names:
                if not name:
                    continue
                if (root / f"{name}.md").exists():
                    return True
        return False

    def _extract_entity_label(self, text: str, fallback: str) -> str:
        """Pull ``# Entity: <name>`` out of fact file, else use ``fallback``."""
        m = re.search(r"^#\s*Entity:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return fallback


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _neg_iso(value: Optional[str]) -> Tuple[int, str]:
    """Return a sort key that orders ISO timestamps descending.

    The first element flags missing values so empty/None sorts *after*
    real timestamps when used with ``sorted(..., reverse=False)`` ⇒
    real timestamps come first, with the most recent at the front.

    For real timestamps we invert each character so ascending sort
    yields descending timestamp order.
    """
    if not value:
        return (1, "")
    inverted = "".join(
        chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in value
    )
    return (0, inverted)
