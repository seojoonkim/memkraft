"""Concurrency tests for store_core — S4 gate (roadmap §3.1).

Four worker processes hammer ``append`` (4 × 250 = 1000 records) while the
store is read and, in one test, compacted. All coordination uses
``multiprocessing.Barrier`` — no sleeps, no timing assumptions — so the
tests are deterministic: they assert invariants that must hold under *any*
interleaving (no lost or duplicated records, no corrupt lines, no partial
reads), never that a particular interleaving occurred.

The ``spawn`` start method is used explicitly so macOS dev machines and
Linux CI exercise identical process semantics.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from memkraft.store_core import append, compact, mark_tombstone, read_all

PROCS = 4
APPENDS_PER_PROC = 250

_MP = multiprocessing.get_context("spawn")

# Generous per-process ceiling: only ever hit on a genuine deadlock, in which
# case the test must fail loudly rather than hang CI.
_JOIN_TIMEOUT = 120


def _append_worker(path: Path, proc_idx: int, count: int, barrier) -> None:
    """Append ``count`` records tagged with (proc, seq) after the barrier."""
    barrier.wait()
    for seq in range(count):
        append(path, {"proc": proc_idx, "seq": seq})


def _compact_worker(path: Path, barrier) -> None:
    """Run one compact in the middle of the append competition."""
    barrier.wait()
    compact(path)


def _make_appenders(path: Path, barrier) -> list:
    return [
        _MP.Process(target=_append_worker, args=(path, i, APPENDS_PER_PROC, barrier))
        for i in range(PROCS)
    ]


def _start_and_join(procs) -> None:
    for p in procs:
        p.start()
    try:
        for p in procs:
            p.join(_JOIN_TIMEOUT)
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join(5)
    assert [p.exitcode for p in procs] == [0] * len(procs)


def _expected_pairs() -> set:
    return {(i, s) for i in range(PROCS) for s in range(APPENDS_PER_PROC)}


class TestParallelAppend:
    """S4.1 — 4 × 250 appends: 1000 records, 0 corrupt lines, 0 duplicate ids."""

    def test_all_records_land_exactly_once_without_corruption(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        barrier = _MP.Barrier(PROCS)

        _start_and_join(_make_appenders(path, barrier))

        result = read_all(path)
        assert result.skipped == 0
        assert len(result.records) == PROCS * APPENDS_PER_PROC
        ids = [r["id"] for r in result.records]
        assert len(set(ids)) == len(ids)
        # every (proc, seq) landed exactly once — no lost or doubled appends
        assert {(r["proc"], r["seq"]) for r in result.records} == _expected_pairs()

    def test_no_interleaved_bytes_on_the_wire(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        barrier = _MP.Barrier(PROCS)

        _start_and_join(_make_appenders(path, barrier))

        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        lines = raw.splitlines()
        assert len(lines) == PROCS * APPENDS_PER_PROC
        # every physical line is a standalone JSON object — writers never
        # interleaved inside a line
        for line in lines:
            assert isinstance(json.loads(line), dict)


class TestReadDuringAppends:
    """S4.2 — read_all under concurrent appends never raises or sees partial lines."""

    def test_repeated_read_all_is_clean_and_monotonic(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        # readers don't take the lock, so the parent joins the same barrier
        # and reads continuously while the appenders run
        barrier = _MP.Barrier(PROCS + 1)
        procs = _make_appenders(path, barrier)
        for p in procs:
            p.start()
        try:
            barrier.wait(timeout=_JOIN_TIMEOUT)
            prev_count = 0
            while True:
                still_running = any(p.is_alive() for p in procs)
                result = read_all(path)  # must not raise
                assert result.skipped == 0  # no partial/corrupt line ever visible
                # append-only store: a snapshot never shrinks or tears
                assert len(result.records) >= prev_count
                prev_count = len(result.records)
                for r in result.records:
                    assert r["schema_version"] == 1
                    assert r["id"]
                    assert "proc" in r and "seq" in r
                if not still_running:
                    break
        finally:
            for p in procs:
                p.join(_JOIN_TIMEOUT)
                if p.is_alive():
                    p.terminate()
                    p.join(5)
        assert [p.exitcode for p in procs] == [0] * len(procs)
        # the loop's final pass ran after every appender exited
        assert prev_count == PROCS * APPENDS_PER_PROC


class TestCompactDuringAppends:
    """S4.3 — one compact amid the append competition loses zero live records."""

    def test_compact_race_preserves_every_live_record(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        # seed the store so compact has real work: live records that must
        # survive and tombstoned ones that must stay gone
        pre_live = [append(path, {"kind": "pre", "n": i}) for i in range(10)]
        doomed = [append(path, {"kind": "doomed", "n": i}) for i in range(5)]
        for rec in doomed:
            mark_tombstone(path, rec["id"])

        barrier = _MP.Barrier(PROCS + 1)
        procs = _make_appenders(path, barrier)
        procs.append(_MP.Process(target=_compact_worker, args=(path, barrier)))

        _start_and_join(procs)

        result = read_all(path)
        assert result.skipped == 0
        live_ids = {r["id"] for r in result.records}
        # every pre-existing live record survived the rewrite
        assert {r["id"] for r in pre_live} <= live_ids
        # every append whose call returned is durable, wherever compact landed
        appended = {
            (r["proc"], r["seq"]) for r in result.records if r.get("kind") != "pre"
        }
        assert appended == _expected_pairs()
        assert len(result.records) == len(pre_live) + PROCS * APPENDS_PER_PROC
        # tombstoned records never resurface
        assert not live_ids & {r["id"] for r in doomed}
        # audit path agrees: no duplicates and nothing corrupt anywhere
        audit = read_all(path, include_tombstoned=True)
        assert audit.skipped == 0
        audit_ids = [r["id"] for r in audit.records]
        assert len(set(audit_ids)) == len(audit_ids)
