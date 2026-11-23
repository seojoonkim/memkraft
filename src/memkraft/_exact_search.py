"""Exact (grep-style) search over the raw memory files.

Ranked recall returns the best-matching snippets; this returns every literal
occurrence of a phrase, newest file first, so an agent can find the precise
past wording without shelling out to grep over the memory directory.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

TEXT_SUFFIXES = (".md", ".jsonl", ".json", ".txt")
MAX_LINE_CHARS = 300
MAX_LIMIT = 50


class ExactSearchError(ValueError):
    """Bad input to :func:`exact_search`."""


def _candidate_files(root: Path) -> List[tuple]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not name.endswith(TEXT_SUFFIXES):
                continue
            path = os.path.join(dirpath, name)
            try:
                found.append((os.stat(path).st_mtime, path))
            except OSError:
                continue
    found.sort(reverse=True)
    return found


def exact_search(base_dir: Any, query: str, *, folder: Optional[str] = None,
                 limit: int = 10, budget_s: float = 4.0,
                 max_line_chars: int = MAX_LINE_CHARS) -> Dict[str, Any]:
    """Return lines containing ``query`` (case-insensitive literal), newest file first.

    ``complete`` is False when the scan stopped early (limit reached or time
    budget spent), so more matches may exist; ``scanned_files``/``total_files``
    say how far it got.
    """
    needle = (query or "").strip()
    if len(needle) < 2:
        raise ExactSearchError("query must be at least 2 characters")
    base = Path(base_dir).resolve()
    root = base
    if folder:
        root = (base / folder).resolve()
        if root != base and base not in root.parents:
            raise ExactSearchError("folder must be inside the memory directory")
        if not root.is_dir():
            raise ExactSearchError("folder not found: {}".format(folder))
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    needle_b = needle.encode("utf-8").lower()
    files = _candidate_files(root)
    deadline = time.monotonic() + max(0.1, float(budget_s))
    results: List[Dict[str, Any]] = []
    scanned = 0
    complete = True
    for mtime, path in files:
        if len(results) >= limit or time.monotonic() > deadline:
            complete = False
            break
        scanned += 1
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if needle_b not in data.lower():
            continue
        rel = os.path.relpath(path, base)
        recorded = time.strftime("%Y-%m-%d", time.localtime(mtime))
        for number, raw in enumerate(data.split(b"\n"), 1):
            if needle_b not in raw.lower():
                continue
            text = raw.decode("utf-8", "replace").strip()
            results.append({"path": rel, "line": number, "recorded": recorded,
                            "text": text[:max_line_chars] if max_line_chars else text})
            if len(results) >= limit:
                complete = False  # stopped at the limit; more matches may exist
                break
    return {"results": results, "scanned_files": scanned, "total_files": len(files),
            "complete": complete}


__all__ = ["exact_search", "ExactSearchError"]
