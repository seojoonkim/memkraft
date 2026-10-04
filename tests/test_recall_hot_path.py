"""Regression tests for the recall hot path on large corpora.

1. The trusted fast path must decide "corpus unchanged" without building a
   ``Path`` object per file (the dominant cost on a 30k-file store), while
   still detecting out-of-process edits via (mtime_ns, size).
2. Hermes prefetch must not let chat-derived template entities crowd out real
   documents that rank just below them.
"""
from __future__ import annotations

import io
import os
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft import _corpus_index as ci
from memkraft import _core_lifecycle_helpers as lh


@pytest.fixture(autouse=True)
def _fresh_index():
    ci.reset_for_tests()
    yield
    ci.reset_for_tests()


def _store(tmp_path: Path, n: int = 40) -> MemKraft:
    mk = MemKraft(base_dir=str(tmp_path / "mem"))
    with redirect_stdout(io.StringIO()):
        mk.init(verbose=False)
    for i in range(n):
        (mk.live_notes_dir / f"note-{i:03d}.md").write_text(
            f"# note {i}\n\nalpha beta token{i}\n", encoding="utf-8"
        )
    return mk


def test_stat_snapshot_matches_legacy_fingerprint(tmp_path):
    mk = _store(tmp_path)
    dirs = [mk.entities_dir, mk.live_notes_dir, mk.decisions_dir, mk.originals_dir,
            mk.inbox_dir, mk.tasks_dir, mk.meetings_dir, mk.debug_dir, mk.base_dir / "artifacts"]
    snapshot = lh.md_stat_snapshot(dirs, mk.base_dir)
    legacy_fp, legacy_items = ci._compute_fingerprint(list(lh.all_md_files(dirs, mk.base_dir)))
    fp, path_hash = ci._fingerprint_from_snapshot(snapshot)
    assert fp == legacy_fp
    assert path_hash == ci._compute_path_set_hash(p for p, _ in legacy_items)
    assert [s[0] for s in snapshot] == [str(p) for p, _ in legacy_items]


def test_fast_path_does_not_build_paths_when_unchanged(tmp_path, monkeypatch):
    mk = _store(tmp_path)
    with redirect_stdout(io.StringIO()):
        assert mk.search("alpha", top_k=3)
    calls = {"n": 0}
    real = lh.all_md_files

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(lh, "all_md_files", counting)
    with redirect_stdout(io.StringIO()):
        assert mk.search("beta", top_k=3)
    assert calls["n"] == 0, "unchanged corpus must be validated from the stat snapshot alone"


def test_fast_path_still_sees_out_of_process_edit(tmp_path):
    mk = _store(tmp_path)
    with redirect_stdout(io.StringIO()):
        assert not mk.search("zebracorn", top_k=3)
    target = mk.live_notes_dir / "note-007.md"
    # Simulate another process appending without calling MemKraft's invalidate hooks.
    with target.open("a", encoding="utf-8") as fh:
        fh.write("zebracorn appears here\n")
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    with redirect_stdout(io.StringIO()):
        hits = mk.search("zebracorn", top_k=3)
    assert hits and hits[0]["file"].endswith("note-007.md")


def test_fast_path_sees_out_of_process_new_file(tmp_path):
    mk = _store(tmp_path)
    with redirect_stdout(io.StringIO()):
        assert not mk.search("quokkaword", top_k=3)
    (mk.live_notes_dir / "late.md").write_text("quokkaword\n", encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        hits = mk.search("quokkaword", top_k=3)
    assert hits and hits[0]["file"].endswith("late.md")


def _template_entity(name: str) -> str:
    rows = "\n".join(
        f"- **2026-10-0{d}** | 배포 점검 끝났어 {name} [Source: hermes:2026090{d}_000000_abc#assistant | Confidence: experimental]"
        for d in range(1, 4)
    )
    return (
        f"# {name}\n\n**Tier: recall**\n\n## State\n- **Role:** (enrichment needed)\n\n"
        f"## Open Threads\n- [ ] Initial entity — enrichment needed\n\n---\n\n## Timeline\n\n{rows}\n"
    )


def test_prefetch_reaches_real_docs_below_chat_template_entities(tmp_path, monkeypatch):
    pytest.importorskip("agent.memory_provider")
    from memkraft.hermes_provider import MemKraftMemoryProvider
    from memkraft._core_search_helpers import is_chat_derived_template_entity

    hermes_home = tmp_path / "home"
    store = hermes_home / "memkraft"
    monkeypatch.setenv("MEMKRAFT_DIR", str(store))
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(tmp_path / "owner"))
    monkeypatch.setenv("MEMKRAFT_DEVELOPMENT_EXPERIENCE", "0")
    provider = MemKraftMemoryProvider()
    with redirect_stdout(io.StringIO()):
        provider.initialize(session_id="t", hermes_home=str(hermes_home))
    ents = store / "entities"
    ents.mkdir(parents=True, exist_ok=True)
    # 30 chat-derived fragment pages carry the exact query phrase, so they
    # outrank the real note (which only shares the individual words).
    for i in range(30):
        text = _template_entity(f"조각{i:02d}")
        assert is_chat_derived_template_entity(text)
        (ents / f"조각{i:02d}.md").write_text(text, encoding="utf-8")
    real = store / "artifacts" / "release-note.md"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("# 릴리스 기록\n\n4.2.0 배포를 마쳤고 설치본 점검 결과 정상이야.\n", encoding="utf-8")

    with redirect_stdout(io.StringIO()):
        raw = provider._store.search("배포 점검", top_k=12)
    assert all(r["file"].startswith("entities/") for r in raw), "fixture must reproduce the crowding"

    text = provider.prefetch("배포 점검", session_id="t")
    assert "artifacts/release-note.md" in text
    assert "entities/조각" not in text
