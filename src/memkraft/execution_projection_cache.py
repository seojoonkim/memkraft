"""Local filesystem cache for execution projections.

The events log remains authoritative.  A cache entry is usable only when its
file identity fingerprint and caller-injected projection inputs match exactly;
all other cases recompute from the log.  Entries are replaced atomically, so a
reader observes either a complete old entry (which fails its fingerprint check)
or a complete new one.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, NamedTuple, Union

from .execution_projection import project, project_leases
from .execution_protocol import canonical_timestamp, parse_timestamp
from .store_core import read_all

__all__ = ["CachedProjection", "cache_path", "read_projection"]

_CACHE_SCHEMA = 1
_CACHE_DIRECTORY = "projection-cache-v1"


class CachedProjection(NamedTuple):
    """The stable projection and the clock-sensitive lease projection."""

    state: Dict[str, Any]
    leases: Dict[str, Any]


def _fingerprint(path: Path) -> Dict[str, int]:
    """Return the file identity used for validation, including absence."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"device": 0, "inode": 0, "size": 0, "mtime_ns": 0}
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def cache_path(path: Union[str, Path], now: Any, goal_id: str) -> Path:
    """Return the private sidecar path for one goal.

    ``now`` remains in the public signature for compatibility with the first
    preview implementation, but stable projections are intentionally shared
    across injected instants.  The state digest excludes ``evaluated_at``.
    """
    events_path = Path(path)
    canonical_timestamp(now)
    key = json.dumps([goal_id], ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    name = hashlib.sha256(key).hexdigest() + ".json"
    return events_path.parent / _CACHE_DIRECTORY / name


def _payload(fingerprint: Dict[str, int], evaluated_at: str, goal_id: str,
             value: CachedProjection) -> Dict[str, Any]:
    body = {
        "cache_schema": _CACHE_SCHEMA,
        "fingerprint": fingerprint,
        "goal_id": goal_id,
        "now": evaluated_at,
        "state": value.state,
        "leases": value.leases,
    }
    body["checksum"] = _checksum(body)
    return body


def _checksum(body: Dict[str, Any]) -> str:
    """Integrity checksum for cache JSON (not a protocol digest)."""
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _leases_at(leases: Dict[str, Any], evaluated_at: str) -> Dict[str, Any]:
    """Re-evaluate cached lease history at an injected instant."""
    instant = parse_timestamp(evaluated_at)
    last = leases["last"]
    active = {
        scope_key: held for scope_key, held in last.items()
        if not held["released"] and parse_timestamp(held["expires_at"]) > instant
    }
    return {"active": active, "last": last, "max_fence": leases["max_fence"]}


def _load(sidecar: Path, fingerprint: Dict[str, int], evaluated_at: str,
          goal_id: str, include_leases: bool) -> Union[CachedProjection, None]:
    try:
        with sidecar.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        checksum = payload.get("checksum")
        body = {key: value for key, value in payload.items() if key != "checksum"}
        if not isinstance(checksum, str) or checksum != _checksum(body):
            return None
        if (payload.get("cache_schema") != _CACHE_SCHEMA
                or payload.get("fingerprint") != fingerprint
                or payload.get("goal_id") != goal_id
                or not isinstance(payload.get("state"), dict)
                or not isinstance(payload.get("leases"), dict)):
            return None
        if include_leases and not {"active", "last", "max_fence"}.issubset(
                payload["leases"]):
            return None
        state = dict(payload["state"])
        state["evaluated_at"] = evaluated_at
        leases = _leases_at(payload["leases"], evaluated_at) if include_leases else {}
        return CachedProjection(state, leases)
    except (OSError, ValueError, TypeError):
        return None


def _write_atomic(sidecar: Path, payload: Dict[str, Any]) -> None:
    """Best-effort atomic write; cache IO can never make state.read fail."""
    temporary = None
    fd = None
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".projection-", suffix=".tmp",
                                         dir=str(sidecar.parent))
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar)
        temporary = None
    except OSError:
        # The sidecar is an optimization only.  Missing permissions, a full
        # disk, and replacement races all fall back to the authoritative log.
        pass
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def read_projection(path: Union[str, Path], now: Any, goal_id: str,
                    include_leases: bool = False) -> CachedProjection:
    """Read or recompute a projection for exactly ``(log, goal_id, now)``.

    Fingerprints are checked again after reading either cache or log.  Thus an
    append or inode replacement concurrent with the read cannot bless an entry
    for bytes other than those projected.  Under continuous mutation this
    simply returns a fresh uncached fold, preserving correctness over hit rate.
    """
    events_path = Path(path)
    evaluated_at = canonical_timestamp(now)
    sidecar = cache_path(events_path, evaluated_at, goal_id)

    before = _fingerprint(events_path)
    cached = _load(sidecar, before, evaluated_at, goal_id, include_leases)
    if cached is not None and _fingerprint(events_path) == before:
        return cached

    # Retry once if a writer changes the file while it is read.  read_all itself
    # remains the source of truth; an unstable read is returned but never cached.
    value = None
    for _attempt in range(2):
        before = _fingerprint(events_path)
        read = read_all(events_path)
        state = project(read.records, evaluated_at, goal_id, read.skipped)
        leases = (project_leases(read.records, evaluated_at, goal_id)
                  if include_leases else {})
        value = CachedProjection(state, leases)
        after = _fingerprint(events_path)
        if after == before:
            _write_atomic(sidecar, _payload(after, evaluated_at, goal_id, value))
            return value

    assert value is not None  # the fixed retry loop always executes
    return value
