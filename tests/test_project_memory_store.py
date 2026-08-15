import json
import os
from pathlib import Path

import pytest

from memkraft import MemKraft

AS_OF = "2026-08-15T00:00:00Z"


def _project(tmp_path):
    p = tmp_path / "project"; p.mkdir(); (p / "x.md").write_text("# X\nbody\n")
    return p


def _tree(root):
    return [(str(p.relative_to(root)), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]


def test_dry_run_writes_nothing(tmp_path):
    p = _project(tmp_path); base = tmp_path / "memory"; base.mkdir(); before = _tree(base)
    out = MemKraft(base_dir=base).project_build(p, as_of=AS_OF)
    assert _tree(base) == before and out["dry_run"] and out["snapshot_id"]


def test_unowned_preexisting_root_is_refused(tmp_path):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    plan = mk.project_build(p, as_of=AS_OF)
    root = Path(plan["output_root"]); root.mkdir(parents=True)
    from memkraft.project_memory.errors import ProjectMemoryError
    with pytest.raises(ProjectMemoryError) as exc: mk.project_build(p, as_of=AS_OF, dry_run=False)
    assert exc.value.code == "E_PM_OWNERSHIP"


def test_atomic_replace_failure_leaves_no_snapshot(tmp_path, monkeypatch):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    import memkraft.project_memory.store as store
    real = store.os.replace
    monkeypatch.setattr(store.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError): mk.project_build(p, as_of=AS_OF, dry_run=False)
    root = Path(mk.project_build(p, as_of=AS_OF)["output_root"])
    assert not (root / "manifest.json").exists() and not list((root / "snapshots").glob("sha256:*"))
    monkeypatch.setattr(store.os, "replace", real)
    assert mk.project_build(p, as_of=AS_OF, dry_run=False)["status"] == "applied"


def test_manifest_replace_failure_after_existing_build_is_retryable(tmp_path, monkeypatch):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    mk.project_build(p, as_of=AS_OF, dry_run=False)
    (p / "x.md").write_text("# X\nchanged\n")
    import memkraft.project_memory.store as store
    real = store.os.replace

    def fail_manifest(source, destination):
        if str(destination).endswith("manifest.json"):
            raise OSError("manifest replace failed")
        return real(source, destination)

    monkeypatch.setattr(store.os, "replace", fail_manifest)
    with pytest.raises(OSError, match="manifest replace failed"):
        mk.project_update(p, as_of=AS_OF, dry_run=False)
    monkeypatch.setattr(store.os, "replace", real)
    assert mk.project_update(p, as_of=AS_OF, dry_run=False)["status"] == "applied"


def test_fsync_failure_after_manifest_publish_keeps_published_snapshot(tmp_path, monkeypatch):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    import memkraft.project_memory.store as store
    real_replace = store.os.replace
    real_fsync_dir = store._fsync_dir
    state = {"manifest_published": False}

    def track_replace(source, destination):
        result = real_replace(source, destination)
        if str(destination).endswith("manifest.json"):
            state["manifest_published"] = True
        return result

    def fail_after_manifest(directory):
        if state["manifest_published"]:
            raise OSError("root fsync failed")
        return real_fsync_dir(directory)

    monkeypatch.setattr(store.os, "replace", track_replace)
    monkeypatch.setattr(store, "_fsync_dir", fail_after_manifest)
    with pytest.raises(OSError, match="root fsync failed"):
        mk.project_build(p, as_of=AS_OF, dry_run=False)
    monkeypatch.setattr(store, "_fsync_dir", real_fsync_dir)
    monkeypatch.setattr(store.os, "replace", real_replace)
    assert mk.project_context("body", p)["sections"]
    assert mk.project_build(p, as_of=AS_OF, dry_run=False)["status"] == "already_applied"


def test_idempotent_apply_adds_no_bytes(tmp_path):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    mk.project_build(p, as_of=AS_OF, dry_run=False); before = _tree(mk.base_dir)
    out = mk.project_build(p, as_of=AS_OF, dry_run=False)
    assert out["status"] == "already_applied" and _tree(mk.base_dir) == before


def test_conflicting_snapshot_is_digest_mismatch(tmp_path):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory")
    plan = mk.project_build(p, as_of=AS_OF); root = Path(plan["output_root"])
    root.mkdir(parents=True); (root / ".memkraft-pmc-owned").write_text(json.dumps({"compiler_schema":1,"project_id":plan["project_id"]}))
    dest = root / "snapshots" / plan["snapshot_id"]; dest.mkdir(parents=True); (dest / "bad").write_text("bad")
    from memkraft.project_memory.errors import ProjectMemoryError
    with pytest.raises(ProjectMemoryError) as exc: mk.project_build(p, as_of=AS_OF, dry_run=False)
    assert exc.value.code == "E_PM_DIGEST_MISMATCH"


def test_newer_marker_schema_is_rejected(tmp_path):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory"); plan = mk.project_build(p, as_of=AS_OF)
    root = Path(plan["output_root"]); root.mkdir(parents=True); (root / ".memkraft-pmc-owned").write_text(json.dumps({"compiler_schema":2,"project_id":plan["project_id"]}))
    from memkraft.project_memory.errors import ProjectMemoryError
    with pytest.raises(ProjectMemoryError) as exc: mk.project_build(p, as_of=AS_OF, dry_run=False)
    assert exc.value.code == "E_PM_SCHEMA_UNKNOWN"


def test_written_modes_are_private(tmp_path):
    p = _project(tmp_path); mk = MemKraft(base_dir=tmp_path / "memory"); out = mk.project_build(p, as_of=AS_OF, dry_run=False)
    root = Path(out["output_root"])
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in root.rglob("*") if f.is_file())
