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
import os as _os_walk  # local alias to avoid collision with module-level os later

_SYSTEM_FILES = frozenset({"RESOLVER.md", "TEMPLATES.md", "open-loops.md", "fact-registry.md"})


def _iter_md_via_scandir(directory: Path, skip_system: bool = False):
    """v2.8.4: os.scandir-based walker — single stat per entry.

    The legacy ``Path.glob("*.md")`` + ``is_symlink()`` path costs two
    ``lstat`` calls per file (one inside glob to materialise the Path,
    one for ``is_symlink``).  ``os.scandir`` exposes the dirent flags
    via :meth:`os.DirEntry.is_symlink` and :meth:`os.DirEntry.is_file`
    which on macOS/Linux read from the cached dirent (zero extra
    syscalls beyond the directory read).
    """
    try:
        it = _os_walk.scandir(directory)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return
    try:
        for entry in it:
            name = entry.name
            if not name.endswith(".md"):
                continue
            if skip_system and name in _SYSTEM_FILES:
                continue
            # is_symlink reads dirent on POSIX — no extra stat.
            try:
                if entry.is_symlink():
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            yield Path(entry.path)
    finally:
        try:
            it.close()
        except Exception:
            pass


def all_md_files(dirs: List[Path], base_dir: Path):
    """Yield all markdown files from the given directories and base_dir.

    v2.8.4: uses :func:`os.scandir` (single dirent read per file)
    instead of ``Path.glob`` + ``is_symlink`` (which double-stats).
    """
    seen = set()
    for subdir in dirs:
        for md in _iter_md_via_scandir(subdir, skip_system=False):
            if md not in seen:
                seen.add(md)
                yield md
    for md in _iter_md_via_scandir(base_dir, skip_system=True):
        if md not in seen:
            seen.add(md)
            yield md


# ── Touch last accessed ────────────────────────────────────
# v2.8.2 — per-process throttle so search hot loops don't write-on-read
# for the same paths repeatedly.  ``Last Accessed:`` is purely cosmetic
# (the markdown body field is *not* the YAML frontmatter ``last_accessed``
# that decay.py consumes), so coalescing repeats is safe.
import os as _os
import time as _time

_TOUCH_LAST_AT: "dict[Path, float]" = {}
try:
    _TOUCH_THROTTLE_SECONDS = float(_os.environ.get("MEMKRAFT_TOUCH_THROTTLE_SECONDS", "60"))
except ValueError:  # pragma: no cover
    _TOUCH_THROTTLE_SECONDS = 60.0
_TOUCH_DISABLED = _os.environ.get("MEMKRAFT_DISABLE_TOUCH_LAST_ACCESSED") == "1"
_TOUCH_CACHE_LIMIT = 4096  # cap so the throttle map can't grow unbounded


def _touch_should_skip(full_path: Path) -> bool:
    """True if this path was touched within the throttle window."""
    if _TOUCH_DISABLED:
        return True
    if _TOUCH_THROTTLE_SECONDS <= 0:
        return False
    now = _time.time()
    last = _TOUCH_LAST_AT.get(full_path)
    if last is not None and (now - last) < _TOUCH_THROTTLE_SECONDS:
        return True
    # Bound memory: if cache is full, drop the oldest entry.
    if len(_TOUCH_LAST_AT) >= _TOUCH_CACHE_LIMIT:
        try:
            oldest = min(_TOUCH_LAST_AT, key=_TOUCH_LAST_AT.get)  # type: ignore[arg-type]
            _TOUCH_LAST_AT.pop(oldest, None)
        except ValueError:
            pass
    _TOUCH_LAST_AT[full_path] = now
    return False


def _touch_reset_for_tests() -> None:
    """Test hook — clears the throttle map."""
    _TOUCH_LAST_AT.clear()


def touch_last_accessed(base_dir: Path, rel_path: str, timestamp: str) -> None:
    """Update 'Last Accessed' timestamp in an entity/note file.

    v2.8.2: throttled per-process to avoid write-on-read storms during
    repeated searches.  Window: ``MEMKRAFT_TOUCH_THROTTLE_SECONDS``
    (default 60s).  Set ``MEMKRAFT_DISABLE_TOUCH_LAST_ACCESSED=1`` to
    skip entirely.
    """
    if not rel_path:
        return
    try:
        full_path = base_dir / rel_path
        if _touch_should_skip(full_path):
            return
        if not full_path.exists() or not full_path.is_file():
            return
        content = full_path.read_text(encoding="utf-8", errors="replace")
        pattern = r'\*\*Last Accessed:\*\*\s*.*'
        replacement = f'**Last Accessed:** {timestamp}'
        if re.search(pattern, content):
            new_content = re.sub(pattern, replacement, content)
        else:
            last_update_pattern = r'(\*\*Last Update:\*\*\s*[^\n]+)'
            m = re.search(last_update_pattern, content)
            if m:
                insert_pos = m.end()
                new_content = content[:insert_pos] + f'\n- **Last Accessed:** {timestamp}' + content[insert_pos:]
            else:
                tc_pattern = r'(## Tracking Config\n)'
                m2 = re.search(tc_pattern, content)
                if m2:
                    insert_pos = m2.end()
                    new_content = content[:insert_pos] + f'- **Last Accessed:** {timestamp}\n' + content[insert_pos:]
                else:
                    new_content = content
        # Skip write if no actual change (cheap byte compare).
        if new_content == content:
            return
        full_path.write_text(new_content, encoding="utf-8")
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
