"""ReasoningBank — MemKraft v2.7.1.

Records the agent's step-by-step reasoning trajectories
(thought → action → outcome) so future tasks can:

1. Recall relevant past lessons (success patterns).
2. Detect repeated failure patterns (anti-patterns).

Design principles
-----------------
* Additive only — no existing API signature changes.
* Stdlib only.
* Storage lives under ``<base_dir>/.memkraft/`` (never user markdown):
    - ``trajectories/<task_id>.jsonl`` (append-only)
    - ``patterns.json`` (atomic rename writes)
* Forgiving — auto-starts trajectories, slugifies task ids,
  tolerates corrupt JSONL lines, idempotent on duplicate completes.

See ``docs/REASONING_BANK_DESIGN.md`` for full spec.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


__all__ = ["ReasoningBankMixin"]


# ── Module Constants ────────────────────────────────────────────────
MIN_REPEAT_WARN = 2
MAX_LESSONS_PER_PATTERN = 3
MAX_TASK_IDS_PER_PATTERN = 50
SCHEMA_VERSION = 1
_VALID_STATUSES = ("success", "failure", "partial")

# Tiny stopword fallback when self.stopwords is unavailable.
_FALLBACK_STOPWORDS = {
    # English
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that", "the",
    "to", "was", "were", "will", "with", "this", "i", "we", "you", "he",
    "she", "they", "them", "my", "our", "your", "their", "do", "did", "does",
    "not", "no", "yes", "if", "then", "than", "so", "such", "can", "could",
    "should", "would", "may", "might", "must", "after", "before", "again",
    # Korean common
    "그", "저", "이", "하다", "있다", "없다", "되다", "그리고", "그래서", "하지만",
    "또는", "또한", "그러나", "때문에", "위해", "통해", "에서", "으로", "에게",
}


# ── Helpers ─────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_task_id(raw: str) -> str:
    """Slugify a task id; empty → short uuid.

    Dots are NOT preserved — we want filesystem-safe ids that can't be
    confused with path traversal (e.g. ``..``) on any platform.
    """
    if not raw or not str(raw).strip():
        return uuid.uuid4().hex[:12]
    s = _SAFE_RE.sub("-", str(raw).strip())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        return uuid.uuid4().hex[:12]
    # Bound length to keep filenames sane on every fs.
    return s[:120]


def _coerce_tags(tags) -> List[str]:
    """Accept either comma-string or iterable; return normalized list."""
    if tags is None:
        return []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, Iterable):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def _coerce_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s in _VALID_STATUSES:
        return s
    return "partial"


_PUNCT_RE = re.compile(r"[^\w\s가-힣]+", re.UNICODE)


def _tokenize(text: str, stopwords: Optional[set] = None) -> List[str]:
    """Lowercase + strip punctuation + drop stopwords. Stable order."""
    if not text:
        return []
    cleaned = _PUNCT_RE.sub(" ", str(text).lower())
    raw = [t for t in cleaned.split() if t]
    sw = stopwords if stopwords is not None else _FALLBACK_STOPWORDS
    return [t for t in raw if t not in sw and len(t) > 1]


def _derive_signature(lesson: str, tags: List[str], stopwords: Optional[set] = None) -> str:
    """Deterministic, stopword-aware signature derivation."""
    src = " ".join([lesson or ""] + list(tags or []))
    toks = _tokenize(src, stopwords=stopwords)
    if not toks:
        # Degenerate — fall back to a short sha1 of the raw lesson for stability.
        h = hashlib.sha1((lesson or "").encode("utf-8")).hexdigest()[:8]
        return f"unsignatured-{h}"
    # Dedup, take the 4 longest, then sort alphabetically for stability.
    uniq = list(dict.fromkeys(toks))
    uniq.sort(key=lambda t: (-len(t), t))
    chosen = sorted(uniq[:4])
    return "-".join(chosen)


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return inter / union


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON via tmp + replace so partial writes can't corrupt readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Tolerant JSONL reader — skips corrupt lines."""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


# ── Mixin ───────────────────────────────────────────────────────────
class ReasoningBankMixin:
    """Adds trajectory + reasoning recall APIs to ``MemKraft``."""

    # ── path helpers (private) ─────────────────────────────────────
    def _trajectory_dir(self) -> Path:
        d = Path(self.base_dir) / ".memkraft" / "trajectories"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _trajectory_path(self, task_id: str) -> Path:
        return self._trajectory_dir() / f"{_safe_task_id(task_id)}.jsonl"

    def _patterns_path(self) -> Path:
        p = Path(self.base_dir) / ".memkraft" / "patterns.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _stopwords(self) -> set:
        sw = getattr(self, "stopwords", None)
        if isinstance(sw, set):
            return sw
        if isinstance(sw, (list, tuple)):
            return set(sw)
        return _FALLBACK_STOPWORDS

    # ── public API ────────────────────────────────────────────────
    def trajectory_start(
        self,
        task_id: str,
        *,
        title: str = "",
        tags: Any = "",
    ) -> Dict[str, Any]:
        """Begin a new reasoning trajectory.

        Idempotent: if the JSONL file already exists, no second start is
        appended (we just return the existing path).
        """
        tid = _safe_task_id(task_id)
        path = self._trajectory_path(tid)
        started_at = _now_iso()

        # Don't double-record start if file already has content.
        existing_lines = _read_jsonl(path)
        if any(r.get("kind") == "start" for r in existing_lines):
            for r in existing_lines:
                if r.get("kind") == "start":
                    started_at = r.get("started_at", started_at)
                    break
            return {"task_id": tid, "started_at": started_at, "path": str(path)}

        record = {
            "kind": "start",
            "task_id": tid,
            "title": str(title or ""),
            "tags": _coerce_tags(tags),
            "started_at": started_at,
            "schema_version": SCHEMA_VERSION,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"task_id": tid, "started_at": started_at, "path": str(path)}

    def trajectory_log(
        self,
        task_id: str,
        step: int,
        *,
        thought: str = "",
        action: str = "",
        outcome: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a (thought, action, outcome) step to the trajectory.

        Auto-starts the trajectory if no ``start`` record exists yet.
        """
        tid = _safe_task_id(task_id)
        path = self._trajectory_path(tid)
        if not any(r.get("kind") == "start" for r in _read_jsonl(path)):
            self.trajectory_start(tid)

        ts = _now_iso()
        record = {
            "kind": "step",
            "task_id": tid,
            "step": int(step),
            "thought": str(thought or ""),
            "action": str(action or ""),
            "outcome": str(outcome or ""),
            "metadata": metadata or {},
            "ts": ts,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"task_id": tid, "step": int(step), "appended_at": ts}

    def trajectory_complete(
        self,
        task_id: str,
        *,
        status: str = "success",
        lesson: str = "",
        pattern_signature: str = "",
        tags: Any = "",
    ) -> Dict[str, Any]:
        """Finish a trajectory and update pattern stats.

        - Appends a ``complete`` record to the JSONL.
        - Upserts ``patterns.json`` under ``"<status>::<signature>"``.
        - Logs a high-importance event when a failure pattern repeats.
        - Idempotent on (task_id, status, signature): a second call won't
          double-bump pattern counts.
        """
        tid = _safe_task_id(task_id)
        path = self._trajectory_path(tid)
        st = _coerce_status(status)
        tag_list = _coerce_tags(tags)

        # Pull tags from the start record if caller didn't pass any.
        if not tag_list:
            for r in _read_jsonl(path):
                if r.get("kind") == "start":
                    tag_list = list(r.get("tags") or [])
                    break

        sig = pattern_signature.strip() if pattern_signature else ""
        if not sig:
            sig = _derive_signature(lesson, tag_list, stopwords=self._stopwords())

        completed_at = _now_iso()

        # Detect previously-completed-with-same-(status,signature) BEFORE we append,
        # so we can stay idempotent.
        prior_completes = [
            r for r in _read_jsonl(path) if r.get("kind") == "complete"
        ]
        is_duplicate_signature = any(
            (r.get("status") == st and r.get("pattern_signature") == sig)
            for r in prior_completes
        )

        record = {
            "kind": "complete",
            "task_id": tid,
            "status": st,
            "lesson": str(lesson or ""),
            "pattern_signature": sig,
            "tags": tag_list,
            "completed_at": completed_at,
        }
        # Auto-start if user jumped straight to complete.
        if not any(r.get("kind") == "start" for r in _read_jsonl(path)):
            self.trajectory_start(tid, tags=tag_list)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Update patterns.json (skip count bump if duplicate to stay idempotent).
        bucket = self._upsert_pattern(
            status=st,
            signature=sig,
            task_id=tid,
            lesson=str(lesson or ""),
            timestamp=completed_at,
            bump_count=not is_duplicate_signature,
        )

        # Repeat-failure surfacing — fire only when count crosses threshold AND
        # we actually bumped the count (not on idempotent re-completes).
        duplicate_count = int(bucket.get("count", 1))
        if (
            st == "failure"
            and not is_duplicate_signature
            and duplicate_count >= MIN_REPEAT_WARN
            and hasattr(self, "log_event")
        ):
            try:
                short_lesson = (lesson or "").strip().splitlines()[0][:160]
                event_text = (
                    f"⚠️ Repeated failure pattern '{sig}' "
                    f"({duplicate_count}x): {short_lesson}"
                )
                self.log_event(
                    event=event_text,
                    tags="reasoning-bank,repeat-failure",
                    importance="high",
                )
            except Exception:
                # Best-effort only — never let logging break the API.
                pass

        return {
            "task_id": tid,
            "status": st,
            "lesson": str(lesson or ""),
            "pattern_signature": sig,
            "completed_at": completed_at,
            "duplicate_count": duplicate_count,
        }

    def reasoning_recall(
        self,
        query: str,
        *,
        top_k: int = 3,
        status: str = "",
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Retrieve completed trajectories most relevant to ``query``.

        Token-overlap (Jaccard) over (title ∪ lesson ∪ tags ∪ signature).
        Stopword-aware via ``self.stopwords`` if loaded.
        """
        sw = self._stopwords()
        q_tokens = _tokenize(query or "", stopwords=sw)
        if not q_tokens:
            return []

        status_filter = (status or "").strip().lower()
        out: List[Dict[str, Any]] = []
        tdir = self._trajectory_dir()
        if not tdir.exists():
            return []

        for jsonl_path in sorted(tdir.glob("*.jsonl")):
            records = _read_jsonl(jsonl_path)
            if not records:
                continue
            start_rec = next((r for r in records if r.get("kind") == "start"), None)
            complete_rec = None
            # Use the LAST complete record (latest state) when multiple exist.
            for r in records:
                if r.get("kind") == "complete":
                    complete_rec = r
            if complete_rec is None:
                continue  # Skip in-flight trajectories.

            if status_filter and complete_rec.get("status") != status_filter:
                continue

            title = (start_rec or {}).get("title", "") if start_rec else ""
            tags = list(complete_rec.get("tags") or (start_rec or {}).get("tags") or [])
            lesson = complete_rec.get("lesson", "")
            sig = complete_rec.get("pattern_signature", "")

            doc_text = " ".join([title, lesson, sig, " ".join(tags)])
            d_tokens = _tokenize(doc_text, stopwords=sw)
            score = _jaccard(q_tokens, d_tokens)
            if score < min_score or score <= 0.0:
                continue

            step_count = max(0, len(records) - (1 if start_rec else 0)
                                          - (1 if complete_rec else 0))
            out.append({
                "task_id": complete_rec.get("task_id"),
                "title": title,
                "status": complete_rec.get("status"),
                "lesson": lesson,
                "pattern_signature": sig,
                "score": round(float(score), 4),
                "completed_at": complete_rec.get("completed_at"),
                "tags": tags,
                "step_count": step_count,
                "path": str(jsonl_path),
            })

        out.sort(key=lambda r: (-r["score"], r.get("completed_at") or ""), reverse=False)
        # The above sort tuple makes "earlier completed_at" come first when
        # scores tie, which is the opposite of what we want — re-sort cleanly:
        out.sort(key=lambda r: (-r["score"], -_iso_to_epoch(r.get("completed_at") or "")))
        return out[: max(0, int(top_k))]

    def reasoning_patterns(
        self,
        *,
        status: str = "",
        min_count: int = 1,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """List recorded patterns sorted by frequency.

        Filters by status (``""`` = all) and a minimum occurrence count.
        """
        data = self._read_patterns()
        patterns = data.get("patterns", {}) or {}
        status_filter = (status or "").strip().lower()

        out: List[Dict[str, Any]] = []
        for key, bucket in patterns.items():
            if not isinstance(bucket, dict):
                continue
            if status_filter and bucket.get("status") != status_filter:
                continue
            cnt = int(bucket.get("count", 0))
            if cnt < int(min_count):
                continue
            out.append({
                "signature": bucket.get("signature", ""),
                "status": bucket.get("status", ""),
                "count": cnt,
                "first_seen": bucket.get("first_seen"),
                "last_seen": bucket.get("last_seen"),
                "task_ids": list(bucket.get("task_ids") or []),
                "lessons": list(bucket.get("lessons") or []),
            })

        out.sort(key=lambda b: (-b["count"], -_iso_to_epoch(b.get("last_seen") or "")))
        return out[: max(0, int(top_k))]

    def trajectory_get(self, task_id: str) -> Dict[str, Any]:
        """Return the full reconstructed view of a trajectory.

        Raises FileNotFoundError if the trajectory file is missing.
        """
        tid = _safe_task_id(task_id)
        path = self._trajectory_path(tid)
        if not path.exists():
            raise FileNotFoundError(f"trajectory not found: {tid}")
        records = _read_jsonl(path)
        start_rec = next((r for r in records if r.get("kind") == "start"), {})
        complete_rec = None
        for r in records:
            if r.get("kind") == "complete":
                complete_rec = r
        steps = [r for r in records if r.get("kind") == "step"]
        steps.sort(key=lambda r: int(r.get("step", 0)))

        return {
            "task_id": tid,
            "title": start_rec.get("title", ""),
            "status": (complete_rec or {}).get("status", "in-progress"),
            "lesson": (complete_rec or {}).get("lesson", ""),
            "pattern_signature": (complete_rec or {}).get("pattern_signature", ""),
            "tags": list(start_rec.get("tags") or []),
            "started_at": start_rec.get("started_at"),
            "completed_at": (complete_rec or {}).get("completed_at"),
            "steps": steps,
            "path": str(path),
        }

    # ── private helpers ───────────────────────────────────────────
    def _read_patterns(self) -> Dict[str, Any]:
        path = self._patterns_path()
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "patterns": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "patterns" not in data:
                return {"schema_version": SCHEMA_VERSION, "patterns": {}}
            return data
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SCHEMA_VERSION, "patterns": {}}

    def _upsert_pattern(
        self,
        *,
        status: str,
        signature: str,
        task_id: str,
        lesson: str,
        timestamp: str,
        bump_count: bool,
    ) -> Dict[str, Any]:
        data = self._read_patterns()
        patterns = data.setdefault("patterns", {})
        key = f"{status}::{signature}"
        bucket = patterns.get(key)
        if bucket is None:
            bucket = {
                "status": status,
                "signature": signature,
                "count": 1 if bump_count else 0,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "task_ids": [task_id],
                "lessons": [lesson] if lesson else [],
            }
            patterns[key] = bucket
        else:
            if bump_count:
                bucket["count"] = int(bucket.get("count", 0)) + 1
            bucket["last_seen"] = timestamp
            # task_ids: dedup-and-bound (FIFO).
            ids = list(bucket.get("task_ids") or [])
            if task_id in ids:
                ids.remove(task_id)
            ids.append(task_id)
            bucket["task_ids"] = ids[-MAX_TASK_IDS_PER_PATTERN:]
            # lessons: dedup-and-bound (last 3 distinct, FIFO).
            lessons = list(bucket.get("lessons") or [])
            if lesson:
                if lesson in lessons:
                    lessons.remove(lesson)
                lessons.append(lesson)
                bucket["lessons"] = lessons[-MAX_LESSONS_PER_PATTERN:]

        data["schema_version"] = SCHEMA_VERSION
        _atomic_write_json(self._patterns_path(), data)
        return bucket


# ── Module-level helper used by ranking ────────────────────────────
def _iso_to_epoch(s: str) -> float:
    """Best-effort ISO → epoch seconds; failures sort to 0."""
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        # Try stripping a trailing Z just in case.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
