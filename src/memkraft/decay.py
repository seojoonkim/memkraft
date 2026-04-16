"""Reversible Decay + Tombstone — MemKraft v0.8.0

Memories don't get deleted. They *fade* with a numeric ``decay_weight``
(0.0-1.0). They can always be restored. When fully tombstoned they're
moved to ``memory/.memkraft/tombstones/`` but the file is preserved.

Storage
-------

YAML frontmatter fields added to any Markdown memory file::

    ---
    decay_weight: 0.3
    decay_count: 2
    last_accessed: 2026-04-16
    tombstoned: false
    tombstoned_at: null
    ---

All fields are optional; missing = ``decay_weight=1.0``, not tombstoned.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Fields we manage. Anything else in frontmatter is preserved untouched.
_DECAY_FIELDS = {
    "decay_weight",
    "decay_count",
    "last_accessed",
    "tombstoned",
    "tombstoned_at",
}


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: Dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if v.lower() in ("null", "none", "~", ""):
            out[k] = None
        elif v.lower() == "true":
            out[k] = True
        elif v.lower() == "false":
            out[k] = False
        else:
            try:
                if "." in v:
                    out[k] = float(v)
                else:
                    out[k] = int(v)
            except ValueError:
                # strip surrounding quotes if present
                if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
                    v = v[1:-1]
                out[k] = v
    return out


def _serialise_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


def _write_frontmatter(text: str, data: Dict[str, Any]) -> str:
    """Return ``text`` with its frontmatter replaced by ``data``.

    The order of keys is preserved by writing known-decay keys first in a
    stable order, then any extra keys alphabetically.
    """
    known_order = [
        "tier",  # used by tiers.py — keep near the top if present
        "decay_weight",
        "decay_count",
        "last_accessed",
        "tombstoned",
        "tombstoned_at",
    ]
    keys: List[str] = [k for k in known_order if k in data]
    keys += sorted(k for k in data if k not in keys)

    body_start = 0
    m = _FRONTMATTER_RE.match(text)
    if m:
        body_start = m.end()
    body = text[body_start:]

    lines = ["---"]
    for k in keys:
        lines.append(f"{k}: {_serialise_value(data[k])}")
    lines.append("---\n")
    return "\n".join(lines) + body


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


class DecayMixin:
    """Mixin added to :class:`MemKraft` providing the decay_* API."""

    # --- path helpers ------------------------------------------------------

    def _tombstone_dir(self) -> Path:
        p = self.base_dir / ".memkraft" / "tombstones"  # type: ignore[attr-defined]
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _resolve_memory(self, memory_id: str) -> Optional[Path]:
        """Resolve a memory id to an existing file.

        Accepts: absolute path, relative path (relative to ``base_dir``),
        or a bare slug that we search across the common subdirs.
        """
        p = Path(memory_id)
        if p.is_absolute() and p.exists():
            return p
        candidate = self.base_dir / memory_id  # type: ignore[attr-defined]
        if candidate.exists():
            return candidate
        # search by stem across common memory dirs
        stem = Path(memory_id).stem
        for sub in ("entities", "live-notes", "facts", "decisions",
                    "originals", "inbox", "tasks", "meetings", "debug"):
            d: Path = self.base_dir / sub  # type: ignore[attr-defined]
            if not d.exists():
                continue
            for f in d.glob("*.md"):
                if f.stem == stem:
                    return f
        return None

    # --- low-level frontmatter ops ----------------------------------------

    def _read_decay_fm(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        return _parse_frontmatter(text)

    def _update_decay_fm(self, path: Path, updates: Dict[str, Any]) -> None:
        text = path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        fm.update(updates)
        path.write_text(_write_frontmatter(text, fm), encoding="utf-8")

    # --- public API --------------------------------------------------------

    def decay_apply(
        self,
        memory_id: str,
        *,
        decay_rate: float = 0.5,
    ) -> Dict[str, Any]:
        """Reduce the decay weight of a memory (but keep it on disk).

        Returns the new decay state.  ``decay_rate`` must be in ``(0, 1)``;
        the current weight is multiplied by ``(1 - decay_rate)``.
        """
        if not (0.0 < decay_rate < 1.0):
            raise ValueError("decay_rate must be between 0 and 1 (exclusive)")
        path = self._resolve_memory(memory_id)
        if path is None:
            raise FileNotFoundError(f"memory not found: {memory_id}")
        fm = self._read_decay_fm(path)
        weight = float(fm.get("decay_weight", 1.0))
        count = int(fm.get("decay_count", 0))
        new_weight = round(weight * (1.0 - decay_rate), 6)
        updates = {
            "decay_weight": new_weight,
            "decay_count": count + 1,
            "last_accessed": _today(),
        }
        self._update_decay_fm(path, updates)
        return {"path": str(path), **updates}

    def decay_list(
        self,
        *,
        below_threshold: float = 1.0,
        include_tombstoned: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return memories whose ``decay_weight`` is strictly below threshold."""
        out: List[Dict[str, Any]] = []
        base: Path = self.base_dir  # type: ignore[attr-defined]
        for f in base.rglob("*.md"):
            # skip the hidden .memkraft tree (tombstones live there)
            if ".memkraft" in f.parts and not include_tombstoned:
                continue
            fm = self._read_decay_fm(f)
            if not fm:
                continue
            if fm.get("tombstoned") and not include_tombstoned:
                continue
            w = float(fm.get("decay_weight", 1.0))
            if w < below_threshold:
                out.append({
                    "path": str(f),
                    "decay_weight": w,
                    "decay_count": int(fm.get("decay_count", 0)),
                    "last_accessed": fm.get("last_accessed"),
                    "tombstoned": bool(fm.get("tombstoned", False)),
                })
        out.sort(key=lambda r: r["decay_weight"])
        return out

    def decay_restore(self, memory_id: str) -> Dict[str, Any]:
        """Fully restore a decayed (or tombstoned) memory."""
        path = self._resolve_memory(memory_id)
        if path is None:
            # maybe it's in the tombstone directory
            tomb = self._tombstone_dir()
            for f in tomb.rglob("*.md"):
                if f.stem == Path(memory_id).stem:
                    # move it back; we don't know original dir, put under inbox
                    inbox = self.base_dir / "inbox"  # type: ignore[attr-defined]
                    inbox.mkdir(parents=True, exist_ok=True)
                    dest = inbox / f.name
                    shutil.move(str(f), str(dest))
                    path = dest
                    break
        if path is None:
            raise FileNotFoundError(f"memory not found: {memory_id}")
        updates = {
            "decay_weight": 1.0,
            "decay_count": 0,
            "last_accessed": _today(),
            "tombstoned": False,
            "tombstoned_at": None,
        }
        self._update_decay_fm(path, updates)
        return {"path": str(path), **updates}

    def decay_run(
        self,
        *,
        criteria: Optional[Dict[str, Any]] = None,
        decay_rate: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Batch-decay memories matching ``criteria``.

        Supported criteria keys:
          * ``older_than_days`` (int) — ``last_accessed`` older than N days
          * ``access_count_lt`` (int) — ``decay_count`` strictly less than N
          * ``weight_gt`` (float)     — current weight strictly greater

        Returns the list of affected memories.
        """
        criteria = criteria or {}
        older = criteria.get("older_than_days")
        count_lt = criteria.get("access_count_lt")
        weight_gt = criteria.get("weight_gt")

        cutoff: Optional[datetime] = None
        if older is not None:
            cutoff = datetime.now() - timedelta(days=int(older))

        affected: List[Dict[str, Any]] = []
        base: Path = self.base_dir  # type: ignore[attr-defined]
        for f in base.rglob("*.md"):
            if ".memkraft" in f.parts:
                continue
            fm = self._read_decay_fm(f)
            if fm.get("tombstoned"):
                continue
            if weight_gt is not None:
                if float(fm.get("decay_weight", 1.0)) <= float(weight_gt):
                    continue
            if count_lt is not None:
                # NOTE: decay_count is "how many times decayed", not accesses;
                # we keep the name from the spec so existing scripts still work.
                if int(fm.get("decay_count", 0)) >= int(count_lt):
                    continue
            if cutoff is not None:
                last = fm.get("last_accessed")
                if last:
                    dt = _parse_date(str(last))
                    if dt is not None and dt >= cutoff:
                        continue
            try:
                res = self.decay_apply(str(f), decay_rate=decay_rate)
                affected.append(res)
            except Exception:  # pragma: no cover — defensive
                continue
        return affected

    def decay_tombstone(self, memory_id: str) -> Dict[str, Any]:
        """Full tombstone: mark and move file to the tombstones folder.

        Search still ignores tombstoned files unless explicitly asked.
        """
        path = self._resolve_memory(memory_id)
        if path is None:
            raise FileNotFoundError(f"memory not found: {memory_id}")
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        self._update_decay_fm(path, {
            "tombstoned": True,
            "tombstoned_at": now,
            "decay_weight": 0.0,
        })
        tomb = self._tombstone_dir()
        dest = tomb / path.name
        # if a file with the same name exists in tombstone, suffix
        i = 1
        while dest.exists():
            dest = tomb / f"{path.stem}.{i}{path.suffix}"
            i += 1
        shutil.move(str(path), str(dest))
        return {"path": str(dest), "tombstoned_at": now}

    def decay_is_tombstoned(self, memory_id: str) -> bool:
        path = self._resolve_memory(memory_id)
        if path is None:
            # check tombstone dir
            tomb = self._tombstone_dir()
            return any(f.stem == Path(memory_id).stem for f in tomb.rglob("*.md"))
        fm = self._read_decay_fm(path)
        return bool(fm.get("tombstoned", False))
