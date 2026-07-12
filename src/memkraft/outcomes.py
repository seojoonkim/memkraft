"""Append-only outcome feedback and deterministic context utility preview."""
from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store_core import _lock_current_inode, _unlock, append, read_all

HALF_LIFE_DAYS = 30.0
MAX_UPDATE = 0.2


@contextmanager
def _outcome_transaction(base_dir: Path):
    """Serialize A2 check-and-append transactions across threads/processes."""
    path = base_dir / ".memkraft" / "outcomes.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _lock_current_inode(str(path), os.O_RDWR | os.O_CREAT)
    try:
        yield
    finally:
        _unlock(fd)
        os.close(fd)


def _instant(value: Optional[datetime]) -> datetime:
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("now must be a datetime")
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _item_id(section: str, item: Dict[str, Any]) -> str:
    record_id = item.get("event_id") or item.get("record_id") or item.get("id")
    identity = {
        "section": section,
        "record_id": record_id,
        "subject_id": item.get("subject_id"),
        "key": item.get("key"),
        "candidate_id": item.get("candidate_id"),
        "source": item.get("source"),
        "provenance": item.get("provenance"),
    }
    if record_id is None:
        # Deterministic compatibility identity for legacy/compiler items without IDs.
        identity["value"] = item.get("value")
        identity["valid_from"] = item.get("valid_from")
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class OutcomeLoopMixin:
    """A2 APIs. Outcome events are authoritative; utility is replayed from them."""

    def _outcome_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "outcomes.jsonl"

    def _usage_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "context_usages.jsonl"

    def _record_context_usage(self, result: Dict[str, Any]) -> None:
        items = []
        for section, values in result["sections"].items():
            for position, item in enumerate(values):
                items.append({
                    "item_id": _item_id(section, item), "section": section,
                    "position": position, "source": item.get("source"),
                    "provenance": item.get("provenance"),
                })
        with _outcome_transaction(Path(self.base_dir)):
            rows = read_all(self._usage_path()).records
            if any(row.get("usage_id") == result["usage_id"] for row in rows):
                return
            append(self._usage_path(), {"usage_id": result["usage_id"], "source_items": items})

    def report_outcome(
        self, usage_id: str, outcome: str, reward: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if not isinstance(usage_id, str) or not usage_id.strip():
            raise ValueError("usage_id must not be empty")
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("outcome must not be empty")
        outcome = outcome.strip()
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("metadata must be a dictionary")
        metadata = dict(metadata or {})
        if reward is None:
            reward = 1.0 if outcome.lower() in {"success", "positive", "helpful"} else -1.0
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            raise TypeError("reward must be a finite number")
        if not math.isfinite(float(reward)):
            raise ValueError("reward must be a finite number")
        reward = max(-1.0, min(1.0, float(reward)))
        usages = [row for row in read_all(self._usage_path()).records if row.get("usage_id") == usage_id]
        if not usages:
            raise KeyError("unknown usage_id")
        key = metadata.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or not key.strip()):
            raise ValueError("idempotency_key must be a non-empty string")
        key = key or hashlib.sha256(json.dumps(
            {"usage_id": usage_id, "outcome": outcome, "reward": reward, "metadata": metadata},
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode()).hexdigest()
        fingerprint = {"usage_id": usage_id, "outcome": outcome, "reward": reward, "metadata": metadata}
        with _outcome_transaction(Path(self.base_dir)):
            prior = [row for row in read_all(self._outcome_path()).records if row.get("idempotency_key") == key]
            if prior:
                old = prior[0]
                if any(old.get(k) != v for k, v in fingerprint.items()):
                    raise ValueError("idempotency key already used with different outcome")
                return {**old, "status": "already_reported"}
            event = {
                **fingerprint, "idempotency_key": key,
                "reported_at": _instant(now).isoformat(),
                "source_items": usages[0]["source_items"], "max_update": MAX_UPDATE,
            }
            append(self._outcome_path(), event)
            return {**event, "status": "applied"}

    def context_utility(self, now: Optional[datetime] = None) -> Dict[str, Dict[str, float]]:
        at = _instant(now)
        scores: Dict[str, float] = {}
        for event in read_all(self._outcome_path()).records:
            age = max(0.0, (at - datetime.fromisoformat(event["reported_at"])).total_seconds() / 86400.0)
            decayed = float(event["reward"]) * (0.5 ** (age / HALF_LIFE_DAYS))
            for item in event.get("source_items", []):
                # Rank-sensitive credit avoids moving every item in a used context equally.
                delta = max(-MAX_UPDATE, min(MAX_UPDATE, decayed * MAX_UPDATE / (item["position"] + 1)))
                item_id = item["item_id"]
                scores[item_id] = max(-1.0, min(1.0, scores.get(item_id, 0.0) + delta))
        return {key: {"score": scores[key]} for key in sorted(scores)}

    def _context_item_id(self, section: str, item: Dict[str, Any]) -> str:
        return _item_id(section, item)
