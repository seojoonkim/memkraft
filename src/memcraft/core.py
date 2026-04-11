#!/usr/bin/env python3
"""MemCraft Core — Memory operations and management"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


class MemCraft:
    """The compound knowledge system for AI agents"""

    def __init__(self, base_dir: str = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(os.environ.get("MEMCRAFT_DIR", Path.cwd() / "memory"))
        self.entities_dir = self.base_dir / "entities"
        self.live_notes_dir = self.base_dir / "live-notes"
        self.decisions_dir = self.base_dir / "decisions"
        self.originals_dir = self.base_dir / "originals"
        self.inbox_dir = self.base_dir / "inbox"
        self.tasks_dir = self.base_dir / "tasks"
        self.meetings_dir = self.base_dir / "meetings"

    # ── Init ──────────────────────────────────────────────────
    def init(self, path: str = "."):
        target = Path(path) / "memory"
        target.mkdir(parents=True, exist_ok=True)
        for subdir in ["entities", "live-notes", "decisions", "originals", "inbox", "tasks", "meetings"]:
            (target / subdir).mkdir(exist_ok=True)

        # RESOLVER.md
        resolver_path = target / "RESOLVER.md"
        if not resolver_path.exists():
            shutil.copy2(Path(__file__).parent / "templates" / "RESOLVER.md", resolver_path)

        # TEMPLATES.md
        templates_path = target / "TEMPLATES.md"
        if not templates_path.exists():
            shutil.copy2(Path(__file__).parent / "templates" / "TEMPLATES.md", templates_path)

        print(f"✅ MemCraft initialized at {target}")
        print("   Directories: entities/, live-notes/, decisions/, originals/, inbox/, tasks/, meetings/")
        print("   Files: RESOLVER.md, TEMPLATES.md")

    # ── Track ─────────────────────────────────────────────────
    def track(self, name: str, entity_type: str = "person", source: str = ""):
        self.live_notes_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slugify(name)
        filepath = self.live_notes_dir / f"{slug}.md"

        if filepath.exists():
            print(f"⚠️ Already tracking: {filepath}")
            print(f"   Use 'memcraft update \"{name}\" --info \"...\"' to add info")
            return

        now = datetime.now().strftime("%Y-%m-%d")
        content = f"""# {name} (Live Note)

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
- [[{slug}]]

## Open Threads
- [ ] Initial setup — enrichment needed

---

## Timeline (Full Record)

- **{now}** | Live note created [Source: {source or 'Manual'}]
"""
        filepath.write_text(content)
        print(f"✅ Tracking: {filepath}")

    # ── Update ────────────────────────────────────────────────
    def update(self, name: str, info: str, source: str = "manual"):
        slug = self._slugify(name)
        filepath = self.live_notes_dir / f"{slug}.md"

        if not filepath.exists():
            print(f"⚠️ Not tracking '{name}'. Use 'memcraft track' first.")
            return

        content = filepath.read_text()
        now = datetime.now().strftime("%Y-%m-%d")

        # Increment update count (English + Korean)
        count_match = re.search(r'(?:Update Count|업데이트 횟수):\*\* (\d+)', content)
        if count_match:
            new_count = int(count_match.group(1)) + 1
            old_str = count_match.group(0)
            new_str = old_str.replace(str(count_match.group(1)), str(new_count))
            content = content.replace(old_str, new_str)

        # Update last update date (English + Korean)
        last_match = re.search(r'(?:Last Update|마지막 업데이트):\*\* \d{4}-\d{2}-\d{2}', content)
        if last_match:
            content = content.replace(last_match.group(), re.sub(r'\d{4}-\d{2}-\d{2}', now, last_match.group()))

        # Add to Recent Activity (English + Korean)
        for marker in ["## Recent Activity", "## 최근 동향"]:
            recent_idx = content.find(marker)
            if recent_idx != -1:
                insert_pos = content.find("\n", content.find("\n", recent_idx) + 1) + 1
                content = content[:insert_pos] + f"- **{now}** | {info} [Source: {source}]\n" + content[insert_pos:]
                break

        # Add to Timeline (English + Korean)
        for marker in ["## Timeline (Full Record)\n\n", "## 타임라인 (전체 기록)\n\n", "## Timeline\n\n"]:
            if marker in content:
                content = content.replace(
                    marker,
                    f"{marker}- **{now}** | {info} [Source: {source}]\n\n"
                )
                break

        filepath.write_text(content)
        print(f"✅ Updated: {filepath}")

    # ── List ──────────────────────────────────────────────────
    def list_entities(self):
        if not self.live_notes_dir.exists():
            print("No tracked entities. Use 'memcraft track' to start.")
            return

        found = False
        for md in sorted(self.live_notes_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            content = md.read_text()
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

        if not found:
            print("No tracked entities. Use 'memcraft track' to start.")

    # ── Brief ─────────────────────────────────────────────────
    def brief(self, name: str, save: bool = False):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        slug = self._slugify(name)
        brief_parts = [f"# 📋 Meeting Brief: {name}", f"Generated: {now}", ""]

        # Entity page
        entity_path = self.entities_dir / f"{slug}.md"
        if entity_path.exists():
            content = entity_path.read_text()
            brief_parts.append("## 👤 Entity Info")
            if "---" in content:
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
            brief_parts.append(f"## ⚠️ Entity '{name}' not found")
            brief_parts.append("   → Use `memcraft track` or `memcraft detect` to create")
            brief_parts.append("")

        # Live note
        live_path = self.live_notes_dir / f"{slug}.md"
        if live_path.exists():
            content = live_path.read_text()
            brief_parts.append("## 🔄 Live Note")
            for section in ["Current State", "현재 상태", "Key Points", "키 포인트", "Recent Activity", "최근 동향"]:
                text = self._extract_section(content, section)
                if text:
                    brief_parts.append(f"**{section}:** {text[:300]}")
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
        brief_parts.append(f"*Auto-generated by MemCraft | `memcraft brief \"{name}\"`*")

        output = "\n".join(brief_parts)
        print(output)

        if save:
            save_path = self.meetings_dir / f"{datetime.now().strftime('%Y-%m-%d')}-{slug}-brief.md"
            self.meetings_dir.mkdir(parents=True, exist_ok=True)
            save_path.write_text(output)
            print(f"\n💾 Saved: {save_path}")

    # ── Detect ────────────────────────────────────────────────
    def detect(self, text: str, source: str = "", no_llm: bool = False, dry_run: bool = False):
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
    def dream(self, date: str = None, dry_run: bool = False):
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        print(f"🌙 Dream Cycle — {target_date}")
        print(f"   Mode: {'dry-run' if dry_run else 'live'}")

        issues = {
            "incomplete_sources": 0,
            "thin_entities": 0,
            "duplicate_entities": 0,
            "inbox_overdue": 0,
        }

        # Check for incomplete source attributions
        print("   🔍 Scanning for incomplete source attributions...")
        for md in self._all_md_files():
            content = md.read_text()
            if "## Timeline" in content:
                section = content.split("## Timeline")[1].split("\n## ")[0]
                for line in section.split("\n"):
                    if line.strip().startswith("- **") and "[Source:" not in line and "{" not in line:
                        issues["incomplete_sources"] += 1

        # Check for thin entity pages
        print("   🔍 Scanning for thin entity pages...")
        if self.entities_dir.exists():
            for md in self.entities_dir.glob("*.md"):
                if md.stat().st_size < 300:
                    issues["thin_entities"] += 1

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

        total = sum(issues.values())
        print(f"\n🌙 Dream Cycle complete: {total} total issues found")
        print(f"   Incomplete sources: {issues['incomplete_sources']}")
        print(f"   Thin entities: {issues['thin_entities']}")
        print(f"   Inbox overdue: {issues['inbox_overdue']}")

    # ── Lookup ────────────────────────────────────────────────
    def lookup(self, query: str, json_output: bool = False):
        results = []

        # Search entities
        if self.entities_dir.exists():
            for md in self.entities_dir.glob("*.md"):
                content = md.read_text().lower()
                if query.lower() in content:
                    results.append({"source": "entity", "file": md.stem, "relevance": "high"})

        # Search live notes
        if self.live_notes_dir.exists():
            for md in self.live_notes_dir.glob("*.md"):
                content = md.read_text().lower()
                if query.lower() in content:
                    results.append({"source": "live-note", "file": md.stem, "relevance": "high"})

        # Search decisions
        if self.decisions_dir.exists():
            for md in self.decisions_dir.glob("*.md"):
                content = md.read_text().lower()
                if query.lower() in content:
                    results.append({"source": "decision", "file": md.stem, "relevance": "medium"})

        # Search inbox
        if self.inbox_dir.exists():
            for md in self.inbox_dir.glob("*.md"):
                content = md.read_text().lower()
                if query.lower() in content:
                    results.append({"source": "inbox", "file": md.stem, "relevance": "low"})

        if json_output:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            if not results:
                print(f"No results for '{query}'. Consider web search as fallback.")
            else:
                for r in results:
                    print(f"  [{r['relevance']}] {r['source']}: {r['file']}")

    # ── Helpers ───────────────────────────────────────────────
    def _slugify(self, text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r'[^\w\s\-\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]', '', text)
        text = re.sub(r'\s+', '-', text)
        return text[:80]

    def _detect_regex(self, text: str) -> list:
        entities = []
        common = {'The', 'This', 'That', 'And', 'But', 'For', 'Not', 'All', 'Has', 'Was', 'Are', 'Its', 'Our', 'Their', 'Yc', 'From', 'With'}

        names_2 = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', text)
        names_3 = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+)\b', text)
        korean_names = re.findall(r'[\uAC00-\uD7AF]{2,4}', text)
        korean_stopwords = {'했다', '한다', '했어', '했음', '이는', '그는', '그녀', '우리', '그들', '이것', '저것', '모든', '위해', '하여', '하고', '에서', '으로', '로서', '대표로서', '이나', '지만', '면서', '부터', '까지', '같은', '대한', '관한', '통해', '오픈', '소스', '오픈소스', '논의', '논의함', '보고', '보고서', '시작', '완료', '진행', '결과', '기반', '경우', '상황', '현재', '이후', '이전', '오늘', '내일', '어제', '정도', '부분', '가능', '필요', '생각', '문제', '방법', '개발', '적용', '설정', '확인', '업데이트', '추가', '수정', '삭제', '생성', '실행', '테스트', '분석', '검색', '연결', '설치'}

        names_3_words = set()
        for n in names_3:
            names_3_words.update(n.split())
        for name in set(names_3):
            if name not in common:
                entities.append({"name": name, "type": "person", "context": "auto-detected"})
        for name in set(names_2):
            if name not in common and name.split()[0] not in names_3_words and name.split()[1] not in names_3_words:
                entities.append({"name": name, "type": "person", "context": "auto-detected"})
        for name in set(korean_names):
            if len(name) >= 2 and name not in korean_stopwords:
                entities.append({"name": name, "type": "person", "context": "auto-detected (Korean)"})

        handles = re.findall(r'@(\w+)', text)
        for handle in set(handles):
            entities.append({"name": handle, "type": "person", "context": "mentioned via @handle"})

        return entities

    def _create_entity(self, name: str, entity_type: str = "person", source: str = ""):
        self.entities_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slugify(name)
        filepath = self.entities_dir / f"{slug}.md"

        if filepath.exists():
            # Append to timeline
            content = filepath.read_text()
            now = datetime.now().strftime("%Y-%m-%d")
            timeline_marker = "## Timeline\n\n"
            if timeline_marker in content:
                content = content.replace(
                    timeline_marker,
                    f"{timeline_marker}- **{now}** | Re-detected [Source: {source}]\n"
                )
                filepath.write_text(content)
            return

        now = datetime.now().strftime("%Y-%m-%d")
        content = f"""# {name}

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
        filepath.write_text(content)

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
        for subdir in [self.entities_dir, self.live_notes_dir, self.decisions_dir, self.inbox_dir, self.tasks_dir]:
            if subdir.exists():
                yield from subdir.glob("*.md")
