"""Top-k upper-bound pruning must not change visible search results."""
import os
import random

import pytest

from memkraft import MemKraft
from memkraft import _search_prune as sp


def _make_store(tmp_path, n=600, seed=7):
    rnd = random.Random(seed)
    base = tmp_path / "mk"
    mk = MemKraft(base_dir=str(base))
    mk.init()
    vocab = ["deploy", "gateway", "memory", "slack", "vooy", "notion", "cron", "sync",
             "carrot", "cave", "recall", "timeout", "prefetch", "index", "budget"]
    vocab += ["w%03d" % j for j in range(300)]
    notes = base / "entities"
    notes.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        words = ["2026", "hermes"] + [rnd.choice(vocab) for _ in range(rnd.randint(3, 60))]
        heading = "# " + " ".join(rnd.sample(vocab[:15], 2))
        date = "**2026-11-%02d**" % rnd.randint(1, 27)
        (notes / f"note-{i:04d}.md").write_text(heading + "\n" + date + "\n" + " ".join(words) + "\n")
    from memkraft._corpus_index import invalidate

    invalidate()
    mk = MemKraft(base_dir=str(base))
    assert len(mk.search("2026 hermes", top_k=3, cache=False)) == 3
    return mk


QUERIES = [
    "2026 hermes deploy gateway timeout",
    "hermes memory slack vooy notion sync 2026 cron",
    "carrot cave",
    "deploy gateway",
    "prefetch timeout recall index budget 2026 hermes",
    "note 0042",
]


@pytest.mark.parametrize("k", [1, 5, 12])
def test_pruned_topk_matches_full_scan(tmp_path, monkeypatch, k):
    mk = _make_store(tmp_path)
    for q in QUERIES:
        monkeypatch.delenv("MEMKRAFT_DISABLE_TOPK_PRUNE", raising=False)
        pruned = mk.search(q, top_k=k, cache=False)
        monkeypatch.setenv("MEMKRAFT_DISABLE_TOPK_PRUNE", "1")
        full = mk.search(q, top_k=k, cache=False)
        assert pruned == full, q
        if q != "note 0042":
            assert full, q


def test_prune_reads_fewer_files(tmp_path, monkeypatch):
    mk = _make_store(tmp_path)
    q = "2026 hermes prefetch timeout"
    mk.search(q, top_k=5, cache=False)  # warm index
    reads = {"n": 0}
    orig = MemKraft._safe_read

    def counting(self, path):
        reads["n"] += 1
        return orig(self, path)

    monkeypatch.setattr(MemKraft, "_safe_read", counting)
    monkeypatch.delenv("MEMKRAFT_DISABLE_TOPK_PRUNE", raising=False)
    mk.search(q, top_k=5, cache=False)
    pruned_reads = reads["n"]
    reads["n"] = 0
    monkeypatch.setenv("MEMKRAFT_DISABLE_TOPK_PRUNE", "1")
    mk.search(q, top_k=5, cache=False)
    assert pruned_reads < reads["n"]


def test_unknown_doc_tf_never_pruned(tmp_path):
    from pathlib import Path

    assert sp.score_upper_bound(
        md=Path("x.md"), query_lower="a b c", query_tokens=["ab"], interior_tokens=[],
        lin_idf={"ab": 1.0}, log_idf={"ab": 1.0}, total_lin=1.0, doc_tf=None,
    ) == 2.0
    assert not sp.can_stop(0.5, None)
