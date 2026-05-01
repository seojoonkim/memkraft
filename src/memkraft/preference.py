"""Preference Layer — MemKraft PersonaMem Enhancement (v2.1.0)

Extends bitemporal facts with preference-specific semantics:
- strength: 0~1 preference intensity
- category: food/travel/music/etc.
- reason: why the preference changed
- evolution: track full preference sequence

Philosophy preserved: zero deps, plain markdown, no LLM calls.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Preference-specific marker (extends bitemporal marker)
_PREF_RE = re.compile(
    r"<!--\s*valid:\[(?P<vfrom>[^\]]*)\.\.(?P<vto>[^\]\)]*)[\]\)]\s+"
    r"recorded:(?P<recorded>[^\s>]+)\s+"
    r"strength:(?P<strength>[\d.]+)\s+"
    r"category:(?P<category>\w+)\s*-->"
)

# Simple preference line (no strength/category — defaults to 1.0 / "general")
_SIMPLE_PREF_RE = re.compile(
    r"<!--\s*valid:\[(?P<vfrom>[^\]]*)\.\.(?P<vto>[^\]\)]*)[\]\)]\s+"
    r"recorded:(?P<recorded>[^\s>]+)\s*-->"
)


class PreferenceMixin:
    """Preference tracking layer on top of bitemporal facts."""

    def pref_set(self, entity: str, key: str, value: str,
                 category: str = "general",
                 strength: float = 1.0,
                 reason: str = "",
                 source: str = "",
                 valid_from: Optional[str] = None) -> Dict[str, Any]:
        """Set a preference for an entity.

        Args:
            entity: Entity name (e.g., "Simon")
            key: Preference key (e.g., "food", "travel_style")
            value: Preference value (e.g., "korean_bbq", "adventure")
            category: Category for grouping (food, travel, music, etc.)
            strength: 0~1 preference intensity
            reason: Why this preference (for evolution tracking)
            source: Where we learned this
            valid_from: When this preference started (ISO date)

        Returns:
            dict with operation details
        """
        if not valid_from:
            valid_from = datetime.now().strftime("%Y-%m-%d")

        slug = self._slugify(entity)
        pref_dir = self.base_dir / "preferences"
        # B3 (v2.7.3): use parents=True so a fresh base_dir works first-call.
        # Previously a never-initialized base_dir raised FileNotFoundError
        # because the parent itself didn't exist yet.
        pref_dir.mkdir(exist_ok=True, parents=True)
        pref_file = pref_dir / f"{slug}.md"

        # Close any existing open-ended preference for this key
        closed = self._close_preference(pref_file, key, valid_from)

        # Write new preference
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        marker = f"<!-- valid:[{valid_from}..) recorded:{now} strength:{strength:.2f} category:{category} -->"
        line = f"- {key}: {value} {marker}"

        if reason:
            line += f"\n  - reason: {reason}"
        if source:
            line += f"\n  - source: {source}"

        with open(pref_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return {
            "entity": entity,
            "key": key,
            "value": value,
            "category": category,
            "strength": strength,
            "closed_previous": closed,
            "file": str(pref_file),
        }

    def pref_get(self, entity: str, key: Optional[str] = None,
                 at_time: Optional[str] = None,
                 category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get current (or historical) preferences for an entity."""
        slug = self._slugify(entity)
        pref_file = self.base_dir / "preferences" / f"{slug}.md"

        if not pref_file.exists():
            return []

        prefs = self._parse_preferences(pref_file)

        if key:
            prefs = [p for p in prefs if p["key"] == key]
        if category:
            prefs = [p for p in prefs if p["category"] == category]

        if at_time:
            prefs = [p for p in prefs
                     if p["valid_from"] <= at_time
                     and (p["valid_to"] is None or p["valid_to"] >= at_time)]
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            prefs = [p for p in prefs
                     if p["valid_to"] is None or p["valid_to"] >= today]

        return prefs

    def pref_evolution(self, entity: str, key: str) -> List[Dict[str, Any]]:
        """Get full evolution sequence of a preference (PersonaMem track_full_preference_evolution)."""
        slug = self._slugify(entity)
        pref_file = self.base_dir / "preferences" / f"{slug}.md"

        if not pref_file.exists():
            return []

        prefs = self._parse_preferences(pref_file)
        key_prefs = [p for p in prefs if p["key"] == key]
        key_prefs.sort(key=lambda p: p["valid_from"])
        return key_prefs

    def pref_context(self, entity: str, scenario: str = "",
                     max_prefs: int = 20) -> Dict[str, Any]:
        """Build preference context for a scenario (cross-domain transfer).

        B4 (v2.7.3): ``scenario`` is now optional. When omitted (or empty),
        no category filtering is applied — the call returns the top
        ``max_prefs`` preferences ranked purely by ``strength``, which is
        what callers that just want "all current preferences for this
        entity, ranked" already expected.
        """
        category_map = {
            "food": ["food", "cuisine", "restaurant", "cooking", "diet", "meal"],
            "travel": ["travel", "trip", "vacation", "hotel", "flight", "destination"],
            "music": ["music", "song", "playlist", "concert", "artist", "genre"],
            "entertainment": ["movie", "show", "book", "game", "hobby", "sport"],
            "shopping": ["shopping", "brand", "style", "fashion", "product"],
            "work": ["work", "career", "job", "professional", "business"],
            "health": ["health", "fitness", "exercise", "wellness", "medical"],
            "education": ["education", "learning", "course", "study", "school"],
        }

        scenario_lower = (scenario or "").lower()
        relevant_categories = set()
        if scenario_lower:
            for cat, keywords in category_map.items():
                if any(kw in scenario_lower for kw in keywords):
                    relevant_categories.add(cat)

        # No scenario, or scenario didn't hit any known keyword → consider
        # every category equally (cat_score = 1.0 for all prefs, ordering
        # falls back to pure strength).
        if not relevant_categories:
            relevant_categories = set(category_map.keys())

        all_prefs = self.pref_get(entity)

        scored = []
        for pref in all_prefs:
            cat_score = 1.0 if pref["category"] in relevant_categories else 0.3
            total_score = cat_score * pref["strength"]
            scored.append((total_score, pref))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_prefs = [p for _, p in scored[:max_prefs]]

        return {
            "entity": entity,
            "scenario": scenario,
            "relevant_categories": list(relevant_categories),
            "preferences": top_prefs,
            "total_count": len(all_prefs),
        }

    def pref_conflicts(self, entity: str) -> List[Dict[str, Any]]:
        """Detect preference conflicts (same key, different values)."""
        slug = self._slugify(entity)
        pref_file = self.base_dir / "preferences" / f"{slug}.md"

        if not pref_file.exists():
            return []

        prefs = self._parse_preferences(pref_file)

        by_key: Dict[str, List] = {}
        for p in prefs:
            by_key.setdefault(p["key"], []).append(p)

        conflicts = []
        for key, key_prefs in by_key.items():
            if len(key_prefs) > 1:
                values = set(p["value"] for p in key_prefs)
                if len(values) > 1:
                    key_prefs.sort(key=lambda p: p["valid_from"])
                    conflicts.append({
                        "key": key,
                        "values": [
                            {"value": p["value"],
                             "valid_from": p["valid_from"],
                             "valid_to": p["valid_to"],
                             "strength": p["strength"]}
                            for p in key_prefs
                        ],
                        "current": key_prefs[-1]["value"],
                    })

        return conflicts

    def pref_conflicts_all(self) -> List[Dict[str, Any]]:
        """Detect preference conflicts across ALL entities.

        Scans every preference file and reports cases where the same
        entity has the same preference key mapped to different values.

        Returns:
            list[dict]: Each entry has ``entity``, ``conflict``
            (descriptive string), and ``facts`` (list of the
            conflicting value dicts).
        """
        pref_dir = self.base_dir / "preferences"
        if not pref_dir.exists():
            return []

        results: List[Dict[str, Any]] = []
        for pref_file in sorted(pref_dir.glob("*.md")):
            entity = pref_file.stem
            entity_conflicts = self.pref_conflicts(entity)
            for c in entity_conflicts:
                values = [v["value"] for v in c.get("values", [])]
                results.append({
                    "entity": entity,
                    "conflict": f"{c['key']}: {' vs '.join(values)}",
                    "facts": c.get("values", []),
                })

        return results

    def _slugify(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]

    def _close_preference(self, pref_file: Path, key: str,
                          before_date: str) -> bool:
        if not pref_file.exists():
            return False

        content = pref_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        modified = False

        for i, line in enumerate(lines):
            if not line.strip().startswith(f"- {key}:"):
                continue

            m = _PREF_RE.search(line) or _SIMPLE_PREF_RE.search(line)
            if m and not m.group("vto"):
                old_marker = m.group(0)
                vfrom = m.group("vfrom")
                recorded = m.group("recorded")
                strength = m.group("strength") if "strength" in (m.groupdict() or {}) else "1.00"
                category = m.group("category") if "category" in (m.groupdict() or {}) else "general"

                new_marker = (
                    f"<!-- valid:[{vfrom}..{before_date}] recorded:{recorded} "
                    f"strength:{strength} category:{category} -->"
                )
                lines[i] = line.replace(old_marker, new_marker)
                modified = True

        if modified:
            pref_file.write_text("\n".join(lines), encoding="utf-8")

        return modified

    def _parse_preferences(self, pref_file: Path) -> List[Dict[str, Any]]:
        content = pref_file.read_text(encoding="utf-8")
        results = []

        for raw_line in content.split("\n"):
            line = raw_line.strip()
            if not line.startswith("- "):
                continue

            # B2 (2026-04-25): attach `- reason: ...` continuation lines
            # onto the most recent parsed preference. pref_set writes
            # reasons as a 2-space indented bullet beneath the main pref;
            # _parse_preferences previously discarded these.
            stripped_key = line[2:].split(":", 1)[0].strip().lower() if ":" in line[2:] else ""
            if stripped_key == "reason" and results:
                _, _, rest = line.partition(":")
                reason_val = rest.strip()
                if reason_val and not results[-1].get("reason"):
                    results[-1]["reason"] = reason_val
                continue

            m = _PREF_RE.search(line)
            if m:
                key_val = line.split("<!--")[0].strip()
                parts = key_val.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].lstrip("- ").strip()
                    value = parts[1].strip()
                    results.append({
                        "key": key,
                        "value": value,
                        "valid_from": m.group("vfrom") or None,
                        "valid_to": m.group("vto") or None,
                        "recorded": m.group("recorded"),
                        "strength": float(m.group("strength")),
                        "category": m.group("category"),
                        "reason": None,
                    })
                continue

            m = _SIMPLE_PREF_RE.search(line)
            if m:
                key_val = line.split("<!--")[0].strip()
                parts = key_val.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].lstrip("- ").strip()
                    value = parts[1].strip()
                    results.append({
                        "key": key,
                        "value": value,
                        "valid_from": m.group("vfrom") or None,
                        "valid_to": m.group("vto") or None,
                        "recorded": m.group("recorded"),
                        "strength": 1.0,
                        "category": "general",
                        "reason": None,
                    })

        return results
