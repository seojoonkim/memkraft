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
    compact(path) -> CompactResult        # physical removal (S3)

Tombstones (S2): the store is append-only — ``mark_tombstone`` never
rewrites existing lines; it appends a marker record
``{"tombstone": true, "tombstone_of": <id>}``. ``read_all`` hides, by
default, records carrying ``tombstone: true`` (markers included) and any
record whose ``id`` is targeted by a marker; ``include_tombstoned=True``
exposes everything (compaction/audit path).

Concurrency: append takes an ``fcntl.flock`` exclusive lock on the store
file and writes the whole newline-terminated line with a single
``os.write`` call, so concurrent appenders never interleave bytes. Because
``compact`` swaps the store to a new inode with ``os.replace`` while
holding that lock, every locker re-stats the path after acquiring the lock
and retries on a fresh descriptor if the inode changed — otherwise a
writer that queued on the old inode would append to an orphaned file and
the record would be silently lost (S4 concurrency gate).

Compaction (S3): ``compact`` physically drops tombstone markers, the
records they target, and corrupt lines, preserving live lines byte-for-byte
in file order. It rewrites through ``<store>.compact.tmp`` in the same
directory and swaps it in with ``os.replace``, so a kill mid-compact leaves
either the original or the finished file; a stale temp from an interrupted
run is discarded by the next compact (or by this one, via the replace).
Automatic compaction triggers and snapshots are out of scope (S4+).

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Union

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

SCHEMA_VERSION = 1

#: Poll interval for the bounded non-blocking lock retry.
_LOCK_RETRY_S = 0.005


class StoreBusy(RuntimeError):
    """Raised when a bounded lock attempt gives up: the store stayed locked."""

    def __init__(self, path: Union[str, Path]):
        super().__init__(f"store busy (lock held): {path}")
        self.path = Path(path)


def _lock_exclusive(
    fd: int, deadline: Optional[float] = None, path_str: str = ""
) -> None:
    """Take a process-level exclusive lock on ``fd``.

    Blocks until acquired when ``deadline`` is ``None``. Otherwise retries a
    non-blocking lock until ``time.monotonic()`` passes ``deadline``, then
    raises :class:`StoreBusy`.
    """
    if deadline is None:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise StoreBusy(path_str)
            time.sleep(_LOCK_RETRY_S)


def _lock_shared(fd: int) -> None:
    """Take a blocking shared lock, or the portable exclusive fallback."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_SH)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _lock_current_inode(
    path_str: str, flags: int, timeout_s: Optional[float] = None
) -> int:
    """Open ``path_str`` and take an exclusive ``flock`` on its *current* inode.

    ``flock`` binds to the open file, not the path: a process that opened
    the store, then waited while ``compact`` held the lock and ran
    ``os.replace``, would wake up holding a lock on an inode no path points
    to — its write would be lost. So after acquiring the lock, re-stat the
    path; if the file was replaced (or unlinked) in the meantime, release
    and start over on the file now at the path. Raises whatever ``os.open``
    raises (e.g. ``FileNotFoundError`` without ``O_CREAT``).

    With ``timeout_s`` set, the wait is bounded: the lock is retried
    non-blockingly and :class:`StoreBusy` is raised once the budget is spent.
    The default keeps the unbounded blocking wait.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        fd = os.open(path_str, flags, 0o644)
        try:
            _lock_exclusive(fd, deadline, path_str)
            st_fd = os.fstat(fd)
            try:
                st_path = os.stat(path_str)
            except FileNotFoundError:
                st_path = None
            if st_path is not None and (st_path.st_dev, st_path.st_ino) == (
                st_fd.st_dev,
                st_fd.st_ino,
            ):
                return fd
        except BaseException:
            os.close(fd)
            raise
        _unlock(fd)
        os.close(fd)


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


class CompactResult(NamedTuple):
    """Line counts from a compact run: what stayed and why each removal happened."""

    kept: int
    removed_tombstoned: int
    removed_markers: int
    removed_corrupt: int


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
    fd = _lock_current_inode(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, data)
    finally:
        _unlock(fd)
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


def _compact_tmp_path(path: Path) -> Path:
    return path.with_name(path.name + ".compact.tmp")


def compact(path: Union[str, Path]) -> CompactResult:
    """Physically remove tombstoned records, markers, and corrupt lines.

    Live lines keep their original bytes and order. The store is rewritten
    through ``<store>.compact.tmp`` and swapped in with ``os.replace`` under
    the same exclusive lock append takes, so readers only ever see the
    original or the finished file; a stale temp left by an interrupted
    compact is discarded. A missing or empty file is a no-op success (a
    missing file is not created).
    """
    path = Path(path)
    tmp = _compact_tmp_path(path)
    try:
        fd = _lock_current_inode(str(path), os.O_RDWR)
    except FileNotFoundError:
        tmp.unlink(missing_ok=True)
        return CompactResult(0, 0, 0, 0)
    try:
        try:
            raw = b""
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                raw += chunk

            lines = raw.splitlines(keepends=True)
            parsed: List[Any] = []  # dict per valid record line, None per corrupt
            tombstoned_ids = set()
            for line in lines:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    parsed.append(None)
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    obj = None
                if not isinstance(obj, dict):
                    obj = None
                elif obj.get("tombstone") and obj.get("tombstone_of") is not None:
                    tombstoned_ids.add(obj["tombstone_of"])
                parsed.append(obj)

            kept_lines: List[bytes] = []
            kept = removed_tombstoned = removed_markers = removed_corrupt = 0
            for line, obj in zip(lines, parsed):
                if obj is None:
                    if line.strip():  # blank filler lines vanish uncounted
                        removed_corrupt += 1
                    continue
                if obj.get("tombstone"):
                    removed_markers += 1
                    continue
                if obj.get("id") in tombstoned_ids:
                    removed_tombstoned += 1
                    continue
                kept += 1
                kept_lines.append(line if line.endswith(b"\n") else line + b"\n")

            out = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(out, b"".join(kept_lines))
                os.fsync(out)
            finally:
                os.close(out)
            os.replace(str(tmp), str(path))
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
    return CompactResult(
        kept=kept,
        removed_tombstoned=removed_tombstoned,
        removed_markers=removed_markers,
        removed_corrupt=removed_corrupt,
    )
