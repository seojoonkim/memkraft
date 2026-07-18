"""Fail-closed local trajectory provenance bridge to spike 001."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_SPIKE_001 = Path(__file__).resolve().parents[1] / "001-reasoning-path-executor"
if str(_SPIKE_001) not in sys.path:
    sys.path.insert(0, str(_SPIKE_001))

# This import target is fixed by this module, never selected by trajectory data.
from executor import ExecutionResult, execute_validated_path  # noqa: E402

PARSER_IDENTITY = "spike001.execute_validated_path.exact-grammar.v1"
MAX_TRAJECTORY_BYTES = 64 * 1024
MAX_TRAJECTORY_LINES = 256
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,120}")
_IDS = (
    "A.inclusion_exclusion_sum",
    "B.legendre_factorial_exponent",
    "C.shortest_grid_paths",
    "D.divisor_count_prime_powers",
    "E.sum_squares_or_cubes",
    "F.modular_exponentiation",
)


def _digest(procedure_id: str, version: int) -> str:
    canonical = json.dumps(
        {
            "parser_identity": PARSER_IDENTITY,
            "procedure_id": procedure_id,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


PROCEDURE_REGISTRY = {
    procedure_id: {"version": 1, "registry_digest": _digest(procedure_id, 1)}
    for procedure_id in _IDS
}
_MANIFEST_HMAC_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class TrustedManifestEntry:
    task_id: str
    path: str
    content_sha256: str
    procedure_id: str
    procedure_version: int
    registry_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_id, str)
            or not _SAFE_TASK_ID.fullmatch(self.task_id)
            or not isinstance(self.path, str)
            or not Path(self.path).is_absolute()
            or not isinstance(self.content_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256)
            or not isinstance(self.procedure_id, str)
            or type(self.procedure_version) is not int
            or not isinstance(self.registry_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.registry_digest)
        ):
            raise TypeError("invalid trusted manifest entry")
        expected = PROCEDURE_REGISTRY.get(self.procedure_id)
        if expected != {
            "version": self.procedure_version,
            "registry_digest": self.registry_digest,
        }:
            raise ValueError("manifest entry is not in the exact registry")


def _manifest_payload(entries: tuple[TrustedManifestEntry, ...]) -> bytes:
    return json.dumps(
        [
            {
                "task_id": entry.task_id,
                "path": entry.path,
                "content_sha256": entry.content_sha256,
                "procedure_id": entry.procedure_id,
                "procedure_version": entry.procedure_version,
                "registry_digest": entry.registry_digest,
            }
            for entry in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _seal_manifest(entries: tuple[TrustedManifestEntry, ...]) -> str:
    return hmac.new(
        _MANIFEST_HMAC_KEY, _manifest_payload(entries), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class TrustedManifest:
    """Process-local sealed authorization outside the trajectory store boundary."""

    entries: tuple[TrustedManifestEntry, ...]
    seal: str

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            type(entry) is not TrustedManifestEntry for entry in self.entries
        ):
            raise TypeError("manifest entries must be an exact tuple of trusted entries")
        if not isinstance(self.seal, str) or not re.fullmatch(r"[0-9a-f]{64}", self.seal):
            raise TypeError("manifest seal must be a SHA-256 HMAC")
        task_ids = [entry.task_id for entry in self.entries]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("duplicate manifest task_id")


def procedure_ref(procedure_id: str) -> dict[str, Any]:
    """Return the immutable registry reference to record in step metadata."""
    entry = PROCEDURE_REGISTRY[procedure_id]
    return {
        "id": procedure_id,
        "version": entry["version"],
        "registry_digest": entry["registry_digest"],
    }


def _fallback(reason: str) -> ExecutionResult:
    return ExecutionResult("fallback", None, None, reason)


def _read_bounded_regular_file(path: Path) -> bytes:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("trajectory is not a regular non-symlink file")
    if before.st_size > MAX_TRAJECTORY_BYTES:
        raise ValueError("trajectory exceeds byte bound")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("opened trajectory is not regular")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("trajectory changed while opening")
        data = os.read(descriptor, MAX_TRAJECTORY_BYTES + 1)
        if len(data) > MAX_TRAJECTORY_BYTES:
            raise ValueError("trajectory exceeds byte bound")
        if os.read(descriptor, 1):
            raise ValueError("trajectory exceeds byte bound")
        return data
    finally:
        os.close(descriptor)


def build_trusted_manifest(
    entries: Iterable[tuple[os.PathLike[str] | str, str, str]],
) -> TrustedManifest:
    """Authorize completed bytes; call only from trusted seeding/orchestration."""
    if isinstance(entries, (str, bytes)):
        raise TypeError("manifest entries must be tuple triples")
    authorized: list[TrustedManifestEntry] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, tuple) or len(item) != 3:
            raise TypeError("manifest entry must be an exact tuple triple")
        path, task_id, procedure_id = item
        if (
            not isinstance(path, (str, os.PathLike))
            or not isinstance(task_id, str)
            or not _SAFE_TASK_ID.fullmatch(task_id)
            or not isinstance(procedure_id, str)
        ):
            raise TypeError("invalid manifest entry types")
        if task_id in seen:
            raise ValueError("duplicate manifest task_id")
        if procedure_id not in PROCEDURE_REGISTRY:
            raise ValueError("unknown manifest procedure")
        resolved = Path(path).resolve(strict=True)
        if resolved.name != f"{task_id}.jsonl":
            raise ValueError("manifest path and task_id disagree")
        raw = _read_bounded_regular_file(resolved)
        reference = procedure_ref(procedure_id)
        authorized.append(
            TrustedManifestEntry(
                task_id=task_id,
                path=str(resolved),
                content_sha256=hashlib.sha256(raw).hexdigest(),
                procedure_id=procedure_id,
                procedure_version=reference["version"],
                registry_digest=reference["registry_digest"],
            )
        )
        seen.add(task_id)
    frozen_entries = tuple(authorized)
    return TrustedManifest(frozen_entries, _seal_manifest(frozen_entries))


def _verify(
    task: str,
    hit: Any,
    base_dir: os.PathLike[str] | str,
    manifest: TrustedManifest,
) -> str:
    if not isinstance(task, str) or not isinstance(hit, dict):
        raise ValueError("invalid task or hit")
    task_id = hit.get("task_id")
    hit_path = hit.get("path")
    if (
        not isinstance(task_id, str)
        or not _SAFE_TASK_ID.fullmatch(task_id)
        or not isinstance(hit_path, str)
        or not hit_path
        or hit.get("status") != "success"
    ):
        raise ValueError("invalid hit identity, status, or path")

    supplied = Path(hit_path)
    if ".." in supplied.parts:
        raise ValueError("path traversal component")
    root = (Path(base_dir).resolve() / ".memkraft" / "trajectories").resolve()
    resolved = supplied.resolve(strict=True)
    if resolved.parent != root or resolved.name != f"{task_id}.jsonl":
        raise ValueError("trajectory path is outside store or has forged filename")

    raw = _read_bounded_regular_file(supplied)
    lines = raw.splitlines()
    if not lines or len(lines) > MAX_TRAJECTORY_LINES:
        raise ValueError("trajectory line count outside bounds")
    records = []
    for line in lines:
        value = json.loads(line.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("trajectory record must be an object")
        records.append(value)

    starts = [record for record in records if record.get("kind") == "start"]
    completes = [record for record in records if record.get("kind") == "complete"]
    if len(starts) != 1 or len(completes) != 1:
        raise ValueError("trajectory needs exactly one start and completion")
    if records[0] is not starts[0] or records[-1] is not completes[0]:
        raise ValueError("start and completion order is invalid")
    if any(record.get("kind") not in {"start", "step", "complete"} for record in records):
        raise ValueError("unknown trajectory record kind")
    if any(record.get("task_id") != task_id for record in records):
        raise ValueError("trajectory task identity mismatch")

    start = starts[0]
    complete = completes[0]
    if complete.get("status") != "success":
        raise ValueError("trajectory did not complete successfully")
    for hit_key, record, record_key in (
        ("title", start, "title"),
        ("lesson", complete, "lesson"),
        ("pattern_signature", complete, "pattern_signature"),
    ):
        if not isinstance(hit.get(hit_key), str) or hit[hit_key] != record.get(record_key):
            raise ValueError("recall summary does not match trajectory")

    references = []
    for record in records:
        if record.get("kind") != "step":
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("step metadata must be an object")
        if "procedure_ref" in metadata:
            references.append(metadata["procedure_ref"])
    if len(references) != 1:
        raise ValueError("trajectory needs exactly one procedure reference")

    reference = references[0]
    if not isinstance(reference, dict) or set(reference) != {
        "id",
        "version",
        "registry_digest",
    }:
        raise ValueError("invalid procedure reference shape")
    procedure_id = reference["id"]
    version = reference["version"]
    digest = reference["registry_digest"]
    if (
        not isinstance(procedure_id, str)
        or type(version) is not int
        or not isinstance(digest, str)
    ):
        raise ValueError("invalid procedure reference types")
    expected = PROCEDURE_REGISTRY.get(procedure_id)
    if expected is None or reference != procedure_ref(procedure_id):
        raise ValueError("procedure reference is not in the exact registry")

    if type(manifest) is not TrustedManifest:
        raise TypeError("missing or malformed trusted manifest")
    # Frozen dataclasses can still be forged via object.__new__/object.__setattr__.
    # Revalidate the complete trust object at every authorization boundary.
    manifest.__post_init__()
    for entry in manifest.entries:
        entry.__post_init__()
    if not hmac.compare_digest(manifest.seal, _seal_manifest(manifest.entries)):
        raise ValueError("trusted manifest seal mismatch")
    matches = [entry for entry in manifest.entries if entry.task_id == task_id]
    if len(matches) != 1 or type(matches[0]) is not TrustedManifestEntry:
        raise ValueError("trajectory is not authorized by trusted manifest")
    authorized = matches[0]
    if (
        authorized.path != str(resolved)
        or authorized.procedure_id != procedure_id
        or authorized.procedure_version != version
        or authorized.registry_digest != digest
        or not hmac.compare_digest(
            authorized.content_sha256, hashlib.sha256(raw).hexdigest()
        )
    ):
        raise ValueError("trusted manifest binding mismatch")
    return procedure_id


def execute_recalled_path(
    task: str,
    hit: Any,
    *,
    base_dir: os.PathLike[str] | str,
    manifest: TrustedManifest | None,
) -> ExecutionResult:
    """Verify store evidence plus external authorization, then invoke executor."""
    try:
        procedure_id = _verify(task, hit, base_dir, manifest)  # type: ignore[arg-type]
    except (
        AttributeError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        return _fallback(f"provenance verification failed: {exc}")
    return execute_validated_path(task, procedure_id=procedure_id, trusted=True)
