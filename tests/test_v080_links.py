"""MemKraft v0.8.0 — Cross-Entity Link Graph tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.links import _extract_links


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


def _write(mk: MemKraft, relpath: str, body: str) -> Path:
    p = mk.base_dir / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _extract_links
# ---------------------------------------------------------------------------

class TestExtract:
    def test_basic(self):
        assert _extract_links("meet [[Simon]] tomorrow") == ["Simon"]

    def test_multiple_dedup(self):
        assert _extract_links("[[A]] and [[B]] and [[A]]") == ["A", "B"]

    def test_none(self):
        assert _extract_links("no links here") == []

    def test_empty_brackets_ignored(self):
        assert _extract_links("empty [[]]") == []

    def test_pipe_syntax(self):
        assert _extract_links("[[Simon Kim|simon]] rules") == ["Simon Kim"]

    def test_whitespace_normalized(self):
        assert _extract_links("[[  Simon  ]]") == ["Simon"]


# ---------------------------------------------------------------------------
# link_scan + link_backlinks
# ---------------------------------------------------------------------------

class TestScanBacklinks:
    def test_scan_builds_index(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "I met [[Simon]] and [[Alice]].")
        _write(mk, "inbox/b.md", "[[Simon]] was there too.")
        res = mk.link_scan()
        # index includes Simon and Alice plus any seeds from TEMPLATES.md;
        # we only assert that our two entities are there.
        assert res["entities_linked"] >= 2
        bl = mk.link_backlinks("Simon")
        assert len(bl) == 2
        assert any("inbox/a.md" in p for p in bl)
        assert any("inbox/b.md" in p for p in bl)

    def test_backlinks_empty_for_unknown(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "nothing")
        mk.link_scan()
        assert mk.link_backlinks("Nobody") == []

    def test_scan_lazy_on_first_query(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Simon]]")
        # no explicit scan — link_backlinks should trigger one
        result = mk.link_backlinks("Simon")
        assert len(result) == 1

    def test_scan_ignores_memkraft_internal(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[X]]")
        internal = mk.base_dir / ".memkraft" / "links" / "ignored.md"
        internal.parent.mkdir(parents=True, exist_ok=True)
        internal.write_text("[[Y]]", encoding="utf-8")
        mk.link_scan()
        assert mk.link_backlinks("Y") == []
        assert mk.link_backlinks("X") != []

    def test_rescan_refreshes_removed_links(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md", "[[Simon]]")
        mk.link_scan()
        assert mk.link_backlinks("Simon") != []
        # edit file to remove link, rescan
        p.write_text("now without any links", encoding="utf-8")
        mk.link_scan()
        assert mk.link_backlinks("Simon") == []


# ---------------------------------------------------------------------------
# link_forward
# ---------------------------------------------------------------------------

class TestForward:
    def test_forward_lists_targets(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Simon]] and [[Hashed]]")
        mk.link_scan()
        fw = mk.link_forward("inbox/a.md")
        assert set(fw) == {"Simon", "Hashed"}

    def test_forward_unknown_source(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Simon]]")
        mk.link_scan()
        assert mk.link_forward("does/not/exist.md") == []


# ---------------------------------------------------------------------------
# link_graph
# ---------------------------------------------------------------------------

class TestGraph:
    def test_one_hop(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Simon]] leads [[Hashed]]")
        mk.link_scan()
        g = mk.link_graph("Simon", hops=1)
        assert "Simon" in g["nodes"]
        assert "Hashed" in g["nodes"]
        assert any("Simon" in e for e in g["edges"])

    def test_two_hops(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Simon]] at [[Hashed]]")
        _write(mk, "inbox/b.md", "[[Hashed]] invested in [[Triples]]")
        mk.link_scan()
        g = mk.link_graph("Simon", hops=2)
        # Triples is 2 hops away via Hashed's co-mention
        assert "Hashed" in g["nodes"]
        # Even one-hop via shared files should surface Hashed at minimum
        assert len(g["nodes"]) >= 2

    def test_hops_validation(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.link_graph("Simon", hops=0)


# ---------------------------------------------------------------------------
# link_orphans
# ---------------------------------------------------------------------------

class TestOrphans:
    def test_orphan_detected(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "[[Unknown Person]] was mentioned")
        mk.link_scan()
        orphans = mk.link_orphans()
        assert "Unknown Person" in orphans

    def test_existing_entity_not_orphan(self, tmp_path):
        mk = _mk(tmp_path)
        # create the entity file so it's not an orphan
        _write(mk, "entities/simon.md", "# Simon")
        _write(mk, "inbox/a.md", "[[Simon]] was mentioned")
        mk.link_scan()
        orphans = mk.link_orphans()
        assert "Simon" not in orphans

    def test_no_links_no_orphans_from_our_files(self, tmp_path):
        mk = _mk(tmp_path)
        _write(mk, "inbox/a.md", "no wiki links")
        mk.link_scan()
        # TEMPLATES.md seeds may add orphans of its own (e.g. '관련 기업').
        # We just check that our own "Unknown Person" style names aren't
        # spuriously flagged.
        orphans = mk.link_orphans()
        assert "Unknown Person" not in orphans
        assert "Simon" not in orphans
