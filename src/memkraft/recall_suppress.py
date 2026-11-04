"""Recall-level forgetting: keep forgotten facts out of recalled snippets.

``forget`` and ``do_not_remember`` act on the structured event log, but a
Hermes agent recalls from markdown turn records (``artifacts/``,
``live-notes/``) that still quote the fact verbatim.  A forget request that
only tombstones an event therefore leaves the fact one recall away (the
BEMULA finding: confirmation is not deletion).

This module keeps a suppression list of *digests* of forgotten text — never
the text itself, so the list does not become a second copy of the secret —
and lets recall drop any snippet that still contains one.  Matching is on a
normalized form (casefold, collapsed whitespace).  Long values also register
their 12-character head and tail so a snippet cut through the middle of the
value is still caught.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MIN_CHARS = 6        # shorter values ("yes", "42") would suppress everything
EDGE_CHARS = 12      # head/tail window for values cut by snippet boundaries
EDGE_MIN_VALUE = 16  # only values this long register head/tail windows

_WS = re.compile(r"\s+")
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Tuple[Any, ...], Dict[int, Set[str]]]] = {}


def normalize(text: str) -> str:
    return _WS.sub(" ", str(text or "")).strip().casefold()


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digests_for(value: str) -> List[Tuple[int, str]]:
    """Return ``(length, sha256)`` windows that identify ``value``."""
    norm = normalize(value)
    if len(norm) < MIN_CHARS:
        return []
    out = [(len(norm), _h(norm))]
    if len(norm) >= EDGE_MIN_VALUE:
        out.append((EDGE_CHARS, _h(norm[:EDGE_CHARS])))
        out.append((EDGE_CHARS, _h(norm[-EDGE_CHARS:])))
    return out


def suppress_path(base_dir: Any) -> Path:
    return Path(str(base_dir)) / ".memkraft" / "recall_suppress.jsonl"


def record(base_dir: Any, values: Iterable[str], *, reason: str = "forget") -> int:
    """Append digests for ``values``; return how many new windows were added."""
    path = suppress_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        known = {(r.get("len"), r.get("sha256")) for r in _read(path)}
        rows = []
        for value in values:
            for length, digest in digests_for(value):
                if (length, digest) not in known:
                    known.add((length, digest))
                    rows.append({"len": length, "sha256": digest, "reason": reason})
        if rows:
            with path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        _CACHE.pop(str(path), None)
    return len(rows)


def _read(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("len"), int) and isinstance(row.get("sha256"), str):
            rows.append(row)
    return rows


def _event_values(base_dir: Any) -> List[str]:
    """Values of events that are tombstoned or denied by a policy."""
    from .store_core import read_all

    meta = Path(str(base_dir)) / ".memkraft"
    events = meta / "events.jsonl"
    if not events.exists():
        return []
    rows = read_all(events, True).records
    tombstoned = {r.get("tombstone_of") for r in rows if r.get("tombstone") is True}
    policies = read_all(meta / "deny_policies.jsonl").records
    from .derived_views import DerivedViewsMixin

    out = []
    for row in rows:
        if row.get("tombstone") or not isinstance(row.get("value"), str):
            continue
        if row.get("id") in tombstoned or (
            policies and DerivedViewsMixin._denied_by(policies, row.get("subject_id"), row.get("key") or "")
        ):
            out.append(row["value"])
    return out


def _stamp(path: Path) -> Tuple[Any, ...]:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (None, None)


def windows(base_dir: Any) -> Dict[int, Set[str]]:
    """Return ``{window_length: {sha256, ...}}`` for everything suppressed."""
    path = suppress_path(base_dir)
    meta = path.parent
    stamp = tuple(_stamp(p) for p in (path, meta / "events.jsonl", meta / "deny_policies.jsonl"))
    key = str(path)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    table: Dict[int, Set[str]] = {}
    for row in _read(path):
        table.setdefault(row["len"], set()).add(row["sha256"])
    for value in _event_values(base_dir):
        for length, digest in digests_for(value):
            table.setdefault(length, set()).add(digest)
    with _LOCK:
        _CACHE[key] = (stamp, table)
    return table


def contains_suppressed(text: str, table: Optional[Dict[int, Set[str]]]) -> bool:
    if not table or not text:
        return False
    norm = normalize(text)
    for length, digests in table.items():
        if length > len(norm):
            continue
        for start in range(0, len(norm) - length + 1):
            if _h(norm[start:start + length]) in digests:
                return True
    return False
