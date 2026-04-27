"""
MemKraft 1.1.0 — Autonomous Memory Management

Lifecycle API: flush / compact / digest / health

"Memory should manage itself."
"""
from __future__ import annotations

import json
import re
import time
import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class LifecycleMixin:
    """
    Memory lifecycle management — flush, compact, digest, health.

    Mixin for MemKraft. Requires self.base_dir, self.log_event(),
    self.track(), self.update(), self.tier_set() to be available.
    """

    # ------------------------------------------------------------------
    # flush
    # ------------------------------------------------------------------

    def flush(self, source_path: str, strategy: str = "auto") -> dict:
        """
        Import external markdown file → MemKraft structured data.

        Args:
            source_path: Path to markdown file (e.g. MEMORY.md)
            strategy: "auto" (detect sections), "events" (event list),
                      "facts" (fact table)

        Returns:
            {"imported": N, "entities": N, "events": N, "facts": N}
        """
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        content = path.read_text(encoding="utf-8")
        source_name = str(path.name)

        if strategy == "auto":
            stats = self._flush_auto(content, source=source_name)
        elif strategy == "events":
            stats = self._flush_events(content, source=source_name)
        elif strategy == "facts":
            stats = self._flush_facts(content, source=source_name)
        else:
            raise ValueError(f"Unknown strategy: {strategy!r}. Use 'auto', 'events', or 'facts'.")

        return stats

    def _flush_auto(self, content: str, source: str) -> dict:
        """Auto-detect sections and import appropriately."""
        stats = {"imported": 0, "entities": 0, "events": 0, "facts": 0}

        sections = re.split(r"^## ", content, flags=re.MULTILINE)
        for section in sections:
            if not section.strip():
                continue
            lines = section.split("\n")
            title = lines[0].strip()
            body = "\n".join(lines[1:])

            # Progress/event sections → log_event
            if any(x in title for x in ["M30", "M90", "P1", "P2", "진행", "완료", "Done"]):
                count = self._import_list_items(body, source=source, event_type=title)
                stats["events"] += count
                stats["imported"] += count

            # Lessons → log_event importance=high
            elif any(x in title for x in ["교훈", "lesson", "Lesson", "Lessons", "교훈/실수"]):
                count = self._import_list_items(
                    body, source=source, event_type="lesson", importance="high"  # mapped to 'high' in _import_list_items
                )
                stats["events"] += count
                stats["imported"] += count

            # Project/entity tables → track entity
            elif any(x in title for x in ["프로젝트", "Project", "진행 중", "Services", "서비스"]):
                count = self._import_table_rows(body, source=source, entity_type="project")
                stats["entities"] += count
                stats["imported"] += count

        return stats

    def _import_list_items(
        self,
        text: str,
        source: str,
        event_type: str = "event",
        importance: str = "normal",
    ) -> int:
        """Parse list items (- xxx) and log as events."""
        count = 0
        # Map friendly names to valid log_event importance values
        _importance_map = {"medium": "normal", "low": "normal", "high": "high", "normal": "normal"}
        safe_importance = _importance_map.get(importance, "normal")
        for line in text.split("\n"):
            stripped = line.strip()
            # Match: - [날짜] 내용  OR  - 내용
            m = re.match(r"^[-*]\s+(?:\[[\d\-]+\]\s*)?(.+)$", stripped)
            if m:
                item_content = m.group(1).strip()
                if len(item_content) > 10:
                    try:
                        self.log_event(
                            item_content,
                            tags=event_type,
                            importance=safe_importance,
                        )
                        count += 1
                    except Exception:
                        pass
        return count

    def _import_table_rows(
        self, text: str, source: str, entity_type: str = "entity"
    ) -> int:
        """Parse markdown table rows and track as entities."""
        count = 0
        rows = [
            line
            for line in text.split("\n")
            if line.strip().startswith("|") and "---" not in line
        ]
        if len(rows) < 2:
            return 0

        # Skip header row
        for row in rows[1:]:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if not cells:
                continue
            entity_id = cells[0]
            if len(entity_id) >= 2:
                try:
                    self.track(entity_id, entity_type=entity_type, source=source)
                    if len(cells) > 1:
                        self.update(entity_id, " | ".join(cells[1:]), source=source)
                    count += 1
                except Exception:
                    pass
        return count

    def _flush_events(self, content: str, source: str) -> dict:
        """Import all list items as events."""
        count = self._import_list_items(content, source=source)
        return {"imported": count, "entities": 0, "events": count, "facts": 0}

    def _flush_facts(self, content: str, source: str) -> dict:
        """Import table rows as facts (entity tracking)."""
        count = self._import_table_rows(content, source=source)
        return {"imported": count, "entities": count, "events": 0, "facts": count}

    # ------------------------------------------------------------------
    # compact
    # ------------------------------------------------------------------

    def compact(self, max_chars: int = 15000, dry_run: bool = False) -> dict:
        """
        Move old/low-importance items to archival tier.

        Args:
            max_chars: Target maximum size for active memory (chars)
            dry_run: If True, preview without making changes

        Returns:
            {"moved": N, "remaining_entities": M, "freed_chars": K, "dry_run": bool}
        """
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return {
                "moved": 0,
                "remaining_entities": 0,
                "freed_chars": 0,
                "dry_run": dry_run,
            }

        now = time.time()
        moved = 0
        freed_chars = 0
        remaining = 0
        total_chars = self._estimate_memory_size()

        # Sort by mtime ascending (oldest first)
        entity_files = sorted(entities_dir.glob("*.md"), key=lambda f: f.stat().st_mtime)

        for entity_file in entity_files:
            try:
                content = entity_file.read_text(encoding="utf-8")
                tier = self._extract_frontmatter_value(content, "tier") or "recall"

                if tier == "archival":
                    remaining += 1
                    continue

                mtime = entity_file.stat().st_mtime
                days_old = (now - mtime) / 86400.0
                importance = self._extract_frontmatter_value(content, "importance") or "medium"

                should_archive = False

                # Rule 1: older than 90 days and not core
                if days_old > 90 and tier != "core":
                    should_archive = True

                # Rule 2: low importance and older than 30 days
                if importance == "low" and days_old > 30:
                    should_archive = True

                # Rule 3: total size exceeds max_chars; archive oldest recall
                if total_chars > max_chars and tier == "recall" and days_old > 30:
                    should_archive = True

                if should_archive:
                    freed_chars += len(content)
                    total_chars -= len(content)
                    if not dry_run:
                        self.tier_set(entity_file.stem, tier="archival")
                    moved += 1
                else:
                    remaining += 1

            except Exception:
                remaining += 1
                continue

        return {
            "moved": moved,
            "remaining_entities": remaining,
            "freed_chars": freed_chars,
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------
    # digest
    # ------------------------------------------------------------------

    def digest(self, output_path: str, max_chars: int = 15000) -> dict:
        """
        Render MemKraft state → MEMORY.md (always ≤ max_chars).

        Args:
            output_path: Where to write the digest (e.g. MEMORY.md)
            max_chars: Maximum output size in characters

        Returns:
            {"chars": N, "entities": M, "sections": [...], "truncated": bool}
        """
        sections = []
        total_chars = 0

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M KST")
        header = (
            f"# MEMORY.md — Auto-generated by MemKraft\n"
            f"**Updated:** {now_str} | **Source:** MemKraft digest\n\n"
        )
        sections.append(header)
        total_chars += len(header)

        # core tier entities
        if total_chars < max_chars - 2000:
            core_section = self._digest_tier(
                "core", max_chars=max_chars - total_chars - 2000
            )
            if core_section:
                block = f"## 🔴 Core Memory\n{core_section}\n\n"
                sections.append(block)
                total_chars += len(block)

        # recall tier (recent 30 days)
        if total_chars < max_chars - 1500:
            recall_section = self._digest_tier(
                "recall",
                max_chars=max_chars - total_chars - 1000,
                recent_days=30,
            )
            if recall_section:
                block = f"## 🟡 Recent Memory (30d)\n{recall_section}\n\n"
                sections.append(block)
                total_chars += len(block)

        # archival count
        if total_chars < max_chars - 200:
            archival_count = self._count_tier("archival")
            if archival_count > 0:
                line = (
                    f"## 🔵 Archived\n"
                    f"{archival_count} entities archived. "
                    f"Run `mk.search()` to retrieve.\n\n"
                )
                sections.append(line)
                total_chars += len(line)

        # recent events
        if total_chars < max_chars - 500:
            events_section = self._digest_recent_events(
                max_chars=max_chars - total_chars - 300
            )
            if events_section:
                block = f"## 📅 Recent Events\n{events_section}\n\n"
                sections.append(block)
                total_chars += len(block)

        output = "".join(sections)
        truncated = False

        if len(output) > max_chars:
            output = (
                output[: max_chars - 100]
                + "\n\n*[digest truncated — run mk.digest() for full output]*\n"
            )
            truncated = True

        Path(output_path).write_text(output, encoding="utf-8")

        return {
            "chars": len(output),
            "entities": self._count_all_entities(),
            "sections": [s.split("\n")[0][:60] for s in sections],
            "truncated": truncated,
        }

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """
        Diagnose memory health state.

        Returns:
            {
                "total_chars": N,
                "tier_distribution": {"core": N, "recall": N, "archival": N},
                "entity_count": N,
                "entity_types": {"person": N, ...},
                "edge_counts": {"works_at": N, ...},
                "decay_distribution": {"fresh": N, "stale": N, "archival": N},
                "recent_hits_7d": N,
                "recommendations": [...],
                "status": "healthy" | "warning" | "critical"
            }
        """
        total_chars = self._estimate_memory_size()
        tier_dist = {
            "core": self._count_tier("core"),
            "recall": self._count_tier("recall"),
            "archival": self._count_tier("archival"),
        }
        entity_count = self._count_all_entities()
        recommendations = []
        status = "healthy"

        # ── v2.4: Entity types from graph DB ──
        entity_types = self._health_entity_types()

        # ── v2.4: Edge counts by relation ──
        edge_counts = self._health_edge_counts()

        # ── v2.4: Decay distribution ──
        decay_dist = self._health_decay_distribution()

        # ── v2.4: Recent 7-day hit count ──
        recent_hits_7d = self._health_recent_hits(7)

        if total_chars > 100_000:
            recommendations.append(
                f"Memory is {total_chars // 1000}KB — run mk.compact() to reduce"
            )
            status = "critical"
        elif total_chars > 50_000:
            recommendations.append(
                f"Memory is {total_chars // 1000}KB — consider running mk.compact()"
            )
            status = "warning"

        if tier_dist["recall"] > 500:
            recommendations.append(
                f"{tier_dist['recall']} recall-tier entities — "
                f"compact() will archive old ones"
            )
            if status == "healthy":
                status = "warning"

        # v2.3 — suggest consolidate() when duplicates accumulate
        try:
            dup_count = self._count_duplicate_facts()  # type: ignore[attr-defined]
        except Exception:
            dup_count = 0
        if dup_count > 10:
            recommendations.append(
                f"{dup_count} duplicate facts detected — run mk.consolidate() to merge"
            )
            if status == "healthy":
                status = "warning"

        if not recommendations:
            recommendations.append("Memory is healthy ✅")

        # v2.4: convenience scalars for edge_count and top_entities
        edge_count = sum(edge_counts.values()) if isinstance(edge_counts, dict) else 0
        top_entities = self._health_top_entities()

        return {
            "total_chars": total_chars,
            "tier_distribution": tier_dist,
            "entity_count": entity_count,
            "entity_types": entity_types,
            "edge_counts": edge_counts,
            "edge_count": edge_count,
            "top_entities": top_entities,
            "decay_distribution": decay_dist,
            "recent_hits_7d": recent_hits_7d,
            "recommendations": recommendations,
            "status": status,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _estimate_memory_size(self) -> int:
        """Estimate total memory size in characters (bytes)."""
        total = 0
        base = Path(self.base_dir)
        for md_file in base.rglob("*.md"):
            try:
                total += md_file.stat().st_size
            except Exception:
                pass
        return total

    def _extract_frontmatter_value(self, content: str, key: str) -> str | None:
        """Extract a value from YAML frontmatter."""
        m = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
        return m.group(1).strip().strip("\"'") if m else None

    def _digest_tier(
        self,
        tier: str,
        max_chars: int = 5000,
        recent_days: int | None = None,
    ) -> str:
        """Render entities of a given tier as a bullet list."""
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return ""

        lines = []
        now = time.time()

        for entity_file in sorted(
            entities_dir.glob("*.md"), key=lambda f: -f.stat().st_mtime
        ):
            try:
                content = entity_file.read_text(encoding="utf-8")
                entity_tier = self._extract_frontmatter_value(content, "tier") or "recall"
                if entity_tier != tier:
                    continue

                if recent_days is not None:
                    mtime = entity_file.stat().st_mtime
                    if (now - mtime) / 86400 > recent_days:
                        continue

                # First non-frontmatter, non-empty line as summary
                body_lines = [
                    ln
                    for ln in content.split("\n")
                    if ln.strip()
                    and not ln.startswith("---")
                    and not re.match(r"^\w[\w_]*\s*:", ln)
                ]
                summary = body_lines[0][:100] if body_lines else entity_file.stem
                lines.append(f"- **{entity_file.stem}**: {summary}")

                if sum(len(ln) for ln in lines) > max_chars:
                    lines.append("- *(more — run mk.search() to retrieve)*")
                    break
            except Exception:
                continue

        return "\n".join(lines)

    def _digest_recent_events(self, max_chars: int = 2000) -> str:
        """Render recent events from events log."""
        events_file = Path(self.base_dir) / "events.jsonl"
        if not events_file.exists():
            return ""

        lines = []
        try:
            all_events = []
            with open(events_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        all_events.append(json.loads(line))
                    except Exception:
                        pass

            for event in reversed(all_events[-10:]):
                desc = event.get("description", event.get("event", ""))[:80]
                ts = event.get("timestamp", "")[:10]
                lines.append(f"- [{ts}] {desc}")
                if sum(len(ln) for ln in lines) > max_chars:
                    break
        except Exception:
            pass

        return "\n".join(lines)

    def _count_tier(self, tier: str) -> int:
        """Count entities in a given tier."""
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return 0
        count = 0
        for f in entities_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                if self._extract_frontmatter_value(content, "tier") == tier:
                    count += 1
            except Exception:
                pass
        return count

    def _count_all_entities(self) -> int:
        """Count total entity files."""
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return 0
        return len(list(entities_dir.glob("*.md")))

    def _health_entity_types(self) -> dict:
        """Count entities by type from graph DB nodes table."""
        try:
            import sqlite3
            db_path = Path(self.base_dir) / "graph.db"
            if not db_path.exists():
                return {}
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT node_type, COUNT(*) as cnt FROM nodes GROUP BY node_type"
            ).fetchall()
            conn.close()
            return {row["node_type"]: row["cnt"] for row in rows}
        except Exception:
            return {}

    def _health_edge_counts(self) -> dict:
        """Count edges by relation type from graph DB."""
        try:
            import sqlite3
            db_path = Path(self.base_dir) / "graph.db"
            if not db_path.exists():
                return {}
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT relation, COUNT(*) as cnt FROM edges GROUP BY relation ORDER BY cnt DESC"
            ).fetchall()
            conn.close()
            return {row["relation"]: row["cnt"] for row in rows}
        except Exception:
            return {}

    def _health_decay_distribution(self) -> dict:
        """Compute decay distribution: fresh (<7d), stale (7-30d), archival (>30d).

        Based on mtime of entity files in live-notes/.
        """
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return {"fresh": 0, "stale": 0, "archival": 0}
        import time
        now = time.time()
        fresh = stale = archival = 0
        for f in entities_dir.glob("*.md"):
            try:
                age_days = (now - f.stat().st_mtime) / 86400
                if age_days < 7:
                    fresh += 1
                elif age_days < 30:
                    stale += 1
                else:
                    archival += 1
            except Exception:
                pass
        return {"fresh": fresh, "stale": stale, "archival": archival}

    def _health_top_entities(self, limit: int = 5) -> list:
        """Return top N entities by reference count (from graph DB or entity files)."""
        try:
            import sqlite3
            db_path = Path(self.base_dir) / "graph.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                rows = conn.execute(
                    "SELECT to_id, COUNT(*) as cnt FROM edges "
                    "GROUP BY to_id ORDER BY cnt DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                conn.close()
                if rows:
                    return [{"name": r[0], "count": r[1]} for r in rows]
        except Exception:
            pass
        # Fallback: top entities by file size (proxy for richness)
        try:
            entities_dir = Path(self.base_dir) / "entities"
            if entities_dir.exists():
                files = sorted(entities_dir.glob("*.md"), key=lambda f: f.stat().st_size, reverse=True)
                return [{"name": f.stem, "count": f.stat().st_size} for f in files[:limit]]
        except Exception:
            pass
        return []

    def _health_recent_hits(self, days: int = 7) -> int:
        """Count entities accessed within the last N days.

        Uses 'Last Accessed' field added by search hit decay reset (v2.4).
        Falls back to mtime if 'Last Accessed' not found.
        """
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return 0
        import time
        now = time.time()
        cutoff = now - (days * 86400)
        count = 0
        for f in entities_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                # Look for 'Last Accessed: YYYY-MM-DD HH:MM:SS' pattern
                m = re.search(r'\*\*Last Accessed:\*\*\s*(\d{4}-\d{2}-\d{2})', content)
                if m:
                    from datetime import datetime as _dt
                    access_dt = _dt.strptime(m.group(1), "%Y-%m-%d")
                    if access_dt.timestamp() >= cutoff:
                        count += 1
                else:
                    # Fallback: use mtime
                    if f.stat().st_mtime >= cutoff:
                        count += 1
            except Exception:
                pass
        return count

    # ------------------------------------------------------------------
    # cleanup_orphans (v2.5.0)
    # ------------------------------------------------------------------

    def cleanup_orphans(self, dry_run: bool = True) -> dict:
        """Detect (and optionally archive) entity files with zero references.

        An entity is considered "orphaned" if no other file in the
        memory directory references its slug (via wiki-link ``[[slug]]``
        or plain text mention).

        Args:
            dry_run:
                If True (default), returns the orphan list without
                modifying anything.  If False, moves orphaned files to
                ``archival`` tier.

        Returns:
            dict: ``{"orphans": [...], "moved": int}``
        """
        entities_dir = Path(self.base_dir) / "live-notes"
        if not entities_dir.exists():
            return {"orphans": [], "moved": 0}

        # 1. Collect all entity slugs
        entity_files = list(entities_dir.glob("*.md"))
        if not entity_files:
            return {"orphans": [], "moved": 0}

        slugs: dict[str, Path] = {}
        for f in entity_files:
            slug = f.stem.lower()
            slugs[slug] = f

        # 2. Scan all .md files for references to each slug
        all_md = list(Path(self.base_dir).rglob("*.md"))
        # Exclude the entity file itself from the reference search
        referenced: set = set()

        for md_file in all_md:
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                continue
            for slug in slugs:
                if slug in referenced:
                    continue
                # Wiki-link: [[slug]] or [[slug|alias]]
                if f"[[{slug}]]" in content or f"[[{slug}|" in content:
                    referenced.add(slug)
                    continue
                # Plain text mention (word boundary check for short slugs)
                if len(slug) >= 4 and slug in content:
                    referenced.add(slug)

        # 3. Orphans = slugs not referenced by any other file
        orphans = [s for s in slugs if s not in referenced]

        # 4. If not dry_run, archive orphans
        moved = 0
        if not dry_run:
            for slug in orphans:
                try:
                    self.tier_set(slug, tier="archival")
                    moved += 1
                except Exception:
                    pass

        return {"orphans": orphans, "moved": moved}

    # ------------------------------------------------------------------
    # watch / unwatch / schedule  (M2 Lifecycle API)
    # ------------------------------------------------------------------

    def watch(self, path: str, on_change: str = "flush", interval: int = 300) -> None:
        """Watch a file/directory for changes and trigger action automatically.

        Runs a background daemon thread that polls ``path`` every ``interval``
        seconds.  When a modification is detected the chosen ``on_change``
        action is executed.

        Args:
            path: File or directory path to monitor.
            on_change: Action to trigger on change.
                ``"flush"`` — call :py:meth:`flush` on the changed file.
                ``"compact"`` — call :py:meth:`compact`.
                ``"digest"`` — call :py:meth:`digest` on the changed file.
                Any callable — called with ``(changed_path: str)``.
            interval: Poll interval in seconds (default: 300).
        """
        import threading
        import os
        import time

        path = str(path)

        def _watch_loop() -> None:
            last_mtime: dict = {}
            while getattr(self, "_watching", False):
                try:
                    if os.path.isfile(path):
                        mtime = os.path.getmtime(path)
                        if path in last_mtime and mtime != last_mtime[path]:
                            try:
                                if on_change == "flush":
                                    self.flush(path)
                                elif on_change == "compact":
                                    self.compact()
                                elif on_change == "digest":
                                    self.digest(path)
                                elif callable(on_change):
                                    on_change(path)
                            except Exception:
                                pass  # best-effort
                        last_mtime[path] = mtime
                    elif os.path.isdir(path):
                        for root, _dirs, files in os.walk(path):
                            for fname in files:
                                if not fname.endswith(".md"):
                                    continue
                                fpath = os.path.join(root, fname)
                                try:
                                    mtime = os.path.getmtime(fpath)
                                except OSError:
                                    continue
                                if fpath in last_mtime and mtime != last_mtime[fpath]:
                                    try:
                                        if on_change == "flush":
                                            self.flush(fpath)
                                        elif callable(on_change):
                                            on_change(fpath)
                                    except Exception:
                                        pass
                                last_mtime[fpath] = mtime
                except Exception:
                    pass
                time.sleep(interval)

        self._watching = True
        self._watch_thread = threading.Thread(target=_watch_loop, daemon=True, name="mk-watcher")
        self._watch_thread.start()

    def unwatch(self) -> None:
        """Stop the background file watcher started by :py:meth:`watch`."""
        self._watching = False

    def schedule(self, pipeline, cron_expr: str) -> None:
        """Schedule a memory-management pipeline using a cron expression.

        Args:
            pipeline: Ordered list of actions to run.  Each item may be:
                ``"compact"`` — run :py:meth:`compact`.
                Any zero-argument callable.
            cron_expr: Standard 5-field cron expression, e.g. ``"0 23 * * *"``
                (nightly at 23:00).

        Note:
            Requires the ``apscheduler`` package.  Install with::

                pip install "memkraft[schedule]"
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            raise ImportError(
                "schedule() requires 'apscheduler'. "
                "Install with: pip install \"memkraft[schedule]\""
            )

        def _run_pipeline() -> None:
            for action in pipeline:
                try:
                    if action == "compact":
                        self.compact()
                    elif callable(action):
                        action()
                except Exception:
                    pass  # best-effort; one failing step must not abort the rest

        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression (expected 5 fields): {cron_expr!r}")
        minute, hour, day, month, day_of_week = parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(_run_pipeline, trigger)
        scheduler.start()
        self._scheduler = scheduler
