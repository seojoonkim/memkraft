"""MemKraft v0.8.0 — Bitemporal Fact Layer tests."""

from __future__ import annotations

import pytest

from memkraft import MemKraft
from memkraft.bitemporal import (
    _format_interval,
    _normalise_date,
    format_line,
    parse_line,
)


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


# ---------------------------------------------------------------------------
# parsing / formatting helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_format_interval_closed(self):
        assert _format_interval("2020-01-01", "2021-01-01") == "[2020-01-01..2021-01-01]"

    def test_format_interval_open_upper(self):
        assert _format_interval("2020-01-01", None) == "[2020-01-01..)"

    def test_format_line_roundtrip(self):
        line = format_line("role", "CEO", "2020-01-01", None, "2026-04-17T00:00")
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed["key"] == "role"
        assert parsed["value"] == "CEO"
        assert parsed["valid_from"] == "2020-01-01"
        assert parsed["valid_to"] is None
        assert parsed["recorded_at"] == "2026-04-17T00:00"

    def test_parse_line_returns_none_for_plain_bullet(self):
        assert parse_line("- just a note without marker") is None

    def test_parse_line_returns_none_for_non_bullet(self):
        assert parse_line("# Heading") is None

    def test_normalise_date_handles_none_and_sentinels(self):
        assert _normalise_date(None) is None
        assert _normalise_date("") is None
        assert _normalise_date("now") is None
        assert _normalise_date("None") is None
        assert _normalise_date("2020-01-01") == "2020-01-01"


# ---------------------------------------------------------------------------
# fact_add
# ---------------------------------------------------------------------------

class TestFactAdd:
    def test_basic_add_persists_file(self, tmp_path):
        mk = _mk(tmp_path)
        r = mk.fact_add("Simon", "role", "CEO of Hashed", valid_from="2020-03-01")
        assert r["entity"] == "Simon"
        assert r["key"] == "role"
        assert r["valid_from"] == "2020-03-01"
        assert r["valid_to"] is None
        # file exists
        path = mk.base_dir / "facts" / "simon.md"
        assert path.exists()
        assert "role: CEO of Hashed" in path.read_text()

    def test_add_requires_entity(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.fact_add("", "role", "x")

    def test_add_requires_key(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.fact_add("Simon", "", "x")

    def test_add_rejects_inverted_interval(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.fact_add("Simon", "role", "x", valid_from="2020-01-01", valid_to="2019-01-01")

    def test_add_with_explicit_recorded_at(self, tmp_path):
        mk = _mk(tmp_path)
        r = mk.fact_add("Simon", "role", "CTO", valid_from="2018-01-01",
                        valid_to="2020-02-29", recorded_at="2024-05-10T14:22")
        assert r["recorded_at"] == "2024-05-10T14:22"


# ---------------------------------------------------------------------------
# fact_at
# ---------------------------------------------------------------------------

class TestFactAt:
    def test_returns_active_fact(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CTO", valid_from="2018-01-01", valid_to="2020-02-29")
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        r = mk.fact_at("Simon", "role", as_of="2019-06-01")
        assert r is not None
        assert r["value"] == "CTO"

    def test_returns_current_fact_when_as_of_in_future_interval(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        r = mk.fact_at("Simon", "role", as_of="2026-04-17")
        assert r is not None
        assert r["value"] == "CEO"

    def test_returns_none_before_any_fact(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        assert mk.fact_at("Simon", "role", as_of="2010-01-01") is None

    def test_returns_none_for_unknown_entity(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.fact_at("Nobody", "role", as_of="2020-01-01") is None

    def test_later_recorded_wins_on_overlap(self, tmp_path):
        mk = _mk(tmp_path)
        # two competing facts for the same time window — the one recorded
        # later represents the more recent belief
        mk.fact_add("Simon", "role", "OLD", valid_from="2020-01-01",
                    valid_to="2022-01-01", recorded_at="2021-01-01T00:00")
        mk.fact_add("Simon", "role", "NEW", valid_from="2020-01-01",
                    valid_to="2022-01-01", recorded_at="2024-01-01T00:00")
        r = mk.fact_at("Simon", "role", as_of="2021-06-01")
        assert r["value"] == "NEW"


# ---------------------------------------------------------------------------
# fact_history
# ---------------------------------------------------------------------------

class TestFactHistory:
    def test_history_sorted_by_recorded(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CTO",
                    valid_from="2018-01-01", valid_to="2020-02-29",
                    recorded_at="2024-05-10T14:22")
        mk.fact_add("Simon", "role", "CEO",
                    valid_from="2020-03-01",
                    recorded_at="2026-04-17T00:30")
        hist = mk.fact_history("Simon")
        assert [h["value"] for h in hist] == ["CTO", "CEO"]

    def test_history_filter_by_key(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        mk.fact_add("Simon", "city", "Seoul", valid_from="2020-01-01")
        hist = mk.fact_history("Simon", key="city")
        assert len(hist) == 1
        assert hist[0]["value"] == "Seoul"

    def test_history_empty_for_unknown_entity(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.fact_history("Nobody") == []

    def test_fact_list_alias(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        mk.fact_add("Simon", "city", "Seoul", valid_from="2020-01-01")
        assert len(mk.fact_list("Simon")) == 2

    def test_fact_keys(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        mk.fact_add("Simon", "city", "Seoul", valid_from="2020-01-01")
        mk.fact_add("Simon", "role", "CTO",
                    valid_from="2018-01-01", valid_to="2020-02-29")
        assert mk.fact_keys("Simon") == ["city", "role"]


# ---------------------------------------------------------------------------
# fact_invalidate
# ---------------------------------------------------------------------------

class TestFactInvalidate:
    def test_invalidates_open_fact(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        n = mk.fact_invalidate("Simon", "role", invalid_at="2026-04-17")
        assert n == 1
        # fact_at in 2027 should now return None
        assert mk.fact_at("Simon", "role", as_of="2027-01-01") is None
        # but still findable in history
        hist = mk.fact_history("Simon", key="role")
        assert hist[0]["valid_to"] == "2026-04-17"

    def test_invalidate_noop_when_nothing_open(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CTO",
                    valid_from="2018-01-01", valid_to="2020-02-29")
        n = mk.fact_invalidate("Simon", "role", invalid_at="2026-01-01")
        assert n == 0

    def test_invalidate_unknown_entity(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.fact_invalidate("Nobody", "role") == 0

    def test_invalidate_preserves_unrelated_lines(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
        mk.fact_add("Simon", "city", "Seoul", valid_from="2020-01-01")
        mk.fact_invalidate("Simon", "role", invalid_at="2026-04-17")
        # city is still open-ended
        r = mk.fact_at("Simon", "city", as_of="2027-01-01")
        assert r is not None
        assert r["value"] == "Seoul"


# ---------------------------------------------------------------------------
# back-compat / isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_facts_dir_created_lazily(self, tmp_path):
        mk = _mk(tmp_path)
        # no facts yet — facts dir may or may not exist, but queries are safe
        assert mk.fact_history("Anyone") == []
        assert mk.fact_at("Anyone", "x") is None

    def test_multiple_entities_isolated(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("Alice", "role", "Eng", valid_from="2020-01-01")
        mk.fact_add("Bob", "role", "PM", valid_from="2019-01-01")
        assert mk.fact_at("Alice", "role")["value"] == "Eng"
        assert mk.fact_at("Bob", "role")["value"] == "PM"

    def test_unicode_entity_name_slugified(self, tmp_path):
        mk = _mk(tmp_path)
        mk.fact_add("김서준", "role", "CEO", valid_from="2020-03-01")
        hist = mk.fact_history("김서준")
        assert len(hist) == 1
