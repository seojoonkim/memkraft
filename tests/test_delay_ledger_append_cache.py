"""The delay append cache must match the full-history path exactly.

Regression (2026-11-01): every delay write re-read and re-folded the whole
``events.jsonl``; a 63 MB ledger cost ~3 s per record. A long-lived MemKraft
now folds only the appended suffix, and every case the cache cannot vouch for
falls back to the legacy full read.
"""
import json
import os

import pytest

from memkraft import MemKraft
from memkraft import delay_ledger
from memkraft.delay_ledger import DelayError

NOW = "2026-11-01T00:00:00Z"


def _task(mk, run_id, elapsed, subject="s.task"):
    mk.delay_run_start(run_id, "task", subject, now=NOW)
    return mk.delay_run_finish(run_id, elapsed, now=NOW)


def _count_reads(monkeypatch):
    calls = {"n": 0}
    real = delay_ledger.read_all

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(delay_ledger, "read_all", counting)
    return calls


def test_warm_writes_skip_the_full_history_read(tmp_path, monkeypatch):
    mk = MemKraft(base_dir=str(tmp_path))
    for i in range(10):
        _task(mk, "warm%03d" % i, 100 + i)
    calls = _count_reads(monkeypatch)
    for i in range(10, 20):
        _task(mk, "warm%03d" % i, 100 + i)
    assert calls["n"] == 0


def test_cached_and_legacy_paths_write_identical_records(tmp_path):
    def run(base, cached):
        mk = MemKraft(base_dir=str(base))
        for i in range(12):
            if not cached:
                mk._delay_cache = None
                mk.__dict__["_delay_cached_state"] = lambda path: None
            mk.delay_run_start("t%03d" % i, "task", "s.x", now=NOW)
            mk.delay_run_start("t%03d-p" % i, "phase", "s.x.p", now=NOW,
                               parent_run_id="t%03d" % i)
            mk.delay_run_finish("t%03d-p" % i, 1000 if i != 9 else 90000, now=NOW)
            mk.delay_run_finish("t%03d" % i, 2000 + i, now=NOW)
        mk.delay_record_retrospective("retro-1", "t009-p", "slow.phase", now=NOW)
        mk.delay_record_action("act-1", "retro-1", "cap.phase", now=NOW)
        lines = mk._delay_events_path().read_text().splitlines()
        return [{k: v for k, v in json.loads(line).items()
                 if k not in ("id", "created_at")} for line in lines]

    assert run(tmp_path / "a", True) == run(tmp_path / "b", False)


def test_appends_from_another_instance_are_folded(tmp_path):
    first, second = MemKraft(base_dir=str(tmp_path)), MemKraft(base_dir=str(tmp_path))
    _task(first, "one001", 10)
    first.delay_run_start("parent1", "task", "s.task", now=NOW)
    second.delay_run_start("child01", "phase", "s.phase", now=NOW, parent_run_id="parent1")
    with pytest.raises(DelayError) as error:
        first.delay_run_finish("parent1", 5, now=NOW)
    assert error.value.code == "E_DELAY_HIERARCHY"
    second.delay_run_finish("child01", 5, now=NOW)
    assert first.delay_run_finish("parent1", 5, now=NOW)["event_seq"] == 6


def test_idempotent_replay_is_served_from_the_cache(tmp_path, monkeypatch):
    mk = MemKraft(base_dir=str(tmp_path))
    first = mk.delay_run_start("idem001", "task", "s.task", now=NOW, operation_id="op-1")
    calls = _count_reads(monkeypatch)
    again = mk.delay_run_start("idem001", "task", "s.task", now=NOW, operation_id="op-1")
    assert again["outcome"] == "already_applied"
    assert again["record_id"] == first["record_id"]
    with pytest.raises(DelayError) as error:
        mk.delay_run_start("idem002", "task", "s.task", now=NOW, operation_id="op-1")
    assert error.value.code == "E_DELAY_IDEMPOTENCY_MISMATCH"
    assert calls["n"] == 0


def test_rewritten_ledger_drops_the_cache(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    for i in range(3):
        _task(mk, "rw%03d" % i, 10)
    path = mk._delay_events_path()
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:2]))  # same inode, truncated
    result = mk.delay_run_start("after01", "task", "s.task", now=NOW)
    assert result["event_seq"] == 3


def test_replaced_file_with_same_length_drops_the_cache(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    _task(mk, "aaa001", 10)
    path = mk._delay_events_path()
    other = MemKraft(base_dir=str(tmp_path / "other"))
    _task(other, "bbb001", 10)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(other._delay_events_path().read_bytes())
    os.replace(replacement, path)
    os.chmod(path, 0o600)
    with pytest.raises(DelayError) as error:
        mk.delay_run_finish("aaa001", 10, now=NOW)
    assert error.value.code == "E_DELAY_NOT_FOUND"


@pytest.mark.parametrize("tail", [b"not json\n", b"{\"partial\": ", b"[]\n"])
def test_corrupt_suffix_falls_back_and_refuses(tmp_path, tail):
    mk = MemKraft(base_dir=str(tmp_path))
    _task(mk, "cor001", 10)
    with open(mk._delay_events_path(), "ab") as handle:
        handle.write(tail)
    if tail.endswith(b"\n"):
        with pytest.raises(DelayError) as error:
            mk.delay_run_start("cor002", "task", "s.task", now=NOW)
        assert error.value.code == "E_DELAY_LOG_CORRUPT"
    else:
        # An unterminated tail is an in-flight append for the legacy reader.
        assert mk.delay_run_start("cor002", "task", "s.task", now=NOW)["event_seq"] == 3


def test_semantically_invalid_suffix_refuses_write(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    _task(mk, "sem001", 10)
    good = json.loads(mk._delay_events_path().read_text().splitlines()[-1])
    forged = dict(good, event_seq=3, operation_id="forged-op", elapsed_ms=11)
    with open(mk._delay_events_path(), "a") as handle:
        handle.write(json.dumps(forged) + "\n")
    with pytest.raises(DelayError) as error:
        mk.delay_run_start("sem002", "task", "s.task", now=NOW)
    assert error.value.code == "E_DELAY_LOG_CORRUPT"
