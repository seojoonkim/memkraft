"""Canonical events and deterministic compiled truth preview APIs."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .store_core import _lock_current_inode, _unlock, append, read_all


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _iso(value: Any) -> tuple[str, datetime]:
    value = _text(value, "valid_from")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError as exc:
            raise ValueError("valid_from must be a valid ISO-8601 date or datetime") from exc
    canonical = parsed.isoformat() if "T" in value or " " in value else parsed.date().isoformat()
    rank = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    return canonical, rank


class DerivedViewsMixin:
    """Add an append-only event log and its rebuildable current-truth view."""

    def _canonical_events_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "events.jsonl"

    def _compiled_truth_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "compiled_truth.jsonl"

    def append_event(
        self,
        subject_id: str,
        key: str,
        value: Any,
        source: Optional[str] = None,
        provenance: Optional[str] = None,
        valid_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one canonical fact event; provenance remains a source alias."""
        if source is None and provenance is None:
            raise ValueError("source (or provenance) is required")
        normalized_provenance = _text(provenance, "provenance") if provenance is not None else None
        resolved_source = _text(source, "source") if source is not None else normalized_provenance
        record: Dict[str, Any] = {
            "subject_id": _text(subject_id, "subject_id"),
            "key": _text(key, "key"),
            "value": value,
            "source": resolved_source,
        }
        if normalized_provenance is not None:
            record["provenance"] = normalized_provenance
        if valid_from is not None:
            record["valid_from"] = _iso(valid_from)[0]
        return append(self._canonical_events_path(), record)

    def compile_truth(self, dry_run: bool = True) -> Dict[str, Any]:
        """Return the exact rebuild plan and optionally atomically apply it."""
        result = read_all(self._canonical_events_path())
        winners: Dict[tuple, tuple] = {}
        invalid = 0
        for order, event in enumerate(result.records):
            if "value" not in event:
                invalid += 1
                continue
            try:
                normalized = dict(event)
                normalized["subject_id"] = _text(event.get("subject_id"), "subject_id")
                normalized["key"] = _text(event.get("key"), "key")
                normalized["source"] = _text(event.get("source"), "source")
                if "provenance" in event:
                    normalized["provenance"] = _text(event["provenance"], "provenance")
                date_rank = datetime.min
                if "valid_from" in event:
                    normalized["valid_from"], date_rank = _iso(event["valid_from"])
            except (TypeError, ValueError):
                invalid += 1
                continue
            identity = (normalized["subject_id"], normalized["key"])
            rank = (date_rank, order)
            if identity not in winners or rank > winners[identity][0]:
                winners[identity] = (rank, normalized)

        records = []
        for identity in sorted(winners, key=lambda item: (str(item[0]), str(item[1]))):
            event = winners[identity][1]
            compiled = {
                "subject_id": event["subject_id"],
                "key": event["key"],
                "value": event["value"],
                "source": event["source"],
            }
            if "valid_from" in event:
                compiled["valid_from"] = event["valid_from"]
            if "provenance" in event:
                compiled["provenance"] = event["provenance"]
            records.append(compiled)

        plan: Dict[str, Any] = {
            "dry_run": dry_run,
            "skipped": result.skipped + invalid,
            "records": records,
        }
        if dry_run:
            return plan

        target = self._compiled_truth_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(target.name + ".rebuild.lock")
        lock_fd = _lock_current_inode(str(lock_path), os.O_RDWR | os.O_CREAT)
        tmp: Optional[Path] = None
        try:
            handle = tempfile.NamedTemporaryFile(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent), delete=False)
            tmp = Path(handle.name)
            handle.close()
            for record in records:
                # Suppress envelope-generated nondeterminism in the derived view.
                append(tmp, {**record, "id": f"{record['subject_id']}:{record['key']}", "created_at": "compiled"})
            os.replace(tmp, target)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            _unlock(lock_fd)
            os.close(lock_fd)
        plan["applied"] = True
        return plan

    def current_truth(self, subject_id: str) -> Dict[str, Any]:
        """Read one subject from the compiled view, never from canonical events."""
        result = read_all(self._compiled_truth_path())
        return {
            record["key"]: record["value"]
            for record in result.records
            if record.get("subject_id") == subject_id
            and "key" in record
            and "value" in record
        }
