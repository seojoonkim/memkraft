"""MemKraft v0.8.0 — Reversible Decay + Tombstone tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


def _write(mk: MemKraft, relpath: str, body: str = "hello") -> Path:
    p = mk.base_dir / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# decay_apply
# ---------------------------------------------------------------------------

class TestDecayApply:
    def test_apply_reduces_weight(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md", "just a note")
        r = mk.decay_apply(str(p), decay_rate=0.5)
        assert r["decay_weight"] == 0.5
        assert r["decay_count"] == 1
        # state persists
        r2 = mk.decay_apply(str(p), decay_rate=0.5)
        assert r2["decay_weight"] == 0.25
        assert r2["decay_count"] == 2

    def test_invalid_rate_raises(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        with pytest.raises(ValueError):
            mk.decay_apply(str(p), decay_rate=0.0)
        with pytest.raises(ValueError):
            mk.decay_apply(str(p), decay_rate=1.0)
        with pytest.raises(ValueError):
            mk.decay_apply(str(p), decay_rate=-0.1)

    def test_missing_file_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.decay_apply("does-not-exist.md")

    def test_apply_resolves_relative_path(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/note.md")
        r = mk.decay_apply("inbox/note.md", decay_rate=0.5)
        assert r["decay_weight"] == 0.5


# ---------------------------------------------------------------------------
# decay_list
# ---------------------------------------------------------------------------

class TestDecayList:
    def test_list_below_threshold(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        c = _write(mk, "inbox/c.md")
        mk.decay_apply(str(a), decay_rate=0.9)   # 0.1
        mk.decay_apply(str(b), decay_rate=0.5)   # 0.5
        # c untouched
        listed = mk.decay_list(below_threshold=0.3)
        paths = {r["path"] for r in listed}
        assert str(a) in paths
        assert str(b) not in paths
        assert str(c) not in paths

    def test_list_excludes_tombstoned_by_default(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.decay_tombstone(str(p))
        listed = mk.decay_list(below_threshold=1.0)
        # moved to tombstones/, skipped because of .memkraft filter
        assert all(".memkraft" not in r["path"] for r in listed)

    def test_list_sorted_ascending(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.decay_apply(str(a), decay_rate=0.9)
        mk.decay_apply(str(b), decay_rate=0.5)
        listed = mk.decay_list(below_threshold=1.0)
        weights = [r["decay_weight"] for r in listed]
        assert weights == sorted(weights)


# ---------------------------------------------------------------------------
# decay_restore
# ---------------------------------------------------------------------------

class TestDecayRestore:
    def test_restore_resets_weight_and_count(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        mk.decay_apply(str(p), decay_rate=0.5)
        mk.decay_apply(str(p), decay_rate=0.5)
        r = mk.decay_restore(str(p))
        assert r["decay_weight"] == 1.0
        assert r["decay_count"] == 0
        assert r["tombstoned"] is False

    def test_restore_from_tombstone(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        mk.decay_tombstone(str(p))
        # file has moved; restore by stem
        mk.decay_restore("note")
        # one of the memory dirs now contains note.md again
        found = list(mk.base_dir.rglob("note.md"))
        alive = [f for f in found if ".memkraft" not in f.parts]
        assert alive, f"expected restored file, saw {found}"

    def test_restore_unknown_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.decay_restore("nope")


# ---------------------------------------------------------------------------
# decay_run (batch)
# ---------------------------------------------------------------------------

class TestDecayRun:
    def test_run_filters_by_weight(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.decay_apply(str(a), decay_rate=0.9)  # 0.1
        affected = mk.decay_run(criteria={"weight_gt": 0.5})
        # a has weight 0.1 → skipped; b has default 1.0 → decayed
        paths = {r["path"] for r in affected}
        assert str(b) in paths
        assert str(a) not in paths

    def test_run_no_matches(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md")
        affected = mk.decay_run(criteria={"weight_gt": 2.0})
        assert affected == []

    def test_run_respects_access_count_lt(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.decay_apply(str(a), decay_rate=0.5)  # count=1
        mk.decay_apply(str(a), decay_rate=0.5)  # count=2
        # criterion: decay only items with count < 1 → only b
        affected = mk.decay_run(criteria={"access_count_lt": 1})
        paths = {r["path"] for r in affected}
        assert str(b) in paths
        assert str(a) not in paths


# ---------------------------------------------------------------------------
# decay_tombstone
# ---------------------------------------------------------------------------

class TestTombstone:
    def test_tombstone_moves_file(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        r = mk.decay_tombstone(str(p))
        assert not p.exists()
        assert Path(r["path"]).exists()
        assert ".memkraft" in Path(r["path"]).parts
        assert "tombstoned_at" in r

    def test_tombstone_marks_file(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        r = mk.decay_tombstone(str(p))
        text = Path(r["path"]).read_text()
        assert "tombstoned: true" in text
        assert "decay_weight: 0.0" in text

    def test_is_tombstoned_query(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md")
        assert mk.decay_is_tombstoned(str(p)) is False
        mk.decay_tombstone(str(p))
        assert mk.decay_is_tombstoned("note") is True

    def test_tombstone_missing_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.decay_tombstone("nope.md")
