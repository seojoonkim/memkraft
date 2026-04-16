"""MemKraft v0.8.0 — Memory Tier Labels + Working Set tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


def _write(mk: MemKraft, relpath: str, body: str = "hi") -> Path:
    p = mk.base_dir / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# tier_set / tier_of
# ---------------------------------------------------------------------------

class TestTierBasic:
    def test_set_and_read(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.tier_set(str(p), tier="core")
        assert mk.tier_of(str(p)) == "core"

    def test_default_tier_when_unset(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        assert mk.tier_of(str(p)) == "recall"

    def test_invalid_tier_raises(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        with pytest.raises(ValueError):
            mk.tier_set(str(p), tier="hot")

    def test_missing_file_raises_on_set(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.tier_set("missing.md", tier="core")

    def test_tier_of_unknown_defaults(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.tier_of("missing.md") == "recall"


# ---------------------------------------------------------------------------
# tier_promote / tier_demote
# ---------------------------------------------------------------------------

class TestPromoteDemote:
    def test_promote_sequence(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.tier_set(str(p), tier="archival")
        mk.tier_promote(str(p))
        assert mk.tier_of(str(p)) == "recall"
        mk.tier_promote(str(p))
        assert mk.tier_of(str(p)) == "core"
        # already at top → stays
        mk.tier_promote(str(p))
        assert mk.tier_of(str(p)) == "core"

    def test_demote_sequence(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.tier_set(str(p), tier="core")
        mk.tier_demote(str(p))
        assert mk.tier_of(str(p)) == "recall"
        mk.tier_demote(str(p))
        assert mk.tier_of(str(p)) == "archival"
        mk.tier_demote(str(p))
        assert mk.tier_of(str(p)) == "archival"

    def test_promote_from_default(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        # no tier set → default recall → promote → core
        mk.tier_promote(str(p))
        assert mk.tier_of(str(p)) == "core"


# ---------------------------------------------------------------------------
# tier_list
# ---------------------------------------------------------------------------

class TestTierList:
    def test_filter_by_tier(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.tier_set(str(a), tier="core")
        mk.tier_set(str(b), tier="archival")
        core_only = mk.tier_list(tier="core")
        assert len(core_only) == 1
        assert core_only[0]["path"] == str(a)

    def test_list_all_sorted_by_priority(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        c = _write(mk, "inbox/c.md")
        mk.tier_set(str(a), tier="archival")
        mk.tier_set(str(b), tier="core")
        mk.tier_set(str(c), tier="recall")
        all_entries = mk.tier_list()
        # core comes first, archival last
        tiers = [e["tier"] for e in all_entries]
        assert tiers.index("core") < tiers.index("recall") < tiers.index("archival")

    def test_invalid_tier_filter_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.tier_list(tier="hot")


# ---------------------------------------------------------------------------
# tier_touch
# ---------------------------------------------------------------------------

class TestTouch:
    def test_touch_increments_count(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        r1 = mk.tier_touch(str(p))
        r2 = mk.tier_touch(str(p))
        assert r1["access_count"] == 1
        assert r2["access_count"] == 2
        assert r2["last_accessed"]

    def test_touch_preserves_tier(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.tier_set(str(p), tier="core")
        mk.tier_touch(str(p))
        assert mk.tier_of(str(p)) == "core"


# ---------------------------------------------------------------------------
# working_set
# ---------------------------------------------------------------------------

class TestWorkingSet:
    def test_working_set_includes_all_core(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.tier_set(str(a), tier="core")
        mk.tier_set(str(b), tier="core")
        ws = mk.working_set(limit=10)
        paths = {e["path"] for e in ws}
        assert str(a) in paths
        assert str(b) in paths

    def test_working_set_respects_limit(self, tmp_path):
        mk = _mk(tmp_path)
        for i in range(5):
            p = _write(mk, f"inbox/{i}.md")
            mk.tier_set(str(p), tier="recall")
            mk.tier_touch(str(p))
        ws = mk.working_set(limit=3)
        assert len(ws) == 3

    def test_working_set_zero_limit(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/a.md")
        mk.tier_set(str(p), tier="core")
        ws = mk.working_set(limit=0)
        assert ws == []

    def test_working_set_negative_limit_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.working_set(limit=-1)

    def test_working_set_excludes_archival(self, tmp_path):
        mk = _mk(tmp_path)
        a = _write(mk, "inbox/a.md")
        b = _write(mk, "inbox/b.md")
        mk.tier_set(str(a), tier="core")
        mk.tier_set(str(b), tier="archival")
        ws = mk.working_set(limit=10)
        paths = {e["path"] for e in ws}
        assert str(a) in paths
        assert str(b) not in paths


# ---------------------------------------------------------------------------
# frontmatter coexistence with decay fields
# ---------------------------------------------------------------------------

class TestCoexistence:
    def test_tier_and_decay_share_frontmatter(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write(mk, "inbox/note.md", "body")
        mk.tier_set(str(p), tier="core")
        mk.decay_apply(str(p), decay_rate=0.5)
        text = p.read_text()
        assert "tier: core" in text
        assert "decay_weight: 0.5" in text
        # tier still readable
        assert mk.tier_of(str(p)) == "core"
