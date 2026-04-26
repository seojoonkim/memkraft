"""Tests for v2.2 — Knowledge Update auto-detect on fact_add().

When ``fact_add`` is called with an open-ended fact (``valid_to is None``)
and an existing open-ended fact for the same ``entity.key`` already exists,
the existing fact's ``valid_to`` should be auto-closed at the new fact's
``valid_from`` (or today, if not specified).

Behaviour can be opted out via ``auto_close_stale=False``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path / "memory"))


def _open_facts(mk: MemKraft, entity: str, key: str):
    return [
        f for f in mk.fact_history(entity, key=key)
        if f["valid_to"] is None
    ]


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_role_update_auto_closes_previous(mk: MemKraft) -> None:
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Simon", "role", "CTO", valid_from="2026-04-01")

    history = mk.fact_history("Simon", key="role")
    assert len(history) == 2

    ceo = next(f for f in history if f["value"] == "CEO")
    cto = next(f for f in history if f["value"] == "CTO")

    assert ceo["valid_to"] == "2026-04-01", "previous fact must be closed"
    assert cto["valid_to"] is None, "new fact stays open-ended"
    assert cto["valid_from"] == "2026-04-01"


def test_only_one_open_fact_after_update(mk: MemKraft) -> None:
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Simon", "role", "CTO", valid_from="2026-04-01")
    open_facts = _open_facts(mk, "Simon", "role")
    assert len(open_facts) == 1
    assert open_facts[0]["value"] == "CTO"


def test_three_consecutive_updates(mk: MemKraft) -> None:
    mk.fact_add("Alice", "title", "Engineer", valid_from="2020-01-01")
    mk.fact_add("Alice", "title", "Senior Engineer", valid_from="2022-06-01")
    mk.fact_add("Alice", "title", "Staff Engineer", valid_from="2025-03-01")

    history = mk.fact_history("Alice", key="title")
    assert len(history) == 3

    eng = next(f for f in history if f["value"] == "Engineer")
    sen = next(f for f in history if f["value"] == "Senior Engineer")
    staff = next(f for f in history if f["value"] == "Staff Engineer")

    assert eng["valid_to"] == "2022-06-01"
    assert sen["valid_to"] == "2025-03-01"
    assert staff["valid_to"] is None
    assert len(_open_facts(mk, "Alice", "title")) == 1


def test_different_keys_do_not_interfere(mk: MemKraft) -> None:
    mk.fact_add("Bob", "role", "CEO", valid_from="2020-01-01")
    mk.fact_add("Bob", "city", "Seoul", valid_from="2021-01-01")
    mk.fact_add("Bob", "city", "Tokyo", valid_from="2024-06-01")

    role_open = _open_facts(mk, "Bob", "role")
    city_open = _open_facts(mk, "Bob", "city")

    assert len(role_open) == 1, "role fact must remain open"
    assert role_open[0]["value"] == "CEO"
    assert role_open[0]["valid_to"] is None

    assert len(city_open) == 1
    assert city_open[0]["value"] == "Tokyo"


def test_different_entities_do_not_interfere(mk: MemKraft) -> None:
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Alice", "role", "CTO", valid_from="2023-06-01")

    simon_open = _open_facts(mk, "Simon", "role")
    alice_open = _open_facts(mk, "Alice", "role")

    assert len(simon_open) == 1
    assert simon_open[0]["value"] == "CEO"
    assert simon_open[0]["valid_to"] is None
    assert len(alice_open) == 1
    assert alice_open[0]["value"] == "CTO"


# ---------------------------------------------------------------------------
# valid_from / valid_to correctness
# ---------------------------------------------------------------------------


def test_close_at_uses_new_valid_from(mk: MemKraft) -> None:
    mk.fact_add("X", "k", "v1", valid_from="2020-01-01")
    mk.fact_add("X", "k", "v2", valid_from="2024-12-31")
    closed = next(f for f in mk.fact_history("X", key="k") if f["value"] == "v1")
    assert closed["valid_to"] == "2024-12-31"


def test_close_at_today_when_new_valid_from_missing(mk: MemKraft) -> None:
    mk.fact_add("X", "k", "v1", valid_from="2020-01-01")
    mk.fact_add("X", "k", "v2")  # no valid_from -> close at today
    closed = next(f for f in mk.fact_history("X", key="k") if f["value"] == "v1")
    assert closed["valid_to"] is not None
    # today's date in ISO YYYY-MM-DD
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", closed["valid_to"])


def test_first_fact_remains_open(mk: MemKraft) -> None:
    """A first ever fact for an entity.key must stay open-ended."""
    mk.fact_add("Solo", "role", "Founder", valid_from="2020-01-01")
    open_facts = _open_facts(mk, "Solo", "role")
    assert len(open_facts) == 1
    assert open_facts[0]["valid_to"] is None
    assert open_facts[0]["value"] == "Founder"


# ---------------------------------------------------------------------------
# as_of() — point-in-time queries see correct state
# ---------------------------------------------------------------------------


def test_as_of_during_first_role_returns_first(mk: MemKraft) -> None:
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Simon", "role", "CTO", valid_from="2026-04-01")

    fact = mk.fact_at("Simon", "role", as_of="2023-06-15")
    assert fact is not None
    assert fact["value"] == "CEO"


def test_as_of_after_transition_returns_new(mk: MemKraft) -> None:
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Simon", "role", "CTO", valid_from="2026-04-01")

    fact = mk.fact_at("Simon", "role", as_of="2026-05-01")
    assert fact is not None
    assert fact["value"] == "CTO"


def test_as_of_one_day_before_and_after_transition(mk: MemKraft) -> None:
    """Day before transition -> old role; day after -> new role.

    The exact transition day is intentionally not asserted because the
    closing of the old fact stamps both intervals with the same
    ``recorded_at`` minute, making the boundary tie-break implementation-
    defined. This test covers the meaningful before/after semantics.
    """
    mk.fact_add("Simon", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add("Simon", "role", "CTO", valid_from="2026-04-01")

    before = mk.fact_at("Simon", "role", as_of="2026-03-31")
    after = mk.fact_at("Simon", "role", as_of="2026-04-02")
    assert before is not None and before["value"] == "CEO"
    assert after is not None and after["value"] == "CTO"


# ---------------------------------------------------------------------------
# fact_history()
# ---------------------------------------------------------------------------


def test_fact_history_preserves_full_record(mk: MemKraft) -> None:
    mk.fact_add("H", "k", "a", valid_from="2020-01-01")
    mk.fact_add("H", "k", "b", valid_from="2022-01-01")
    mk.fact_add("H", "k", "c", valid_from="2024-01-01")

    history = mk.fact_history("H", key="k")
    values = [f["value"] for f in history]
    assert sorted(values) == ["a", "b", "c"]
    # exactly one open-ended fact
    open_count = sum(1 for f in history if f["valid_to"] is None)
    assert open_count == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_already_closed_fact_is_not_modified(mk: MemKraft) -> None:
    """A historical (already closed) fact must not be touched again."""
    mk.fact_add(
        "E",
        "role",
        "Intern",
        valid_from="2018-01-01",
        valid_to="2019-12-31",
    )
    mk.fact_add("E", "role", "Engineer", valid_from="2020-01-01")
    # Now overwrite Engineer with Senior:
    mk.fact_add("E", "role", "Senior", valid_from="2024-01-01")

    history = mk.fact_history("E", key="role")
    intern = next(f for f in history if f["value"] == "Intern")
    engineer = next(f for f in history if f["value"] == "Engineer")
    senior = next(f for f in history if f["value"] == "Senior")

    # Intern's original closure stays at 2019-12-31, not reset to anything new
    assert intern["valid_to"] == "2019-12-31"
    assert intern["valid_from"] == "2018-01-01"
    # Engineer was the only open one before Senior, so it's now closed at 2024-01-01
    assert engineer["valid_to"] == "2024-01-01"
    assert senior["valid_to"] is None


def test_empty_entity_raises(mk: MemKraft) -> None:
    with pytest.raises(ValueError):
        mk.fact_add("", "role", "CEO")
    with pytest.raises(ValueError):
        mk.fact_add("   ", "role", "CEO")


def test_empty_key_raises(mk: MemKraft) -> None:
    with pytest.raises(ValueError):
        mk.fact_add("Simon", "", "CEO")


def test_auto_close_opt_out(mk: MemKraft) -> None:
    """auto_close_stale=False should leave existing open facts alone."""
    mk.fact_add("Simon", "role", "CEO", valid_from="2020-01-01")
    mk.fact_add(
        "Simon",
        "role",
        "Advisor",
        valid_from="2018-01-01",
        valid_to="2019-12-31",
        auto_close_stale=False,
    )

    open_facts = _open_facts(mk, "Simon", "role")
    assert len(open_facts) == 1
    assert open_facts[0]["value"] == "CEO"


def test_backfilling_closed_fact_does_not_close_open(mk: MemKraft) -> None:
    """Even with default auto_close_stale=True, a *closed* new fact (valid_to set)
    should NOT close the currently-open fact, because we only auto-close when
    the new fact is itself open-ended."""
    mk.fact_add("B", "role", "CEO", valid_from="2022-01-01")
    mk.fact_add(
        "B",
        "role",
        "Past Intern",
        valid_from="2010-01-01",
        valid_to="2011-12-31",
    )

    open_facts = _open_facts(mk, "B", "role")
    assert len(open_facts) == 1
    assert open_facts[0]["value"] == "CEO"
    assert open_facts[0]["valid_to"] is None


def test_close_helper_returns_modified_count(mk: MemKraft) -> None:
    mk.fact_add("C", "role", "CEO", valid_from="2020-01-01")
    n = mk._close_stale_facts("C", "role", "2024-01-01")
    assert n == 1
    n2 = mk._close_stale_facts("C", "role", "2024-01-01")
    assert n2 == 0  # nothing left to close


def test_close_helper_on_missing_entity(mk: MemKraft) -> None:
    n = mk._close_stale_facts("NoSuchEntity", "role", "2024-01-01")
    assert n == 0


def test_future_dated_existing_fact_not_closed(mk: MemKraft) -> None:
    """An existing fact whose valid_from is *after* the new fact's
    valid_from must not be auto-closed (would create inverted interval)."""
    mk.fact_add("F", "role", "Future CEO", valid_from="2030-01-01")
    mk.fact_add("F", "role", "Current CTO", valid_from="2024-01-01")

    history = mk.fact_history("F", key="role")
    future = next(f for f in history if f["value"] == "Future CEO")
    current = next(f for f in history if f["value"] == "Current CTO")

    # Future CEO starts in 2030, can't be closed at 2024 — stays open
    assert future["valid_to"] is None
    # Current CTO is the new fact, also open
    assert current["valid_to"] is None
