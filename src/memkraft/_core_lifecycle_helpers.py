"""Internal lifecycle/utility helpers extracted from core.py (WS-A v2.8).

These were originally MemKraft._<name> methods. Moved to free
functions to reduce core.py size and clarify responsibility.
Public MemKraft API is unchanged — methods now delegate here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._regexes import (
    _SLUG_NONWORD_RE,
    _SLUG_WHITESPACE_RE,
)


# ── JSON helpers ───────────────────────────────────────────
def json_load(filepath: Path) -> Dict[str, Any]:
    """Load a JSON file, returning empty dict if missing or invalid."""
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def json_save(filepath: Path, data: Dict[str, Any]) -> None:
    """Save data to a JSON file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Slugify ────────────────────────────────────────────────
def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.strip().lower()
    text = _SLUG_NONWORD_RE.sub('', text)
    text = _SLUG_WHITESPACE_RE.sub('-', text)
    return text[:80]


# ── Korean josa stripping ──────────────────────────────────
def strip_korean_josa(name: str, josa_list: List[str]) -> str:
    """Strip Korean particle (조사) suffixes from a name."""
    for josa in josa_list:
        if name.endswith(josa):
            stripped = name[:-len(josa)]
            if len(stripped) >= 2:
                return stripped
    return name


# ── Section extraction ─────────────────────────────────────
def extract_section(content: str, section_name: str) -> str:
    """Extract a named section from markdown content."""
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


# ── File iteration ─────────────────────────────────────────
def all_md_files(dirs: List[Path], base_dir: Path):
    """Yield all markdown files from the given directories and base_dir."""
    _system_files = {"RESOLVER.md", "TEMPLATES.md", "open-loops.md", "fact-registry.md"}
    seen = set()
    for subdir in dirs:
        if subdir.exists():
            for md in subdir.glob("*.md"):
                if not md.is_symlink() and md not in seen:
                    seen.add(md)
                    yield md
    if base_dir.exists():
        for md in base_dir.glob("*.md"):
            if md.name not in _system_files and not md.is_symlink() and md not in seen:
                seen.add(md)
                yield md


# ── Touch last accessed ────────────────────────────────────
def touch_last_accessed(base_dir: Path, rel_path: str, timestamp: str) -> None:
    """Update 'Last Accessed' timestamp in an entity/note file."""
    if not rel_path:
        return
    try:
        full_path = base_dir / rel_path
        if not full_path.exists() or not full_path.is_file():
            return
        content = full_path.read_text(encoding="utf-8", errors="replace")
        pattern = r'\*\*Last Accessed:\*\*\s*.*'
        replacement = f'**Last Accessed:** {timestamp}'
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
        else:
            last_update_pattern = r'(\*\*Last Update:\*\*\s*[^\n]+)'
            m = re.search(last_update_pattern, content)
            if m:
                insert_pos = m.end()
                content = content[:insert_pos] + f'\n- **Last Accessed:** {timestamp}' + content[insert_pos:]
            else:
                tc_pattern = r'(## Tracking Config\n)'
                m2 = re.search(tc_pattern, content)
                if m2:
                    insert_pos = m2.end()
                    content = content[:insert_pos] + f'- **Last Accessed:** {timestamp}\n' + content[insert_pos:]
        full_path.write_text(content, encoding="utf-8")
    except Exception:
        pass


# ── Gather memory files ────────────────────────────────────
def gather_memory_files(
    all_md_files_fn: Callable[[], Any],
    recent: int = 0,
    tag: str = "",
    date: str = "",
) -> List[Path]:
    """Gather memory files with optional filters."""
    files = list(all_md_files_fn())
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


# ── Version / hash ─────────────────────────────────────────
def get_version() -> str:
    """Return the package version without circular imports."""
    try:
        from memkraft import __version__
        return __version__
    except Exception:
        return "unknown"


def file_hash(path: Path) -> str:
    """SHA-256 of a file's content, truncated to 12 hex chars."""
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return "error"
    return h.hexdigest()[:12]


# ── Debug helpers ──────────────────────────────────────────
def get_debug_file(bug_id: str, debug_dir: Path) -> Optional[Path]:
    """Get the file path for a debug session."""
    filepath = debug_dir / f"{bug_id}.md"
    if filepath.exists():
        return filepath
    return None


def update_debug_status(content: str, new_status: str) -> str:
    """Update the status field in a debug session file."""
    return re.sub(
        r'\*\*Status:\*\* \w+',
        f'**Status:** {new_status}',
        content,
        count=1,
    )


def append_debug_timeline(content: str, entry: str) -> str:
    """Append an entry to the Timeline section of a debug session."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    timeline_entry = f"- **{now}** | {entry}\n"
    marker = "## Timeline\n"
    if marker in content:
        content = content.replace(marker, f"{marker}{timeline_entry}")
    return content


# ── Entity creation ────────────────────────────────────────
def create_entity(
    name: str,
    entity_type: str = "person",
    source: str = "",
    entities_dir: Path = None,
    slugify_fn: Callable[[str], str] = None,
) -> None:
    """Create a new entity file."""
    if entities_dir is None:
        return
    entities_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify_fn(name) if slugify_fn else slugify(name)
    filepath = entities_dir / f"{slug}.md"

    if filepath.exists():
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


def load_snapshot(snapshot_id: str, snapshots_dir: Path) -> Optional[Dict[str, Any]]:
    """Load a snapshot by ID or partial match."""
    if not snapshots_dir.exists():
        return None
    exact = snapshots_dir / f"{snapshot_id}.json"
    if exact.exists():
        return json_load(exact)
    for snap_file in sorted(snapshots_dir.glob("SNAP-*.json"), reverse=True):
        if snapshot_id in snap_file.stem:
            return json_load(snap_file)
        data = json_load(snap_file)
        if data.get("label", "") == snapshot_id:
            return data
    return None
