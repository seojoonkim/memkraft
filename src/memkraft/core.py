#!/usr/bin/env python3
"""MemKraft Core — Memory operations and management.

Zero-dependency compound knowledge system for AI agents.
Supports entity tracking, fact extraction, dream-cycle maintenance,
hybrid search (exact + IDF-weighted + fuzzy), and agentic multi-hop search.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
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

    # ── Init ──────────────────────────────────────────────────
    def init(self, path: str = "") -> None:
        if path:
            target = Path(path) / "memory"
        else:
            target = self.base_dir
        target.mkdir(parents=True, exist_ok=True)
        for subdir in ["entities", "live-notes", "decisions", "originals", "inbox", "tasks", "meetings", "sessions"]:
            (target / subdir).mkdir(exist_ok=True)

        # RESOLVER.md
        resolver_path = target / "RESOLVER.md"
        if not resolver_path.exists():
            shutil.copy2(Path(__file__).parent / "templates" / "RESOLVER.md", resolver_path)

        # TEMPLATES.md
        templates_path = target / "TEMPLATES.md"
        if not templates_path.exists():
            shutil.copy2(Path(__file__).parent / "templates" / "TEMPLATES.md", templates_path)

        print(f"✅ MemKraft initialized at {target}")
        print("   Directories: entities/, live-notes/, decisions/, originals/, inbox/, tasks/, meetings/, sessions/")
        print("   Files: RESOLVER.md, TEMPLATES.md")

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
    def brief(self, name: str, save: bool = False) -> None:
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
    def dream(self, date: str = None, dry_run: bool = False) -> Dict[str, Any]:
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
                if md.stat().st_size < 300:
                    issues["thin_entities"] += 1
                    details["thin_entities"].append(str(md.relative_to(self.base_dir)))

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
                age_hours = (now_ts - md.stat().st_mtime) / 3600
                if age_hours > 48:
                    issues["inbox_overdue"] += 1
                    details["inbox_overdue"].append(f"{md.name} ({age_hours:.0f}h)")

        # Check for bloated entity pages (auto-compact)
        # Inspired by Recursive Language Models (arXiv:2512.24601):
        # bloated pages waste context window — flag for compaction
        print("   🔍 Scanning for bloated pages (auto-compact candidates)...")
        for md in self._all_md_files():
            size = md.stat().st_size
            if size > 4000:  # >4KB suggests Compiled Truth needs condensing
                issues["bloated_pages"] += 1
                rel = md.relative_to(self.base_dir)
                suggestion = self._compression_suggestion(md, size)
                details["bloated_pages"].append(f"{rel} ({size}B) — {suggestion}")
                if issues["bloated_pages"] <= 5:
                    print(f"      ⚠️ {rel} ({size}B) — {suggestion}")

        total = sum(issues.values())
        print(f"\n🌙 Dream Cycle complete: {total} total issues found")
        print(f"   Incomplete sources: {issues['incomplete_sources']}")
        print(f"   Source-less facts: {issues['sourceless_facts']}")
        print(f"   Thin entities: {issues['thin_entities']}")
        print(f"   Duplicate entities: {issues['duplicate_entities']}")
        print(f"   Inbox overdue: {issues['inbox_overdue']}")
        print(f"   Bloated pages: {issues['bloated_pages']}")

        if not dry_run:
            meta_dir = self.base_dir / ".memkraft"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "last-dream-timestamp").write_text(str(datetime.now().timestamp()), encoding="utf-8")

        return {"issues": issues, "details": details, "total": total}

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

    # ── Extract ──────────────────────────────────────────────
    def extract(self, text: str, source: str = "", dry_run: bool = False) -> List[Dict[str, Any]]:
        """Auto-extract entities and facts from text, write to memory."""
        return self.extract_conversations(text, source=source, dry_run=dry_run)

    def extract_conversations(self, input_text: str = "", source: str = "", dry_run: bool = False) -> List[Dict[str, Any]]:
        """Auto-extract entities/facts from markdown text, file path, or stdin."""
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

        for f in facts:
            f["source"] = resolved_source
            if dry_run:
                f["action"] = "would_append"
            else:
                self._append_fact(f["entity"], f["fact"], resolved_source)
                f["action"] = "appended"
            results.append(f)

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

    def _append_fact(self, entity_name: str, fact: str, source: str = ""):
        """Append a fact to an entity's live note."""
        slug = self._slugify(entity_name)
        # Try live-notes first, then entities
        for directory in [self.live_notes_dir, self.entities_dir]:
            filepath = directory / f"{slug}.md"
            if filepath.exists():
                now = datetime.now().strftime("%Y-%m-%d")
                content = filepath.read_text(encoding="utf-8", errors="replace")
                # Add to Key Points section
                for marker in ["## Key Points\n", "## 키 포인트\n"]:
                    if marker in content:
                        content = content.replace(marker, f"{marker}- {fact} [Source: {source}]\n")
                        break
                else:
                    # Add to timeline if no Key Points section
                    for marker in ["## Timeline\n\n", "## Timeline (Full Record)\n\n"]:
                        if marker in content:
                            content = content.replace(marker, f"{marker}- **{now}** | {fact} [Source: {source}]\n")
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
            mtime = md.stat().st_mtime
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
                mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
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
            if md.stat().st_mtime > since:
                changed_files.append(str(md.relative_to(self.base_dir)))

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
            mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
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
            mtime = datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d")
            index[rel] = {
                "date": mtime,
                "summary": summary[:200],
                "tags": tags,
                "sections": sections[:20],
                "size": md.stat().st_size,
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

    # ── Memory Decay ────────────────────────────────────────────
    def decay(self, days: int = 90, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Downgrade stale facts older than N days. Reduces noise in search results."""
        threshold = datetime.now() - timedelta(days=days)
        results = []

        for md in self._all_md_files():
            content = self._safe_read(md)
            if not content:
                continue
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
                            results.append({"file": str(md.relative_to(self.base_dir)), "line": line.strip()[:80], "age_days": (datetime.now() - entry_date).days})
                        else:
                            line = line.replace("- **", "- ⏳ ", 1)
                            modified = True
                new_lines.append(line)

            if modified and not dry_run:
                md.write_text("\n".join(new_lines), encoding="utf-8")

        if results:
            action = "would flag" if dry_run else "flagged"
            print(f"📉 Decay: {action} {len(results)} entries older than {days} days")
            for r in results[:10]:
                print(f"  [{r['age_days']}d] {r['file']}: {r['line']}")
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
                if md.stat().st_size > max_length * 3:
                    targets.append(md.stem.replace("-", " "))

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

    # ── Agentic Search ──────────────────────────────────────────
    def agentic_search(self, query: str, max_hops: int = 2, json_output: bool = False) -> List[Dict[str, Any]]:
        """Multi-step search: decompose query → search → traverse links → re-rank → check sufficiency."""
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

        # Step 4: Contextual Re-ranking
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
                        from datetime import datetime
                        latest = max(dates)
                        days_old = (datetime.now() - datetime.strptime(latest, "%Y-%m-%d")).days
                        if days_old < 7:
                            bonus += 0.05
                    except (ValueError, ImportError):
                        pass
            r["score"] = round(min(1.0, r.get("score", 0) + bonus), 2)

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
            print(f"🔍 Agentic search: {len(merged)} results for '{query}' (decomposed into {len(sub_queries)} queries, {max_hops} hops)")
            for r in merged[:10]:
                hop_marker = " ↳" if r.get("hop") else ""
                snippet = f"\n     {r['snippet'][:80]}" if r.get('snippet') else ""
                print(f"  [{r['score']:.2f}] {r['file']}{snippet}{hop_marker}")

        if json_output:
            return merged
        return merged

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
        for subdir in [self.entities_dir, self.live_notes_dir, self.decisions_dir, self.originals_dir, self.inbox_dir, self.tasks_dir, self.meetings_dir]:
            if subdir.exists():
                yield from subdir.glob("*.md")
        # Include daily notes and base-dir markdown files (exclude system/auto-generated files)
        _system_files = {"RESOLVER.md", "TEMPLATES.md", "open-loops.md", "fact-registry.md"}
        if self.base_dir.exists():
            for md in self.base_dir.glob("*.md"):
                if md.name not in _system_files:
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
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
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
