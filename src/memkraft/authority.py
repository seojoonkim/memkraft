"""Decision authority provenance (MemKraft 3.8, Feature 2).

An append-only local note of *where* the authority behind a decision is said
to live: a source reference, the policy version it was read at, and the scopes
it was claimed for. The record is **inert**. MemKraft stores it, derives its
lifecycle status, and optionally compiles it into context. It never checks the
external system the reference points at, never turns a claim into an approval,
and never gates, starts, orders, or authorizes work — Hermes remains the sole
authority for all of that. ``authority_verified`` is therefore written as
``False`` and is never derived from anything else.

Selection is a pure function of the persisted records and an explicitly
supplied timezone-aware ``now``; there is no implicit clock, so a compiled
context is reproducible from its inputs. Semantically malformed records are
dropped, but physical JSONL corruption fails the ledger closed. At most
:data:`MAX_ACTIVE_AUTHORITY` entries are ever returned.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_protocol import ExecutionError, canonical_timestamp
from .store_core import append, read_all

__all__ = [
    "AuthorityMixin",
    "AuthorityError",
    "AUTHORITY_ERROR_REGISTRY",
    "MAX_ACTIVE_AUTHORITY",
    "MAX_AUTHORITY_SCOPES",
    "MAX_AUTHORITY_TOKENS",
    "authority_states",
    "active_authority",
]

#: Most authority entries that may ever reach a caller or a compiled context.
MAX_ACTIVE_AUTHORITY = 3
#: Hard ceiling on the rendered authority section, independent of any budget.
MAX_AUTHORITY_TOKENS = 300
#: Most scopes a single assertion may name.
MAX_AUTHORITY_SCOPES = 8

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_MAX_SOURCE_REF = 512
_ASSERTION = "authority_assertion"
_REVOCATION = "authority_revocation"
_ASSERTED = "asserted"
_REVOKED = "revoked"
_SUPERSEDED = "superseded"
_EXPIRED = "expired"

AUTHORITY_ERROR_REGISTRY = {
    "E_AUTHORITY_VALIDATION": ("input", False),
}


class AuthorityError(ValueError):
    """Stable, non-retryable error for authority provenance operations."""

    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        error_class, retryable = AUTHORITY_ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


def _fail(message: str, field: Optional[str] = None) -> None:
    details = {"path": field} if field is not None else {}
    raise AuthorityError("E_AUTHORITY_VALIDATION", message, details)


def require_aware_now(value: Any, field: str = "now") -> str:
    """Require an explicit timezone-aware ``datetime`` and canonicalize it.

    A string would be a wire value someone else already interpreted, and a
    naive datetime silently means "whatever this machine's zone is" — both
    make lifecycle selection depend on something the caller did not state.
    """
    if not isinstance(value, datetime):
        _fail("%s must be a timezone-aware datetime" % field, field)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        _fail("%s must be timezone-aware" % field, field)
    try:
        return canonical_timestamp(value)
    except (ExecutionError, TypeError, ValueError):
        _fail("%s is not a representable timestamp" % field, field)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail("%s must be a bounded identifier" % field, field)
    return value


def _source_ref(value: Any) -> str:
    if not isinstance(value, str):
        _fail("source_ref must be a string", "source_ref")
    text = value.strip()
    if not text or len(text) > _MAX_SOURCE_REF:
        _fail("source_ref must be a non-empty bounded string", "source_ref")
    if any(character < " " for character in text):
        _fail("source_ref must not contain control characters", "source_ref")
    return text


def _scopes(value: Any) -> List[str]:
    if not isinstance(value, list) or not value:
        _fail("scopes must be a non-empty list of identifiers", "scopes")
    if len(value) > MAX_AUTHORITY_SCOPES:
        _fail("scopes must name at most %d entries" % MAX_AUTHORITY_SCOPES, "scopes")
    for scope in value:
        _identifier(scope, "scopes")
    # Sorted and deduplicated so an equivalent claim always renders identically.
    return sorted(set(value))


def _expires_at(value: Any, issued_at: str) -> Optional[str]:
    if value is None:
        return None
    expires_at = require_aware_now(value, "expires_at")
    if expires_at <= issued_at:
        _fail("expires_at must be after issued_at", "expires_at")
    return expires_at


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
    if kind not in (_ASSERTION, _REVOCATION) or record.get("schema_version") != 1:
        return False
    authority_id = record.get("authority_id")
    if not isinstance(authority_id, str) or not _IDENTIFIER.fullmatch(authority_id):
        return False
    issued_at = _canonical(record.get("issued_at"))
    if issued_at is None:
        return False
    if kind == _REVOCATION:
        return record.get("status") == _REVOKED
    source_ref = record.get("source_ref")
    version = record.get("authority_version")
    scopes = record.get("scopes")
    expires_at = record.get("expires_at")
    if expires_at is not None:
        expires_at = _canonical(expires_at)
        if expires_at is None or expires_at <= issued_at:
            return False
    return bool(
        isinstance(source_ref, str)
        and source_ref == source_ref.strip()
        and 0 < len(source_ref) <= _MAX_SOURCE_REF
        and not any(character < " " for character in source_ref)
        and isinstance(version, str) and _IDENTIFIER.fullmatch(version)
        and isinstance(scopes, list) and 0 < len(scopes) <= MAX_AUTHORITY_SCOPES
        and scopes == sorted(set(scopes))
        and all(isinstance(scope, str) and _IDENTIFIER.fullmatch(scope)
                for scope in scopes)
        and record.get("status") == _ASSERTED
        and record.get("privacy") == "local_private"
        and record.get("authority_verified") is False
    )


def _entry(record: Dict[str, Any], status: str) -> Dict[str, Any]:
    return {
        "record_id": record.get("id"),
        "authority_id": record["authority_id"],
        "source_ref": record["source_ref"],
        "authority_version": record["authority_version"],
        "scopes": list(record["scopes"]),
        "issued_at": record["issued_at"],
        "expires_at": record.get("expires_at"),
        "status": status,
        # Recorded provenance is a claim about an external system, never a
        # finding about one; MemKraft has no basis to say otherwise.
        "authority_verified": False,
    }


def authority_states(records: List[Dict[str, Any]],
                     now: Optional[str] = None) -> List[Dict[str, Any]]:
    """Derive assertion lifecycle status, optionally as of ``now``.

    An assertion is ``superseded`` once a later assertion exists for the same
    ``authority_id``, ``revoked`` when the newest event for it is a revocation,
    and ``asserted`` otherwise. Nothing is ever rewritten: status is a reading
    of the append-only log, not a mutation of it.
    """
    usable = [
        record for record in records
        if _usable(record) and (now is None or record["issued_at"] <= now)
    ]
    newest_assertion: Dict[str, tuple] = {}
    newest_revocation: Dict[str, tuple] = {}
    for index, record in enumerate(usable):
        target = (newest_assertion if record["record_type"] == _ASSERTION
                  else newest_revocation)
        event_key = (record["issued_at"], index)
        current = target.get(record["authority_id"])
        if current is None or event_key > current:
            target[record["authority_id"]] = event_key

    entries = []
    for index, record in enumerate(usable):
        if record["record_type"] != _ASSERTION:
            continue
        authority_id = record["authority_id"]
        assertion_key = (record["issued_at"], index)
        revoked_key = newest_revocation.get(authority_id)
        if assertion_key < newest_assertion[authority_id]:
            status = _SUPERSEDED
        elif revoked_key is not None and revoked_key > assertion_key:
            status = _REVOKED
        elif (now is not None and record.get("expires_at") is not None
              and record["expires_at"] <= now):
            status = _EXPIRED
        else:
            status = _ASSERTED
        entries.append(_entry(record, status))
    entries.sort(key=lambda entry: entry["authority_id"])
    entries.sort(key=lambda entry: entry["issued_at"], reverse=True)
    return entries


def active_authority(records: List[Dict[str, Any]], now: str,
                     limit: int = MAX_ACTIVE_AUTHORITY) -> List[Dict[str, Any]]:
    """Select the live authority provenance at ``now`` from ``records``.

    ``now`` is a canonical timestamp. Only ``asserted`` entries whose window
    contains ``now`` survive; the result is ordered newest first with
    ``authority_id`` as the tiebreak so it is stable across calls.
    """
    live = [
        entry for entry in authority_states(records, now=now)
        if entry["status"] == _ASSERTED
        and entry["issued_at"] <= now
        and (entry["expires_at"] is None or now < entry["expires_at"])
    ]
    return live[:limit]


class AuthorityMixin:
    """Additive, inert decision-authority provenance API."""

    def _authority_events_path(self) -> Path:
        return Path(self.base_dir) / ".memkraft" / "authority" / "events.jsonl"

    def _authority_read(self) -> List[Dict[str, Any]]:
        result = read_all(self._authority_events_path())
        if result.skipped:
            _fail("authority ledger is not structurally valid")
        return result.records

    def _authority_limit(self, limit: Any) -> int:
        if (not isinstance(limit, int) or isinstance(limit, bool)
                or not 1 <= limit <= MAX_ACTIVE_AUTHORITY):
            _fail("limit must be from 1 through %d" % MAX_ACTIVE_AUTHORITY, "limit")
        return limit

    def authority_assert(self, authority_id: Any, *, source_ref: Any,
                         authority_version: Any, scopes: Any, issued_at: Any,
                         expires_at: Any = None) -> Dict[str, Any]:
        """Append a claim about where the authority for ``authority_id`` lives.

        This records a pointer and nothing more. It grants nothing, and a later
        assertion for the same ``authority_id`` supersedes this one.
        """
        stamped_at = require_aware_now(issued_at, "issued_at")
        record = {
            "record_type": _ASSERTION,
            "authority_id": _identifier(authority_id, "authority_id"),
            "source_ref": _source_ref(source_ref),
            "authority_version": _identifier(authority_version, "authority_version"),
            "scopes": _scopes(scopes),
            "issued_at": stamped_at,
            "expires_at": _expires_at(expires_at, stamped_at),
            "status": _ASSERTED,
            "privacy": "local_private",
            "authority_verified": False,
        }
        written = append(self._authority_events_path(), record)
        return {"outcome": "applied", "record_id": written["id"], "record": written}

    def authority_revoke(self, authority_id: Any, *, issued_at: Any) -> Dict[str, Any]:
        """Append a marker that withdraws the current claim for ``authority_id``."""
        record = {
            "record_type": _REVOCATION,
            "authority_id": _identifier(authority_id, "authority_id"),
            "issued_at": require_aware_now(issued_at, "issued_at"),
            "status": _REVOKED,
        }
        written = append(self._authority_events_path(), record)
        return {"outcome": "applied", "record_id": written["id"], "record": written}

    def authority_states(self, *, now: Any) -> List[Dict[str, Any]]:
        """Return every recorded assertion with its derived lifecycle status."""
        return authority_states(self._authority_read(), now=require_aware_now(now))

    def authority_active(self, *, now: Any,
                         limit: int = MAX_ACTIVE_AUTHORITY) -> List[Dict[str, Any]]:
        """Return the live authority provenance at ``now`` (at most ``limit``)."""
        limit = self._authority_limit(limit)
        return active_authority(self._authority_read(), require_aware_now(now), limit)
