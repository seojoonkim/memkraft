"""Internal detection/extraction helpers extracted from core.py (WS-A v2.8).

These were originally MemKraft._<name> methods. Moved to free
functions to reduce core.py size and clarify responsibility.
Public MemKraft API is unchanged — methods now delegate here.
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._regexes import (
    _SOURCE_TAG_RE,
    _FIELD_KV_RE,
    _FACT_REGISTRY_LINE_RE,
    _STATE_ROLE_RE,
    _STATE_ROLE_IS_RE,
    _STATE_AFFIL_RE,
    _STATE_AFFIL_VERB_RE,
    _STATE_STATUS_RE,
    _STATE_LOCATION_RE,
    _STATE_LOCATION_IS_RE,
    _CONFLICT_TAG_RE,
    _TIER_RE,
)


# ── Conflict detection ─────────────────────────────────────
def is_opposing(old: str, new: str) -> bool:
    """Detect if two facts are opposing/contradictory."""
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

    old_match = _FIELD_KV_RE.search(old)
    new_match = _FIELD_KV_RE.search(new)
    if old_match and new_match:
        if old_match.group(1).lower() == new_match.group(1).lower():
            if old_match.group(2).strip().lower() != new_match.group(2).strip().lower():
                return True

    sim = SequenceMatcher(None, old, new).ratio()
    if sim > 0.6 and sim < 0.95:
        old_tokens = old.split()
        new_tokens = new.split()
        if len(old_tokens) >= 3 and len(new_tokens) >= 3:
            if old_tokens[:2] == new_tokens[:2] and old_tokens[2:] != new_tokens[2:]:
                return True

    return False


def extract_bullet_facts(content: str) -> List[str]:
    """Extract bullet-point facts from Key Points and Timeline sections."""
    from ._regexes import _CONFLICT_TAG_RE, _DATE_BULLET_RE, _PENDING_BULLET_RE
    facts = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 10:
            clean = _SOURCE_TAG_RE.sub('', stripped)
            clean = _CONFLICT_TAG_RE.sub('', clean)
            clean = _DATE_BULLET_RE.sub('- ', clean)
            clean = _PENDING_BULLET_RE.sub('- ', clean)
            clean = clean.strip()
            if clean.startswith("- ") and len(clean) > 5:
                facts.append(clean[2:])  # Strip leading "- "
    return facts


def tag_conflict(filepath: Path, old_fact: str, new_fact: str, source: str) -> None:
    """Tag a conflict in the entity file: keep both, mark with [CONFLICT]."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
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
    except Exception:
        pass


def write_conflicts_report(conflicts: List[Dict[str, Any]], base_dir: Path) -> Path:
    """Write or update CONFLICTS.md with detected conflicts."""
    conflicts_path = base_dir / "CONFLICTS.md"
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


# ── Fact extraction ────────────────────────────────────────
def extract_facts(text: str) -> List[Dict[str, str]]:
    """Extract key facts from text using regex patterns."""
    facts = []
    # Pattern: "X is Y", "X was Y", "X serves as Y", "X joined Y"
    fact_patterns = [
        r'([A-Z][a-z]+ [A-Z][a-z]+) (?:is|was|serves as|joined|became|founded|leads|runs|leads) (.+?)(?:\.|,|;|$)',
        r'([\uAC00-\uD7AF]{2,4})(?:은|는|이|가) (.+?)(?:이다|다|했다|임|됨|\.)',
    ]
    import re as _re
    for pattern in fact_patterns:
        for match in _re.finditer(pattern, text):
            entity_name = match.group(1).strip()
            fact_text = match.group(2).strip()
            if len(fact_text) > 5 and len(entity_name) > 1:
                facts.append({"entity": entity_name, "fact": fact_text, "type": "fact"})
    return facts


def resolve_extract_input(
    input_text: str,
    safe_read_fn: Callable[[Path], str],
    base_dir: Path,
) -> Tuple[str, str]:
    """Resolve extract input from a file path, literal text, or stdin."""
    import sys
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


def extract_registry_facts(text: str) -> List[str]:
    """Extract numeric/date facts for the cross-domain fact registry."""
    from ._regexes import _MONEY_RE, _PERCENT_RE, _COUNT_ITEMS_RE, _DATE_YYYYMMDD_RE
    facts = []
    for pattern in [_MONEY_RE, _PERCENT_RE, _COUNT_ITEMS_RE]:
        for m in pattern.finditer(text):
            facts.append(m.group().strip())

    for m in _DATE_YYYYMMDD_RE.finditer(text):
        start = max(0, m.start() - 20)
        prefix = text[start:m.start()].lower()
        if "source" in prefix or "update" in prefix or "started" in prefix or "**" in prefix:
            continue
        facts.append(m.group())

    return list(dict.fromkeys(facts))


def write_fact_registry(facts: list, base_dir: Path, source: str = "") -> List[str]:
    """Append de-duplicated facts to fact-registry.md."""
    from ._regexes import _FACT_REGISTRY_LINE_RE
    clean_facts = [f.strip() for f in facts if f and f.strip()]
    if not clean_facts:
        return []

    registry = base_dir / "fact-registry.md"
    registry.parent.mkdir(parents=True, exist_ok=True)
    existing = registry.read_text(encoding="utf-8", errors="replace") if registry.exists() else "# Fact Registry\n\nCross-domain index of concrete data points.\n\n"
    existing_facts = set(_FACT_REGISTRY_LINE_RE.findall(existing))
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


# ── State changes ──────────────────────────────────────────
def apply_state_changes(content: str, info: str) -> Tuple[str, List[str]]:
    """Apply state changes from info text to content. Returns (new_content, changes)."""
    from ._regexes import _SECTION_HEADER_RE, _NEXT_SECTION_RE, _PLACEHOLDER_RE
    candidates = extract_state_candidates(info)
    if not candidates:
        return content, []

    section_match = _SECTION_HEADER_RE.search(content)
    if not section_match:
        return content, []

    section_start = section_match.end()
    next_section = _NEXT_SECTION_RE.search(content[section_start:])
    section_end = section_start + next_section.start() if next_section else len(content)
    section = content[section_start:section_end]
    transitions = []

    for field, new_value in candidates.items():
        field_pattern = re.compile(rf'(^- \*\*{re.escape(field)}:\*\* )(.*)$', re.MULTILINE)
        match = field_pattern.search(section)
        if match:
            old_value = match.group(2).strip()
            if is_material_state_change(old_value, new_value):
                transitions.append(f"{field} changed from `{old_value}` to `{new_value}`")
            section = section[:match.start(2)] + new_value + section[match.end(2):]
            continue

        placeholder_match = _PLACEHOLDER_RE.search(section)
        insertion = f"- **{field}:** {new_value}\n"
        if placeholder_match:
            section = section[:placeholder_match.start()] + insertion + section[placeholder_match.end():]
        else:
            section = insertion + section

    return content[:section_start] + section + content[section_end:], transitions


def extract_state_candidates(info: str) -> Dict[str, str]:
    """Extract simple state fields from update text."""
    fields = {
        "Role": [_STATE_ROLE_RE, _STATE_ROLE_IS_RE],
        "Affiliation": [_STATE_AFFIL_RE, _STATE_AFFIL_VERB_RE],
        "Status": [_STATE_STATUS_RE],
        "Location": [_STATE_LOCATION_RE, _STATE_LOCATION_IS_RE],
    }
    candidates = {}
    for field, patterns in fields.items():
        for pattern in patterns:
            match = pattern.search(info)
            if match:
                value = match.group(1).strip(" .;")
                if value:
                    candidates[field] = value[:200]
                break
    return candidates


def is_material_state_change(old_value: str, new_value: str) -> bool:
    """Check if a state change is material (not just formatting)."""
    if not old_value or not new_value:
        return True
    old_clean = old_value.lower().strip("() ")
    new_clean = new_value.lower().strip("() ")
    if old_clean == new_clean:
        return False
    if old_clean in ("enrichment needed", "unknown", ""):
        return True
    return True


# ── Fact appending ─────────────────────────────────────────
def append_fact(
    entity_name: str,
    fact: str,
    source: str = "",
    confidence: str = "experimental",
    applicability: str = "",
    slugify_fn: Callable[[str], str] = None,
    live_notes_dir: Path = None,
    entities_dir: Path = None,
) -> None:
    """Append a fact to an entity's live note."""
    slug = slugify_fn(entity_name) if slugify_fn else entity_name.lower().replace(" ", "-")
    conf_tag = f" | Confidence: {confidence}" if confidence else ""
    app_tag = f" | {applicability}" if applicability else ""
    for directory in [live_notes_dir, entities_dir]:
        if directory is None:
            continue
        filepath = directory / f"{slug}.md"
        if filepath.exists():
            now = datetime.now().strftime("%Y-%m-%d")
            content = filepath.read_text(encoding="utf-8", errors="replace")
            for marker in ["## Key Points\n", "## 키 포인트\n"]:
                if marker in content:
                    content = content.replace(marker, f"{marker}- {fact} [Source: {source}{conf_tag}]{app_tag}\n")
                    break
            else:
                for marker in ["## Timeline\n\n", "## Timeline (Full Record)\n\n"]:
                    if marker in content:
                        content = content.replace(marker, f"{marker}- **{now}** | {fact} [Source: {source}{conf_tag}]{app_tag}\n")
                        break
            filepath.write_text(content, encoding="utf-8")
            return


# ── Content classification ─────────────────────────────────
def classify_content(content: str) -> str:
    """Classify content based on heuristics."""
    lower = content.lower()
    if any(kw in lower for kw in ["decided", "decision", "chose", "agreed"]):
        return "decision"
    if any(kw in lower for kw in ["todo", "task", "action item", "need to", "must"]):
        return "task"
    if any(kw in lower for kw in ["ceo", "cto", "founder", "investor", "director"]):
        return "entity"
    return "entity"


def route_to_dir(route: str, dirs: Dict[str, Path]) -> Optional[Path]:
    """Map classification to directory."""
    return dirs.get(route)


# ── Compression suggestion ─────────────────────────────────
def compression_suggestion(
    md: Path,
    size: int,
    safe_read_fn: Callable[[Path], str],
) -> str:
    """Generate an actionable compression suggestion for a bloated page."""
    content = safe_read_fn(md)
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
