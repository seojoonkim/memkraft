#!/usr/bin/env python3
"""MemKraft Core — Memory operations and management.

Zero-dependency compound knowledge system for AI agents.
Supports entity tracking, fact extraction, dream-cycle maintenance,
hybrid search (exact + IDF-weighted + fuzzy), and agentic multi-hop search.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MemKraft:
    """The compound knowledge system for AI agents.

    All data is stored as plain Markdown files under ``base_dir``.
    No external dependencies required — search, NER, and maintenance
    are implemented with stdlib only.
    """

    # ── Debug Session States ─────────────────────────────────
    DEBUG_STATES = ("OBSERVE", "HYPOTHESIZE", "EXPERIMENT", "CONCLUDE")
    HYPOTHESIS_STATUSES = ("testing", "rejected", "confirmed")
    EVIDENCE_RESULTS = ("supports", "contradicts", "neutral")

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(os.environ.get("MEMKRAFT_DIR", Path.cwd() / "memory"))
        self.entities_dir = self.base_dir / "entities"
        self.live_notes_dir = self.base_dir / "live-notes"
        self.decisions_dir = self.base_dir / "decisions"
        self.originals_dir = self.base_dir / "originals"
        self.inbox_dir = self.base_dir / "inbox"
        self.tasks_dir = self.base_dir / "tasks"
        self.meetings_dir = self.base_dir / "meetings"
        self.debug_dir = self.base_dir / "debug"
        self.snapshots_dir = self.base_dir / ".memkraft" / "snapshots"
        self.channels_dir = self.base_dir / ".memkraft" / "channels"
        self.context_tasks_dir = self.base_dir / ".memkraft" / "tasks"
        self.agents_dir = self.base_dir / ".memkraft" / "agents"

    # ── Init ──────────────────────────────────────────────────
    def init(self, path: str = "", force: bool = False, verbose: bool = True) -> Dict[str, Any]:
        """Initialize memory structure in base_dir.

        Args:
            path: Optional target path. If given, creates ``<path>/memory/``.
                  Otherwise uses ``self.base_dir``.
            force: If True, recreates top-level template files (RESOLVER.md,
                   TEMPLATES.md) even if they exist.
            verbose: If True (default), prints a summary banner. Set False
                     for quiet scripting.

        Returns:
            dict: ``{"created": [...], "exists": [...], "base_dir": "..."}``
            — lists are relative path strings (e.g. ``"entities/"``).
        """
        if path:
            target = Path(path) / "memory"
        else:
            target = self.base_dir

        created: List[str] = []
        exists: List[str] = []

        if target.exists():
            exists.append(str(target))
        else:
            created.append(str(target))
        target.mkdir(parents=True, exist_ok=True)

        subdirs = ["entities", "live-notes", "decisions", "originals", "inbox",
                   "tasks", "meetings", "sessions", "debug"]
        for subdir in subdirs:
            sd = target / subdir
            if sd.exists():
                exists.append(f"{subdir}/")
            else:
                created.append(f"{subdir}/")
            sd.mkdir(exist_ok=True)

        for inner in ["snapshots", "channels", "tasks", "agents"]:
            p = target / ".memkraft" / inner
            if p.exists():
                exists.append(f".memkraft/{inner}/")
            else:
                created.append(f".memkraft/{inner}/")
            p.mkdir(parents=True, exist_ok=True)

        # RESOLVER.md
        resolver_path = target / "RESOLVER.md"
        if not resolver_path.exists() or force:
            shutil.copy2(Path(__file__).parent / "templates" / "RESOLVER.md", resolver_path)
            created.append("RESOLVER.md")
        else:
            exists.append("RESOLVER.md")

        # TEMPLATES.md
        templates_path = target / "TEMPLATES.md"
        if not templates_path.exists() or force:
            shutil.copy2(Path(__file__).parent / "templates" / "TEMPLATES.md", templates_path)
            created.append("TEMPLATES.md")
        else:
            exists.append("TEMPLATES.md")

        if verbose:
            print(f"✅ MemKraft initialized at {target}")
            print("   Directories: entities/, live-notes/, decisions/, originals/, inbox/, tasks/, meetings/, sessions/")
            print("   Files: RESOLVER.md, TEMPLATES.md")

        return {
            "created": created,
            "exists": exists,
            "base_dir": str(target),
        }

    # ── Track ─────────────────────────────────────────────────
    def track(self, name: str, entity_type: str = "person", source: str = "") -> Optional[Path]:
        name = name.strip()
        if not name:
            print("Error: Entity name cannot be empty.")
            return None
        try:
            self.live_notes_dir.mkdir(parents=True, exist_ok=True)
            slug = self._slugify(name)
            filepath = self.live_notes_dir / f"{slug}.md"

            if filepath.exists():
                print(f"⚠️ Already tracking: {filepath}")
                print(f"   Use 'memkraft update \"{name}\" --info \"...\"' to add info")
                return None

            now = datetime.now().strftime("%Y-%m-%d")
            content = f"""# {name} (Live Note)

**Tier: core**

> 🔄 Auto-tracked — updates automatically as new information arrives

## Tracking Config
- **Type:** {entity_type}
- **Started:** {now}
- **Last Update:** {now}
- **Update Count:** 1
- **Source:** {source or 'Manual'}

## Current State
(Latest information accumulates here)

## Recent Activity
- **{now}** | Tracking started [Source: {source or 'Manual'}]

## Key Points
(Key points are automatically summarized here)

## Related Entities
(Links auto-populated as relationships are discovered)

## Open Threads
- [ ] Initial setup — enrichment needed

---

## Timeline (Full Record)

- **{now}** | Live note created [Source: {source or 'Manual'}]
"""
            filepath.write_text(content, encoding="utf-8")
            print(f"✅ Tracking: {filepath.relative_to(self.base_dir.parent)}")
        except OSError as e:
            print(f"❌ Error creating tracking file: {e}")
            return None

    # ── Update ────────────────────────────────────────────────
    def update(self, name: str, info: str, source: str = "manual") -> None:
        if not info or not info.strip():
            return  # Skip empty updates

        slug = self._slugify(name)
        filepath = self.live_notes_dir / f"{slug}.md"

        if not filepath.exists():
            print(f"⚠️ Not tracking '{name}'. Use 'memkraft track' first.")
            return None

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
            now = datetime.now().strftime("%Y-%m-%d")
            content, state_transitions = self._apply_state_changes(content, info)

            # Increment update count
            count_match = re.search(r'(?:Update Count|업데이트 횟수):\*\* (\d+)', content)
            if count_match:
                new_count = int(count_match.group(1)) + 1
                old_str = count_match.group(0)
                new_str = old_str[:old_str.rfind(count_match.group(1))] + str(new_count)
                content = content[:count_match.start()] + new_str + content[count_match.end():]

            # Update last update date
            last_match = re.search(r'(?:Last Update|마지막 업데이트):\*\* \d{4}-\d{2}-\d{2}', content)
            if last_match:
                new_date_str = re.sub(r'\d{4}-\d{2}-\d{2}', now, last_match.group())
                content = content[:last_match.start()] + new_date_str + content[last_match.end():]

            # Add to Recent Activity
            for marker in ["## Recent Activity", "## 최근 동향"]:
                recent_idx = content.find(marker)
                if recent_idx != -1:
                    insert_pos = content.find("\n", recent_idx) + 1
                    content = content[:insert_pos] + f"- **{now}** | {info} [Source: {source}]\n" + content[insert_pos:]
                    break

            # Add to Timeline
            for marker in ["## Timeline (Full Record)\n\n", "## 타임라인 (전체 기록)\n\n", "## Timeline\n\n"]:
                if marker in content:
                    transition_text = ""
                    for transition in state_transitions:
                        transition_text += f"- **{now}** | State transition: {transition} [Source: {source}]\n"
                    content = content.replace(
                        marker,
                        f"{marker}{transition_text}- **{now}** | {info} [Source: {source}]\n\n"
                    )
                    break

            filepath.write_text(content, encoding="utf-8")
            print(f"✅ Updated: {filepath.relative_to(self.base_dir.parent)}")
        except OSError as e:
            print(f"❌ Error updating file: {e}")
            return None

    # ── List ──────────────────────────────────────────────────
    def list_entities(self) -> None:
        found = False

        # List live notes
        if self.live_notes_dir.exists():
            for md in sorted(self.live_notes_dir.glob("*.md")):
                if md.name == "README.md":
                    continue
                content = self._safe_read(md)
                count_val = "?"
                date_val = "?"
                for line in content.split("\n"):
                    if "Update Count" in line or "업데이트 횟수" in line:
                        nums = re.findall(r'\d+', line)
                        if nums:
                            count_val = nums[-1]
                    if "Last Update" in line or "마지막 업데이트" in line:
                        dates = re.findall(r'\d{4}-\d{2}-\d{2}', line)
                        if dates:
                            date_val = dates[-1]
                print(f"  📌 {md.stem} (updates: {count_val}, last: {date_val})")
                found = True

        # List entity pages
        if self.entities_dir.exists():
            for md in sorted(self.entities_dir.glob("*.md")):
                if md.name == "README.md":
                    continue
                print(f"  📄 {md.stem}")
                found = True

        if not found:
            print("No entities found. Use 'memkraft track' or 'memkraft detect' to start.")

    # ── Brief ─────────────────────────────────────────────────
    def brief(self, name: str, save: bool = False, file_back: bool = False) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        slug = self._slugify(name)
        brief_parts = [f"# 📋 Meeting Brief: {name}", f"Generated: {now}", ""]

        # Entity page
        entity_path = self.entities_dir / f"{slug}.md"
        live_path_check = self.live_notes_dir / f"{slug}.md"
        if entity_path.exists():
            content = entity_path.read_text(encoding="utf-8", errors="replace")
            brief_parts.append("## 👤 Entity Info")
            
            # Extract meaningful summary (skip Title, Tier, etc.)
            summary_lines = []
            capture = False
            for line in content.split("\n"):
                if line.startswith("## Executive Summary") or line.startswith("## State"):
                    capture = True
                    continue
                elif line.startswith("## ") and capture:
                    break
                
                if capture and line.strip() and not line.startswith("#") and not line.startswith("**Tier"):
                    summary_lines.append(line.strip())
            
            if summary_lines:
                brief_parts.append("\n".join(summary_lines))
            elif "---" in content:
                brief_parts.append(content.split("---")[0].strip())
            else:
                brief_parts.append(content[:800])
            # Timeline
            for marker in ["## Timeline", "## 타임라인"]:
                if marker in content:
                    section = content.split(marker)[1].split("\n## ")[0]
                    entries = [l.strip() for l in section.split("\n") if l.strip().startswith("- **")][:5]
                    if entries:
                        brief_parts.append("\n## 📅 Recent Timeline")
                        brief_parts.extend(entries)
                    break
            # Open threads
            open_items = re.findall(r'- \[ \] (.+?)(?:\n|$)', content)
            if open_items:
                brief_parts.append("\n## 🔓 Open Threads")
                for item in open_items:
                    brief_parts.append(f"- [ ] {item}")
            brief_parts.append("")
        else:
            if not live_path_check.exists():
                brief_parts.append(f"## ⚠️ '{name}' not found")
                brief_parts.append("   → Use `memkraft track` or `memkraft detect` to create")
            brief_parts.append("")

        # Live note
        live_path = self.live_notes_dir / f"{slug}.md"
        if live_path.exists():
            content = live_path.read_text(encoding="utf-8", errors="replace")
            brief_parts.append("## 🔄 Live Note")
            for section in ["Current State", "현재 상태", "Key Points", "키 포인트", "Recent Activity", "최근 동향"]:
                text = self._extract_section(content, section)
                if text:
                    brief_parts.append(f"**{section}:** {text[:300]}")
            brief_parts.append("")

        # Related decisions
        if self.decisions_dir.exists():
            related_decisions = []
            for md in self.decisions_dir.glob("*.md"):
                dcontent_orig = self._safe_read(md)
                dcontent = dcontent_orig.lower()
                if name.lower() in dcontent or slug in dcontent or f"[[{slug}]]" in dcontent:
                    first_line = self._first_meaningful_line(dcontent_orig)
                    related_decisions.append(f"  - {md.stem}: {first_line[:80]}")
            if related_decisions:
                brief_parts.append("## 📌 Related Decisions")
                brief_parts.extend(related_decisions[:10])
                brief_parts.append("")

        # Checklist
        brief_parts.append("## ✅ Pre-Meeting Checklist")
        brief_parts.append("- [ ] Review recent communication history")
        brief_parts.append("- [ ] Review open threads")
        if not entity_path.exists():
            brief_parts.append("- [ ] ⚠️ Create entity page")
        brief_parts.append("- [ ] Plan post-meeting updates")
        brief_parts.append("")
        brief_parts.append("---")
        brief_parts.append(f"*Auto-generated by MemKraft | `memkraft brief \"{name}\"`*")

        output = "\n".join(brief_parts)
        print(output)

        if save:
            save_path = self.meetings_dir / f"{datetime.now().strftime('%Y-%m-%d')}-{slug}-brief.md"
            self.meetings_dir.mkdir(parents=True, exist_ok=True)
            save_path.write_text(output, encoding="utf-8")
            print(f"\n💾 Saved: {save_path}")

        if file_back:
            now = datetime.now().strftime("%Y-%m-%d")
            for directory in [self.live_notes_dir, self.entities_dir]:
                filepath = directory / f"{slug}.md"
                if filepath.exists():
                    content = self._safe_read(filepath)
                    feedback = f"- **{now}** | [Filed back] Brief generated for '{name}' [Source: brief | Confidence: verified]"
                    for marker in ["## Timeline (Full Record)\n\n", "## Timeline\n\n"]:
                        if marker in content:
                            content = content.replace(marker, f"{marker}{feedback}\n")
                            filepath.write_text(content, encoding="utf-8")
                            print(f"📂 Filed back brief generation to {filepath.name} timeline")
                            break
                    break

    # ── Detect ────────────────────────────────────────────────
    def detect(self, text: str, source: str = "", dry_run: bool = False) -> None:
        entities = self._detect_regex(text)
        for e in entities:
            e["source"] = source
            if dry_run:
                e["action"] = "would_create"
                e["path"] = str(self.entities_dir / f"{self._slugify(e['name'])}.md")
            else:
                self._create_entity(e["name"], e.get("type", "person"), source)
                e["action"] = "created"
                e["path"] = str(self.entities_dir / f"{self._slugify(e['name'])}.md")
        print(json.dumps(entities, indent=2, ensure_ascii=False))

    # ── Dream ─────────────────────────────────────────────────
    def dream(self, date: str = None, dry_run: bool = False, resolve_conflicts: bool = False) -> Dict[str, Any]:
        """Run the Dream Cycle — nightly maintenance for memory health.

        Performs 7 health checks:
        1. Incomplete source attributions in timelines
        2. Thin entity pages (<300 bytes)
        3. Duplicate entities (normalized slug comparison)
        4. Overdue inbox items (>48 hours)
        5. Bloated pages (>4KB — context window waste)
        6. Source-less facts in Key Points (no ``[Source: ...]``)
        7. Bloated page compression suggestions

        Returns a dict of ``{check_name: count}`` for programmatic use.
        """
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        print(f"🌙 Dream Cycle — {target_date}")
        print(f"   Mode: {'dry-run' if dry_run else 'live'}")

        issues: Dict[str, int] = {
            "incomplete_sources": 0,
            "thin_entities": 0,
            "duplicate_entities": 0,
            "inbox_overdue": 0,
            "bloated_pages": 0,
            "sourceless_facts": 0,
        }
        details: Dict[str, List[str]] = {k: [] for k in issues}

        # Ensure daily note exists before running (skip in dry-run to keep read-only)
        if not dry_run:
            self.ensure_daily_note()

        # Check for incomplete source attributions
        print("   🔍 Scanning for incomplete source attributions...")
        for md in self._all_md_files():
            content = self._safe_read(md)
            if "## Timeline" in content:
                section = content.split("## Timeline")[1].split("\n## ")[0]
                for line in section.split("\n"):
                    if line.strip().startswith("- **") and "[Source:" not in line and "{" not in line:
                        issues["incomplete_sources"] += 1
                        rel = str(md.relative_to(self.base_dir))
                        details["incomplete_sources"].append(f"{rel}: {line.strip()[:80]}")

        # Check for source-less facts in Key Points
        print("   🔍 Scanning for source-less facts in Key Points...")
        for md in self._all_md_files():
            content = self._safe_read(md)
            for marker in ["## Key Points\n", "## 키 포인트\n", "## 핵심 포인트\n"]:
                if marker in content:
                    section = content.split(marker)[1].split("\n## ")[0]
                    for line in section.split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("- ") and len(stripped) > 10 and "[Source:" not in stripped:
                            # Skip placeholder lines
                            if stripped.startswith("(") or "enrichment needed" in stripped.lower():
                                continue
                            issues["sourceless_facts"] += 1
                            rel = str(md.relative_to(self.base_dir))
                            details["sourceless_facts"].append(f"{rel}: {stripped[:80]}")
                    break

        # Check for thin entity pages
        print("   🔍 Scanning for thin entity pages...")
        if self.entities_dir.exists():
            for md in self.entities_dir.glob("*.md"):
                try:
                    if md.stat().st_size < 300:
                        issues["thin_entities"] += 1
                        details["thin_entities"].append(str(md.relative_to(self.base_dir)))
                except OSError:
                    continue

        # Check for duplicate entities
        print("   🔍 Scanning for duplicate entities...")
        if self.entities_dir.exists():
            seen_normalized: Dict[str, str] = {}
            for md in self.entities_dir.glob("*.md"):
                # Normalize slug: lowercase, strip common suffixes
                norm = md.stem.lower().replace("-", "")
                # Also strip Korean particles for comparison
                norm_kr = re.sub(r'(이|을|를|은|는|에|로|의)$', '', norm)
                for n in [norm, norm_kr]:
                    if n in seen_normalized and seen_normalized[n] != md.stem:
                        issues["duplicate_entities"] += 1
                        msg = f"{md.stem} ↔ {seen_normalized[n]}"
                        details["duplicate_entities"].append(msg)
                        print(f"      ⚠️ Possible duplicate: {msg}")
                    else:
                        seen_normalized[n] = md.stem

        # Check for overdue inbox items
        print("   🔍 Scanning inbox for overdue items...")
        if self.inbox_dir.exists():
            now_ts = datetime.now().timestamp()
            for md in self.inbox_dir.glob("*.md"):
                if md.name.startswith("_") or md.name == "README.md":
                    continue
                try:
                    age_hours = (now_ts - md.stat().st_mtime) / 3600
                except OSError:
                    continue
                if age_hours > 48:
                    issues["inbox_overdue"] += 1
                    details["inbox_overdue"].append(f"{md.name} ({age_hours:.0f}h)")

        # Check for bloated entity pages (auto-compact)
        # Inspired by Recursive Language Models (arXiv:2512.24601):
        # bloated pages waste context window — flag for compaction
        print("   🔍 Scanning for bloated pages (auto-compact candidates)...")
        for md in self._all_md_files():
            try:
                size = md.stat().st_size
            except OSError:
                continue
            if size > 4000:  # >4KB suggests Compiled Truth needs condensing
                issues["bloated_pages"] += 1
                rel = md.relative_to(self.base_dir)
                suggestion = self._compression_suggestion(md, size)
                details["bloated_pages"].append(f"{rel} ({size}B) — {suggestion}")
                if issues["bloated_pages"] <= 5:
                    print(f"      ⚠️ {rel} ({size}B) — {suggestion}")

        # Check for facts without confidence levels
        print("   🔍 Scanning for facts without confidence levels...")
        issues["no_confidence"] = 0
        details["no_confidence"] = []
        for md in self._all_md_files():
            content = self._safe_read(md)
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- ") and "[Source:" in stripped and "Confidence:" not in stripped:
                    # Skip placeholder lines
                    if stripped.startswith("(") or "enrichment needed" in stripped.lower():
                        continue
                    issues["no_confidence"] += 1
                    rel = str(md.relative_to(self.base_dir))
                    details["no_confidence"].append(f"{rel}: {stripped[:80]}")

        # Check for unresolved conflicts
        print("   🔍 Scanning for unresolved conflicts...")
        conflicts_path = self.base_dir / "CONFLICTS.md"
        conflict_count = 0
        if conflicts_path.exists():
            cc = conflicts_path.read_text(encoding="utf-8", errors="replace")
            conflict_count = cc.count("❌ unresolved")
        issues["unresolved_conflicts"] = conflict_count
        details["unresolved_conflicts"] = []

        # Resolve conflicts if flag is set
        conflict_resolution = None
        if resolve_conflicts and conflict_count > 0:
            print("   ⚔️ Resolving conflicts (strategy: newest)...")
            conflict_resolution = self.resolve_conflicts(strategy="newest", dry_run=dry_run)

        total = sum(issues.values())
        print(f"\n🌙 Dream Cycle complete: {total} total issues found")
        print(f"   Incomplete sources: {issues['incomplete_sources']}")
        print(f"   Source-less facts: {issues['sourceless_facts']}")
        print(f"   No confidence tag: {issues['no_confidence']}")
        print(f"   Thin entities: {issues['thin_entities']}")
        print(f"   Duplicate entities: {issues['duplicate_entities']}")
        print(f"   Inbox overdue: {issues['inbox_overdue']}")
        print(f"   Bloated pages: {issues['bloated_pages']}")
        print(f"   Unresolved conflicts: {issues['unresolved_conflicts']}")

        if not dry_run:
            meta_dir = self.base_dir / ".memkraft"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "last-dream-timestamp").write_text(str(datetime.now().timestamp()), encoding="utf-8")

        # Run health check as part of Dream Cycle
        print("\n   🏥 Running health check...")
        health_result = self.health_check()

        result = {"issues": issues, "details": details, "total": total, "health": health_result}
        if conflict_resolution:
            result["conflict_resolution"] = conflict_resolution
        return result

    def _compression_suggestion(self, md: Path, size: int) -> str:
        """Generate an actionable compression suggestion for a bloated page."""
        content = self._safe_read(md)
        timeline_lines = 0
        key_points = 0
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- **") and "**" in stripped[4:]:
                timeline_lines += 1
            if stripped.startswith("- ") and "[Source:" in stripped:
                key_points += 1

        suggestions = []
        if timeline_lines > 20:
            suggestions.append(f"condense timeline ({timeline_lines} entries → keep latest 10)")
        if key_points > 10:
            suggestions.append(f"merge similar key points ({key_points} items)")
        if size > 8000:
            suggestions.append("split into sub-pages or promote to archival tier")
        if not suggestions:
            suggestions.append("consider condensing Compiled Truth")
        return "; ".join(suggestions)

    # ── Dialectic Synthesis (Conflict Detection) ───────────
    def detect_conflicts(self, entity_name: str, new_fact: str,
                         threshold: float = 0.4) -> List[Dict[str, Any]]:
        """Detect if a new fact conflicts with existing entity facts.

        Uses difflib.SequenceMatcher to find same-subject + opposing predicate.
        Returns list of conflict dicts with old_fact, new_fact, similarity, etc.
        """
        slug = self._slugify(entity_name)
        conflicts = []

        # Look in both live-notes and entities
        for directory in [self.live_notes_dir, self.entities_dir]:
            filepath = directory / f"{slug}.md"
            if not filepath.exists():
                continue

            content = self._safe_read(filepath)
            existing_facts = self._extract_bullet_facts(content)

            new_lower = new_fact.lower().strip()
            for old_fact in existing_facts:
                old_lower = old_fact.lower().strip()
                # Skip if identical
                if old_lower == new_lower:
                    continue

                # Check similarity — high similarity means same topic
                sim = SequenceMatcher(None, old_lower, new_lower).ratio()
                if sim < threshold:
                    continue

                # Check for opposing predicates (negation, different values)
                is_conflict = self._is_opposing(old_lower, new_lower)
                if is_conflict:
                    conflicts.append({
                        "entity": entity_name,
                        "old_fact": old_fact,
                        "new_fact": new_fact,
                        "similarity": round(sim, 2),
                        "file": str(filepath.relative_to(self.base_dir)),
                    })

        return conflicts

    def _is_opposing(self, old: str, new: str) -> bool:
        """Detect if two facts are opposing/contradictory."""
        # Negation detection
        negation_pairs = [
            ("is ", "is not "), ("is ", "isn't "),
            ("was ", "was not "), ("was ", "wasn't "),
            ("can ", "cannot "), ("can ", "can't "),
            ("will ", "will not "), ("will ", "won't "),
            ("이다", "아니다"), ("맞다", "틀리다"), ("맞다", "아니다"),
            ("true", "false"), ("yes", "no"),
            ("active", "inactive"), ("open", "closed"),
            ("joined", "left"), ("started", "stopped"),
            ("accepted", "rejected"), ("approved", "denied"),
        ]

        for pos, neg in negation_pairs:
            if (pos in old and neg in new) or (neg in old and pos in new):
                return True

        # Same field, different value detection
        # Pattern: "Role: X" vs "Role: Y" where X != Y
        field_pattern = r'(\w+):\s*(.+?)$'
        old_match = re.search(field_pattern, old)
        new_match = re.search(field_pattern, new)
        if old_match and new_match:
            if old_match.group(1).lower() == new_match.group(1).lower():
                if old_match.group(2).strip().lower() != new_match.group(2).strip().lower():
                    return True

        # High similarity (>0.6) but not identical suggests value change
        sim = SequenceMatcher(None, old, new).ratio()
        if sim > 0.6 and sim < 0.95:
            # Check if they share a subject but differ in predicate
            old_tokens = old.split()
            new_tokens = new.split()
            if len(old_tokens) >= 3 and len(new_tokens) >= 3:
                # Same first 2 words (subject) but different rest (predicate)
                if old_tokens[:2] == new_tokens[:2] and old_tokens[2:] != new_tokens[2:]:
                    return True

        return False

    def _extract_bullet_facts(self, content: str) -> List[str]:
        """Extract bullet-point facts from Key Points and Timeline sections."""
        facts = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") and len(stripped) > 10:
                # Clean up: remove source tags, timestamps, markers
                clean = re.sub(r'\[Source:.*?\]', '', stripped)
                clean = re.sub(r'\[CONFLICT\]', '', clean)
                clean = re.sub(r'^- \*\*\d{4}-\d{2}-\d{2}\*\* \| ', '- ', clean)
                clean = re.sub(r'^- ⏳ ', '- ', clean)
                clean = clean.strip()
                if clean.startswith("- ") and len(clean) > 5:
                    facts.append(clean[2:])  # Strip leading "- "
        return facts

    def _tag_conflict(self, filepath: Path, old_fact: str, new_fact: str, source: str) -> None:
        """Tag a conflict in the entity file: keep both, mark with [CONFLICT]."""
        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d")

        # Add [CONFLICT] tag to the new fact in Key Points
        for marker in ["## Key Points\n", "── 키 포인트\n"]:
            if marker in content:
                conflict_entry = f"- [CONFLICT] {new_fact} [Source: {source}] (conflicts with: {old_fact[:60]})\n"
                content = content.replace(marker, f"{marker}{conflict_entry}")
                break

        # Add to Timeline
        for marker in ["## Timeline\n\n", "## Timeline (Full Record)\n\n"]:
            if marker in content:
                content = content.replace(
                    marker,
                    f"{marker}- **{now}** | [CONFLICT] Detected conflict: '{new_fact[:50]}' vs '{old_fact[:50]}' [Source: {source}]\n"
                )
                break

        filepath.write_text(content, encoding="utf-8")

    def _write_conflicts_report(self, conflicts: List[Dict[str, Any]]) -> Path:
        """Write or update CONFLICTS.md with detected conflicts."""
        conflicts_path = self.base_dir / "CONFLICTS.md"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        if conflicts_path.exists():
            existing = conflicts_path.read_text(encoding="utf-8", errors="replace")
        else:
            existing = "# ⚔️ Fact Conflicts\n\nAuto-detected contradictions in memory. Resolve with `memkraft dream --resolve-conflicts`.\n\n"

        if conflicts:
            existing += f"## {now}\n\n"
            for c in conflicts:
                existing += f"### {c['entity']}\n"
                existing += f"- **Old:** {c['old_fact']}\n"
                existing += f"- **New:** {c['new_fact']}\n"
                existing += f"- **Similarity:** {c['similarity']}\n"
                existing += f"- **File:** {c['file']}\n"
                existing += f"- **Status:** ❌ unresolved\n\n"

        conflicts_path.write_text(existing, encoding="utf-8")
        return conflicts_path

    def resolve_conflicts(self, strategy: str = "newest", dry_run: bool = False) -> Dict[str, Any]:
        """Resolve detected conflicts in CONFLICTS.md.

        Strategies:
        - 'newest': Keep the newest fact (default)
        - 'confidence': Keep the fact with higher confidence level (verified > experimental > hypothesis)
        - 'keep-both': Keep both with [CONFLICT] tag
        - 'prompt': Generate synthesis prompt for LLM resolution
        """
        conflicts_path = self.base_dir / "CONFLICTS.md"
        if not conflicts_path.exists():
            print("✅ No conflicts to resolve.")
            return {"resolved": 0, "remaining": 0}

        content = conflicts_path.read_text(encoding="utf-8", errors="replace")

        # Parse unresolved conflicts
        unresolved = []
        conflict_blocks = re.findall(
            r'### (.+?)\n- \*\*Old:\*\* (.+?)\n- \*\*New:\*\* (.+?)\n- \*\*Similarity:\*\* ([\d.]+)\n- \*\*File:\*\* (.+?)\n- \*\*Status:\*\* ❌ unresolved',
            content
        )

        for entity, old_fact, new_fact, sim, filepath in conflict_blocks:
            unresolved.append({
                "entity": entity,
                "old_fact": old_fact,
                "new_fact": new_fact,
                "similarity": float(sim),
                "file": filepath,
            })

        if not unresolved:
            print("✅ No unresolved conflicts.")
            return {"resolved": 0, "remaining": 0}

        resolved = 0
        for conflict in unresolved:
            if dry_run:
                print(f"  [dry-run] Would resolve: {conflict['entity']} — {strategy}")
                resolved += 1
                continue

            if strategy == "newest":
                # Remove old fact's [CONFLICT] tags, keep new fact
                md_path = self.base_dir / conflict["file"]
                if md_path.exists():
                    fc = md_path.read_text(encoding="utf-8", errors="replace")
                    # Remove [CONFLICT] tag from the new fact line
                    fc = fc.replace(f"[CONFLICT] {conflict['new_fact']}", conflict['new_fact'])
                    md_path.write_text(fc, encoding="utf-8")
                resolved += 1

            elif strategy == "confidence":
                # Keep the fact with higher confidence level
                old_conf = self._extract_fact_confidence(conflict["old_fact"])
                new_conf = self._extract_fact_confidence(conflict["new_fact"])
                conf_order = {"verified": 3, "experimental": 2, "hypothesis": 1, "": 0}
                winner = "new" if conf_order.get(new_conf, 0) >= conf_order.get(old_conf, 0) else "old"
                md_path = self.base_dir / conflict["file"]
                if md_path.exists():
                    fc = md_path.read_text(encoding="utf-8", errors="replace")
                    if winner == "new":
                        fc = fc.replace(f"[CONFLICT] {conflict['new_fact']}", conflict['new_fact'])
                    md_path.write_text(fc, encoding="utf-8")
                print(f"  ✅ Resolved {conflict['entity']}: {winner} fact wins (confidence: {new_conf or 'none'} vs {old_conf or 'none'})")
                resolved += 1

            elif strategy == "keep-both":
                # Just mark as resolved in CONFLICTS.md
                resolved += 1

            elif strategy == "prompt":
                # Generate synthesis prompt
                print(f"  🤖 Synthesis prompt for {conflict['entity']}:")
                print(f"     Given these contradictory facts about {conflict['entity']}:")
                print(f"     1. {conflict['old_fact']}")
                print(f"     2. {conflict['new_fact']}")
                print(f"     Which is correct? Synthesize a single accurate statement.")
                resolved += 1

        # Update CONFLICTS.md: mark resolved
        if not dry_run:
            content = content.replace("❌ unresolved", "✅ resolved")
            conflicts_path.write_text(content, encoding="utf-8")

        remaining = len(unresolved) - resolved
        print(f"⚔️ Conflicts: {resolved} resolved ({strategy}), {remaining} remaining")
        return {"resolved": resolved, "remaining": remaining, "strategy": strategy}

    # ── Extract ──────────────────────────────────────────────
    # ── Confidence Levels ─────────────────────────────────────
    CONFIDENCE_LEVELS = ("verified", "experimental", "hypothesis")
    CONFIDENCE_WEIGHTS = {"verified": 1.0, "experimental": 0.7, "hypothesis": 0.4}

    def extract(self, text: str, source: str = "", dry_run: bool = False,
                confidence: str = "experimental",
                applicability: str = "") -> List[Dict[str, Any]]:
        """Auto-extract entities and facts from text, write to memory.

        Args:
            confidence: Confidence level for extracted facts
                        (verified / experimental / hypothesis). Default: experimental.
            applicability: Applicability condition string for the facts.
                          Format: 'When: condition' or 'When NOT: condition' or both
                          separated by ' | '. Optional.
        """
        return self.extract_conversations(text, source=source, dry_run=dry_run,
                                          confidence=confidence, applicability=applicability)

    def extract_conversations(self, input_text: str = "", source: str = "", dry_run: bool = False,
                               confidence: str = "experimental",
                               applicability: str = "") -> List[Dict[str, Any]]:
        """Auto-extract entities/facts from markdown text, file path, or stdin.

        Args:
            confidence: Confidence level for extracted facts
                        (verified / experimental / hypothesis). Default: experimental.
            applicability: Applicability condition string (optional).
                          Format: 'When: condition' or 'When NOT: condition'.
        """
        text, resolved_source = self._resolve_extract_input(input_text)
        if source:
            resolved_source = source

        if not text.strip():
            print("No input text provided. Usage: memkraft extract <text-or-filepath> --source <source>")
            return []

        entities = self._detect_regex(text)
        facts = self._extract_facts(text)
        registry_facts = self._extract_registry_facts(text)
        results = []

        for e in entities:
            e["source"] = resolved_source
            if dry_run:
                e["action"] = "would_create"
                e["path"] = str(self.entities_dir / f"{self._slugify(e['name'])}.md")
            else:
                self._create_entity(e["name"], e.get("type", "person"), resolved_source)
                e["action"] = "created"
                e["path"] = str(self.entities_dir / f"{self._slugify(e['name'])}.md")
            results.append(e)

        all_conflicts = []
        for f in facts:
            f["source"] = resolved_source

            # Dialectic Synthesis: check for conflicts before appending
            conflicts = self.detect_conflicts(f["entity"], f["fact"])
            if conflicts:
                f["conflicts"] = conflicts
                all_conflicts.extend(conflicts)
                if not dry_run:
                    for c in conflicts:
                        conflict_path = self.base_dir / c["file"]
                        if conflict_path.exists():
                            self._tag_conflict(conflict_path, c["old_fact"], f["fact"], resolved_source)

            if dry_run:
                f["action"] = "would_append"
            else:
                self._append_fact(f["entity"], f["fact"], resolved_source,
                                  confidence=confidence, applicability=applicability)
                f["action"] = "appended"
            f["confidence"] = confidence
            if applicability:
                f["applicability"] = applicability
            results.append(f)

        # Write CONFLICTS.md if any conflicts detected
        if all_conflicts and not dry_run:
            conflicts_path = self._write_conflicts_report(all_conflicts)
            results.append({"type": "conflicts", "count": len(all_conflicts), "path": str(conflicts_path), "action": "written"})

        registry_entries = []
        for f in facts:
            registry_entries.append(f"{f['entity']}: {f['fact']}")
        registry_entries.extend(registry_facts)

        if registry_entries:
            if dry_run:
                results.append({"type": "fact-registry", "facts": registry_entries, "action": "would_write"})
            else:
                written = self._write_fact_registry(registry_entries, resolved_source)
                results.append({"type": "fact-registry", "facts": written, "action": "written", "count": len(written)})

        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results

    def _extract_facts(self, text: str) -> List[Dict[str, str]]:
        """Extract key facts from text using regex patterns."""
        facts = []
        # Pattern: "X is Y", "X was Y", "X serves as Y", "X joined Y"
        fact_patterns = [
            r'([A-Z][a-z]+ [A-Z][a-z]+) (?:is|was|serves as|joined|became|founded|leads|runs|leads) (.+?)(?:\.|,|;|$)',
            r'([\uAC00-\uD7AF]{2,4})(?:은|는|이|가) (.+?)(?:이다|다|했다|임|됨|\.)',
        ]
        for pattern in fact_patterns:
            for match in re.finditer(pattern, text):
                entity_name = match.group(1).strip()
                fact_text = match.group(2).strip()
                if len(fact_text) > 5 and len(entity_name) > 1:
                    facts.append({"entity": entity_name, "fact": fact_text, "type": "fact"})
        return facts

    def _resolve_extract_input(self, input_text: str) -> Tuple[str, str]:
        """Resolve extract input from a file path, literal text, or stdin."""
        if input_text:
            # Skip path check for very long inputs (clearly not a file path)
            if len(input_text) <= 4096:
                maybe_path = Path(input_text).expanduser()
                try:
                    if maybe_path.exists() and maybe_path.is_file():
                        return maybe_path.read_text(encoding="utf-8", errors="replace"), str(maybe_path)
                except OSError:
                    pass  # Not a valid path, treat as text
            return input_text, "inline"

        try:
            if not sys.stdin.isatty():
                return sys.stdin.read(), "stdin"
        except (OSError, AttributeError):
            pass

        return "", ""

    def _extract_registry_facts(self, text: str) -> List[str]:
        """Extract numeric/date facts for the cross-domain fact registry."""
        facts = []
        patterns = [
            r'[\$₩€]\s?[\d,.]+(?:\s*(?:million|billion|trillion|만|억|조|M|B|K))?\b',
            r'\d+(?:\.\d+)?%',
            r'\d+(?:,\d+)*(?:\s+(?:items|users|employees|members|people|명|개|건|팀))',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                facts.append(m.group().strip())

        for m in re.finditer(r'\d{4}-\d{2}-\d{2}', text):
            start = max(0, m.start() - 20)
            prefix = text[start:m.start()].lower()
            if "source" in prefix or "update" in prefix or "started" in prefix or "**" in prefix:
                continue
            facts.append(m.group())

        return list(dict.fromkeys(facts))

    def _write_fact_registry(self, facts: list, source: str = "") -> List[str]:
        """Append de-duplicated facts to fact-registry.md."""
        clean_facts = [f.strip() for f in facts if f and f.strip()]
        if not clean_facts:
            return []

        registry = self.base_dir / "fact-registry.md"
        registry.parent.mkdir(parents=True, exist_ok=True)
        existing = registry.read_text(encoding="utf-8", errors="replace") if registry.exists() else "# Fact Registry\n\nCross-domain index of concrete data points.\n\n"
        existing_facts = set(re.findall(r'^- (.+?)(?: \[Source:.*\])?$', existing, re.MULTILINE))
        new_facts = [f for f in dict.fromkeys(clean_facts) if f not in existing_facts]
        if not new_facts:
            return []

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        existing += f"\n## {now}\n"
        for fact in new_facts:
            source_suffix = f" [Source: {source}]" if source else ""
            existing += f"- {fact}{source_suffix}\n"
        registry.write_text(existing, encoding="utf-8")
        return new_facts

    def _apply_state_changes(self, content: str, info: str) -> Tuple[str, List[str]]:
        """Update Current State lines and return human-readable transitions."""
        candidates = self._extract_state_candidates(info)
        if not candidates:
            return content, []

        section_match = re.search(r'## (?:Current State|State|현재 상태)\n', content)
        if not section_match:
            return content, []

        section_start = section_match.end()
        next_section = re.search(r'\n## ', content[section_start:])
        section_end = section_start + next_section.start() if next_section else len(content)
        section = content[section_start:section_end]
        transitions = []

        for field, new_value in candidates.items():
            field_pattern = re.compile(rf'(^- \*\*{re.escape(field)}:\*\* )(.*)$', re.MULTILINE)
            match = field_pattern.search(section)
            if match:
                old_value = match.group(2).strip()
                if self._is_material_state_change(old_value, new_value):
                    transitions.append(f"{field} changed from `{old_value}` to `{new_value}`")
                section = section[:match.start(2)] + new_value + section[match.end(2):]
                continue

            placeholder_match = re.search(r'^\((?:Latest information accumulates here|enrichment needed).*\)\n?', section, re.MULTILINE)
            insertion = f"- **{field}:** {new_value}\n"
            if placeholder_match:
                section = section[:placeholder_match.start()] + insertion + section[placeholder_match.end():]
            else:
                section = insertion + section

        return content[:section_start] + section + content[section_end:], transitions

    def _extract_state_candidates(self, info: str) -> Dict[str, str]:
        """Extract simple state fields from update text."""
        fields = {
            "Role": [
                r'(?:^|\b)(?:role|title|position)\s*(?::|is|=)\s*(.+)$',
                r'\b(?:is|became|serves as|was named|appointed as)\s+(?:the\s+)?(.+?)(?:\.|$)',
            ],
            "Affiliation": [
                r'(?:^|\b)(?:affiliation|company|organization|org)\s*(?::|is|=)\s*(.+)$',
                r'\b(?:joined|left|moved to)\s+(.+?)(?:\.|$)',
            ],
            "Status": [
                r'(?:^|\b)status\s*(?::|is|=)\s*(.+)$',
            ],
            "Location": [
                r'(?:^|\b)location\s*(?::|is|=)\s*(.+)$',
                r'\b(?:based in|located in)\s+(.+?)(?:\.|$)',
            ],
        }
        candidates = {}
        for field, patterns in fields.items():
            for pattern in patterns:
                match = re.search(pattern, info, re.IGNORECASE)
                if match:
                    value = match.group(1).strip(" .;")
                    if value:
                        candidates[field] = value[:200]
                    break
        return candidates

    def _is_material_state_change(self, old_value: str, new_value: str) -> bool:
        old_clean = old_value.strip()
        new_clean = new_value.strip()
        if old_clean.lower() == new_clean.lower():
            return False
        if not old_clean or "enrichment needed" in old_clean.lower() or "latest information" in old_clean.lower():
            return False
        return True

    def _append_fact(self, entity_name: str, fact: str, source: str = "",
                     confidence: str = "experimental",
                     applicability: str = ""):
        """Append a fact to an entity's live note.

        Args:
            confidence: Confidence level (verified / experimental / hypothesis).
            applicability: Applicability condition (optional).
        """
        slug = self._slugify(entity_name)
        conf_tag = f" | Confidence: {confidence}" if confidence else ""
        app_tag = f" | {applicability}" if applicability else ""
        # Try live-notes first, then entities
        for directory in [self.live_notes_dir, self.entities_dir]:
            filepath = directory / f"{slug}.md"
            if filepath.exists():
                now = datetime.now().strftime("%Y-%m-%d")
                content = filepath.read_text(encoding="utf-8", errors="replace")
                # Add to Key Points section
                for marker in ["## Key Points\n", "## 키 포인트\n"]:
                    if marker in content:
                        content = content.replace(marker, f"{marker}- {fact} [Source: {source}{conf_tag}]{app_tag}\n")
                        break
                else:
                    # Add to timeline if no Key Points section
                    for marker in ["## Timeline\n\n", "## Timeline (Full Record)\n\n"]:
                        if marker in content:
                            content = content.replace(marker, f"{marker}- **{now}** | {fact} [Source: {source}{conf_tag}]{app_tag}\n")
                            break
                filepath.write_text(content, encoding="utf-8")
                return

    # ── Cognify ────────────────────────────────────────────────
    def cognify(self, dry_run: bool = False, apply: bool = False) -> None:
        """Process inbox items — recommendation-only by default. Use --apply to auto-move."""
        if not self.inbox_dir.exists():
            print("No inbox directory found. Run 'memkraft init' first.")
            return

        # Default is recommendation-only; --apply enables auto-classify
        should_apply = apply and not dry_run

        results = {"processed": 0, "skipped": 0, "routed": {}}
        for md in sorted(self.inbox_dir.glob("*.md")):
            if md.name.startswith("_") or md.name == "README.md":
                continue

            content = self._safe_read(md).strip()
            if len(content) < 20:
                results["skipped"] += 1
                continue

            # Classify based on content heuristics
            route = self._classify_content(content)
            results["routed"][md.name] = route

            if should_apply:
                target_dir = self._route_to_dir(route)
                if target_dir:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_path = target_dir / md.name
                    md.rename(target_path)

            results["processed"] += 1

        mode_str = "recommend" if not should_apply else "applied"
        print(f"🧠 Cognify complete ({mode_str} mode): {results['processed']} processed, {results['skipped']} skipped")
        for name, route in results["routed"].items():
            if should_apply:
                print(f"   routed: {name} → {route}")
            else:
                print(f"   → {name}: {route} (use --apply to move)")

    def _classify_content(self, content: str) -> str:  # noqa: PLR0911
        """Classify content based on heuristics."""
        lower = content.lower()
        # Decision markers
        if any(kw in lower for kw in ["decided", "decision", "chose", "agreed"]):
            return "decision"
        # Task markers
        if any(kw in lower for kw in ["todo", "task", "action item", "need to", "must"]):
            return "task"
        # Person markers
        if any(kw in lower for kw in ["ceo", "cto", "founder", "investor", "director"]):
            return "entity"
        # Default to entity
        return "entity"

    def _route_to_dir(self, route: str) -> Path:
        """Map classification to directory."""
        mapping = {"entity": self.entities_dir, "decision": self.decisions_dir, "task": self.tasks_dir}
        return mapping.get(route)

    # ── Promote (Memory Tiers) ────────────────────────────────
    def promote(self, name: str, tier: str = "core") -> None:
        """Change memory tier for an entity."""
        if tier not in ("core", "recall", "archival"):
            print(f"Invalid tier '{tier}'. Use: core, recall, archival")
            return

        slug = self._slugify(name)
        for directory in [self.live_notes_dir, self.entities_dir]:
            filepath = directory / f"{slug}.md"
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8", errors="replace")
                # Update or add tier
                if "Tier:" in content:
                    content = re.sub(r'\*\*Tier: \w+', f'**Tier: {tier}', content, count=1)
                else:
                    # Add tier after title
                    content = content.replace("\n\n> ", f"\n\n**Tier: {tier}**\n\n> ", 1)
                    if "**Tier:" not in content:
                        lines = content.split("\n", 2)
                        lines.insert(1, f"\n**Tier: {tier}**")
                        content = "\n".join(lines)
                filepath.write_text(content, encoding="utf-8")
                print(f"✅ Promoted '{name}' → {tier}")
                return

        print(f"⚠️ Entity '{name}' not found")

    # ── Diff ──────────────────────────────────────────────────
    def diff(self) -> None:
        """Show changes since last Dream Cycle."""
        meta_dir = self.base_dir / ".memkraft"
        ts_file = meta_dir / "last-dream-timestamp"

        if ts_file.exists():
            try:
                since = float(ts_file.read_text(encoding="utf-8", errors="replace").strip())
            except ValueError:
                since = 0.0
        else:
            since = 0.0

        changes = []
        for md in self._all_md_files():
            try:
                mtime = md.stat().st_mtime
            except OSError:
                continue
            if mtime > since:
                # Use st_birthtime on macOS, st_ctime on Linux (neither is perfect)
                try:
                    birth = md.stat().st_birthtime  # macOS only
                    change_type = "created" if birth > since else "modified"
                except AttributeError:
                    change_type = "changed"  # Linux: can't reliably distinguish
                rel_path = md.relative_to(self.base_dir)
                changes.append((change_type, str(rel_path), datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")))

        if not changes:
            print("No changes since last Dream Cycle.")
        else:
            print(f"Changes since last Dream Cycle ({len(changes)}):")
            for ctype, path, mtime in sorted(changes, key=lambda x: x[2], reverse=True):
                icon = "🆕" if ctype == "created" else "✏️"
                print(f"  {icon} {ctype}: {path} ({mtime})")

    # ── Search (Fuzzy) ────────────────────────────────────────
    def search(self, query: str, fuzzy: bool = False) -> List[Dict[str, Any]]:
        """Search memory with hybrid exact/token matching and optional fuzzy matching."""
        if not query or not query.strip():
            return []

        results = []
        query_lower = query.lower()
        query_tokens = self._search_tokens(query_lower)

        # Compute IDF (Inverse Document Frequency) for BM25-style scoring
        all_files = list(self._all_md_files())
        doc_count = max(len(all_files), 1)
        token_doc_freq = {}  # How many docs contain each token
        for md in all_files:
            try:
                doc_text = md.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            doc_tokens = set(self._search_tokens(doc_text))
            for t in doc_tokens:
                token_doc_freq[t] = token_doc_freq.get(t, 0) + 1

        for md in all_files:
            try:
                content = self._safe_read(md)
            except OSError:
                continue
            content_lower = content.lower()
            rel_path = md.relative_to(self.base_dir)
            filename_lower = md.stem.lower().replace("-", " ")

            lines = content_lower.split("\n")
            lines_orig = content.split("\n")
            exact_score = 0.0
            token_score = 0.0
            fuzzy_score = 0.0
            phrase_bonus = 0.0
            heading_bonus = 0.0
            recency_bonus = 0.0
            best_snippet = ""

            if query_lower in content_lower:
                exact_score = 1.0
                for idx, line in enumerate(lines):
                    if query_lower in line:
                        start = max(0, idx - 3)
                        end = min(len(lines), idx + 4)
                        best_snippet = " | ".join(l.strip() for l in lines_orig[start:end] if l.strip())[:200]
                        break

            if query_lower in filename_lower:
                exact_score = max(exact_score, 0.8)
                if not best_snippet:
                    best_snippet = md.stem

            if query_tokens:
                content_tokens = set(self._search_tokens(content_lower))
                filename_tokens = set(self._search_tokens(filename_lower))
                # IDF-weighted token scoring: rare tokens count more
                idf_weights = []
                for t in query_tokens:
                    df = token_doc_freq.get(t, 1)
                    idf = max(0.1, (doc_count - df + 0.5) / (df + 0.5))  # BM25 IDF
                    idf_weights.append(idf)
                total_idf = sum(idf_weights) or 1.0
                matched_weight = sum(w for t, w in zip(query_tokens, idf_weights) if t in content_tokens or t in filename_tokens)
                token_score = matched_weight / total_idf
                if token_score and not best_snippet:
                    best_snippet = self._best_token_snippet(query_tokens, lines, lines_orig)

                # Phrase matching: consecutive bigrams get a bonus
                if len(query_tokens) >= 2:
                    bigram_hits = 0
                    bigram_total = len(query_tokens) - 1
                    for i in range(bigram_total):
                        bigram = f"{query_tokens[i]} {query_tokens[i+1]}"
                        if bigram in content_lower:
                            bigram_hits += 1
                    if bigram_total > 0:
                        phrase_bonus = 0.15 * (bigram_hits / bigram_total)

                # Heading match bonus: query in # heading lines
                for line in lines:
                    if line.startswith("#") and query_lower in line:
                        heading_bonus = 0.1
                        break

            # Date-aware recency bonus
            dates = re.findall(r'\*\*(\d{4}-\d{2}-\d{2})\*\*', content)
            if dates:
                try:
                    latest = max(dates)
                    days_old = (datetime.now() - datetime.strptime(latest, "%Y-%m-%d")).days
                    if days_old < 7:
                        recency_bonus = 0.05
                    elif days_old < 30:
                        recency_bonus = 0.02
                except ValueError:
                    pass

            if fuzzy:
                best_fuzzy_snippet = ""
                for idx, line in enumerate(lines):
                    score = SequenceMatcher(None, query_lower, line.strip()).ratio()
                    if score > fuzzy_score:
                        fuzzy_score = score
                        start = max(0, idx - 3)
                        end = min(len(lines), idx + 4)
                        best_fuzzy_snippet = " | ".join(l.strip() for l in lines_orig[start:end] if l.strip())[:200]
                name_score = SequenceMatcher(None, query_lower, filename_lower).ratio()
                if name_score > fuzzy_score:
                    fuzzy_score = name_score
                    best_fuzzy_snippet = md.stem
                if fuzzy_score >= 0.3 and not best_snippet:
                    best_snippet = best_fuzzy_snippet

            # Composite scoring with phrase, heading, and recency bonuses
            if exact_score:
                final_score = max(exact_score, min(1.0, (exact_score * 0.65) + (token_score * 0.3)))
            else:
                final_score = min(1.0, (token_score * 0.6) + (fuzzy_score * 0.4))
            final_score = min(1.0, final_score + phrase_bonus + heading_bonus + recency_bonus)
            if exact_score or token_score or (fuzzy and fuzzy_score >= 0.3):
                results.append({"file": str(rel_path), "score": round(final_score, 2), "match": md.stem, "snippet": best_snippet})

        results.sort(key=lambda x: x["score"], reverse=True)

        if not results:
            print(f"No results for '{query}'.")
        else:
            for r in results[:20]:
                snippet_display = f"\n     {r['snippet'][:100]}" if r.get('snippet') else ""
                print(f"  [{r['score']:.2f}] {r['file']}{snippet_display}")
        return results

    # ── Links (Backlinks) ─────────────────────────────────────
    def links(self, name: str) -> None:
        """Show all backlinks to an entity."""
        slug = self._slugify(name)
        targets = [f"[[{name}]]", f"[[{slug}]]"]
        backlinks = []

        for md in self._all_md_files():
            try:
                content = self._safe_read(md)
            except OSError:
                continue
            for target in targets:
                if target in content:
                    rel_path = md.relative_to(self.base_dir)
                    # Extract context around the link
                    idx = content.find(target)
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(target) + 40)
                    context = content[start:end].replace("\n", " ").strip()
                    backlinks.append({"file": str(rel_path), "context": context})
                    break

        if not backlinks:
            print(f"No backlinks to '{name}'.")
        else:
            print(f"Backlinks to '{name}' ({len(backlinks)}):")
            for bl in backlinks:
                print(f"  📎 {bl['file']}")
                print(f"     ...{bl['context']}...")

    # ── Query (Progressive Disclosure) ────────────────────────
    def query(self, query: str = "", level: int = 1, recent: int = 0,
              tag: str = "", date: str = ""):
        """Progressive disclosure query — 3 levels of token efficiency."""
        files = self._gather_memory_files(recent=recent, tag=tag, date=date)

        # Pre-read and filter in single pass to avoid double I/O
        file_contents = {}
        for f in files:
            try:
                c = f.read_text(encoding="utf-8", errors="replace")
                file_contents[f] = c
            except Exception:
                continue

        if query:
            file_contents = {f: c for f, c in file_contents.items() if query.lower() in c.lower() or query.lower() in f.name.lower()}

        if not file_contents:
            print("No matching files found.")
            return

        for md, content in file_contents.items():
            rel = md.relative_to(self.base_dir)

            if level == 1:
                # Level 1: Index — date, first-line summary, tags (~50-100 tokens)
                first_line = self._first_meaningful_line(content)
                tags = self._extract_tags(content)
                try:
                    mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    mtime = "unknown"
                tag_str = f" [{tags}]" if tags else ""
                print(f"  {mtime} {rel}{tag_str}")
                print(f"    {first_line[:100]}")

            elif level == 2:
                # Level 2: Section headers + first line of each section
                print(f"\n📄 {rel}")
                sections = re.split(r'^(#{1,3} )', content, flags=re.MULTILINE)
                for i in range(1, len(sections), 2):
                    header = sections[i] + sections[i+1].split('\n')[0] if i+1 < len(sections) else ""
                    body = sections[i+1] if i+1 < len(sections) else ""
                    first_body = [l.strip() for l in body.split('\n') if l.strip() and not l.startswith('#')]
                    first_body = first_body[0][:80] if first_body else ""
                    print(f"  {header.strip()}")
                    if first_body:
                        print(f"    {first_body}")

            elif level == 3:
                # Level 3: Full file content
                print(f"\n{'='*60}")
                print(f"📄 {rel}")
                print(f"{'='*60}")
                print(content)

    # ── Session Event Logging ───────────────────────────────────
    def log_event(self, event: str, tags: str = "", importance: str = "normal",
                   entity: str = "", task: str = "", decision: str = ""):
        """Log a structured event to sessions JSONL."""
        sessions_dir = self.base_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        entry = {
            "ts": now.isoformat(),
            "event": event,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "importance": importance,
            "entity": entity,
            "task": task,
            "decision": decision,
        }
        filepath = sessions_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"📝 Logged: {event[:60]}")

    def log_read(self, date: str = None):
        """Read session events from JSONL."""
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        filepath = self.base_dir / "sessions" / f"{target_date}.jsonl"
        if not filepath.exists():
            print(f"No events for {target_date}.")
            return
        events = []
        for line in filepath.read_text(encoding="utf-8", errors="replace").strip().split("\n"):
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not events:
            print(f"No events for {target_date}.")
            return
        print(f"📋 Session events for {target_date} ({len(events)} events):")
        for e in events:
            imp = "🔴" if e.get("importance") == "high" else "🟡" if e.get("importance") == "medium" else "⚪"
            tags_str = f" [{','.join(e.get('tags', []))}]" if e.get('tags') else ""
            print(f"  {imp} {e['ts'][11:19]} {e['event'][:80]}{tags_str}")

    # ── Daily Retrospective ─────────────────────────────────────
    def retro(self, dry_run: bool = False):
        """Generate daily retrospective — Well / Bad / Next."""
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"🔄 Daily Retrospective — {today}")

        # Ensure daily note exists (skip in dry-run to keep read-only)
        if not dry_run:
            self.ensure_daily_note()

        # Collect session events
        events = []
        event_file = self.base_dir / "sessions" / f"{today}.jsonl"
        if event_file.exists():
            for line in event_file.read_text(encoding="utf-8", errors="replace").strip().split("\n"):
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Collect inbox items
        inbox_items = []
        if self.inbox_dir.exists():
            for md in self.inbox_dir.glob("*.md"):
                if not md.name.startswith("_"):
                    inbox_items.append(md.name)

        # Collect today's changes
        meta_dir = self.base_dir / ".memkraft"
        ts_file = meta_dir / "last-dream-timestamp"
        try:
            since = float(ts_file.read_text(encoding="utf-8", errors="replace").strip()) if ts_file.exists() else 0.0
        except ValueError:
            since = 0.0
        changed_files = []
        for md in self._all_md_files():
            try:
                if md.stat().st_mtime > since:
                    changed_files.append(str(md.relative_to(self.base_dir)))
            except OSError:
                continue

        # Build retrospective
        well = []
        bad = []
        next_actions = []
        entities_touched = set()
        decisions_made = []

        for e in events:
            if e.get("entity"):
                entities_touched.add(e["entity"])
            if e.get("decision"):
                decisions_made.append(e["decision"])
            if "fail" in e["event"].lower() or "error" in e["event"].lower():
                bad.append(e["event"])
            elif e.get("tags") and "todo" in e.get("tags", []):
                next_actions.append(e["event"])
            else:
                well.append(e["event"])

        # Inbox overdue = bad
        if inbox_items:
            bad.append(f"{len(inbox_items)} inbox items unprocessed")

        prefix = "[DRY RUN] " if dry_run else ""
        print(f"\n{prefix}✅ Well (went well):")
        for w in well[:10] or ["(none)"]:
            print(f"  • {w}")

        print(f"\n{prefix}⚠️ Bad (issues):")
        for b in bad[:10] or ["(none)"]:
            print(f"  • {b}")

        print(f"\n{prefix}➡️ Next (action items):")
        for n in next_actions[:10] or ["(none)"]:
            print(f"  • {n}")

        if entities_touched:
            print(f"\n{prefix}👥 Entities touched: {', '.join(sorted(entities_touched)[:20])}")
        if decisions_made:
            print(f"{prefix}📌 Decisions made: {len(decisions_made)}")
        if changed_files:
            print(f"{prefix}📄 Files changed: {len(changed_files)}")

    # ── Health Check (Memory Health Assertions) ──────────
    def health_check(self) -> Dict[str, Any]:
        """Run memory health assertions — self-diagnostic for memory quality.

        Assertions:
        1. All entities have source attribution
        2. No orphan facts (entity-disconnected)
        3. No duplicate facts
        4. No inbox items older than 7 days
        5. No unresolved conflicts in CONFLICTS.md

        Returns dict with pass_rate (%), failed items list, and health_score.
        """
        assertions = []
        total_checks = 0
        passed_checks = 0

        # 1. All entities have source attribution
        assertion_1 = {"name": "source_attribution", "description": "All entities have source attribution", "passed": True, "failures": []}
        for directory in [self.entities_dir, self.live_notes_dir]:
            if directory.exists():
                for md in directory.glob("*.md"):
                    if md.name == "README.md":
                        continue
                    content = self._safe_read(md)
                    if "[Source:" not in content:
                        assertion_1["passed"] = False
                        assertion_1["failures"].append(str(md.relative_to(self.base_dir)))
        total_checks += 1
        if assertion_1["passed"]:
            passed_checks += 1
        assertions.append(assertion_1)

        # 2. No orphan facts (facts not linked to any entity)
        assertion_2 = {"name": "no_orphan_facts", "description": "No orphan facts (entity-disconnected)", "passed": True, "failures": []}
        entity_slugs = set()
        for directory in [self.entities_dir, self.live_notes_dir]:
            if directory.exists():
                for md in directory.glob("*.md"):
                    entity_slugs.add(md.stem)
        # Check fact-registry for facts not linked to any entity
        registry_path = self.base_dir / "fact-registry.md"
        if registry_path.exists():
            reg_content = self._safe_read(registry_path)
            for line in reg_content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- ") and len(stripped) > 5:
                    fact_text = stripped[2:].lower()
                    linked = any(slug.replace("-", " ") in fact_text or slug in fact_text for slug in entity_slugs)
                    if not linked and ":" in stripped:
                        # Entity-prefixed facts like "entity: fact" are linked
                        prefix = stripped[2:].split(":")[0].strip().lower()
                        linked = any(slug.replace("-", " ") == prefix or slug == prefix for slug in entity_slugs)
                    if not linked and len(entity_slugs) > 0:
                        assertion_2["passed"] = False
                        assertion_2["failures"].append(stripped[:80])
        total_checks += 1
        if assertion_2["passed"]:
            passed_checks += 1
        assertions.append(assertion_2)

        # 3. No duplicate facts
        assertion_3 = {"name": "no_duplicate_facts", "description": "No duplicate facts", "passed": True, "failures": []}
        all_facts_set: Dict[str, str] = {}  # fact_text -> file
        for md in self._all_md_files():
            content = self._safe_read(md)
            rel = str(md.relative_to(self.base_dir))
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- ") and len(stripped) > 15:
                    clean = re.sub(r'\[Source:.*?\]', '', stripped).strip()
                    clean = re.sub(r'^- \*\*\d{4}-\d{2}-\d{2}\*\* \| ', '- ', clean)
                    clean_lower = clean.lower()
                    if clean_lower in all_facts_set and all_facts_set[clean_lower] != rel:
                        assertion_3["passed"] = False
                        assertion_3["failures"].append(f"{clean[:60]} (in {all_facts_set[clean_lower]} and {rel})")
                    elif len(clean_lower) > 10:
                        all_facts_set[clean_lower] = rel
        total_checks += 1
        if assertion_3["passed"]:
            passed_checks += 1
        assertions.append(assertion_3)

        # 4. No inbox items older than 7 days
        assertion_4 = {"name": "inbox_freshness", "description": "No inbox items older than 7 days", "passed": True, "failures": []}
        if self.inbox_dir.exists():
            now_ts = datetime.now().timestamp()
            for md in self.inbox_dir.glob("*.md"):
                if md.name.startswith("_") or md.name == "README.md":
                    continue
                try:
                    age_days = (now_ts - md.stat().st_mtime) / 86400
                except OSError:
                    continue
                if age_days > 7:
                    assertion_4["passed"] = False
                    assertion_4["failures"].append(f"{md.name} ({age_days:.0f} days old)")
        total_checks += 1
        if assertion_4["passed"]:
            passed_checks += 1
        assertions.append(assertion_4)

        # 5. No unresolved conflicts in CONFLICTS.md
        assertion_5 = {"name": "no_unresolved_conflicts", "description": "No unresolved conflicts in CONFLICTS.md", "passed": True, "failures": []}
        conflicts_path = self.base_dir / "CONFLICTS.md"
        if conflicts_path.exists():
            cc = self._safe_read(conflicts_path)
            unresolved_count = cc.count("❌ unresolved")
            if unresolved_count > 0:
                assertion_5["passed"] = False
                assertion_5["failures"].append(f"{unresolved_count} unresolved conflict(s)")
        total_checks += 1
        if assertion_5["passed"]:
            passed_checks += 1
        assertions.append(assertion_5)

        # Compute health score
        pass_rate = round((passed_checks / total_checks) * 100, 1) if total_checks > 0 else 100.0
        health_score = "A" if pass_rate >= 80 else "B" if pass_rate >= 60 else "C" if pass_rate >= 40 else "D"

        result = {
            "pass_rate": pass_rate,
            "passed": passed_checks,
            "total": total_checks,
            "health_score": health_score,
            "assertions": assertions,
        }

        # Print report
        print(f"🏥 Memory Health Check")
        print(f"   Score: {health_score} ({pass_rate}% pass rate, {passed_checks}/{total_checks})")
        for a in assertions:
            icon = "✅" if a["passed"] else "❌"
            print(f"   {icon} {a['description']}")
            if not a["passed"]:
                for f in a["failures"][:5]:
                    print(f"      → {f}")
                if len(a["failures"]) > 5:
                    print(f"      ... and {len(a['failures']) - 5} more")

        return result

    # ── Ensure Daily Note ───────────────────────────────────────
    def ensure_daily_note(self):
        """Create today's daily note if missing (fallback safety)."""
        today = datetime.now().strftime("%Y-%m-%d")
        daily_path = self.base_dir / f"{today}.md"
        if not daily_path.exists():
            content = f"# Daily Note — {today}\n\n## Summary\n(Auto-created by MemKraft)\n\n## Events\n\n## Decisions\n\n## Notes\n"
            daily_path.write_text(content, encoding="utf-8")
            print(f"📝 Created daily note: {daily_path}")
        return daily_path

    # ── Decision Distillation ───────────────────────────────────
    def distill_decisions(self):
        """Scan for decision candidates from events and daily notes."""
        decision_kw_en = ["decided", "decision", "chose", "agreed", "approved", "rejected", "postponed"]
        decision_kw_kr = ["결정", "채택", "승인", "보류", "통일", "제한", "확정"]
        all_kw = decision_kw_en + decision_kw_kr

        candidates = []

        # Scan session events
        sessions_dir = self.base_dir / "sessions"
        if sessions_dir.exists():
            for jsonl in sessions_dir.glob("*.jsonl"):
                for line in jsonl.read_text(encoding="utf-8", errors="replace").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_text = entry.get("event", "")
                    if any(kw in event_text.lower() for kw in decision_kw_en) or any(kw in event_text for kw in decision_kw_kr):
                        candidates.append({"source": f"sessions/{jsonl.name}", "event": event_text, "importance": entry.get("importance", "normal")})

        # Scan decisions dir
        if self.decisions_dir.exists():
            for md in self.decisions_dir.glob("*.md"):
                content = self._safe_read(md)
                if any(kw in content.lower() for kw in decision_kw_en) or any(kw in content for kw in decision_kw_kr):
                    candidates.append({"source": f"decisions/{md.name}", "event": content[:100].replace('\n', ' '), "importance": "high"})

        # Scan daily notes (exclude template/system files)
        excluded = {"RESOLVER.md", "TEMPLATES.md", "open-loops.md", "fact-registry.md"}
        for md in self.base_dir.glob("*.md"):
            if md.name in excluded:
                continue
            content = self._safe_read(md)
            for line in content.split("\n"):
                line_lower = line.lower()
                if any(kw in line_lower for kw in decision_kw_en) or any(kw in line for kw in decision_kw_kr):
                    if line.strip() and not line.startswith("#"):
                        candidates.append({"source": str(md.relative_to(self.base_dir)), "event": line.strip(), "importance": "normal"})

        if not candidates:
            print("No decision candidates found.")
            return

        print(f"📋 Decision candidates ({len(candidates)}):")
        for c in candidates[:20]:
            print(f"  [{c['importance']}] {c['source']}: {c['event'][:80]}")

    # ── Open Loop Tracking ───────────────────────────────────────
    def open_loops(self, dry_run: bool = False):
        """Scan for unresolved/pending items across all memory files."""
        pending_kw = ["pending", "waiting", "대기", "필요", "블로커", "확인 필요",
                      "TODO", "FIXME", "[ ]", "⏳", "미해결", "미완료"]

        loops = []
        for md in self._all_md_files():
            content = self._safe_read(md)
            rel = str(md.relative_to(self.base_dir))
            try:
                mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                mtime = "unknown"
            for line in content.split("\n"):
                line_s = line.strip()
                if not line_s or line_s.startswith("#"):
                    continue
                if any(kw in line_s for kw in pending_kw):
                    loops.append({"file": rel, "line": line_s[:120], "date": mtime})

        if not loops:
            print("No open loops found. 🎉")
            return

        print(f"🔓 Open Loops ({len(loops)}):")
        for l in sorted(loops, key=lambda x: x["date"])[:30]:
            print(f"  [{l['date']}] {l['file']}: {l['line'][:80]}")

        if not dry_run:
            # Write open-loops.md hub
            hub = self.base_dir / "open-loops.md"
            content = "# Open Loops\n\nAuto-generated by MemKraft\n\n"
            for l in sorted(loops, key=lambda x: x["date"]):
                content += f"- [{l['date']}] {l['file']}: {l['line'][:100]}\n"
            hub.write_text(content, encoding="utf-8")
            print(f"\n💾 Updated: {hub}")

    # ── Memory Index ─────────────────────────────────────────────
    def build_index(self):
        """Build .memkraft/index.json for progressive disclosure."""
        meta_dir = self.base_dir / ".memkraft"
        meta_dir.mkdir(parents=True, exist_ok=True)
        index = {}

        for md in self._all_md_files():
            rel = str(md.relative_to(self.base_dir))
            # Skip if already indexed (deduplicate)
            if rel in index:
                continue
            content = self._safe_read(md)
            summary = self._first_meaningful_line(content)
            tags = self._extract_tags(content)
            sections = [l.strip() for l in content.split("\n") if l.startswith("#")]
            try:
                mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
                size = md.stat().st_size
            except OSError:
                mtime = "unknown"
                size = 0
            index[rel] = {
                "date": mtime,
                "summary": summary[:200],
                "tags": tags,
                "sections": sections[:20],
                "size": size,
            }

        # Write index
        index_path = meta_dir / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        print(f"📇 Index built: {len(index)} files → {index_path.relative_to(self.base_dir)}")

    # ── Wiki Link Suggestion ─────────────────────────────────────
    def suggest_links(self):
        """Suggest [[wiki-links]] that should be added based on entity names."""
        # Get existing entity slugs
        entity_slugs = set()
        if self.entities_dir.exists():
            for md in self.entities_dir.glob("*.md"):
                entity_slugs.add(md.stem)
        if self.live_notes_dir.exists():
            for md in self.live_notes_dir.glob("*.md"):
                entity_slugs.add(md.stem)

        if not entity_slugs:
            print("No entities to suggest links for.")
            return

        suggestions = []
        for md in self._all_md_files():
            content = self._safe_read(md)
            rel = str(md.relative_to(self.base_dir))
            for slug in entity_slugs:
                if md.stem == slug:
                    continue  # Don't suggest self-links
                # Check if slug appears in text but not as [[slug]]
                pattern = r'\b' + re.escape(slug.replace("-", " ")) + r'\b'
                # Also check hyphenated form
                pattern_hyphen = re.escape(slug)
                for p in [pattern, pattern_hyphen]:
                    for match in re.finditer(p, content, re.IGNORECASE):
                        start = max(0, match.start() - 2)
                        if content[start:start+2] == "[[":
                            continue  # Already a link
                        context_start = max(0, match.start() - 20)
                        context_end = min(len(content), match.end() + 20)
                        context = content[context_start:context_end].replace("\n", " ").strip()
                        suggestions.append({"file": rel, "slug": slug, "context": context[:80]})
                        break  # One suggestion per slug per file
                    else:
                        continue
                    break  # Found one, skip to next slug

        if not suggestions:
            print("No link suggestions found.")
            return

        print(f"🔗 Link suggestions ({len(suggestions)}):")
        for s in suggestions[:20]:
            print(f"  {s['file']}: add [[{s['slug']}]] — \"{s['context']}\"")

    # ── Fact Registry ───────────────────────────────────────────
    def extract_facts_registry(self, text: str = ""):
        """Extract numeric/date facts and route to fact-registry.md."""
        # If no text provided, scan recent files
        if not text:
            texts = []
            for md in self._all_md_files():
                texts.append(self._safe_read(md))
            text = " ".join(texts)

        facts = []
        # Currency: $N, ₩N, €N
        for m in re.finditer(r'[\$₩€]\s?[\d,.]+(?:\s*(?:million|billion|trillion|만|억|조|M|B|K))?\b', text, re.IGNORECASE):
            facts.append(m.group())
        # Percentages
        for m in re.finditer(r'\d+(?:\.\d+)?%', text):
            facts.append(m.group())
        # Dates
        for m in re.finditer(r'\d{4}-\d{2}-\d{2}', text):
            # Skip if it looks like metadata ([Source: ..., Last Update: ..., - **YYYY-MM-DD**)
            start = max(0, m.start() - 20)
            prefix = text[start:m.start()].lower()
            if "source" in prefix or "update" in prefix or "started" in prefix or "**" in prefix:
                continue
            facts.append(m.group())
        # Quantities: N items/users/employees/members
        for m in re.finditer(r'\d+(?:,\d+)*(?:\s+(?:items|users|employees|members|people|명|개|건|팀))', text, re.IGNORECASE):
            facts.append(m.group().strip())

        if not facts:
            print("No facts extracted.")
            return

        facts = list(dict.fromkeys(facts))  # Deduplicate preserving order
        print(f"📊 Facts extracted ({len(facts)}):")
        for f in facts[:30]:
            print(f"  • {f}")

        # Write to fact-registry.md (deduplicate against existing)
        registry = self.base_dir / "fact-registry.md"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = registry.read_text(encoding="utf-8", errors="replace") if registry.exists() else "# Fact Registry\n\nCross-domain index of concrete data points.\n\n"
        # Skip facts that already exist in the registry
        existing_facts = set(re.findall(r'^- (.+)$', existing, re.MULTILINE))
        new_facts = [f for f in facts if f not in existing_facts]
        if new_facts:
            existing += f"\n## {now}\n"
            for f in new_facts:
                existing += f"- {f}\n"
            registry.write_text(existing, encoding="utf-8")
            print(f"\n💾 Updated: {registry.relative_to(self.base_dir.parent)} ({len(new_facts)} new facts)")
        else:
            print("\n✅ No new facts to add (all already in registry)")

    # ── Memory Decay (with type-aware differential curves) ────
    def decay(self, days: int = 90, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Downgrade stale facts older than N days. Reduces noise in search results.

        Supports memory-type-aware differential decay curves:
        identity memories decay slowly (effective threshold = days / 0.1 = 10x longer),
        routine memories decay fast (effective threshold ≈ days).
        """
        base_threshold = timedelta(days=days)
        results = []

        for md in self._all_md_files():
            content = self._safe_read(md)
            if not content:
                continue

            # Determine memory type for differential decay
            memory_type = self.classify_memory_type(content)
            decay_mult = self.get_decay_multiplier(memory_type)
            # Effective threshold: identity (0.1) → days/0.1 = 10x, routine (0.9) → days/0.9 ≈ 1x
            effective_days = int(days / max(decay_mult, 0.05))
            threshold = datetime.now() - timedelta(days=effective_days)

            modified = False
            lines = content.split("\n")
            new_lines = []

            for line in lines:
                # Find timeline entries with dates
                date_match = re.search(r'\*\*(\d{4}-\d{2}-\d{2})\*\*', line)
                if date_match:
                    entry_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                    if entry_date < threshold and "⏳" not in line:
                        # Mark as stale (don't delete, just flag)
                        if dry_run:
                            results.append({"file": str(md.relative_to(self.base_dir)), "line": line.strip()[:80], "age_days": (datetime.now() - entry_date).days, "memory_type": memory_type, "effective_days": effective_days})
                        else:
                            line = line.replace("- **", "- ⏳ ", 1)
                            modified = True
                new_lines.append(line)

            if modified and not dry_run:
                md.write_text("\n".join(new_lines), encoding="utf-8")

        if results:
            action = "would flag" if dry_run else "flagged"
            print(f"📉 Decay: {action} {len(results)} entries older than {days} days (type-aware)")
            for r in results[:10]:
                print(f"  [{r['age_days']}d, {r['memory_type']}] {r['file']}: {r['line']}")
        else:
            print(f"📉 Decay: no entries older than {days} days")
        return results

    # ── Fact Dedup ──────────────────────────────────────────────
    def dedup(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Merge duplicate/similar facts across entity pages."""
        all_facts = []  # [(entity, fact_text, file_path)]

        for md in self._all_md_files():
            content = self._safe_read(md)
            if not content:
                continue
            # Extract bullet-point facts from Key Points and Timeline
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- ") and len(stripped) > 10:
                    # Strip source tags for comparison
                    clean = re.sub(r'\[Source:.*?\]', '', stripped).strip()
                    if clean.startswith("- "):
                        all_facts.append((md.stem, clean, str(md.relative_to(self.base_dir))))

        # Find similar pairs
        duplicates = []
        seen = set()
        for i, (entity1, fact1, path1) in enumerate(all_facts):
            for j, (entity2, fact2, path2) in enumerate(all_facts):
                if i >= j:
                    continue
                pair_key = (min(path1, path2), max(path1, path2), min(fact1, fact2))
                if pair_key in seen:
                    continue
                similarity = SequenceMatcher(None, fact1, fact2).ratio()
                if similarity >= 0.85:
                    duplicates.append({"fact1": fact1[:60], "path1": path1, "fact2": fact2[:60], "path2": path2, "similarity": round(similarity, 2)})
                    seen.add(pair_key)

        if duplicates:
            action = "would merge" if dry_run else "detected"
            print(f"🔗 Dedup: {action} {len(duplicates)} duplicate pairs")
            for d in duplicates[:10]:
                print(f"  [{d['similarity']:.0%}] {d['path1']}: {d['fact1']}")
                print(f"        {d['path2']}: {d['fact2']}")
        else:
            print("🔗 Dedup: no duplicates found")
        return duplicates

    # ── Auto-Summarize ──────────────────────────────────────────
    def summarize(self, name: str = None, max_length: int = 500) -> List[Dict[str, str]]:
        """Generate a compact summary of an entity's current state."""
        if name:
            targets = [name]
        else:
            # Summarize all bloated pages
            targets = []
            for md in self._all_md_files():
                try:
                    if md.stat().st_size > max_length * 3:
                        targets.append(md.stem.replace("-", " "))
                except OSError:
                    continue

        results = []
        for target in targets:
            slug = self._slugify(target)
            filepath = self.live_notes_dir / f"{slug}.md"
            if not filepath.exists():
                filepath = self.entities_dir / f"{slug}.md"
            if not filepath.exists():
                continue

            content = self._safe_read(filepath)
            if not content:
                continue

            # Extract key sections
            summary_parts = []
            for section in ["## Current State", "## 현재 상태", "## Key Points", "## 핵심 포인트"]:
                if section in content:
                    section_text = content.split(section)[1].split("\n## ")[0]
                    bullets = [l.strip() for l in section_text.split("\n") if l.strip().startswith("- ")][:5]
                    if bullets:
                        summary_parts.extend(bullets)

            # Extract recent timeline (last 3 entries)
            for marker in ["## Timeline", "## 타임라인"]:
                if marker in content:
                    section = content.split(marker)[1].split("\n## ")[0]
                    entries = [l.strip() for l in section.split("\n") if l.strip().startswith("- **")][:3]
                    if entries:
                        summary_parts.append("\nRecent:")
                        summary_parts.extend(entries)
                    break

            if summary_parts:
                summary = "\n".join(summary_parts)[:max_length]
                # Write summary as comment in the file
                summary_marker = "<!-- AUTO-SUMMARY -->"
                if summary_marker not in content:
                    if not name:  # Only auto-write for bulk summarize
                        new_content = content.replace("# ", f"# ", 1)
                        # Insert after first heading
                        heading_end = content.find("\n", content.find("# "))
                        if heading_end > 0:
                            new_content = content[:heading_end+1] + f"\n{summary_marker}\n{summary}\n<!-- END-AUTO-SUMMARY -->\n" + content[heading_end+1:]
                            filepath.write_text(new_content, encoding="utf-8")
                results.append({"entity": target, "summary": summary[:100] + "..." if len(summary) > 100 else summary})
                print(f"📝 Summarized: {target} ({len(summary)} chars)")
            else:
                print(f"📝 Skipped: {target} (nothing to summarize)")

        if not results:
            print("📝 Summarize: no entities to summarize")
        return results

    # ── Memory Type Classification ─────────────────────────────
    # Memory type → decay multiplier (lower = slower decay)
    MEMORY_TYPE_DECAY: Dict[str, float] = {
        "identity": 0.1,     # Who I am — decays very slowly
        "belief": 0.2,       # Core beliefs and values
        "preference": 0.3,   # Preferences and opinions
        "relationship": 0.3, # Relationship knowledge
        "skill": 0.4,        # Learned procedures
        "episodic": 0.6,     # Specific events
        "routine": 0.9,      # Daily routines — decays fast
        "transient": 1.0,    # Temporary info — decays fastest
        "default": 0.5,      # Unclassified
    }

    def classify_memory_type(self, text: str) -> str:
        """Classify text into a memory type for differential decay."""
        text_lower = text.lower()
        identity_kw = ["i am", "my name", "i'm", "내 이름", "나는", "저는", "identity", "core"]
        belief_kw = ["believe", "value", "principle", "conviction", "philosophy", "신념", "가치", "원칙"]
        preference_kw = ["prefer", "like", "favorite", "hate", "dislike", "좋아", "싫어", "선호"]
        relationship_kw = ["friend", "colleague", "partner", "team", "동료", "친구", "팀"]
        routine_kw = ["daily", "routine", "every day", "always", "usually", "매일", "항상", "일상"]
        transient_kw = ["today", "tonight", "right now", "currently", "오늘", "지금", "현재"]
        skill_kw = ["how to", "procedure", "workflow", "방법", "절차", "워크플로우"]

        for kw in identity_kw:
            if kw in text_lower:
                return "identity"
        for kw in belief_kw:
            if kw in text_lower:
                return "belief"
        for kw in preference_kw:
            if kw in text_lower:
                return "preference"
        for kw in relationship_kw:
            if kw in text_lower:
                return "relationship"
        for kw in skill_kw:
            if kw in text_lower:
                return "skill"
        for kw in routine_kw:
            if kw in text_lower:
                return "routine"
        for kw in transient_kw:
            if kw in text_lower:
                return "transient"
        return "default"

    def get_decay_multiplier(self, memory_type: str) -> float:
        """Return the decay multiplier for a given memory type."""
        return self.MEMORY_TYPE_DECAY.get(memory_type, self.MEMORY_TYPE_DECAY["default"])

    # ── Goal-Weighted Reconstruction ──────────────────────────
    def _goal_weighted_rerank(self, results: List[Dict[str, Any]], context: str) -> List[Dict[str, Any]]:
        """Re-rank search results based on goal context (Conway SMS).

        Same query with different context produces different rankings.
        Uses difflib similarity between context and file content + memory-type decay.
        """
        if not context or not results:
            return results

        context_lower = context.lower()
        context_tokens = set(self._search_tokens(context_lower))

        for r in results:
            md_path = self.base_dir / r["file"]
            content = self._safe_read(md_path)
            if not content:
                continue

            content_lower = content.lower()

            # 1. Context relevance: how well does this result match the goal?
            context_score = 0.0
            if context_lower in content_lower:
                context_score = 0.3
            else:
                content_tokens = set(self._search_tokens(content_lower))
                overlap = len(context_tokens & content_tokens)
                if context_tokens:
                    context_score = 0.2 * (overlap / len(context_tokens))

            # 2. Semantic similarity via difflib
            # Compare context against most relevant section
            best_section_sim = 0.0
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped and len(stripped) > 10:
                    sim = SequenceMatcher(None, context_lower, stripped.lower()).ratio()
                    if sim > best_section_sim:
                        best_section_sim = sim
            context_score += best_section_sim * 0.15

            # 3. Memory-type decay adjustment
            memory_type = self.classify_memory_type(content)
            decay_mult = self.get_decay_multiplier(memory_type)
            # For identity/belief memories, boost score; for routine/transient, penalize
            type_bonus = 0.05 * (1.0 - decay_mult)  # identity → +0.045, routine → +0.005

            # 4. Tier-context alignment
            tier_bonus = 0.0
            if "Tier: core" in content:
                tier_bonus = 0.03

            # 5. Confidence weighting: verified facts boost score
            confidence_bonus = self._compute_confidence_bonus(content)

            # 6. Applicability conditions: boost if current context matches When: conditions
            applicability_bonus = self._compute_applicability_bonus(content, context)

            r["score"] = round(min(1.0, r.get("score", 0) + context_score + type_bonus + tier_bonus + confidence_bonus + applicability_bonus), 2)
            r["memory_type"] = memory_type

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _compute_applicability_bonus(self, content: str, context: str) -> float:
        """Compute a score bonus based on applicability conditions matching context.

        Lines with 'When: X' get boosted if X keywords match context.
        Lines with 'When NOT: X' get penalized if X keywords match context.
        """
        if not context:
            return 0.0

        context_lower = context.lower()
        context_tokens = set(self._search_tokens(context_lower))
        bonus = 0.0
        match_count = 0

        for line in content.split("\n"):
            when_match = re.search(r'When:\s*([^|\]]+)', line)
            when_not_match = re.search(r'When NOT:\s*([^|\]]+)', line)

            if when_match:
                condition = when_match.group(1).strip().lower()
                cond_tokens = set(self._search_tokens(condition))
                overlap = len(context_tokens & cond_tokens)
                if overlap > 0 and cond_tokens:
                    bonus += 0.03 * (overlap / len(cond_tokens))
                    match_count += 1

            if when_not_match:
                condition = when_not_match.group(1).strip().lower()
                cond_tokens = set(self._search_tokens(condition))
                overlap = len(context_tokens & cond_tokens)
                if overlap > 0 and cond_tokens:
                    bonus -= 0.02 * (overlap / len(cond_tokens))
                    match_count += 1

        return round(min(0.1, bonus), 3)

    def _parse_applicability(self, text: str) -> Dict[str, List[str]]:
        """Parse applicability conditions from text.

        Returns dict with 'when' and 'when_not' lists.
        """
        result: Dict[str, List[str]] = {"when": [], "when_not": []}
        for match in re.finditer(r'When:\s*([^|\]]+)', text):
            result["when"].append(match.group(1).strip())
        for match in re.finditer(r'When NOT:\s*([^|\]]+)', text):
            result["when_not"].append(match.group(1).strip())
        return result

    def _compute_confidence_bonus(self, content: str) -> float:
        """Compute a score bonus based on the confidence levels of facts in content.

        Files with more verified facts get a higher bonus.
        """
        verified_count = content.count("Confidence: verified")
        experimental_count = content.count("Confidence: experimental")
        hypothesis_count = content.count("Confidence: hypothesis")
        total = verified_count + experimental_count + hypothesis_count
        if total == 0:
            return 0.0
        weighted = (verified_count * 1.0 + experimental_count * 0.7 + hypothesis_count * 0.4) / total
        return round(0.05 * weighted, 3)

    def _extract_fact_confidence(self, line: str) -> str:
        """Extract confidence level from a fact line."""
        m = re.search(r'Confidence:\s*(verified|experimental|hypothesis)', line)
        return m.group(1) if m else ""

    # ── Agentic Search ──────────────────────────────────────────
    def agentic_search(self, query: str, max_hops: int = 2, json_output: bool = False,
                       context: str = "", file_back: bool = False) -> List[Dict[str, Any]]:
        """Multi-step search: decompose query → search → traverse links → goal-weighted re-rank → check sufficiency.

        Args:
            query: Search query string.
            max_hops: Maximum link traversal hops (default: 2).
            json_output: Return JSON-serializable output.
            context: Goal context for reconstructive re-ranking. Same query with
                     different context produces different result rankings (Conway SMS).
            file_back: If True, file search results back into entity timelines
                       (Query-to-Memory Feedback Loop — compound interest for memory).
        """
        # Step 1: Query Decomposition
        sub_queries = self._decompose_query(query)

        # Step 2: Initial search for each sub-query
        all_results = {}
        for sq in sub_queries:
            results = self.search(sq, fuzzy=True)
            for r in results:
                if r["file"] not in all_results or r["score"] > all_results[r["file"]]["score"]:
                    all_results[r["file"]] = r

        # Step 3: Multi-hop Traversal — follow backlinks
        visited = set(all_results.keys())
        hop_results = {}
        for _ in range(max_hops):
            new_files = []
            for filepath in list(all_results.keys()) + list(hop_results.keys()):
                md_path = self.base_dir / filepath
                if md_path.exists():
                    content = self._safe_read(md_path)
                    # Find [[wiki-links]] in content
                    linked = re.findall(r'\[\[([^\]]+)\]\]', content)
                    for link in linked:
                        link_slug = self._slugify(link)
                        for subdir in [self.entities_dir, self.live_notes_dir, self.decisions_dir]:
                            target = subdir / f"{link_slug}.md"
                            rel = str(target.relative_to(self.base_dir)) if target.exists() else None
                            if rel and rel not in visited:
                                new_files.append((rel, link))
                                visited.add(rel)
            if not new_files:
                break
            for rel_path, link_name in new_files:
                md_path = self.base_dir / rel_path
                content = self._safe_read(md_path)
                if content:
                    # Score relevance to original query
                    content_lower = content.lower()
                    query_lower = query.lower()
                    score = 0.0
                    for token in query_lower.split():
                        if token in content_lower:
                            score += 0.3
                    for sq in sub_queries:
                        if sq.lower() in content_lower:
                            score += 0.5
                    if score > 0:
                        hop_results[rel_path] = {"file": rel_path, "score": round(min(score, 1.0), 2), "match": Path(rel_path).stem, "snippet": content[:100].replace("\n", " "), "hop": True}

        # Merge initial + hop results
        merged = list(all_results.values()) + list(hop_results.values())

        # Step 4: Contextual Re-ranking (basic tier + recency)
        for r in merged:
            bonus = 0.0
            md_path = self.base_dir / r["file"]
            content = self._safe_read(md_path)
            if content:
                # Tier bonus
                if "Tier: core" in content:
                    bonus += 0.1
                elif "Tier: archival" in content:
                    bonus -= 0.05
                # Recency bonus
                dates = re.findall(r'\*\*(\d{4}-\d{2}-\d{2})\*\*', content)
                if dates:
                    try:
                        latest = max(dates)
                        days_old = (datetime.now() - datetime.strptime(latest, "%Y-%m-%d")).days
                        if days_old < 7:
                            bonus += 0.05
                    except ValueError:
                        pass
            r["score"] = round(min(1.0, r.get("score", 0) + bonus), 2)

        # Step 4b: Goal-Weighted Reconstructive Re-ranking (Conway SMS)
        if context:
            merged = self._goal_weighted_rerank(merged, context)
        else:
            merged.sort(key=lambda x: x["score"], reverse=True)

        # Step 5: Sufficiency Check — if top result < 0.5, expand search
        if merged and merged[0]["score"] < 0.5 and len(sub_queries) > 1:
            # Broader search with full query
            expanded = self.search(query, fuzzy=True)
            for r in expanded:
                if r["file"] not in all_results:
                    merged.append(r)
            merged.sort(key=lambda x: x["score"], reverse=True)

        # Output
        if not merged:
            print(f"🔍 Agentic search: no results for '{query}'")
        else:
            ctx_str = f", context='{context[:30]}...'" if context and len(context) > 30 else (f", context='{context}'" if context else "")
            print(f"🔍 Agentic search: {len(merged)} results for '{query}' (decomposed into {len(sub_queries)} queries, {max_hops} hops{ctx_str})")
            for r in merged[:10]:
                hop_marker = " ↳" if r.get("hop") else ""
                type_marker = f" [{r['memory_type']}]" if r.get("memory_type") else ""
                snippet = f"\n     {r['snippet'][:80]}" if r.get('snippet') else ""
                print(f"  [{r['score']:.2f}] {r['file']}{type_marker}{snippet}{hop_marker}")

        # Step 6: Query-to-Memory Feedback Loop (file_back)
        if file_back and merged:
            self._file_back_results(query, merged)

        if json_output:
            return merged
        return merged

    def _file_back_results(self, query: str, results: List[Dict[str, Any]]) -> None:
        """File search results back into entity timelines (feedback loop).

        Creates a compound interest effect: searching enriches memory,
        so future searches return richer results.
        """
        now = datetime.now().strftime("%Y-%m-%d")
        filed_count = 0
        for r in results[:5]:  # Top 5 results only to avoid noise
            md_path = self.base_dir / r["file"]
            if not md_path.exists():
                continue
            content = self._safe_read(md_path)
            snippet = r.get("snippet", "")[:120]
            if not snippet:
                continue

            feedback_entry = f"- **{now}** | [Filed back] Query: '{query[:60]}' — {snippet} [Source: agentic_search | Confidence: experimental]"

            # Append to timeline
            for marker in ["## Timeline (Full Record)\n\n", "## Timeline\n\n"]:
                if marker in content:
                    content = content.replace(marker, f"{marker}{feedback_entry}\n")
                    md_path.write_text(content, encoding="utf-8")
                    filed_count += 1
                    break

        if filed_count:
            print(f"📂 Filed back {filed_count} results into entity timelines")

    def _decompose_query(self, query: str) -> List[str]:
        """Decompose a complex query into sub-queries."""
        # Korean question patterns
        kr_patterns = [r'(.+?)이/가 누구', r'(.+?)의 (.+?)', r'(.+?)은/는 어디', r'(.+?)에 대해']
        for pat in kr_patterns:
            m = re.search(pat, query)
            if m:
                return [g.strip() for g in m.groups() if g.strip()] + [query]

        # English patterns: "X of Y", "who is X", "what is X"
        en_patterns = [
            r"who is (.+)", r"what is (.+)", r"tell me about (.+)",
            r"(.+?) of (.+)", r"(.+?)'s (.+)"
        ]
        for pat in en_patterns:
            m = re.search(pat, query, re.IGNORECASE)
            if m:
                return [g.strip() for g in m.groups() if g.strip()] + [query]

        # Fallback: split on common delimiters
        if len(query) > 20:
            parts = re.split(r'[,.]|\s+(?:and|or|but|그리고|또는)\s+', query)
            if len(parts) > 1:
                return [p.strip() for p in parts if len(p.strip()) > 2]

        return [query]

    # ── Brain-first Lookup ───────────────────────────────────────
    def lookup(self, query: str, json_output: bool = False,
               brain_first: bool = False, full: bool = False):
        """Brain-first lookup with search hierarchy and sufficiency threshold."""
        results = []
        search_dirs = [
            (self.entities_dir, "entity", "high"),
            (self.live_notes_dir, "live-note", "high"),
            (self.decisions_dir, "decision", "medium"),
            (self.base_dir / "meetings", "meeting", "medium"),
            (self.originals_dir, "original", "low"),
            (self.inbox_dir, "inbox", "low"),
            (self.tasks_dir, "task", "low"),
        ]

        high_count = 0
        seen_files = set()  # Deduplicate by filename
        for directory, source, relevance in search_dirs:
            # Brain-first: stop early if we have enough high-relevance results
            if brain_first and not full and high_count >= 2 and relevance != "high":
                break

            if not directory.exists():
                continue

            for md in directory.glob("*.md"):
                rel_path = str(md.relative_to(self.base_dir))
                if rel_path in seen_files:
                    continue  # Skip exact same file
                content = self._safe_read(md).lower()
                if query.lower() in content:
                    results.append({"source": source, "file": md.stem, "rel_path": rel_path, "relevance": relevance})
                    seen_files.add(rel_path)
                    if relevance == "high":
                        high_count += 1

        if json_output:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            if not results:
                print(f"No results for '{query}'. Consider web search as fallback.")
            else:
                for r in results:
                    print(f"  [{r['relevance']}] {r['source']}: {r['file']}")
                if brain_first and not full and high_count >= 2:
                    print(f"  (brain-first: stopped after {high_count} high-relevance results. Use --full for all.)")

    # ── Debug Hypothesis Tracking ─────────────────────────────
    # Inspired by Shen Huang's debug-hypothesis pattern (Karpathy auto-research):
    # 1. List hypotheses before code changes
    # 2. Max 5 lines per experiment
    # 3. Record all evidence to files (compaction-proof)
    # 4. After 2 failures → switch hypothesis
    # "Debugging is memory" — the reasoning chain matters as much as the fix.

    def start_debug(self, bug_description: str) -> Dict[str, Any]:
        """Start a new debug session.

        Creates a DEBUG-{timestamp}.md file and returns a bug_id.
        Follows the OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE flow.
        """
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        bug_id = f"DEBUG-{now.strftime('%Y%m%d-%H%M%S')}"
        filepath = self.debug_dir / f"{bug_id}.md"

        # Avoid collision if multiple sessions start in the same second
        counter = 1
        while filepath.exists():
            bug_id = f"DEBUG-{now.strftime('%Y%m%d-%H%M%S')}-{counter}"
            filepath = self.debug_dir / f"{bug_id}.md"
            counter += 1

        content = f"""# 🐛 {bug_id}

**Description:** {bug_description}
**Started:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Status:** OBSERVE
**Resolution:** (pending)

## Observation
{bug_description}

## Hypotheses
(Hypotheses will be logged here)

## Evidence Log
(Evidence will be recorded here)

## Conclusion
(Pending)

## Timeline
- **{now.strftime('%Y-%m-%d %H:%M')}** | Debug session started: {bug_description}
"""
        filepath.write_text(content, encoding="utf-8")
        print(f"🐛 Debug session started: {bug_id}")
        print(f"   Description: {bug_description}")
        print(f"   File: {filepath.relative_to(self.base_dir)}")
        return {"bug_id": bug_id, "file": str(filepath), "status": "OBSERVE"}

    def log_hypothesis(self, bug_id: str, hypothesis: str,
                       evidence: str = "",
                       status: str = "testing") -> Dict[str, Any]:
        """Log a hypothesis for a debug session.

        Args:
            bug_id: The debug session ID (e.g., DEBUG-20260413-120000)
            hypothesis: The hypothesis text
            evidence: Optional initial evidence
            status: testing | rejected | confirmed
        """
        if status not in self.HYPOTHESIS_STATUSES:
            print(f"❌ Invalid status '{status}'. Use: {', '.join(self.HYPOTHESIS_STATUSES)}")
            return {}

        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Count existing hypotheses to generate ID
        existing_count = content.count("### H")
        hypothesis_id = f"H{existing_count + 1}"

        # Check for "2 failures → switch" warning
        rejected_count = content.count("❌ REJECTED")
        auto_switch_warning = ""
        if rejected_count >= 2 and rejected_count % 2 == 0:
            auto_switch_warning = f"\n> ⚠️ **{rejected_count} hypotheses rejected** — Consider a fundamentally different approach\n"

        # Build hypothesis entry
        evidence_line = f"\n- Initial evidence: {evidence}" if evidence else ""
        hypothesis_entry = f"\n### {hypothesis_id}: {hypothesis}\n- **Status:** 🧪 {status.upper()}\n- **Created:** {now}{evidence_line}\n{auto_switch_warning}"

        # Insert into Hypotheses section
        content = content.replace(
            "## Evidence Log",
            f"{hypothesis_entry}\n## Evidence Log"
        )

        # Update session status to HYPOTHESIZE
        content = self._update_debug_status(content, "HYPOTHESIZE")

        # Add to timeline
        content = self._append_debug_timeline(content, f"Hypothesis {hypothesis_id} added: {hypothesis[:60]}")

        filepath.write_text(content, encoding="utf-8")
        print(f"💡 Hypothesis {hypothesis_id} logged for {bug_id}")
        print(f"   {hypothesis}")
        if auto_switch_warning:
            print(f"   ⚠️ {rejected_count} hypotheses rejected — consider switching approach")

        return {"bug_id": bug_id, "hypothesis_id": hypothesis_id,
                "hypothesis": hypothesis, "status": status}

    def get_hypotheses(self, bug_id: str) -> List[Dict[str, Any]]:
        """Get all hypotheses for a debug session."""
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return []

        content = self._safe_read(filepath)
        hypotheses = []

        # Parse ### HN: hypothesis text
        # Status line may be followed by optional "Rejected reason" before "Created"
        pattern = r'### (H\d+): (.+?)\n- \*\*Status:\*\* (.+?)\n(?:- \*\*Rejected reason:\*\* .+?\n)?- \*\*Created:\*\* (.+?)\n'
        for match in re.finditer(pattern, content):
            h_id = match.group(1)
            h_text = match.group(2).strip()
            status_raw = match.group(3).strip()
            created = match.group(4).strip()

            # Parse status (strip emoji)
            if "TESTING" in status_raw.upper():
                status = "testing"
            elif "REJECTED" in status_raw.upper():
                status = "rejected"
            elif "CONFIRMED" in status_raw.upper():
                status = "confirmed"
            else:
                status = "testing"

            hypotheses.append({
                "hypothesis_id": h_id,
                "hypothesis": h_text,
                "status": status,
                "created": created,
            })

        return hypotheses

    def reject_hypothesis(self, bug_id: str, hypothesis_id: str,
                          reason: str = "") -> Dict[str, Any]:
        """Reject a hypothesis with a reason.

        Rejected hypotheses are permanently preserved for future reference
        ("things we already tried and failed").
        """
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Find and update the hypothesis status
        pattern = rf'(### {re.escape(hypothesis_id)}: .+?\n- \*\*Status:\*\* )🧪 TESTING'
        match = re.search(pattern, content)
        if not match:
            print(f"❌ Hypothesis {hypothesis_id} not found or not in TESTING state")
            return {}

        reason_line = f"\n- **Rejected reason:** {reason}" if reason else ""
        content = content[:match.start(1)] + match.group(1) + f"❌ REJECTED ({now}){reason_line}" + content[match.end():]

        # Check total rejected count for auto-switch detection
        rejected_count = content.count("❌ REJECTED")
        if rejected_count >= 2 and rejected_count % 2 == 0:
            content = self._append_debug_timeline(
                content,
                f"⚠️ AUTO-SWITCH TRIGGER: {rejected_count} hypotheses rejected — consider fundamentally different approach"
            )

        content = self._append_debug_timeline(content, f"{hypothesis_id} rejected: {reason[:60]}")
        filepath.write_text(content, encoding="utf-8")

        print(f"❌ Hypothesis {hypothesis_id} rejected for {bug_id}")
        if reason:
            print(f"   Reason: {reason}")
        if rejected_count >= 2 and rejected_count % 2 == 0:
            print(f"   ⚠️ {rejected_count} rejected — switch hypothesis approach recommended")

        return {"bug_id": bug_id, "hypothesis_id": hypothesis_id,
                "status": "rejected", "reason": reason,
                "total_rejected": rejected_count}

    def confirm_hypothesis(self, bug_id: str, hypothesis_id: str) -> Dict[str, Any]:
        """Confirm a hypothesis and feed back into memory.

        Confirmed hypotheses generate automatic memory feedback
        for future agentic_search queries.
        """
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Find and update the hypothesis status
        pattern = rf'(### {re.escape(hypothesis_id)}: .+?\n- \*\*Status:\*\* )🧪 TESTING'
        match = re.search(pattern, content)
        if not match:
            print(f"❌ Hypothesis {hypothesis_id} not found or not in TESTING state")
            return {}

        content = content[:match.start(1)] + match.group(1) + f"✅ CONFIRMED ({now})" + content[match.end():]

        # Update session status to CONCLUDE
        content = self._update_debug_status(content, "EXPERIMENT")

        content = self._append_debug_timeline(content, f"{hypothesis_id} confirmed ✅")
        filepath.write_text(content, encoding="utf-8")

        # Extract hypothesis text for feedback
        h_pattern = rf'### {re.escape(hypothesis_id)}: (.+?)\n'
        h_match = re.search(h_pattern, content)
        hypothesis_text = h_match.group(1).strip() if h_match else hypothesis_id

        print(f"✅ Hypothesis {hypothesis_id} confirmed for {bug_id}")
        print(f"   {hypothesis_text}")

        return {"bug_id": bug_id, "hypothesis_id": hypothesis_id,
                "status": "confirmed", "hypothesis": hypothesis_text}

    def log_evidence(self, bug_id: str, hypothesis_id: str,
                     evidence_text: str,
                     result: str = "neutral") -> Dict[str, Any]:
        """Log evidence for a hypothesis.

        Args:
            bug_id: Debug session ID
            hypothesis_id: Hypothesis ID (e.g., H1)
            evidence_text: The evidence observation
            result: supports | contradicts | neutral
        """
        if result not in self.EVIDENCE_RESULTS:
            print(f"❌ Invalid result '{result}'. Use: {', '.join(self.EVIDENCE_RESULTS)}")
            return {}

        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        result_icon = {"supports": "✅", "contradicts": "❌", "neutral": "➖"}[result]

        evidence_entry = f"- **{now}** | [{hypothesis_id}] {result_icon} {result}: {evidence_text}\n"

        # Insert into Evidence Log section
        marker = "## Conclusion"
        if marker in content:
            content = content.replace(marker, f"{evidence_entry}\n{marker}")

        # Update session status to EXPERIMENT
        content = self._update_debug_status(content, "EXPERIMENT")

        filepath.write_text(content, encoding="utf-8")

        print(f"📝 Evidence logged for {bug_id}/{hypothesis_id}: {result_icon} {result}")
        return {"bug_id": bug_id, "hypothesis_id": hypothesis_id,
                "evidence": evidence_text, "result": result}

    def get_evidence(self, bug_id: str, hypothesis_id: str = "") -> List[Dict[str, Any]]:
        """Get evidence for a debug session, optionally filtered by hypothesis."""
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return []

        content = self._safe_read(filepath)
        evidence_list = []

        # Parse evidence entries from Evidence Log section
        evidence_section = ""
        if "## Evidence Log" in content and "## Conclusion" in content:
            evidence_section = content.split("## Evidence Log")[1].split("## Conclusion")[0]

        pattern = r'- \*\*(.+?)\*\* \| \[(H\d+)\] (.+?) (supports|contradicts|neutral): (.+)'
        for match in re.finditer(pattern, evidence_section):
            entry = {
                "timestamp": match.group(1).strip(),
                "hypothesis_id": match.group(2).strip(),
                "result": match.group(4).strip(),
                "evidence": match.group(5).strip(),
            }
            if not hypothesis_id or entry["hypothesis_id"] == hypothesis_id:
                evidence_list.append(entry)

        return evidence_list

    def end_debug(self, bug_id: str, resolution: str) -> Dict[str, Any]:
        """End a debug session with a resolution.

        The conclusion is automatically fed back into memory via
        the agentic_search feedback loop, so future similar bugs
        can find this debugging session's results.
        """
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Update conclusion section
        content = content.replace(
            "## Conclusion\n(Pending)",
            f"## Conclusion\n**Resolved:** {now}\n**Resolution:** {resolution}"
        )

        # Update status to CONCLUDE
        content = self._update_debug_status(content, "CONCLUDE")

        # Update resolution field
        content = content.replace("**Resolution:** (pending)", f"**Resolution:** {resolution}")

        content = self._append_debug_timeline(content, f"Debug session concluded: {resolution[:80]}")
        filepath.write_text(content, encoding="utf-8")

        # Feed back into memory for future searches
        # Extract confirmed hypothesis for the feedback
        hypotheses = self.get_hypotheses(bug_id)
        confirmed = [h for h in hypotheses if h["status"] == "confirmed"]
        rejected = [h for h in hypotheses if h["status"] == "rejected"]

        print(f"🏁 Debug session {bug_id} concluded")
        print(f"   Resolution: {resolution}")
        print(f"   Hypotheses: {len(confirmed)} confirmed, {len(rejected)} rejected, {len(hypotheses) - len(confirmed) - len(rejected)} other")

        return {
            "bug_id": bug_id,
            "status": "CONCLUDE",
            "resolution": resolution,
            "hypotheses_total": len(hypotheses),
            "hypotheses_confirmed": len(confirmed),
            "hypotheses_rejected": len(rejected),
        }

    def get_debug_status(self, bug_id: str) -> Dict[str, Any]:
        """Get current status of a debug session."""
        filepath = self._get_debug_file(bug_id)
        if not filepath:
            return {}

        content = self._safe_read(filepath)
        hypotheses = self.get_hypotheses(bug_id)

        # Extract status
        status_match = re.search(r'\*\*Status:\*\* (\w+)', content)
        status = status_match.group(1) if status_match else "UNKNOWN"

        # Extract description
        desc_match = re.search(r'\*\*Description:\*\* (.+)', content)
        description = desc_match.group(1).strip() if desc_match else ""

        # Count evidence
        evidence_count = len(re.findall(r'- \*\*.+?\*\* \| \[H\d+\]', content))

        # Current testing hypothesis
        testing = [h for h in hypotheses if h["status"] == "testing"]
        confirmed = [h for h in hypotheses if h["status"] == "confirmed"]
        rejected = [h for h in hypotheses if h["status"] == "rejected"]

        result = {
            "bug_id": bug_id,
            "status": status,
            "description": description,
            "hypotheses_total": len(hypotheses),
            "hypotheses_testing": len(testing),
            "hypotheses_confirmed": len(confirmed),
            "hypotheses_rejected": len(rejected),
            "evidence_count": evidence_count,
            "current_hypothesis": testing[-1]["hypothesis_id"] if testing else None,
        }

        print(f"🐛 {bug_id} — Status: {status}")
        print(f"   Description: {description[:80]}")
        print(f"   Hypotheses: {len(testing)} testing, {len(confirmed)} confirmed, {len(rejected)} rejected")
        print(f"   Evidence entries: {evidence_count}")
        if testing:
            print(f"   Current: {testing[-1]['hypothesis_id']} — {testing[-1]['hypothesis'][:60]}")

        return result

    def debug_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List past debug sessions."""
        if not self.debug_dir.exists():
            print("No debug sessions found.")
            return []

        sessions = []
        for md in sorted(self.debug_dir.glob("DEBUG-*.md"), reverse=True)[:limit]:
            content = self._safe_read(md)
            status_match = re.search(r'\*\*Status:\*\* (\w+)', content)
            desc_match = re.search(r'\*\*Description:\*\* (.+)', content)
            resolution_match = re.search(r'\*\*Resolution:\*\* (.+)', content)

            status = status_match.group(1) if status_match else "UNKNOWN"
            description = desc_match.group(1).strip() if desc_match else ""
            resolution = resolution_match.group(1).strip() if resolution_match else "(pending)"

            sessions.append({
                "bug_id": md.stem,
                "status": status,
                "description": description[:80],
                "resolution": resolution[:80],
            })

        if sessions:
            print(f"🐛 Debug History ({len(sessions)} sessions):")
            for s in sessions:
                icon = "✅" if s["status"] == "CONCLUDE" else "🔄" if s["status"] != "OBSERVE" else "👁️"
                print(f"  {icon} {s['bug_id']}: {s['description']}")
                if s["status"] == "CONCLUDE":
                    print(f"     → {s['resolution']}")
        else:
            print("No debug sessions found.")

        return sessions

    def search_rejected_hypotheses(self, query: str) -> List[Dict[str, Any]]:
        """Search rejected hypotheses to avoid repeating failed approaches.

        This is the key anti-pattern detector: "we already tried X and it failed."
        """
        if not self.debug_dir.exists():
            return []

        query_lower = query.lower()
        results = []

        for md in self.debug_dir.glob("DEBUG-*.md"):
            content = self._safe_read(md)
            bug_id = md.stem

            # Find rejected hypotheses matching query
            pattern = r'### (H\d+): (.+?)\n- \*\*Status:\*\* ❌ REJECTED[^\n]*\n(?:- \*\*Rejected reason:\*\* ([^\n]+)\n)?'
            for match in re.finditer(pattern, content):
                h_id = match.group(1)
                h_text = match.group(2).strip()
                reason = match.group(3).strip() if match.group(3) else ""

                # Check if query matches hypothesis or reason
                if (query_lower in h_text.lower() or
                    query_lower in reason.lower() or
                    SequenceMatcher(None, query_lower, h_text.lower()).ratio() > 0.4):
                    results.append({
                        "bug_id": bug_id,
                        "hypothesis_id": h_id,
                        "hypothesis": h_text,
                        "reason": reason,
                        "status": "rejected",
                    })

        if results:
            print(f"⚠️ Found {len(results)} rejected hypotheses matching '{query}':")
            for r in results:
                print(f"  ❌ [{r['bug_id']}/{r['hypothesis_id']}] {r['hypothesis'][:70]}")
                if r["reason"]:
                    print(f"     Reason: {r['reason'][:60]}")
        else:
            print(f"No rejected hypotheses found for '{query}'.")

        return results

    def search_debug_sessions(self, query: str) -> List[Dict[str, Any]]:
        """Search past debug sessions by description, hypothesis, or resolution."""
        if not self.debug_dir.exists():
            return []

        query_lower = query.lower()
        results = []

        for md in self.debug_dir.glob("DEBUG-*.md"):
            content = self._safe_read(md)
            content_lower = content.lower()

            if query_lower in content_lower:
                bug_id = md.stem
                desc_match = re.search(r'\*\*Description:\*\* (.+)', content)
                status_match = re.search(r'\*\*Status:\*\* (\w+)', content)
                resolution_match = re.search(r'\*\*Resolution:\*\* (.+)', content)

                results.append({
                    "bug_id": bug_id,
                    "description": desc_match.group(1).strip() if desc_match else "",
                    "status": status_match.group(1) if status_match else "UNKNOWN",
                    "resolution": resolution_match.group(1).strip() if resolution_match else "(pending)",
                    "file": str(md.relative_to(self.base_dir)),
                })

        if results:
            print(f"🔍 Found {len(results)} debug sessions matching '{query}':")
            for r in results:
                icon = "✅" if r["status"] == "CONCLUDE" else "🔄"
                print(f"  {icon} {r['bug_id']}: {r['description'][:60]}")
        else:
            print(f"No debug sessions found for '{query}'.")

        return results

    def _get_debug_file(self, bug_id: str) -> Optional[Path]:
        """Get the file path for a debug session."""
        filepath = self.debug_dir / f"{bug_id}.md"
        if not filepath.exists():
            print(f"❌ Debug session '{bug_id}' not found")
            return None
        return filepath

    def _update_debug_status(self, content: str, new_status: str) -> str:
        """Update the status field in debug session content."""
        return re.sub(
            r'(\*\*Status:\*\* )(OBSERVE|HYPOTHESIZE|EXPERIMENT|CONCLUDE)',
            rf'\g<1>{new_status}',
            content,
            count=1
        )

    def _append_debug_timeline(self, content: str, entry: str) -> str:
        """Append an entry to the debug session timeline."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        timeline_entry = f"- **{now}** | {entry}\n"
        # Find the last line of Timeline section and append
        if "## Timeline\n" in content:
            content = content.rstrip() + "\n" + timeline_entry
        return content

    # ── Channel Context Memory ─────────────────────────────────
    def channel_save(self, channel_id: str, context_data: Dict[str, Any]) -> Path:
        """Save context data for a channel.

        Args:
            channel_id: Channel identifier (e.g. 'telegram-46291309').
            context_data: Arbitrary dict of context to persist.

        Returns:
            Path to the saved JSON file.
        """
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.channels_dir / f"{channel_id}.json"
        # Merge with existing if present
        existing = self._json_load(filepath)
        existing.update(context_data)
        existing["last_updated"] = datetime.now().isoformat()
        self._json_save(filepath, existing)
        return filepath

    def channel_load(self, channel_id: str) -> Dict[str, Any]:
        """Load context data for a channel.

        Returns empty dict if channel has no saved context.
        """
        filepath = self.channels_dir / f"{channel_id}.json"
        return self._json_load(filepath)

    def channel_update(self, channel_id: str, key: str, value: Any,
                        mode: str = "set") -> Dict[str, Any]:
        """Update a single field in a channel's context.

        Creates the channel context file if it doesn't exist.

        Args:
            channel_id: Channel identifier.
            key: The field name to update.
            value: The new value.
            mode: Update mode:
                - "set" (default): Replace the value.
                - "append": If existing value is a list, append. If not a list,
                  convert to list and append. If value itself is a list,
                  extend the existing list.
                - "merge": If existing value is a dict and value is a dict,
                  shallow merge. Otherwise falls back to set.

        Returns:
            The updated context dict.
        """
        data = self.channel_load(channel_id)
        if mode == "append":
            existing = data.get(key)
            if existing is None:
                # No existing value — set as list
                data[key] = [value] if not isinstance(value, list) else value
            elif isinstance(existing, list):
                if isinstance(value, list):
                    existing.extend(value)
                else:
                    existing.append(value)
            else:
                # Existing is not a list — convert to list
                if isinstance(value, list):
                    data[key] = [existing] + value
                else:
                    data[key] = [existing, value]
        elif mode == "merge":
            existing = data.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                existing.update(value)
            else:
                data[key] = value
        else:
            # Default: set
            data[key] = value
        self.channel_save(channel_id, data)
        return data

    # ── Task Continuity Register ──────────────────────────────────
    def task_start(self, task_id: str, description: str,
                   channel_id: Optional[str] = None,
                   agent: Optional[str] = None,
                   delegated_by: Optional[str] = None) -> Dict[str, Any]:
        """Start a new task and persist its initial state.

        Args:
            task_id: Unique task identifier.
            description: Human-readable description.
            channel_id: Optional associated channel.
            agent: Optional assigned agent.
            delegated_by: Optional agent who delegated this task.

        Returns the initial task record.
        """
        self.context_tasks_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat()
        note = f"Task started: {description}"
        if delegated_by:
            note += f" (delegated by {delegated_by})"
        record = {
            "task_id": task_id,
            "description": description,
            "status": "active",
            "channel_id": channel_id or "",
            "agent": agent or "",
            "delegated_by": delegated_by or "",
            "created": now,
            "history": [
                {"timestamp": now, "status": "active", "note": note}
            ],
        }
        filepath = self.context_tasks_dir / f"{task_id}.json"
        self._json_save(filepath, record)
        return record

    def task_delegate(self, task_id: str, from_agent: str, to_agent: str,
                      context_note: str = "") -> Dict[str, Any]:
        """Delegate an existing task from one agent to another.

        Records a delegation event in the task history.

        Args:
            task_id: The task to delegate.
            from_agent: Agent delegating the task.
            to_agent: Agent receiving the task.
            context_note: Optional context for the receiving agent.

        Returns:
            The updated task record, or empty dict if task not found.
        """
        filepath = self.context_tasks_dir / f"{task_id}.json"
        record = self._json_load(filepath)
        if not record:
            return {}
        now = datetime.now().isoformat()
        note = f"Delegated from {from_agent} to {to_agent}"
        if context_note:
            note += f": {context_note}"
        record["agent"] = to_agent
        record["delegated_by"] = from_agent
        record["history"].append({
            "timestamp": now,
            "status": record.get("status", "active"),
            "note": note,
            "event": "delegation",
            "from_agent": from_agent,
            "to_agent": to_agent,
        })
        self._json_save(filepath, record)
        return record

    def task_update(self, task_id: str, status: str,
                    progress_note: str = "") -> Dict[str, Any]:
        """Update a task's status and add a history entry.

        Returns the updated task record, or empty dict if task not found.
        """
        filepath = self.context_tasks_dir / f"{task_id}.json"
        record = self._json_load(filepath)
        if not record:
            return {}
        now = datetime.now().isoformat()
        record["status"] = status
        record["history"].append({
            "timestamp": now,
            "status": status,
            "note": progress_note,
        })
        self._json_save(filepath, record)
        return record

    def task_complete(self, task_id: str,
                     result_summary: str = "") -> Dict[str, Any]:
        """Mark a task as completed.

        Returns the final task record, or empty dict if task not found.
        """
        filepath = self.context_tasks_dir / f"{task_id}.json"
        record = self._json_load(filepath)
        if not record:
            return {}
        now = datetime.now().isoformat()
        record["status"] = "completed"
        record["completed"] = now
        record["result_summary"] = result_summary
        record["history"].append({
            "timestamp": now,
            "status": "completed",
            "note": result_summary or "Task completed",
        })
        self._json_save(filepath, record)
        return record

    def task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the full history of a task.

        Returns list of history entries, or empty list if task not found.
        """
        filepath = self.context_tasks_dir / f"{task_id}.json"
        record = self._json_load(filepath)
        return record.get("history", [])

    def task_list(self, status: str = "active") -> List[Dict[str, Any]]:
        """List tasks filtered by status.

        Args:
            status: Filter by status ('active', 'completed', 'all').

        Returns:
            List of task records matching the filter.
        """
        self.context_tasks_dir.mkdir(parents=True, exist_ok=True)
        tasks = []
        for f in sorted(self.context_tasks_dir.glob("*.json")):
            record = self._json_load(f)
            if not record:
                continue
            if status == "all" or record.get("status") == status:
                tasks.append(record)
        return tasks

    # ── Agent Working Memory ──────────────────────────────────────
    def agent_save(self, agent_id: str,
                   working_memory: Dict[str, Any]) -> Path:
        """Save working memory for an agent.

        Args:
            agent_id: Agent identifier (e.g. 'zeon', 'sion').
            working_memory: Arbitrary dict of agent context.

        Returns:
            Path to the saved JSON file.
        """
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.agents_dir / f"{agent_id}.json"
        existing = self._json_load(filepath)
        existing.update(working_memory)
        existing["last_updated"] = datetime.now().isoformat()
        self._json_save(filepath, existing)
        return filepath

    def agent_load(self, agent_id: str) -> Dict[str, Any]:
        """Load working memory for an agent.

        Returns empty dict if agent has no saved memory.
        """
        filepath = self.agents_dir / f"{agent_id}.json"
        return self._json_load(filepath)

    def agent_inject(self, agent_id: str,
                     channel_id: Optional[str] = None,
                     task_id: Optional[str] = None,
                     max_history: int = 5,
                     include_completed_tasks: bool = False) -> str:
        """Merge agent + channel + task context into a single prompt block.

        This is the key integration method: it produces a ready-to-inject
        context string that can be prepended to any sub-agent instruction.

        Args:
            agent_id: Agent identifier.
            channel_id: Optional channel to include context from.
            task_id: Optional task to include history from.
            max_history: Maximum number of task history entries to include (default 5).
            include_completed_tasks: If True and channel_id is provided,
                include completed tasks for that channel.

        Returns:
            A formatted context block string.
        """
        parts = []

        # Agent working memory
        agent_mem = self.agent_load(agent_id)
        if agent_mem:
            parts.append("## Agent Working Memory")
            for k, v in agent_mem.items():
                if k == "last_updated":
                    continue
                if isinstance(v, list):
                    parts.append(f"- **{k}:** {', '.join(str(i) for i in v)}")
                elif isinstance(v, dict):
                    parts.append(f"- **{k}:**")
                    for dk, dv in v.items():
                        parts.append(f"  - {dk}: {dv}")
                else:
                    parts.append(f"- **{k}:** {v}")

        # Channel context
        if channel_id:
            ch_data = self.channel_load(channel_id)
            if ch_data:
                parts.append("")
                parts.append("## Channel Context")
                for k, v in ch_data.items():
                    if k == "last_updated":
                        continue
                    if isinstance(v, list):
                        parts.append(f"- **{k}:** {', '.join(str(i) for i in v)}")
                    elif isinstance(v, dict):
                        parts.append(f"- **{k}:**")
                        for dk, dv in v.items():
                            parts.append(f"  - {dk}: {dv}")
                    else:
                        parts.append(f"- **{k}:** {v}")

            # Include completed tasks for this channel if requested
            if include_completed_tasks:
                completed = self.channel_tasks(channel_id, status="completed", limit=max_history)
                if completed:
                    parts.append("")
                    parts.append("## Completed Tasks (Channel)")
                    for t in completed:
                        parts.append(f"- [{t.get('status', '')}] {t['task_id']}: {t.get('description', '')[:80]}")

        # Task context
        if task_id:
            task_record = self._json_load(
                self.context_tasks_dir / f"{task_id}.json"
            )
            if task_record:
                parts.append("")
                parts.append("## Task Context")
                parts.append(f"- **Task:** {task_record.get('description', task_id)}")
                parts.append(f"- **Status:** {task_record.get('status', 'unknown')}")
                history = task_record.get("history", [])
                if history:
                    parts.append("- **History:**")
                    for h in history[-max_history:]:
                        parts.append(f"  - [{h.get('timestamp', '')[:19]}] {h.get('status', '')}: {h.get('note', '')}")

        return "\n".join(parts)

    def agent_handoff(self, from_agent: str, to_agent: str,
                      task_id: Optional[str] = None,
                      context_note: str = "") -> str:
        """Hand off context from one agent to another.

        Transfers from_agent's working memory and related task context
        to to_agent. Records a handoff event in to_agent's working memory.

        Args:
            from_agent: Agent handing off.
            to_agent: Agent receiving the handoff.
            task_id: Optional specific task to include.
            context_note: Optional note about the handoff.

        Returns:
            A formatted context block string ready for injection.
        """
        parts = []
        now = datetime.now().isoformat()

        # Get from_agent's working memory
        from_mem = self.agent_load(from_agent)
        if from_mem:
            parts.append(f"## Handoff from {from_agent}")
            parts.append(f"- **Handoff time:** {now[:19]}")
            if context_note:
                parts.append(f"- **Note:** {context_note}")
            parts.append("")
            parts.append("### Working Memory")
            for k, v in from_mem.items():
                if k == "last_updated":
                    continue
                if isinstance(v, list):
                    parts.append(f"- **{k}:** {', '.join(str(i) for i in v)}")
                elif isinstance(v, dict):
                    parts.append(f"- **{k}:**")
                    for dk, dv in v.items():
                        parts.append(f"  - {dk}: {dv}")
                else:
                    parts.append(f"- **{k}:** {v}")

        # Task context if specified
        if task_id:
            task_record = self._json_load(
                self.context_tasks_dir / f"{task_id}.json"
            )
            if task_record:
                parts.append("")
                parts.append("### Task Context")
                parts.append(f"- **Task:** {task_record.get('description', task_id)}")
                parts.append(f"- **Status:** {task_record.get('status', 'unknown')}")
                history = task_record.get("history", [])
                if history:
                    parts.append("- **History:**")
                    for h in history[-5:]:
                        parts.append(f"  - [{h.get('timestamp', '')[:19]}] {h.get('status', '')}: {h.get('note', '')}")

                # Also delegate the task to the new agent
                self.task_delegate(task_id, from_agent, to_agent, context_note)

        # Record handoff in to_agent's working memory
        to_mem = self.agent_load(to_agent)
        handoff_record = {
            "from": from_agent,
            "timestamp": now,
            "note": context_note,
        }
        if task_id:
            handoff_record["task_id"] = task_id

        existing_handoffs = to_mem.get("handoff_from", [])
        if not isinstance(existing_handoffs, list):
            existing_handoffs = [existing_handoffs]
        existing_handoffs.append(handoff_record)
        to_mem["handoff_from"] = existing_handoffs
        self.agent_save(to_agent, to_mem)

        return "\n".join(parts)

    # ── Channel Tasks ──────────────────────────────────────────────
    def channel_tasks(self, channel_id: str, status: str = "all",
                      limit: int = 5) -> List[Dict[str, Any]]:
        """List tasks associated with a channel.

        Args:
            channel_id: Channel identifier.
            status: Filter by status ('active', 'completed', 'all').
            limit: Maximum number of tasks to return (most recent first).

        Returns:
            List of task records matching the filter, sorted by creation time descending.
        """
        self.context_tasks_dir.mkdir(parents=True, exist_ok=True)
        tasks = []
        for f in self.context_tasks_dir.glob("*.json"):
            record = self._json_load(f)
            if not record:
                continue
            if record.get("channel_id") != channel_id:
                continue
            if status != "all" and record.get("status") != status:
                continue
            tasks.append(record)

        # Sort by created timestamp descending
        tasks.sort(key=lambda t: t.get("created", ""), reverse=True)
        return tasks[:limit]

    # ── Task Cleanup ──────────────────────────────────────────────
    def task_cleanup(self, max_age_days: int = 30,
                     archive: bool = True) -> Dict[str, int]:
        """Clean up completed tasks older than max_age_days.

        Args:
            max_age_days: Age threshold in days for completed tasks.
            archive: If True, move to .memkraft/tasks/archive/.
                     If False, delete permanently.

        Returns:
            Dict with counts: {"archived": N, "deleted": N, "kept": N}
        """
        self.context_tasks_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.context_tasks_dir / "archive"
        if archive:
            archive_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        result = {"archived": 0, "deleted": 0, "kept": 0}

        for f in list(self.context_tasks_dir.glob("*.json")):
            record = self._json_load(f)
            if not record:
                continue

            # Only clean up completed tasks
            if record.get("status") != "completed":
                result["kept"] += 1
                continue

            # Check age
            completed_ts = record.get("completed", record.get("created", ""))
            if not completed_ts:
                result["kept"] += 1
                continue

            try:
                # Handle both ISO format with and without microseconds
                completed_dt = datetime.fromisoformat(completed_ts)
                age_days = (now - completed_dt).days
            except (ValueError, TypeError):
                result["kept"] += 1
                continue

            if age_days < max_age_days:
                result["kept"] += 1
                continue

            if archive:
                target = archive_dir / f.name
                f.rename(target)
                result["archived"] += 1
            else:
                f.unlink()
                result["deleted"] += 1

        return result

    # ── JSON helpers (for channel/task/agent) ─────────────────────
    def _json_load(self, filepath: Path) -> Dict[str, Any]:
        """Load a JSON file, returning empty dict if missing or invalid."""
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _json_save(self, filepath: Path, data: Dict[str, Any]) -> None:
        """Save data to a JSON file."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── Helpers ───────────────────────────────────────────────
    def _slugify(self, text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r'[^\w\s\-\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]', '', text)
        text = re.sub(r'\s+', '-', text)
        return text[:80]

    def _detect_regex(self, text: str) -> List[Dict[str, str]]:
        """Multi-language entity detection via regex (zero ML dependencies).

        Detects: persons (EN/KR/CN/JP), @handles, emails, URLs,
        organizations, products (incl. version numbers), and locations.
        """
        entities: List[Dict[str, str]] = []
        common = {'The', 'This', 'That', 'And', 'But', 'For', 'Not', 'All', 'Has', 'Was', 'Are', 'Its', 'Our', 'Their', 'Yc', 'From', 'With', 'Please', 'Contact', 'However', 'While', 'These', 'Those', 'Each', 'Some', 'Many', 'Other', 'Such', 'Most', 'Last', 'New', 'Old', 'Next', 'Here', 'After', 'Before', 'Between', 'Under', 'Over', 'Every', 'About', 'Also', 'Just', 'When', 'Where', 'Why', 'How', 'What', 'Who', 'Which', 'More', 'Much', 'Very', 'Well', 'Still', 'Even', 'Back', 'Down', 'Only', 'Then', 'Than', 'Both', 'Into', 'Like', 'Made', 'Come', 'Could', 'Would', 'Should', 'Will', 'May', 'Can', 'Did', 'Does'}

        names_2 = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', text)
        names_3 = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+)\b', text)
        korean_names = re.findall(r'[\uAC00-\uD7AF]{2,4}', text)
        # 한국어 동사/형용사 어미 제거 후 이름만 추출
        korean_names_cleaned = []
        for name in korean_names:
            # 동사 어미 제거: 했다, 한다, 해요, 함, 됨, 됐다, etc.
            stripped = re.sub(r'(했|할|해|되|됐|받|만|지|보|주|가|오|알|인|있|없|갈|될|만들|사용|개발|적용|설정|확인|업데이트|추가|수정|삭제|생성|실행|테스트|분석|검색|연결|설치|시작|완료|진행|보고|논의|발표|참여|준비|요청|제안|검토|승인|거절|검증|배포|구축|도입|운영|관리|모니터링|추적|감지|정리|보강|업그레이드|마이그레이션|이|이다|입니다|였다|였음)(다|해|함|요|서|고|며|니|까|지|은|는|이|을|를|와|과|도|만|로|으로|라|라서|의)?$', '', name)
            if len(stripped) >= 2:
                korean_names_cleaned.append(stripped)
        korean_names = korean_names_cleaned
        # 중국어/일본어 한자 패턴 (한자에는 단어 경계가 없어서 연속 추출)
        stopwords = self._load_stopwords()
        korean_stopwords = set(stopwords.get("korean", []))
        chinese_stopwords = set(stopwords.get("chinese", []))
        japanese_stopwords = set(stopwords.get("japanese", []))

        names_3_word_sets = [set(n.split()) for n in names_3]
        for name in set(names_3):
            if name not in common:
                entities.append({"name": name, "type": "person", "context": "auto-detected"})
        for name in set(names_2):
            name_words = set(name.split())
            is_substring = any(name_words.issubset(ws) for ws in names_3_word_sets)
            if name not in common and name.split()[0] not in common and name.split()[1] not in common and not is_substring:
                entities.append({"name": name, "type": "person", "context": "auto-detected"})
        for name in set(korean_names):
            if len(name) >= 2 and name not in korean_stopwords:
                # 한국어 조사 제거: 이, 을, 를, 은, 는, 에, 에서, 로, 으로, 와, 과, 도, 만, 이라, 이라서
                stripped = re.sub(r'([가-힣]+?)([이을를은는에로으와과도만이라서의]+)$', r'\1', name)
                if stripped != name and len(stripped) >= 2 and stripped not in korean_stopwords:
                    name = stripped
                if name not in korean_stopwords and len(name) >= 2:
                    entities.append({"name": name, "type": "person", "context": "auto-detected (Korean)"})

        # 중국어 이름 감지 — 성씨 기반 접근 (한자에는 단어 경계가 없어서 정규식만으로는 한계)
        # 전체 한자 텍스트에서 성씨로 시작하는 2~3글자 패턴 추출
        chinese_surnames = {'王', '李', '张', '刘', '陈', '杨', '赵', '黄', '周', '吴', '徐', '孙', '胡', '朱', '高', '林', '何', '郭', '马', '罗', '梁', '宋', '郑', '谢', '韩', '唐', '冯', '于', '董', '萧', '程', '曹', '袁', '邓', '许', '傅', '沈', '曾', '彭', '吕', '苏', '卢', '蒋', '蔡', '贾', '丁', '魏', '薛', '叶', '阎', '余', '潘', '杜', '戴', '夏', '钟', '汪', '田', '任', '姜', '范', '方', '石', '姚', '谭', '廖', '邹', '熊', '金', '陆', '郝', '孔', '白', '崔', '康', '毛', '邱', '秦', '江', '史', '顾', '侯', '邵', '孟', '龙', '万', '段', '雷', '钱', '汤', '尹', '黎', '易', '常', '武', '乔', '贺', '赖', '龚', '文'}
        # 한자 연속 시퀀스에서 성씨+1~2글자 추출
        chinese_char_runs = re.findall(r'[\u4E00-\u9FFF]+', text)
        seen_chinese = set()
        for run in chinese_char_runs:
            for i, ch in enumerate(run):
                if ch in chinese_surnames:
                    for length in [3, 2]:  # 3글자 이름 먼저
                        if i + length <= len(run):
                            candidate = run[i:i+length]
                            if candidate not in chinese_stopwords and candidate not in japanese_stopwords and candidate not in seen_chinese:
                                seen_chinese.add(candidate)
                                entities.append({"name": candidate, "type": "person", "context": "auto-detected (Chinese)"})
                                break  # 가장 긴 매치 사용

        # 일본어 성씨 기반 감지
        japanese_surnames = {'田中', '佐藤', '鈴木', '高橋', '伊藤', '渡辺', '山本', '中村', '小林', '加藤', '吉田', '山田', '山口', '松本', '井上', '木村', '斎藤', '清水', '山崎', '池田', '橋本', '阿部', '石川', '山下', '中島', '石井', '小川', '前田', '岡田', '長谷川', '藤田', '後藤', '近藤', '村上', '遠藤', '青木', '坂本', '斉藤', '福田', '西村', '藤井', '金子', '岡本', '藤原', '中野', '三浦', '原田', '松田', '竹内', '上田', '中山', '和田', '森田', '柴田', '酒井', '工藤', '横山', '宮崎', '宮本', '内田', '高木', '安藤', '谷口', '大野', '丸山', '今井', '高田', '藤本', '武田', '村田', '上野', '杉山', '増田', '平野', '大塚', '千葉', '久保', '松井', '小島', '岩崎', '桜井', '木下', '野村', '島田', '菊池'}
        for run in chinese_char_runs:
            for js in sorted(japanese_surnames, key=len, reverse=True):
                if js in run:
                    idx = run.find(js)
                    for name_len in [len(js)+2, len(js)+1]:  # 긴 것부터
                        if idx + name_len <= len(run):
                            candidate = run[idx:idx+name_len]
                            if candidate not in chinese_stopwords and candidate not in japanese_stopwords and candidate not in seen_chinese:
                                entities.append({"name": candidate, "type": "person", "context": "auto-detected (Japanese)"})
                                break

        handles = re.findall(r'(?:^|(?<=\s))@(\w+)', text)
        for handle in set(handles):
            entities.append({"name": handle, "type": "person", "context": "mentioned via @handle"})

        # ── Email detection ───────────────────────────────────
        emails = re.findall(r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b', text)
        for email in set(emails):
            entities.append({"name": email, "type": "contact", "context": "auto-detected (email)"})

        # ── URL detection ─────────────────────────────────────
        urls = re.findall(r'https?://[^\s)<>\]]+', text)
        for url in set(urls):
            entities.append({"name": url, "type": "reference", "context": "auto-detected (URL)"})

        # ── Organization detection ─────────────────────────────
        # Known tech companies (extendable)
        known_orgs = {'Apple', 'Google', 'Microsoft', 'Amazon', 'Meta', 'Tesla', 'Netflix', 'Nvidia', 'OpenAI', 'Anthropic', 'Samsung', 'Hashed', 'Tencent', 'Alibaba', 'ByteDance', 'Baidu', 'Sony', 'Toyota', 'Hyundai', 'LG', 'Kakao', 'Naver', 'Coupang', 'Toss', 'Stripe', 'SpaceX', 'Palantir', 'Uber', 'Airbnb', 'Coinbase', 'Binance', 'Riot', 'Epic', 'Valve', 'Blizzard'}
        # "X Corp", "X Inc", "X Ltd", "X Co", "X Foundation", "X Labs", "X Group", "X Capital", "X Ventures"
        org_suffix_pattern = re.findall(r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:Corp|Inc|Ltd|Co|Foundation|Labs|Group|Capital|Ventures|Systems|Technologies|Networks|AI|IO|Software|Digital|Dynamics|Industries|Holdings))\b', text)
        for org in org_suffix_pattern:
            entities.append({"name": org, "type": "organization", "context": "auto-detected (suffix)"})

        # Known org names from text
        for org in known_orgs:
            if re.search(r'\b' + re.escape(org) + r'\b', text):
                # Check not already captured as person
                if not any(e["name"] == org for e in entities):
                    entities.append({"name": org, "type": "organization", "context": "auto-detected (known)"})

        # Korean organizations: 한자+기관/회사/은행/그룹 etc.
        kr_orgs = re.findall(r'([가-힣]{2,8}(?:기관|회사|은행|그룹|재단|연구소|대학|대학교|병원|센터|연합|협회|위원회|청|부|처|실|국|원|전자|자동차|물산|중공업|건설|해운|항공|통신|제약|화학|철강|에너지|인터넷|소프트웨어|테크|랩스|벤처스|캐피탈|파트너스|네트워크|시스템|솔루션|미디어|엔터|엔터테인먼트|게임즈|스튜디오|플랫폼))', text)
        for org in kr_orgs:
            if org not in korean_stopwords:
                entities.append({"name": org, "type": "organization", "context": "auto-detected (Korean org)"})

        # ── Product detection ──────────────────────────────────
        # "X Pro", "X Max", "X Ultra", "X Plus", "X Mini", "X Air"
        product_pattern = re.findall(r'\b([A-Za-z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:Pro|Max|Ultra|Plus|Mini|Air|Lite|SE|Studio|Suite|Cloud|Engine|Platform|OS|OSX))\b', text)
        for prod in product_pattern:
            if not any(e["name"] == prod for e in entities):
                entities.append({"name": prod, "type": "product", "context": "auto-detected (suffix)"})

        # Version-number products: "GPT-5", "iPhone 16", "Claude-3.5"
        # CamelCase or uppercase start + digit (allows iPhone, MacBook, GPT, etc.)
        version_products = re.findall(r'\b([a-zA-Z]*[A-Z][A-Za-z]*[\s-]\d+(?:\.\d+)?(?:\s+(?:Pro|Max|Ultra|Plus|Mini|Air))?)\b', text)
        # Hyphenated: GPT-5, Claude-3
        version_products += re.findall(r'\b([A-Z][A-Za-z]+-\d+(?:\.\d+)?)\b', text)
        for vp in set(version_products):
            vp = vp.strip()
            if len(vp) >= 3 and not any(e["name"] == vp for e in entities):
                if re.fullmatch(r'\d{4}', vp):
                    continue
                entities.append({"name": vp, "type": "product", "context": "auto-detected (version)"})

        # ── Location detection ─────────────────────────────────
        known_locations = {'Seoul', 'Tokyo', 'Beijing', 'Shanghai', 'Singapore', 'London', 'New York', 'San Francisco', 'Berlin', 'Paris', 'Dubai', 'Hong Kong', 'Taipei', 'Bangkok', 'Sydney', 'Toronto', 'Vancouver', 'Busan', 'Jeju', 'Osaka', 'Mumbai', 'Delhi', 'Jakarta', 'Manila', 'Kuala Lumpur'}
        for loc in known_locations:
            if re.search(r'\b' + re.escape(loc) + r'\b', text):
                if not any(e["name"] == loc for e in entities):
                    entities.append({"name": loc, "type": "location", "context": "auto-detected (known)"})

        # Korean locations: 시/도/구/군/읍/면
        kr_locations = re.findall(r'([가-힣]{2,5}(?:시|도|구|군|읍|면|동|로|길))', text)
        for loc in kr_locations:
            if loc not in korean_stopwords and len(loc) >= 3:
                entities.append({"name": loc, "type": "location", "context": "auto-detected (Korean location)"})

        return entities

    def _create_entity(self, name: str, entity_type: str = "person", source: str = ""):
        self.entities_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slugify(name)
        filepath = self.entities_dir / f"{slug}.md"

        if filepath.exists():
            # Append to timeline
            content = filepath.read_text(encoding="utf-8", errors="replace")
            now = datetime.now().strftime("%Y-%m-%d")
            timeline_marker = "## Timeline\n\n"
            if timeline_marker in content:
                content = content.replace(
                    timeline_marker,
                    f"{timeline_marker}- **{now}** | Re-detected [Source: {source}]\n"
                )
                filepath.write_text(content, encoding="utf-8")
            return

        now = datetime.now().strftime("%Y-%m-%d")
        content = f"""# {name}

**Tier: recall**

## Executive Summary
(Type or auto-generate a 1-2 sentence summary)

## State
- **Role:** (enrichment needed)
- **Affiliation:** (enrichment needed)
- **Relationship:** (enrichment needed)
- **Key Context:** (enrichment needed)

## Open Threads
- [ ] Initial entity — enrichment needed

## See Also
(Related items to be linked)

---

## Timeline

- **{now}** | Entity first detected [Source: {source}]
"""
        filepath.write_text(content, encoding="utf-8")

    def _extract_section(self, content: str, section_name: str) -> str:
        marker = f"## {section_name}"
        if marker not in content:
            return ""
        start = content.find(marker) + len(marker)
        end = content.find("\n## ", start)
        if end == -1:
            end = content.find("\n---", start)
        if end == -1:
            end = len(content)
        return content[start:end].strip()

    def _all_md_files(self):
        for subdir in [self.entities_dir, self.live_notes_dir, self.decisions_dir, self.originals_dir, self.inbox_dir, self.tasks_dir, self.meetings_dir, self.debug_dir]:
            if subdir.exists():
                for md in subdir.glob("*.md"):
                    if not md.is_symlink():
                        yield md
        # Include daily notes and base-dir markdown files (exclude system/auto-generated files)
        _system_files = {"RESOLVER.md", "TEMPLATES.md", "open-loops.md", "fact-registry.md"}
        if self.base_dir.exists():
            for md in self.base_dir.glob("*.md"):
                if md.name not in _system_files and not md.is_symlink():
                    yield md

    def _safe_read(self, path: Path) -> str:
        """Read file safely, returning empty string on any error."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def _gather_memory_files(self, recent: int = 0, tag: str = "", date: str = ""):
        """Gather memory files with optional filters."""
        files = list(self._all_md_files())
        # Deduplicate (in case _all_md_files yields same file from base_dir)
        seen = set()
        unique = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        files = unique
        if recent > 0:
            def _safe_mtime(f: Path) -> float:
                try:
                    return f.stat().st_mtime
                except OSError:
                    return 0.0
            files.sort(key=_safe_mtime, reverse=True)
            files = files[:recent]
        if date:
            files = [f for f in files if date in f.read_text(encoding="utf-8", errors="replace") or date in f.name]
        if tag:
            files = [f for f in files if tag.lower() in f.read_text(encoding="utf-8", errors="replace").lower()]
        return files

    def _search_tokens(self, text: str) -> list:
        """Tokenize search text for dependency-free hybrid matching."""
        return [t for t in re.findall(r'[\w\uAC00-\uD7AF\u4E00-\u9FFF]+', text.lower()) if len(t) > 1]

    def _best_token_snippet(self, query_tokens: list, lines: list, lines_orig: list) -> str:
        best_idx = 0
        best_hits = 0
        query_set = set(query_tokens)
        for idx, line in enumerate(lines):
            line_tokens = set(self._search_tokens(line))
            hits = len(query_set & line_tokens)
            if hits > best_hits:
                best_hits = hits
                best_idx = idx
        if best_hits == 0:
            return ""
        start = max(0, best_idx - 3)
        end = min(len(lines), best_idx + 4)
        return " | ".join(l.strip() for l in lines_orig[start:end] if l.strip())[:200]

    def _first_meaningful_line(self, content: str) -> str:
        """Return the first non-heading, non-empty, non-boilerplate line."""
        skip_prefixes = ("#", "---", ">", "**Tier", "- **Type", "- **Started",
                         "- **Last Update", "- **Update Count", "- **Source",
                         "- [[", "(")
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not any(stripped.startswith(p) for p in skip_prefixes) and len(stripped) > 10:
                return stripped
        return ""

    def _extract_tags(self, content: str) -> str:
        """Extract tags from content."""
        tags = re.findall(r'(?:tags?:|태그:)\s*(.+)', content, re.IGNORECASE)
        if tags:
            return tags[0].strip()[:50]
        return ""

    def _load_stopwords(self) -> dict:
        """Load stopwords from JSON file (cached)"""
        if not hasattr(self, '_stopwords_cache'):
            sw_path = Path(__file__).parent / "stopwords.json"
            if sw_path.exists():
                with open(sw_path, 'r', encoding='utf-8') as f:
                    self._stopwords_cache = json.load(f)
            else:
                self._stopwords_cache = {"korean": [], "chinese": [], "japanese": []}
        return self._stopwords_cache

    # ══════════════════════════════════════════════════════════
    # Memory Snapshots & Time Travel (v0.5.0)
    # ══════════════════════════════════════════════════════════

    def _get_version(self) -> str:
        """Return the package version without circular imports."""
        try:
            from memkraft import __version__
            return __version__
        except Exception:
            return "unknown"

    def _file_hash(self, path: Path) -> str:
        """SHA-256 of a file's content, truncated to 12 hex chars."""
        h = hashlib.sha256()
        try:
            h.update(path.read_bytes())
        except OSError:
            return "error"
        return h.hexdigest()[:12]

    def snapshot(self, label: str = "", include_content: bool = False) -> Dict[str, Any]:
        """Create a point-in-time snapshot of all memory files.

        Each snapshot records every Markdown file's path, size, hash,
        last-modified time, first meaningful line (summary), and optionally
        the full content.  Snapshots are saved as JSON under
        ``.memkraft/snapshots/SNAP-<timestamp>.json``.

        Args:
            label: Human-readable label (e.g. "before-migration", "post-dream").
            include_content: If True, embed each file's full text in the
                snapshot (makes time-travel queries richer but larger).

        Returns:
            Snapshot metadata dict with ``snapshot_id``, ``timestamp``,
            ``label``, ``file_count``, ``total_bytes``, and ``path``.
        """
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        _uid = uuid.uuid4().hex[:6]
        snap_id = f"SNAP-{now.strftime('%Y%m%d-%H%M%S')}-{_uid}"
        files: Dict[str, Any] = {}
        total_bytes = 0

        for md in self._all_md_files():
            rel = str(md.relative_to(self.base_dir))
            try:
                stat = md.stat()
                content = self._safe_read(md)
            except OSError:
                continue
            file_entry: Dict[str, Any] = {
                "size": stat.st_size,
                "hash": self._file_hash(md),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "summary": self._first_meaningful_line(content)[:200],
                "sections": [l.strip() for l in content.split("\n") if l.startswith("#")][:15],
                "fact_count": content.count("\n- "),
                "link_count": len(re.findall(r'\[\[[^\]]+\]\]', content)),
            }
            if include_content:
                # Guard: skip embedding content for very large files to avoid memory issues
                if stat.st_size <= 1_048_576:  # 1 MB limit per file
                    file_entry["content"] = content
                else:
                    file_entry["content"] = content[:4096] + f"\n\n[...truncated: {stat.st_size} bytes total...]"
            files[rel] = file_entry
            total_bytes += stat.st_size

        manifest = {
            "snapshot_id": snap_id,
            "timestamp": now.isoformat(),
            "label": label,
            "memkraft_version": self._get_version(),
            "file_count": len(files),
            "total_bytes": total_bytes,
            "files": files,
        }

        snap_path = self.snapshots_dir / f"{snap_id}.json"
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        print(f"📸 Snapshot created: {snap_id}")
        if label:
            print(f"   Label: {label}")
        print(f"   Files: {len(files)} | Size: {total_bytes:,} bytes")
        print(f"   Saved: {snap_path.relative_to(self.base_dir)}")

        return {
            "snapshot_id": snap_id,
            "timestamp": now.isoformat(),
            "label": label,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "path": str(snap_path.relative_to(self.base_dir)),
        }

    def snapshot_list(self) -> List[Dict[str, Any]]:
        """List all saved snapshots, newest first."""
        results: List[Dict[str, Any]] = []
        if not self.snapshots_dir.exists():
            print("No snapshots yet. Run `memkraft snapshot` to create one.")
            return results

        for snap_file in sorted(self.snapshots_dir.glob("SNAP-*.json"), reverse=True):
            try:
                with open(snap_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "snapshot_id": data.get("snapshot_id", snap_file.stem),
                    "timestamp": data.get("timestamp", ""),
                    "label": data.get("label", ""),
                    "file_count": data.get("file_count", 0),
                    "total_bytes": data.get("total_bytes", 0),
                })
            except (json.JSONDecodeError, OSError):
                continue

        if not results:
            print("No snapshots found.")
        else:
            print(f"📸 Snapshots ({len(results)}):")
            for s in results:
                label_str = f' "{s["label"]}"' if s["label"] else ""
                print(f"  {s['snapshot_id']}{label_str} — {s['file_count']} files, {s['total_bytes']:,} bytes ({s['timestamp'][:19]})")
        return results

    def _load_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Load a snapshot by ID or partial match."""
        if not self.snapshots_dir.exists():
            return None
        # Exact match
        exact = self.snapshots_dir / f"{snapshot_id}.json"
        if exact.exists():
            with open(exact, "r", encoding="utf-8") as f:
                return json.load(f)
        # Partial / label match
        for snap_file in sorted(self.snapshots_dir.glob("SNAP-*.json"), reverse=True):
            if snapshot_id in snap_file.stem:
                with open(snap_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            try:
                with open(snap_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("label", "") == snapshot_id:
                    return data
            except (json.JSONDecodeError, OSError):
                continue
        return None

    def snapshot_diff(self, snapshot_a: str, snapshot_b: str = "") -> Dict[str, Any]:
        """Compare two snapshots (or a snapshot vs current state).

        Shows files added, removed, modified, and unchanged between two
        points in time.

        Args:
            snapshot_a: Snapshot ID (the "before").
            snapshot_b: Snapshot ID (the "after"). If empty, compares
                against current live state.

        Returns:
            Dict with ``added``, ``removed``, ``modified``, ``unchanged``
            counts and file lists.
        """
        data_a = self._load_snapshot(snapshot_a)
        if not data_a:
            print(f"❌ Snapshot not found: {snapshot_a}")
            return {}

        if snapshot_b:
            data_b = self._load_snapshot(snapshot_b)
            if not data_b:
                print(f"❌ Snapshot not found: {snapshot_b}")
                return {}
            files_b = data_b["files"]
            label_b = data_b.get("snapshot_id", snapshot_b)
        else:
            # Build live state
            files_b = {}
            for md in self._all_md_files():
                rel = str(md.relative_to(self.base_dir))
                try:
                    size_b = md.stat().st_size
                except OSError:
                    size_b = 0
                files_b[rel] = {
                    "hash": self._file_hash(md),
                    "size": size_b,
                }
            label_b = "LIVE"

        files_a = data_a["files"]
        label_a = data_a.get("snapshot_id", snapshot_a)

        added = []
        removed = []
        modified = []
        unchanged = []

        all_paths = set(list(files_a.keys()) + list(files_b.keys()))
        for path in sorted(all_paths):
            in_a = path in files_a
            in_b = path in files_b
            if in_a and not in_b:
                removed.append({"file": path, "size": files_a[path].get("size", 0)})
            elif not in_a and in_b:
                added.append({"file": path, "size": files_b[path].get("size", 0)})
            elif in_a and in_b:
                hash_a = files_a[path].get("hash", "")
                hash_b = files_b[path].get("hash", "")
                if hash_a != hash_b:
                    size_a = files_a[path].get("size", 0)
                    size_b = files_b[path].get("size", 0)
                    delta = size_b - size_a
                    modified.append({
                        "file": path,
                        "size_before": size_a,
                        "size_after": size_b,
                        "delta": delta,
                        "summary_before": files_a[path].get("summary", "")[:80],
                        "summary_after": files_b[path].get("summary", "")[:80] if isinstance(files_b[path], dict) else "",
                    })
                else:
                    unchanged.append(path)

        result = {
            "snapshot_a": label_a,
            "snapshot_b": label_b,
            "added": added,
            "removed": removed,
            "modified": modified,
            "unchanged_count": len(unchanged),
        }

        print(f"📊 Diff: {label_a} → {label_b}")
        print(f"   ✅ Added: {len(added)} | ❌ Removed: {len(removed)} | ✏️ Modified: {len(modified)} | 📁 Unchanged: {len(unchanged)}")
        if added:
            print("\n   ✅ Added:")
            for a in added[:10]:
                print(f"      + {a['file']} ({a['size']:,} bytes)")
        if removed:
            print("\n   ❌ Removed:")
            for r in removed[:10]:
                print(f"      - {r['file']} ({r['size']:,} bytes)")
        if modified:
            print("\n   ✏️ Modified:")
            for m in modified[:10]:
                sign = "+" if m["delta"] >= 0 else ""
                print(f"      ~ {m['file']} ({sign}{m['delta']:,} bytes)")

        return result

    def time_travel(self, query: str, snapshot_id: str = "",
                    date: str = "") -> List[Dict[str, Any]]:
        """Search memory *as it was* at a past snapshot.

        Answers questions like "what did I know about X on March 1st?" by
        searching against the snapshot's recorded summaries, sections, and
        (if available) content.

        Args:
            query: Search query.
            snapshot_id: Specific snapshot ID. If empty and ``date`` is
                provided, the closest snapshot on or before that date is used.
            date: Date string (YYYY-MM-DD). Used when ``snapshot_id`` is
                not specified.

        Returns:
            List of matching file dicts from the historical snapshot.
        """
        target_snap = None

        if snapshot_id:
            target_snap = self._load_snapshot(snapshot_id)
        elif date:
            # Find closest snapshot on or before the given date
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                print(f"❌ Invalid date format: {date} (use YYYY-MM-DD)")
                return []
            best = None
            best_delta = None
            if self.snapshots_dir.exists():
                for snap_file in self.snapshots_dir.glob("SNAP-*.json"):
                    try:
                        with open(snap_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        ts_raw = data["timestamp"].replace("Z", "+00:00")
                        snap_dt = datetime.fromisoformat(ts_raw)
                        # Normalise to naive UTC for comparison
                        if snap_dt.tzinfo is not None:
                            snap_dt = snap_dt.replace(tzinfo=None)
                        if snap_dt.date() <= target_date.date():
                            delta = (target_date.date() - snap_dt.date()).days
                            best_ts_raw = best["timestamp"].replace("Z", "+00:00") if best else ""
                            best_dt = datetime.fromisoformat(best_ts_raw).replace(tzinfo=None) if best_ts_raw else datetime.min
                            if best_delta is None or delta < best_delta or (
                                delta == best_delta and snap_dt > best_dt
                            ):
                                best = data
                                best_delta = delta
                    except (json.JSONDecodeError, OSError, KeyError, ValueError):
                        continue
            target_snap = best
        else:
            # Use most recent snapshot
            if self.snapshots_dir.exists():
                snaps = sorted(self.snapshots_dir.glob("SNAP-*.json"), reverse=True)
                if snaps:
                    with open(snaps[0], "r", encoding="utf-8") as f:
                        target_snap = json.load(f)

        if not target_snap:
            print("❌ No snapshot found. Create one first with `memkraft snapshot`.")
            return []

        snap_label = target_snap.get("snapshot_id", "unknown")
        snap_time = target_snap.get("timestamp", "")[:19]
        snap_files = target_snap.get("files", {})
        query_lower = query.lower()
        query_tokens = self._search_tokens(query_lower)

        results: List[Dict[str, Any]] = []

        for rel_path, fdata in snap_files.items():
            score = 0.0
            snippet = ""

            # Search in content if available (full time-travel)
            content = fdata.get("content", "")
            if content:
                content_lower = content.lower()
                if query_lower in content_lower:
                    score = 1.0
                    # Extract snippet around match
                    idx = content_lower.find(query_lower)
                    start = max(0, idx - 60)
                    end = min(len(content), idx + len(query) + 60)
                    snippet = content[start:end].replace("\n", " ").strip()
                elif query_tokens:
                    content_tokens = set(self._search_tokens(content_lower))
                    matched = sum(1 for t in query_tokens if t in content_tokens)
                    if matched > 0:
                        score = 0.5 * (matched / len(query_tokens))
                        snippet = fdata.get("summary", "")[:100]
            else:
                # Search summary + sections + filename
                summary = fdata.get("summary", "").lower()
                sections_text = " ".join(fdata.get("sections", [])).lower()
                filename = Path(rel_path).stem.lower().replace("-", " ")
                searchable = f"{filename} {summary} {sections_text}"

                if query_lower in searchable:
                    score = 0.8
                    snippet = fdata.get("summary", "")
                elif query_tokens:
                    searchable_tokens = set(self._search_tokens(searchable))
                    matched = sum(1 for t in query_tokens if t in searchable_tokens)
                    if matched > 0:
                        score = 0.4 * (matched / len(query_tokens))
                        snippet = fdata.get("summary", "")

                # Filename exact match boost
                if query_lower in filename:
                    score = max(score, 0.7)
                    snippet = snippet or fdata.get("summary", "")

            if score > 0:
                results.append({
                    "file": rel_path,
                    "score": round(score, 2),
                    "match": Path(rel_path).stem,
                    "snippet": snippet[:150],
                    "fact_count": fdata.get("fact_count", 0),
                    "hash": fdata.get("hash", ""),
                    "snapshot": snap_label,
                })

        results.sort(key=lambda x: x["score"], reverse=True)

        if not results:
            print(f"🕰️ Time Travel ({snap_label}, {snap_time}): no results for '{query}'")
        else:
            print(f"🕰️ Time Travel ({snap_label}, {snap_time}): {len(results)} results for '{query}'")
            for r in results[:10]:
                snippet_str = f"\n     {r['snippet'][:80]}" if r.get("snippet") else ""
                print(f"  [{r['score']:.2f}] {r['file']}{snippet_str}")

        return results

    def snapshot_entity(self, name: str) -> List[Dict[str, Any]]:
        """Show how an entity evolved across all snapshots.

        Returns a timeline of changes for a specific entity, comparing
        its state across every recorded snapshot.

        Args:
            name: Entity name.

        Returns:
            List of dicts with snapshot_id, timestamp, fact_count,
            size, hash, and change_type for each snapshot.
        """
        slug = self._slugify(name)
        possible_paths = [
            f"entities/{slug}.md",
            f"live-notes/{slug}.md",
        ]

        if not self.snapshots_dir.exists():
            print("No snapshots yet.")
            return []

        timeline: List[Dict[str, Any]] = []
        prev_hash = None

        for snap_file in sorted(self.snapshots_dir.glob("SNAP-*.json")):
            try:
                with open(snap_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            snap_id = data.get("snapshot_id", snap_file.stem)
            snap_time = data.get("timestamp", "")
            files = data.get("files", {})

            found = None
            for p in possible_paths:
                if p in files:
                    found = (p, files[p])
                    break

            if found:
                rel_path, fdata = found
                current_hash = fdata.get("hash", "")
                change_type = "new" if prev_hash is None else (
                    "modified" if current_hash != prev_hash else "unchanged"
                )
                timeline.append({
                    "snapshot_id": snap_id,
                    "timestamp": snap_time[:19],
                    "file": rel_path,
                    "fact_count": fdata.get("fact_count", 0),
                    "size": fdata.get("size", 0),
                    "hash": current_hash,
                    "summary": fdata.get("summary", "")[:100],
                    "change_type": change_type,
                })
                prev_hash = current_hash
            else:
                if prev_hash is not None:
                    timeline.append({
                        "snapshot_id": snap_id,
                        "timestamp": snap_time[:19],
                        "file": "",
                        "fact_count": 0,
                        "size": 0,
                        "hash": "",
                        "summary": "",
                        "change_type": "deleted",
                    })
                    prev_hash = None

        if not timeline:
            print(f"🕰️ No history found for '{name}' across snapshots.")
        else:
            print(f"🕰️ Entity timeline for '{name}' ({len(timeline)} snapshots):")
            for t in timeline:
                icon = {"new": "🆕", "modified": "✏️", "unchanged": "📁", "deleted": "❌"}.get(t["change_type"], "?")
                print(f"  {icon} {t['snapshot_id']} ({t['timestamp']}) — {t['fact_count']} facts, {t['size']:,} bytes")
                if t.get("summary") and t["change_type"] != "unchanged":
                    print(f"     {t['summary'][:80]}")

        return timeline
