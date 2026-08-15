import os
from pathlib import Path

import pytest

from memkraft import MemKraft

AS_OF = "2026-08-15T00:00:00Z"


def _p(tmp_path):
    p=tmp_path/"project"; p.mkdir(); (p/"x.md").write_text("# Architecture\nrelease lineage audit\n## Other\nnoise\n"); return p


def test_status_never_built_is_zero_write(tmp_path):
    p=_p(tmp_path); base=tmp_path/"memory"; mk=MemKraft(base_dir=base)
    out=mk.project_status(p)
    assert not out["initialized"] and out["reason"] == "never_built" and not base.exists()


def test_status_rehashes_metadata_only_change(tmp_path):
    p=_p(tmp_path); mk=MemKraft(base_dir=tmp_path/"memory"); mk.project_build(p, as_of=AS_OF, dry_run=False)
    os.utime(p/"x.md", None)
    assert mk.project_status(p)["stale"] is False


def test_status_reports_added_changed_removed(tmp_path):
    p=_p(tmp_path); mk=MemKraft(base_dir=tmp_path/"memory"); mk.project_build(p, as_of=AS_OF, dry_run=False)
    (p/"x.md").write_text("# Changed\nbytes\n"); (p/"new.md").write_text("new");
    status=mk.project_status(p); assert status["changed"] == ["x.md"] and status["added"] == ["new.md"]
    (p/"x.md").unlink(); status=mk.project_status(p); assert status["removed"] == ["x.md"]


def test_update_matches_fresh_full_build(tmp_path):
    p=_p(tmp_path); mk=MemKraft(base_dir=tmp_path/"m1"); mk.project_build(p, as_of=AS_OF, dry_run=False)
    (p/"x.md").write_text("# New\nrelease lineage\n")
    updated=mk.project_update(p, as_of=AS_OF, dry_run=False)
    fresh=MemKraft(base_dir=tmp_path/"m2").project_build(p, as_of=AS_OF, dry_run=False)
    assert updated["snapshot_id"] == fresh["snapshot_id"]
    assert Path(updated["snapshot_path"], "sections.jsonl").read_bytes() == Path(fresh["snapshot_path"], "sections.jsonl").read_bytes()


def test_context_is_bounded_deterministic_and_cited(tmp_path):
    p=_p(tmp_path); mk=MemKraft(base_dir=tmp_path/"memory"); mk.project_build(p, as_of=AS_OF, dry_run=False)
    a=mk.project_context("release lineage", p, budget=100, top_k=20); b=mk.project_context("release lineage", p, budget=100, top_k=20)
    assert a == b and a["estimated_tokens"] <= 100
    assert all({"locator","content_digest","observation_id"} <= set(s) for s in a["sections"])


def test_context_handles_repeated_identical_sections(tmp_path):
    p=tmp_path/"project"; p.mkdir(); (p/"x.md").write_text("# A\n# A\n")
    mk=MemKraft(base_dir=tmp_path/"memory"); mk.project_build(p, as_of=AS_OF, dry_run=False)
    out=mk.project_context("A", p, budget=1000, top_k=20)
    assert len(out["sections"]) == 2
    assert len({row["section_id"] for row in out["sections"]}) == 2


def test_context_requires_build(tmp_path):
    from memkraft.project_memory.errors import ProjectMemoryError
    with pytest.raises(ProjectMemoryError) as exc: MemKraft(base_dir=tmp_path/"m").project_context("q", _p(tmp_path))
    assert exc.value.code == "E_PM_NOT_BUILT"


def test_context_rejects_tampered_snapshot_evidence(tmp_path):
    from memkraft.project_memory.errors import ProjectMemoryError
    p=_p(tmp_path); mk=MemKraft(base_dir=tmp_path/"memory")
    built=mk.project_build(p, as_of=AS_OF, dry_run=False)
    evidence=Path(built["snapshot_path"], "evidence.jsonl")
    evidence.write_text(evidence.read_text().replace("release lineage audit", "TAMPER"))
    with pytest.raises(ProjectMemoryError) as exc:
        mk.project_context("TAMPER", p)
    assert exc.value.code == "E_PM_DIGEST_MISMATCH"


def test_symlink_escape_and_output_as_project_are_rejected(tmp_path):
    p=_p(tmp_path); outside=tmp_path/"outside.md"; outside.write_text("secret")
    (p/"escape.md").symlink_to(outside)
    from memkraft.project_memory.errors import ProjectMemoryError
    with pytest.raises(ProjectMemoryError) as exc: MemKraft(base_dir=tmp_path/"m").project_build(p, as_of=AS_OF)
    assert exc.value.code == "E_PM_PATH_ESCAPE"


def test_internal_symlink_is_not_double_counted(tmp_path):
    p=_p(tmp_path); (p/"alias.md").symlink_to(p/"x.md")
    out=MemKraft(base_dir=tmp_path/"m").project_build(p, as_of=AS_OF)
    assert out["inputs"]["files"] == 1


def test_excluded_dangling_symlink_does_not_abort_scan(tmp_path):
    p=_p(tmp_path); (p/"ignored.md").symlink_to(p/"missing.md")
    out=MemKraft(base_dir=tmp_path/"m").project_build(
        p, as_of=AS_OF, exclude=["ignored.md"])
    assert out["inputs"]["files"] == 1


def test_non_interference_and_methods_bound(tmp_path):
    mk=MemKraft(base_dir=tmp_path/"m")
    assert all(callable(getattr(mk, n)) for n in ("project_build","project_update","project_status","project_context"))
    assert not (mk.base_dir/".memkraft/project-memory").exists()


def test_project_tree_never_changes(tmp_path):
    p=_p(tmp_path); before={str(x.relative_to(p)):x.read_bytes() for x in p.rglob("*") if x.is_file()}
    mk=MemKraft(base_dir=tmp_path/"m"); mk.project_build(p, as_of=AS_OF, dry_run=False); mk.project_update(p, as_of=AS_OF, dry_run=False)
    after={str(x.relative_to(p)):x.read_bytes() for x in p.rglob("*") if x.is_file()}; assert before == after
