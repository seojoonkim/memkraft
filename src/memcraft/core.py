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
        # 한국어 동사/형용사 어미 제거 후 이름만 추출
        korean_names_cleaned = []
        for name in korean_names:
            # 동사 어미 제거: 했다, 한다, 해요, 함, 됨, 됐다, etc.
            stripped = re.sub(r'(했|할|해|되|됐|받|만|지|보|주|가|오|알|인|있|없|갈|될|할|만들|사용|개발|적용|설정|확인|업데이트|추가|수정|삭제|생성|실행|테스트|분석|검색|연결|설치|시작|완료|진행|보고|논의|발표|참여|준비|요청|제안|검토|승인|거절|검증|배포|구축|도입|운영|관리|모니터링|추적|감지|정리|보강|업그레이드|마이그레이션)(다|해|함|요|서|고|며|니|까|지|은|는|이|을|를|와|과|도|만|로|으로|라|라서)?$', '', name)
            if len(stripped) >= 2:
                korean_names_cleaned.append(stripped)
        korean_names = korean_names_cleaned
        # 중국어/일본어 한자 패턴 (한자에는 단어 경계가 없어서 연속 추출)
        stopwords = self._load_stopwords()
        korean_stopwords = set(stopwords.get("korean", []))
        chinese_stopwords = set(stopwords.get("chinese", []))
        japanese_stopwords = set(stopwords.get("japanese", []))

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
                # 한국어 조사 제거: 이, 을, 를, 은, 는, 에, 에서, 로, 으로, 와, 과, 도, 만, 이라, 이라서
                stripped = re.sub(r'([가-힣]+?)([이을를은는에로으와과도만이라서는]+)$', r'\1', name)
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
                            if candidate not in chinese_stopwords and candidate not in seen_chinese:
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
                            if candidate not in chinese_stopwords:
                                entities.append({"name": candidate, "type": "person", "context": "auto-detected (Japanese)"})
                                break

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

    def _load_stopwords(self) -> dict:
        """불용어를 JSON 파일에서 로드 (캐시)"""
        if not hasattr(self, '_stopwords_cache'):
            sw_path = Path(__file__).parent / "stopwords.json"
            if sw_path.exists():
                with open(sw_path, 'r', encoding='utf-8') as f:
                    self._stopwords_cache = json.load(f)
            else:
                self._stopwords_cache = {"korean": [], "chinese": [], "japanese": []}
        return self._stopwords_cache
