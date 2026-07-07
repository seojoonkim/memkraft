"""store_core — envelope v1 append/read for JSONL stores (MemKraft 2.13, S1+S2).

Atomic single-line appends and sequential reads over a JSONL file. Each
record is wrapped in an envelope: ``id`` and ``created_at`` (UTC ISO-8601)
are filled in when the caller omits them; ``schema_version`` is always
normalized to 1 (envelope metadata, not caller data); all caller-supplied
payload fields are preserved as-is.

API (internal tier):
    append(path, record) -> dict          # the enveloped record as written
    read_all(path, include_tombstoned=False) -> ReadResult
    mark_tombstone(path, record_id) -> dict   # the appended marker record

Tombstones (S2): the store is append-only — ``mark_tombstone`` never
rewrites existing lines; it appends a marker record
``{"tombstone": true, "tombstone_of": <id>}``. ``read_all`` hides, by
default, records carrying ``tombstone: true`` (markers included) and any
record whose ``id`` is targeted by a marker; ``include_tombstoned=True``
exposes everything (compaction/audit path).

Concurrency: append takes an ``fcntl.flock`` exclusive lock on the store
file and writes the whole newline-terminated line with a single
``os.write`` call, so concurrent appenders never interleave bytes.

Compaction (physical removal) and snapshots are out of scope here (S3).

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Union

SCHEMA_VERSION = 1


class RecordNotFoundError(KeyError):
    """Raised when a store operation targets a record ``id`` not in the file."""

    def __init__(self, path: Union[str, Path], record_id: str):
        super().__init__(record_id)
        self.path = Path(path)
        self.record_id = record_id

    def __str__(self) -> str:
        return f"record {self.record_id!r} not found in {self.path}"


class ReadResult(NamedTuple):
    """Records read from a store plus the count of corrupt lines skipped."""

    records: List[Dict[str, Any]]
    skipped: int


def append(path: Union[str, Path], record: Dict[str, Any]) -> Dict[str, Any]:
    """Append ``record`` to the JSONL store at ``path`` as one envelope v1 line.

    Fills ``id`` and ``created_at`` (UTC ISO-8601) when missing; a
    caller-supplied ``schema_version`` is overridden and normalized to 1.
    Creates parent directories and the file if needed. Returns the
    enveloped record exactly as written.
    """
    path = Path(path)
    enveloped: Dict[str, Any] = dict(record)
    enveloped.setdefault("id", uuid.uuid4().hex)
    enveloped["schema_version"] = SCHEMA_VERSION
    enveloped.setdefault(
        "created_at", datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    line = json.dumps(enveloped, ensure_ascii=False, separators=(",", ":")) + "\n"
    data = line.encode("utf-8")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return enveloped


def read_all(path: Union[str, Path], include_tombstoned: bool = False) -> ReadResult:
    """Read all records from the JSONL store at ``path`` in file order.

    By default, records with ``tombstone: true`` (marker records included)
    and records whose ``id`` is targeted by a marker's ``tombstone_of`` are
    hidden; pass ``include_tombstoned=True`` to expose them (compaction and
    audit path). Corrupt lines (invalid JSON or non-object values) are
    skipped and counted in ``ReadResult.skipped``. A missing file reads as
    empty.
    """
    path = Path(path)
    records: List[Dict[str, Any]] = []
    skipped = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if not isinstance(obj, dict):
                    skipped += 1
                    continue
                records.append(obj)
    except FileNotFoundError:
        pass
    if not include_tombstoned:
        tombstoned_ids = {
            r["tombstone_of"]
            for r in records
            if r.get("tombstone") and r.get("tombstone_of") is not None
        }
        records = [
            r
            for r in records
            if not r.get("tombstone") and r.get("id") not in tombstoned_ids
        ]
    return ReadResult(records=records, skipped=skipped)


def mark_tombstone(path: Union[str, Path], record_id: str) -> Dict[str, Any]:
    """Tombstone ``record_id`` by appending a marker record (append-only).

    Existing lines are never modified; the marker
    ``{"tombstone": true, "tombstone_of": record_id}`` suppresses the target
    ``id`` on default reads. Raises :class:`RecordNotFoundError` when no
    record in the file has that ``id``. Returns the enveloped marker as
    written.
    """
    existing = read_all(path, include_tombstoned=True).records
    if not any(r.get("id") == record_id for r in existing):
        raise RecordNotFoundError(path, record_id)
    return append(path, {"tombstone": True, "tombstone_of": record_id})
