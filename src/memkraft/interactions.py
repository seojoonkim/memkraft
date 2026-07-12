"""Last-interaction index for MemKraft 2.13.

Preview tier: append-only interaction log with a derived JSON snapshot.  The
snapshot is always rebuildable from the log.
"""

from __future__ import annotations
import typing

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .store_core import append, read_all


class LastInteractionMixin:
    """Add record_interaction() and last_interaction() preview APIs."""

    def _last_interactions_log_path(self) -> Path:
        return self.base_dir / ".memkraft" / "last_interactions.jsonl"

    def _last_interactions_snapshot_path(self) -> Path:
        return self.base_dir / ".memkraft" / "last_interactions.json"

    def record_interaction(
        self,
        subject_id: str,
        occurred_at: str,
        interaction_id: str,
        **metadata: Any,
    ) -> dict[str, Any]:
        """Preview: append an interaction event and update the derived snapshot."""
        record = append(
            self._last_interactions_log_path(),
            {
                "kind": "interaction",
                "subject_id": str(subject_id),
                "occurred_at": str(occurred_at),
                "interaction_id": str(interaction_id),
                **metadata,
            },
        )
        snapshot = getattr(self, "_last_interactions_snapshot_cache", None)
        if not isinstance(snapshot, dict):
            snapshot = self._load_last_interactions_snapshot()
        current = snapshot.get(str(subject_id))
        if current is None or _is_newer(record, current):
            snapshot[str(subject_id)] = _public_record(record)
        self._last_interactions_snapshot_cache = snapshot
        writes = int(getattr(self, "_last_interactions_since_snapshot", 0)) + 1
        self._last_interactions_since_snapshot = writes
        if writes < 100 or writes % 100 == 0:
            self._write_last_interactions_snapshot(snapshot)
        return _public_record(record)

    def last_interaction(self, subject_id: str) -> typing.Optional[dict[str, Any]]:
        """Preview: return the latest interaction for a subject, rebuilding if needed."""
        key = str(subject_id)
        cached = getattr(self, "_last_interactions_snapshot_cache", None)
        cached_mtime = getattr(self, "_last_interactions_snapshot_mtime", None)
        try:
            current_mtime = self._last_interactions_snapshot_path().stat().st_mtime_ns
        except OSError:
            current_mtime = None
        if isinstance(cached, dict) and cached_mtime == current_mtime and key in cached:
            return cached[key]
        snapshot = self._load_last_interactions_snapshot()
        self._last_interactions_snapshot_cache = snapshot
        self._last_interactions_snapshot_mtime = current_mtime
        if key in snapshot:
            return snapshot[key]
        snapshot = self._rebuild_last_interactions_snapshot()
        self._last_interactions_snapshot_cache = snapshot
        return snapshot.get(key)

    def _load_last_interactions_snapshot(self) -> dict[str, dict[str, Any]]:
        path = self._last_interactions_snapshot_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._rebuild_last_interactions_snapshot()
        if not isinstance(data, dict):
            return self._rebuild_last_interactions_snapshot()
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _rebuild_last_interactions_snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for record in read_all(self._last_interactions_log_path()).records:
            if record.get("kind") != "interaction":
                continue
            subject_id = str(record.get("subject_id", ""))
            if not subject_id:
                continue
            current = snapshot.get(subject_id)
            public = _public_record(record)
            if current is None or _is_newer(public, current):
                snapshot[subject_id] = public
        self._write_last_interactions_snapshot(snapshot)
        return snapshot

    def _write_last_interactions_snapshot(self, snapshot: dict[str, dict[str, Any]]) -> None:
        path = self._last_interactions_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        try:
            self._last_interactions_snapshot_mtime = path.stat().st_mtime_ns
        except OSError:
            self._last_interactions_snapshot_mtime = None


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in {"kind", "schema_version", "id", "created_at"}}


def _is_newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    cand_time = _parse_time(candidate.get("occurred_at"))
    cur_time = _parse_time(current.get("occurred_at"))
    if cand_time != cur_time:
        return cand_time > cur_time
    return str(candidate.get("interaction_id", "")) > str(current.get("interaction_id", ""))


def _parse_time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min
