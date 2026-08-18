"""TTL-bounded current focus (MemKraft 3.8, Feature 1).

An append-only local note of what the operator says the session is about,
each entry bounded by an explicit time-to-live. Focus is *inert*: it is
recorded, expired, and optionally compiled into context. It never gates,
starts, orders, or authorizes work, and MemKraft never verifies any external
authority behind it — Hermes remains the sole authority for that.

Selection is a pure function of the persisted records and an explicitly
supplied timezone-aware ``now``; there is no implicit clock, so a compiled
context is reproducible from its inputs. Semantically malformed records are
dropped, but physical JSONL corruption fails the ledger closed. At most
:data:`MAX_ACTIVE_FOCUS` entries are ever returned.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_protocol import ExecutionError, canonical_timestamp
from .store_core import append, read_all

__all__ = [
    "FocusMixin",
    "FocusError",
    "FOCUS_ERROR_REGISTRY",
    "MAX_ACTIVE_FOCUS",
    "MAX_FOCUS_TOKENS",
    "MAX_FOCUS_TTL_SECONDS",
    "active_focus",
]

#: Most focus entries that may ever reach a caller or a compiled context.
MAX_ACTIVE_FOCUS = 3
#: Hard ceiling on the rendered focus section, independent of any budget.
MAX_FOCUS_TOKENS = 200
#: Longest permitted time-to-live: focus is a session note, not a decision.
MAX_FOCUS_TTL_SECONDS = 86400

_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_MAX_STATEMENT = 512
_FOCUS = "focus"
_RELEASE = "focus_release"

FOCUS_ERROR_REGISTRY = {
    "E_FOCUS_VALIDATION": ("input", False),
}


class FocusError(ValueError):
    """Stable, non-retryable error for focus operations."""

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        error_class, retryable = FOCUS_ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


def _fail(message: str, field: Optional[str] = None) -> None:
    details = {"path": field} if field is not None else {}
    raise FocusError("E_FOCUS_VALIDATION", message, details)


def require_aware_now(value: Any, field: str = "now") -> str:
    """Require an explicit timezone-aware ``datetime`` and canonicalize it.

    A string would be a wire value someone else already interpreted, and a
    naive datetime silently means "whatever this machine's zone is" — both
    make TTL selection depend on something the caller did not state.
    """
    if not isinstance(value, datetime):
        _fail("%s must be a timezone-aware datetime" % field, field)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        _fail("%s must be timezone-aware" % field, field)
    try:
        return canonical_timestamp(value)
    except (ExecutionError, TypeError, ValueError):
        _fail("%s is not a representable timestamp" % field, field)


def _topic(value: Any) -> str:
    if not isinstance(value, str) or not _TOPIC.fullmatch(value):
        _fail("topic must be a bounded identifier", "topic")
    return value


def _statement(value: Any) -> str:
    if not isinstance(value, str):
        _fail("statement must be a string", "statement")
    text = value.strip()
    if not text or len(text) > _MAX_STATEMENT:
        _fail("statement must be a non-empty bounded string", "statement")
    if any(character < " " for character in text):
        _fail("statement must not contain control characters", "statement")
    return text


def _ttl(value: Any) -> int:
    if (not isinstance(value, int) or isinstance(value, bool)
            or not 1 <= value <= MAX_FOCUS_TTL_SECONDS):
        _fail("ttl_seconds must be from 1 through %d" % MAX_FOCUS_TTL_SECONDS,
              "ttl_seconds")
    return value


def _canonical(value: Any) -> Optional[str]:
    """Return ``value`` when it is already a canonical timestamp, else None."""
    if not isinstance(value, str):
        return None
    try:
        return canonical_timestamp(value) if canonical_timestamp(value) == value else None
    except (ExecutionError, TypeError, ValueError):
        return None


def _usable(record: Dict[str, Any]) -> bool:
    """Strict validation of untrusted persisted JSON — repair nothing."""
    kind = record.get("record_type")
    if kind not in (_FOCUS, _RELEASE) or record.get("schema_version") != 1:
        return False
    if not isinstance(record.get("topic"), str) or not _TOPIC.fullmatch(record["topic"]):
        return False
    created_at = _canonical(record.get("created_at"))
    if created_at is None:
        return False
    if kind == _RELEASE:
        return True
    statement = record.get("statement")
    expires_at = _canonical(record.get("expires_at"))
    return bool(
        isinstance(statement, str)
        and statement == statement.strip()
        and 0 < len(statement) <= _MAX_STATEMENT
        and not any(character < " " for character in statement)
        and expires_at is not None
        and expires_at > created_at
        and record.get("privacy") == "local_private"
        and record.get("authority_verified") is False
    )


def active_focus(records: List[Dict[str, Any]], now: str,
                 limit: int = MAX_ACTIVE_FOCUS) -> List[Dict[str, Any]]:
    """Select the live focus entries at ``now`` from persisted ``records``.

    ``now`` is a canonical timestamp. Later records supersede earlier ones for
    the same topic (a release supersedes into nothing), expired and
    not-yet-started entries are dropped, and the result is ordered newest
    first with topic as the tiebreak so it is stable across calls.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if not _usable(record) or record["created_at"] > now:
            continue
        current = latest.get(record["topic"])
        if current is not None and record["created_at"] < current["created_at"]:
            continue
        latest[record["topic"]] = record
    live = [
        record for record in latest.values()
        if record["record_type"] == _FOCUS
        and record["created_at"] <= now < record["expires_at"]
    ]
    live.sort(key=lambda record: record["topic"])
    live.sort(key=lambda record: record["created_at"], reverse=True)
    return [
        {
            "record_id": record.get("id"),
            "topic": record["topic"],
            "statement": record["statement"],
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
        }
        for record in live[:limit]
    ]


class FocusMixin:
    """Additive, inert current-focus API."""

    def _focus_events_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "focus" / "events.jsonl"

    def _focus_read(self) -> List[Dict[str, Any]]:
        result = read_all(self._focus_events_path())
        if result.skipped:
            _fail("focus ledger is not structurally valid")
        return result.records

    def focus_record(self, topic: Any, statement: Any, *, now: Any,
                     ttl_seconds: int = 3600) -> Dict[str, Any]:
        """Append a focus entry for ``topic`` that expires ``ttl_seconds`` later."""
        created_at = require_aware_now(now)
        record = {
            "record_type": _FOCUS,
            "topic": _topic(topic),
            "statement": _statement(statement),
            "created_at": created_at,
            "expires_at": canonical_timestamp(
                now.replace(microsecond=0) + timedelta(seconds=_ttl(ttl_seconds))),
            "privacy": "local_private",
            "authority_verified": False,
        }
        written = append(self._focus_events_path(), record)
        return {"outcome": "applied", "record_id": written["id"], "record": written}

    def focus_release(self, topic: Any, *, now: Any) -> Dict[str, Any]:
        """Append a marker that ends the current focus on ``topic``."""
        record = {
            "record_type": _RELEASE,
            "topic": _topic(topic),
            "created_at": require_aware_now(now),
        }
        written = append(self._focus_events_path(), record)
        return {"outcome": "applied", "record_id": written["id"], "record": written}

    def focus_active(self, *, now: Any,
                     limit: int = MAX_ACTIVE_FOCUS) -> List[Dict[str, Any]]:
        """Return the live focus entries at ``now`` (at most ``limit``)."""
        if (not isinstance(limit, int) or isinstance(limit, bool)
                or not 1 <= limit <= MAX_ACTIVE_FOCUS):
            _fail("limit must be from 1 through %d" % MAX_ACTIVE_FOCUS, "limit")
        return active_focus(self._focus_read(), require_aware_now(now), limit)
