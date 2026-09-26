"""Delay-ledger write cost must stay bounded as history grows.

Regression for a production incident (2026-09-27): Hermes appends ~4 delay
records per tool step and every append re-folded the whole ledger ~3 times,
while the fold itself was quadratic (per-finish window estimate and
child-scan over all runs). A 24k-event ledger added ~55 s to every tool step.
"""
import time

import pytest

from memkraft import MemKraft
from memkraft import delay_ledger


def _grow(mk, tasks, phases_per_task=3):
    seq = 0
    for t in range(tasks):
        tid = f"t{t:05d}"
        mk.delay_run_start(tid, "task", "telegram.development", now="2026-09-27T00:00:00Z")
        for p in range(phases_per_task):
            pid = f"{tid}-p{p}"
            mk.delay_run_start(pid, "phase", "telegram.development.active",
                               now="2026-09-27T00:00:00Z", parent_run_id=tid)
            mk.delay_run_finish(pid, 1000 + (seq % 97), now="2026-09-27T00:00:01Z")
            seq += 1
        mk.delay_run_finish(tid, 5000 + (t % 89), now="2026-09-27T00:00:02Z")


def test_each_append_folds_the_ledger_at_most_once(tmp_path, monkeypatch):
    mk = MemKraft(base_dir=str(tmp_path))
    _grow(mk, 20)
    calls = {"n": 0}
    real = delay_ledger._fold

    def counting(records):
        calls["n"] += 1
        return real(records)

    monkeypatch.setattr(delay_ledger, "_fold", counting)
    mk.delay_run_start("extra", "task", "telegram.development", now="2026-09-27T01:00:00Z")
    assert calls["n"] <= 1, calls
    calls["n"] = 0
    mk.delay_run_start("extra-p0", "phase", "telegram.development.active",
                       now="2026-09-27T01:00:00Z", parent_run_id="extra")
    assert calls["n"] <= 1, calls
    calls["n"] = 0
    mk.delay_run_finish("extra-p0", 1234, now="2026-09-27T01:00:01Z")
    assert calls["n"] <= 1, calls
    calls["n"] = 0
    mk.delay_run_finish("extra", 4321, now="2026-09-27T01:00:02Z")
    assert calls["n"] <= 1, calls


def _fold_seconds(records):
    t = time.perf_counter()
    delay_ledger._fold(records)
    return time.perf_counter() - t


def test_fold_scales_roughly_linearly(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    _grow(mk, 300)  # ~2.4k events; history build itself was quadratic pre-fix
    from memkraft.store_core import read_all
    records, corrupt = delay_ledger._partition(
        read_all(mk._delay_events_path(), include_tombstoned=True))
    assert corrupt == 0
    half = records[: len(records) // 2]
    # Warm up, then take the best of 3 to damp CI noise.
    small = min(_fold_seconds(half) for _ in range(3))
    large = min(_fold_seconds(records) for _ in range(3))
    # Quadratic would be ~4x; allow generous headroom for linear-ish.
    assert large < max(small * 3.0, 0.05), (small, large)
