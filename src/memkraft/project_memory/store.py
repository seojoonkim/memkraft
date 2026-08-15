"""Private owned snapshot store for Project Memory Compiler."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..store_core import StoreBusy, _lock_current_inode, _unlock
from .digest import canonical_json
from .errors import fail
from .model import COMPILER_SCHEMA

LOCK_TIMEOUT_S = 2.0
MARKER = ".memkraft-pmc-owned"


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(path), 0o700)


def _write_file(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("E_PM_DIGEST_MISMATCH", "invalid store metadata", path=str(path), error=str(exc))
    if not isinstance(value, dict):
        fail("E_PM_DIGEST_MISMATCH", "store metadata is not an object", path=str(path))
    schema = value.get("compiler_schema")
    if isinstance(schema, int) and schema > COMPILER_SCHEMA:
        fail("E_PM_SCHEMA_UNKNOWN", "newer compiler schema", path=str(path))
    if schema != COMPILER_SCHEMA:
        fail("E_PM_DIGEST_MISMATCH", "compiler schema mismatch", path=str(path))
    return value


def validate_owned(root: Path, project_id: str, create: bool = False) -> None:
    marker = root / MARKER
    if root.exists():
        if not marker.exists():
            fail("E_PM_OWNERSHIP", "output directory is not owned", path=str(root))
        data = _load_json(marker)
        if data.get("project_id") != project_id:
            fail("E_PM_OWNERSHIP", "ownership marker does not match project", path=str(root))
        return
    if not create:
        return
    _secure_dir(root)
    _write_file(marker, canonical_json({"compiler_schema": 1, "project_id": project_id}).encode("utf-8"))
    _fsync_dir(root)


def read_manifest(root: Path, project_id: str) -> Optional[Dict[str, Any]]:
    if not root.exists():
        return None
    validate_owned(root, project_id)
    path = root / "manifest.json"
    return _load_json(path) if path.exists() else None


def apply_snapshot(root: Path, project_id: str, compiled: Dict[str, Any],
                   project_header: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    validate_owned(root, project_id, create=True)
    _secure_dir(root / "snapshots")
    lock_path = root / "pmc.lock"
    if not lock_path.exists():
        _write_file(lock_path, b"")
    try:
        fd = _lock_current_inode(str(lock_path), os.O_RDWR, timeout_s=LOCK_TIMEOUT_S)
    except StoreBusy:
        fail("E_PM_STORE_BUSY", "project memory store is busy", path=str(lock_path))
    try:
        current = read_manifest(root, project_id)
        snapshot_id = compiled["snapshot_id"]
        destination = root / "snapshots" / snapshot_id
        if current and current.get("current_snapshot_id") == snapshot_id:
            if destination.exists():
                return "already_applied"
            fail("E_PM_DIGEST_MISMATCH", "manifest points to missing snapshot")
        if destination.exists():
            fail("E_PM_DIGEST_MISMATCH", "snapshot identity already has different content",
                 path=str(destination))
        temp = Path(tempfile.mkdtemp(prefix=".pmc-", dir=str(root / "snapshots")))
        try:
            os.chmod(str(temp), 0o700)
            _write_file(temp / "project.json", (canonical_json(project_header) + "\n").encode("utf-8"))
            _write_file(temp / "evidence.jsonl", compiled["evidence_bytes"])
            _write_file(temp / "sections.jsonl", compiled["sections_bytes"])
            _fsync_dir(temp)
            os.replace(str(temp), str(destination))
            _fsync_dir(root / "snapshots")
            manifest_temp = root / ".manifest.tmp"
            _write_file(manifest_temp, (canonical_json(manifest) + "\n").encode("utf-8"))
            os.replace(str(manifest_temp), str(root / "manifest.json"))
            _fsync_dir(root)
        except BaseException:
            shutil.rmtree(str(temp), ignore_errors=True)
            if destination.exists() and not (root / "manifest.json").exists():
                shutil.rmtree(str(destination), ignore_errors=True)
            try:
                (root / ".manifest.tmp").unlink()
            except FileNotFoundError:
                pass
            raise
        return "applied"
    finally:
        _unlock(fd)
        os.close(fd)
