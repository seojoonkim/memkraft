"""Persistent per-document token cache for the corpus index.

A cold process (e.g. a restarted Hermes gateway) used to re-read and
re-tokenize every markdown file before the first search.  The index now
persists per-document token-frequency maps keyed by (mtime_ns, size) and
re-reads only documents whose stat changed.
"""
from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft import _corpus_index as ci
from memkraft._core_search_helpers import search_tokens


@pytest.fixture(autouse=True)
def _fresh_index(monkeypatch):
    monkeypatch.delenv("MEMKRAFT_INDEX_CACHE", raising=False)
    ci.reset_for_tests()
    yield
    ci.reset_for_tests()


def _corpus(tmp_path: Path, n: int = 12) -> tuple[list[Path], Path]:
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    files = []
    for i in range(n):
        p = root / f"note-{i:02d}.md"
        p.write_text(f"# Note {i}\n\nalpha beta gamma{i} shared token topic{i % 3}\n", encoding="utf-8")
        files.append(p)
    return files, tmp_path / "state" / "corpus-tf.bin"


def _get(files, cache_path):
    return ci.get_corpus_index(
        lambda: sorted(files, key=str),
        search_tokens,
        persist_path=cache_path,
    )


def _count_reads(monkeypatch) -> list:
    calls = []
    real = ci._read_doc_tf

    def counting(path, *args, **kwargs):
        calls.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(ci, "_read_doc_tf", counting)
    return calls


def _shape(idx):
    return (
        idx.doc_count,
        idx.avg_doc_len,
        idx.token_doc_freq,
        {str(k): v for k, v in idx.doc_token_freqs.items()},
        {k: [str(idx.doc_id_map[d]) for d in v] for k, v in idx.content_postings.items()},
        {k: [str(idx.doc_id_map[d]) for d in v] for k, v in idx.filename_postings.items()},
        idx.token_bloom,
        idx.filename_corpus,
    )


def test_cold_rebuild_reuses_persisted_tokens(tmp_path, monkeypatch):
    files, cache_path = _corpus(tmp_path)
    first = _get(files, cache_path)
    assert cache_path.exists()

    ci.reset_for_tests()  # simulate a new process
    reads = _count_reads(monkeypatch)
    second = _get(files, cache_path)

    assert reads == []
    assert _shape(second) == _shape(first)


def test_persisted_index_matches_uncached_build(tmp_path):
    files, cache_path = _corpus(tmp_path)
    _get(files, cache_path)
    ci.reset_for_tests()
    warm = _get(files, cache_path)
    ci.reset_for_tests()
    plain = ci.get_corpus_index(lambda: sorted(files, key=str), search_tokens)
    assert _shape(warm) == _shape(plain)


def test_only_changed_documents_are_reread(tmp_path, monkeypatch):
    files, cache_path = _corpus(tmp_path)
    _get(files, cache_path)
    ci.reset_for_tests()

    changed = files[3]
    changed.write_text("# Note 3\n\nfreshlyedited payload\n", encoding="utf-8")
    st = changed.stat()
    os.utime(changed, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    added = files[0].parent / "note-new.md"
    added.write_text("# New\n\nbrandnew words\n", encoding="utf-8")
    removed = files.pop(5)
    removed.unlink()
    files.append(added)

    reads = _count_reads(monkeypatch)
    idx = _get(files, cache_path)

    assert sorted(map(str, reads)) == sorted([str(changed), str(added)])
    assert "freshlyedited" in idx.token_doc_freq
    assert "brandnew" in idx.token_doc_freq
    assert "gamma5" not in idx.token_doc_freq
    assert "gamma3" not in idx.token_doc_freq


def test_corrupt_cache_file_falls_back_and_is_rewritten(tmp_path, monkeypatch):
    files, cache_path = _corpus(tmp_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"not a cache file at all")

    idx = _get(files, cache_path)
    assert idx.doc_count == len(files)

    ci.reset_for_tests()
    reads = _count_reads(monkeypatch)
    _get(files, cache_path)
    assert reads == []


def test_cache_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_INDEX_CACHE", "off")
    files, cache_path = _corpus(tmp_path)
    _get(files, cache_path)
    assert not cache_path.exists()


def test_memkraft_search_persists_and_sees_external_edit_after_restart(tmp_path):
    base = tmp_path / "store"
    mk = MemKraft(base_dir=str(base))
    with redirect_stdout(io.StringIO()):
        mk.init()
    note = base / "entities" / "rocket.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Rocket\n\nlaunchpad ignition sequence\n", encoding="utf-8")

    with redirect_stdout(io.StringIO()):
        assert mk.search("ignition", top_k=5)
    assert any((base / ".memkraft" / "index").glob("*.bin"))

    ci.reset_for_tests()  # new process
    note.write_text("# Rocket\n\nlaunchpad countdown sequence\n", encoding="utf-8")
    st = note.stat()
    os.utime(note, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))

    mk2 = MemKraft(base_dir=str(base))
    with redirect_stdout(io.StringIO()):
        assert mk2.search("countdown", top_k=5)
        assert not mk2.search("ignition", top_k=5, cache=False)


def test_stat_ttl_skips_scan_until_write_or_expiry(tmp_path, monkeypatch):
    files, _ = _corpus(tmp_path)
    scans = []

    def listing():
        scans.append(1)
        return sorted(files, key=str)

    def get():
        return ci.get_corpus_index(listing, search_tokens, trust_write_hooks=True, corpus_key="k")

    ci.set_stat_ttl(30)
    get(); get(); get()
    assert len(scans) == 1
    assert ci.stats()["ttl_hits"] == 2

    ci.invalidate(files[0])  # an in-process write must force a real scan
    get()
    assert len(scans) == 2

    clock = [ci.time.monotonic() + 31]
    monkeypatch.setattr(ci.time, "monotonic", lambda: clock[0])
    get()
    assert len(scans) == 3


def test_stat_ttl_is_scoped_to_one_corpus(tmp_path):
    a, _ = _corpus(tmp_path / "a")
    b, _ = _corpus(tmp_path / "b")
    ci.set_stat_ttl(30)
    ia = ci.get_corpus_index(lambda: sorted(a, key=str), search_tokens, trust_write_hooks=True, corpus_key="a")
    ib = ci.get_corpus_index(lambda: sorted(b, key=str), search_tokens, trust_write_hooks=True, corpus_key="b")
    assert set(map(str, ia.file_list)) == set(map(str, a))
    assert set(map(str, ib.file_list)) == set(map(str, b))


def test_stat_ttl_defaults_off(tmp_path):
    files, _ = _corpus(tmp_path)
    scans = []

    def listing():
        scans.append(1)
        return sorted(files, key=str)

    for _ in range(3):
        ci.get_corpus_index(listing, search_tokens, trust_write_hooks=True, corpus_key="k")
    assert len(scans) == 3
