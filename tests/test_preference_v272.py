"""v2.7.2 — preference API surface tests.

Regression guard against the v2.7.1 bug where `pref_set / pref_get /
pref_context / pref_evolution` were defined on `PreferenceMixin` but
NEVER attached to `MemKraft`, so PersonaMem ingestion silently failed
and recall accuracy regressed by ~13.6pp on PersonaMem 32k.

These tests cover the bare minimum end-to-end contract that PersonaMem
relies on:
  1. The methods exist on a stock MemKraft instance.
  2. pref_set → pref_get round-trip.
  3. Overwriting the same key chronologically closes the previous one.
  4. pref_context returns a usable structure.
  5. pref_evolution returns ordered history.
  6. pref_conflicts still works (i.e. we did not regress v2.5+ aliases).
  7. Korean entity names survive (core._slugify is NOT clobbered).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk():
    with tempfile.TemporaryDirectory() as d:
        yield MemKraft(base_dir=Path(d))


def test_preference_methods_exist(mk):
    # The actual v2.7.1 -> v2.7.2 bug was that these were missing.
    for name in ("pref_set", "pref_get", "pref_context", "pref_evolution",
                 "pref_conflicts", "pref_conflicts_all"):
        assert hasattr(mk, name), f"{name} missing on MemKraft"
        assert callable(getattr(mk, name))


def test_pref_set_get_roundtrip(mk):
    mk.track("Simon", entity_type="person")
    mk.pref_set("Simon", "food", "kimchi",
                category="food", strength=0.9, source="test")

    prefs = mk.pref_get("Simon", key="food")
    assert len(prefs) == 1
    p = prefs[0]
    assert p["key"] == "food"
    assert p["value"] == "kimchi"
    assert p["category"] == "food"
    assert pytest.approx(p["strength"], abs=1e-6) == 0.9
    assert p["valid_to"] is None  # still open


def test_pref_set_overwrite_closes_previous(mk):
    mk.track("Simon")
    mk.pref_set("Simon", "food", "kimchi", category="food",
                valid_from="2024-01-01")
    mk.pref_set("Simon", "food", "ramen", category="food",
                valid_from="2025-01-01")

    # `pref_get` returns currently-valid entries by default
    current = mk.pref_get("Simon", key="food")
    assert len(current) == 1
    assert current[0]["value"] == "ramen"

    # Evolution shows both, in chronological order
    history = mk.pref_evolution("Simon", key="food")
    assert len(history) == 2
    assert [p["value"] for p in history] == ["kimchi", "ramen"]


def test_pref_context_returns_structured_payload(mk):
    mk.track("Simon")
    mk.pref_set("Simon", "food", "kimchi", category="food", strength=1.0)
    mk.pref_set("Simon", "music", "city pop", category="music", strength=0.8)
    mk.pref_set("Simon", "movie", "kurosawa", category="entertainment", strength=0.7)

    ctx = mk.pref_context("Simon", "what should I eat tonight?", max_prefs=20)
    assert isinstance(ctx, dict)
    assert ctx["entity"] == "Simon"
    assert "preferences" in ctx
    # food category should outrank entertainment for a "what should I eat" scenario
    top = ctx["preferences"][0]
    assert top["category"] == "food"


def test_pref_context_unknown_scenario_uses_all_categories(mk):
    mk.track("Simon")
    mk.pref_set("Simon", "x", "y", category="food")
    ctx = mk.pref_context("Simon", "qwerty unknown scenario string", max_prefs=10)
    # All categories considered → the single preference should still come back
    assert any(p["value"] == "y" for p in ctx["preferences"])


def test_pref_conflicts_detects_overlap(mk):
    """`pref_conflicts` (no-arg, scans all entities) and `pref_conflicts_all`
    are both aliased to the same scanner since v2.5.0; we keep that contract.
    """
    mk.track("Simon")
    # Two values for the same key on the same day → conflict signal
    mk.pref_set("Simon", "color", "blue", category="general",
                valid_from="2024-01-01")
    mk.pref_set("Simon", "color", "green", category="general",
                valid_from="2024-01-01")

    conflicts = mk.pref_conflicts_all()
    assert any(
        c.get("entity") == "simon" and "color" in c.get("conflict", "")
        for c in conflicts
    ), f"color conflict missing in {conflicts!r}"

    # `pref_conflicts` is the convenience alias, no-arg scanner
    same = mk.pref_conflicts()
    assert same == conflicts


def test_korean_entity_names_still_work(mk):
    """Core._slugify supports CJK; PreferenceMixin._slugify does NOT.
    This test guards against accidentally attaching PreferenceMixin._slugify
    to _BaseMemKraft and breaking Korean entity names.
    """
    mk.track("김서준", entity_type="person")
    mk.pref_set("김서준", "food", "김치찌개", category="food")

    prefs = mk.pref_get("김서준", key="food")
    assert len(prefs) == 1
    assert prefs[0]["value"] == "김치찌개"


def test_pref_get_empty_for_unknown_entity(mk):
    assert mk.pref_get("NobodyWhoExists") == []


def test_pref_get_filters_by_category(mk):
    mk.track("Simon")
    mk.pref_set("Simon", "a", "v1", category="food")
    mk.pref_set("Simon", "b", "v2", category="music")

    food_only = mk.pref_get("Simon", category="food")
    assert len(food_only) == 1
    assert food_only[0]["value"] == "v1"


def test_pref_set_with_reason_is_preserved(mk):
    mk.track("Simon")
    mk.pref_set("Simon", "food", "kimchi", category="food",
                reason="grew up eating it")
    prefs = mk.pref_get("Simon", key="food")
    assert prefs and prefs[0].get("reason") == "grew up eating it"


def test_pref_conflicts_accepts_entity_arg(mk):
    """PersonaMem (`src/memkraft/personamem.py:756`) calls
    ``mk.pref_conflicts(persona_name)`` — with a single entity
    argument. In v2.7.1 that raised ``TypeError`` and the harness's
    ``try/except Exception`` silently dropped the conflict context.
    Both no-arg and per-entity forms must work."""
    mk.track("Bob")
    mk.pref_set("Bob", "food", "kimchi", category="food",
                valid_from="2024-01-01")
    mk.pref_set("Bob", "food", "ramen", category="food",
                valid_from="2025-01-01")

    # No-arg — global scan (v2.5.0 contract)
    all_conf = mk.pref_conflicts()
    assert isinstance(all_conf, list)
    assert any(c.get("entity") == "bob" for c in all_conf)

    # Single entity — PersonaMem contract (v2.7.2 fix)
    bob_conf = mk.pref_conflicts("Bob")
    assert isinstance(bob_conf, list)
    assert len(bob_conf) == 1
    assert bob_conf[0]["key"] == "food"
    assert bob_conf[0]["current"] == "ramen"
    # Per-entity form returns the v2.1 shape ("values" + "current"),
    # not the v2.5.0 cross-entity shape ("entity" + "conflict").
    assert "values" in bob_conf[0]
