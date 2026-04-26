"""Tests for v2.3+ TemporalChainMixin (multi-session temporal chain).

Coverage targets:
  * _is_multi_session_query — keyword + numeric pattern + Korean
  * _extract_time_window — relative phrases + numeric N-day patterns
  * _get_temporal_chain — temporal-edge filtering by window
  * _get_temporal_chain — bitemporal fact enrichment
  * search_multi integration — temporal chain fed into Pass 3 fusion
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memkraft import MemKraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


@pytest.fixture
def now():
    """Use a real-current 'now' so search_multi's internal call
    (which uses datetime.now()) stays in sync with the fixture data.
    Date arithmetic is still deterministic within a single test run.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def mk_temporal(tmp_path, now):
    """MemKraft seeded with temporal edges + facts spanning multiple
    'sessions' over the past year."""
    mk = MemKraft(base_dir=str(tmp_path))

    # Helper to format ISO date.
    def iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    # --- Temporal edges (graph_type='temporal') ---
    # 3 days ago: deploy_v1 --before--> deploy_v2  (within last week)
    mk.graph_edge(
        "deploy_v1",
        "before",
        "deploy_v2",
        valid_from=iso(now - timedelta(days=3)),
        graph_type="temporal",
    )
    # 10 days ago: meeting_a --followed_by--> meeting_b (within last month)
    mk.graph_edge(
        "meeting_a",
        "followed_by",
        "meeting_b",
        valid_from=iso(now - timedelta(days=10)),
        graph_type="temporal",
    )
    # 60 days ago: q1_review --before--> q2_planning (within last quarter,
    # outside last month)
    mk.graph_edge(
        "q1_review",
        "before",
        "q2_planning",
        valid_from=iso(now - timedelta(days=60)),
        graph_type="temporal",
    )
    # 200 days ago: ancient_event_a --before--> ancient_event_b (outside
    # all reasonable relative windows except 'last year')
    mk.graph_edge(
        "ancient_a",
        "before",
        "ancient_b",
        valid_from=iso(now - timedelta(days=200)),
        graph_type="temporal",
    )

    # --- A non-temporal edge that should NEVER appear in temporal chain ---
    mk.graph_edge("alice", "works_at", "google")  # graph_type='entity' (default)

    # --- Bitemporal facts on temporal-edge endpoints ---
    mk.fact_add(
        "deploy_v1",
        "status",
        "rolled_back",
        valid_from=iso(now - timedelta(days=3)),
        recorded_at=iso(now - timedelta(days=3)),
    )
    mk.fact_add(
        "deploy_v2",
        "status",
        "stable",
        valid_from=iso(now - timedelta(days=2)),
        recorded_at=iso(now - timedelta(days=2)),
    )
    mk.fact_add(
        "meeting_a",
        "topic",
        "kickoff",
        valid_from=iso(now - timedelta(days=10)),
        recorded_at=iso(now - timedelta(days=10)),
    )
    return mk


# ---------------------------------------------------------------------------
# 1. _is_multi_session_query
# ---------------------------------------------------------------------------
class TestIsMultiSessionQuery:
    def test_empty_query(self, mk):
        assert mk._is_multi_session_query("") is False
        assert mk._is_multi_session_query("   ") is False
        assert mk._is_multi_session_query(None) is False  # type: ignore[arg-type]

    def test_simple_factual_not_multi_session(self, mk):
        assert mk._is_multi_session_query("Who is Sarah?") is False
        assert mk._is_multi_session_query("Where does Alice work?") is False

    def test_relative_window_english(self, mk):
        assert mk._is_multi_session_query("What did I do last month?") is True
        assert mk._is_multi_session_query("Anything important last week?") is True
        assert mk._is_multi_session_query("Things that happened recently") is True

    def test_relative_window_korean(self, mk):
        assert mk._is_multi_session_query("지난달 뭐 했어?") is True
        assert mk._is_multi_session_query("최근 이슈가 뭐야?") is True

    def test_aggregation_keywords(self, mk):
        assert mk._is_multi_session_query("How many deploys did we ship?") is True
        assert mk._is_multi_session_query("compare Q1 and Q2 results") is True
        assert mk._is_multi_session_query("얼마나 자주 회의했어?") is True

    def test_numeric_window_pattern(self, mk):
        assert (
            mk._is_multi_session_query(
                "how many incidents in the past 30 days"
            )
            is True
        )
        assert (
            mk._is_multi_session_query("how many bugs in the last 7 days")
            is True
        )


# ---------------------------------------------------------------------------
# 2. _extract_time_window
# ---------------------------------------------------------------------------
class TestExtractTimeWindow:
    def test_no_temporal_phrase_returns_none(self, mk, now):
        assert mk._extract_time_window("hello world", now=now) is None
        assert mk._extract_time_window("Who is Sarah?", now=now) is None

    def test_last_week_returns_7_day_window(self, mk, now):
        win = mk._extract_time_window("what happened last week?", now=now)
        assert win is not None
        start, end = win
        assert end == now
        # 7 days ± a tolerance
        assert (end - start) == timedelta(days=7)

    def test_last_month_returns_30_day_window(self, mk, now):
        win = mk._extract_time_window("anything new last month?", now=now)
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=30)

    def test_last_year_returns_365_day_window(self, mk, now):
        win = mk._extract_time_window("recap last year", now=now)
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=365)

    def test_korean_jinanjul(self, mk, now):
        win = mk._extract_time_window("지난주 회의록", now=now)
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=7)

    def test_numeric_past_n_days(self, mk, now):
        win = mk._extract_time_window(
            "how many incidents in the past 30 days", now=now
        )
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=30)

    def test_numeric_last_n_weeks(self, mk, now):
        win = mk._extract_time_window("activity in the last 2 weeks", now=now)
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=14)

    def test_korean_numeric_window(self, mk, now):
        win = mk._extract_time_window("지난 3개월 동안", now=now)
        assert win is not None
        start, end = win
        assert (end - start) == timedelta(days=90)


# ---------------------------------------------------------------------------
# 3. _get_temporal_chain — edge filtering
# ---------------------------------------------------------------------------
class TestGetTemporalChain:
    def test_no_window_returns_empty(self, mk_temporal, now):
        assert (
            mk_temporal._get_temporal_chain(
                "no temporal terms here", now=now
            )
            == []
        )

    def test_last_week_picks_only_recent_edges(self, mk_temporal, now):
        rows = mk_temporal._get_temporal_chain("last week", now=now)
        # Should include deploy_v1 -> deploy_v2 (3 days ago) but NOT
        # meeting_a -> meeting_b (10 days ago) and NOT q1_review/ancient.
        edge_pairs = {
            (r.get("_from"), r.get("_to"))
            for r in rows
            if r.get("_temporal_edge")
        }
        assert ("deploy_v1", "deploy_v2") in edge_pairs
        assert ("meeting_a", "meeting_b") not in edge_pairs
        assert ("q1_review", "q2_planning") not in edge_pairs
        assert ("ancient_a", "ancient_b") not in edge_pairs

    def test_last_month_picks_recent_and_meeting(self, mk_temporal, now):
        rows = mk_temporal._get_temporal_chain("last month", now=now)
        edge_pairs = {
            (r.get("_from"), r.get("_to"))
            for r in rows
            if r.get("_temporal_edge")
        }
        assert ("deploy_v1", "deploy_v2") in edge_pairs
        assert ("meeting_a", "meeting_b") in edge_pairs
        # Still excludes q1 (60 days ago) and ancient (200 days ago).
        assert ("q1_review", "q2_planning") not in edge_pairs
        assert ("ancient_a", "ancient_b") not in edge_pairs

    def test_last_quarter_includes_q1_review(self, mk_temporal, now):
        rows = mk_temporal._get_temporal_chain("last quarter", now=now)
        edge_pairs = {
            (r.get("_from"), r.get("_to"))
            for r in rows
            if r.get("_temporal_edge")
        }
        assert ("q1_review", "q2_planning") in edge_pairs
        assert ("ancient_a", "ancient_b") not in edge_pairs

    def test_excludes_non_temporal_edges(self, mk_temporal, now):
        # Even with a wide window, the entity-typed edge alice->google
        # must never surface in the temporal chain.
        rows = mk_temporal._get_temporal_chain("last year", now=now)
        edge_pairs = {
            (r.get("_from"), r.get("_to"))
            for r in rows
            if r.get("_temporal_edge")
        }
        assert ("alice", "google") not in edge_pairs

    def test_temporal_chain_recency_ordering(self, mk_temporal, now):
        """Most recent temporal edge should rank above older ones."""
        rows = mk_temporal._get_temporal_chain("last quarter", now=now)
        # Filter to edges only (facts can interleave by score).
        edge_rows = [r for r in rows if r.get("_temporal_edge")]
        scores_by_pair = {
            (r["_from"], r["_to"]): r["score"] for r in edge_rows
        }
        assert (
            scores_by_pair[("deploy_v1", "deploy_v2")]
            >= scores_by_pair[("meeting_a", "meeting_b")]
            >= scores_by_pair[("q1_review", "q2_planning")]
        )

    def test_temporal_chain_includes_attached_facts(self, mk_temporal, now):
        rows = mk_temporal._get_temporal_chain("last week", now=now)
        # A fact about deploy_v1 (status=rolled_back, recorded 3 days ago)
        # should be enriched in.
        fact_keys = {
            (r.get("_entity"), r.get("_key"), r.get("_value"))
            for r in rows
            if r.get("_temporal_fact")
        }
        assert ("deploy_v1", "status", "rolled_back") in fact_keys

    def test_temporal_chain_no_graph_db_safe(self, tmp_path, now):
        """Even on a fresh empty MemKraft the chain returns an empty list."""
        mk = MemKraft(base_dir=str(tmp_path))
        # Should not raise even though no edges exist.
        assert mk._get_temporal_chain("last week", now=now) == []


# ---------------------------------------------------------------------------
# 4. search_multi integration
# ---------------------------------------------------------------------------
class TestSearchMultiIntegration:
    def test_search_multi_unaffected_when_not_multi_session(
        self, mk_temporal
    ):
        """Plain factual queries should still work and not crash."""
        out = mk_temporal.search_multi("Who deployed v1?", top_k=5)
        # We can't guarantee non-empty (no markdown corpus) but it must be
        # a list and must not raise.
        assert isinstance(out, list)

    def test_search_multi_multi_session_query_includes_temporal_match(
        self, mk_temporal
    ):
        out = mk_temporal.search_multi(
            "what deploys happened last week?", top_k=10
        )
        assert isinstance(out, list)
        # At least one result should reference deploy_v1 or deploy_v2.
        matches = [r.get("match", "") for r in out]
        joined = " ".join(matches).lower()
        assert "deploy" in joined or any("deploy" in (r.get("snippet","") or "").lower() for r in out)

    def test_search_multi_does_not_inject_for_old_window(self, mk_temporal):
        """A 'last week' query must not surface the 200-day-old ancient edge."""
        out = mk_temporal.search_multi("last week summary", top_k=10)
        for r in out:
            assert "ancient_a" not in (r.get("match", "") or "")
            assert "ancient_a" not in (r.get("snippet", "") or "")

    def test_search_multi_passes_eq_2_skips_temporal(self, mk_temporal):
        """Temporal chain only kicks in at passes>=3."""
        out = mk_temporal.search_multi(
            "what deploys happened last week?",
            top_k=10,
            passes=2,
        )
        # No crash, no temporal edge synthetic snippet expected.
        for r in out:
            snippet = (r.get("snippet") or "").lower()
            # Snippet of a synthetic temporal edge has the form
            # "<from> --<rel>--> <to>".  At passes=2 we should NOT see
            # that synthetic shape.
            assert "deploy_v1 --before--> deploy_v2" not in snippet
