"""v2.11 — public PreferenceGraphSyncMixin surface on MemKraft."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path))


def _edge_rows(mk: MemKraft) -> list[dict]:
    db_path = mk.base_dir / "graph.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT from_id, relation, to_id, weight, valid_from, valid_until, graph_type
                FROM edges
                ORDER BY from_id, relation, to_id
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def test_preference_graph_sync_methods_exist_on_public_memkraft(mk: MemKraft) -> None:
    for name in (
        "sync_preference_to_graph",
        "sync_all_preferences_to_graph",
        "reason_preference_via_graph",
    ):
        assert hasattr(mk, name), f"{name} missing on public MemKraft"
        assert callable(getattr(mk, name))


def test_sync_single_preference_creates_value_category_and_reason_edges(mk: MemKraft) -> None:
    result = mk.sync_preference_to_graph(
        "Simon",
        {
            "key": "favorite_food",
            "value": "Korean BBQ",
            "category": "food",
            "strength": 0.9,
            "valid_from": "2025-01-01",
            "valid_to": None,
            "reason": "grew up eating it",
        },
    )

    assert result["edges_added"] == 3
    assert result["entity"] == "simon"
    assert result["relation"] == "favorite_food"
    assert result["target"] == "korean-bbq"
    assert result["polarity"] == "positive"

    rows = _edge_rows(mk)
    assert any(
        r["from_id"] == "simon"
        and r["relation"] == "favorite_food"
        and r["to_id"] == "korean-bbq"
        and pytest.approx(r["weight"], abs=1e-6) == 0.9
        and r["valid_from"] == "2025-01-01"
        and r["valid_until"] is None
        and r["graph_type"] == "entity"
        for r in rows
    )
    assert any(
        r["from_id"] == "korean-bbq"
        and r["relation"] == "category"
        and r["to_id"] == "food"
        for r in rows
    )
    assert any(
        r["from_id"] == "korean-bbq"
        and r["relation"] == "because_of"
        and r["to_id"] == "grew-up-eating-it"
        for r in rows
    )


def test_sync_all_preferences_to_graph_includes_closed_preferences_by_default(mk: MemKraft) -> None:
    mk.track("Simon")
    mk.pref_set("Simon", "food", "kimchi", category="food", strength=0.8, valid_from="2024-01-01", reason="old favorite")
    mk.pref_set("Simon", "food", "ramen", category="food", strength=0.9, valid_from="2025-01-01", reason="new favorite")

    result = mk.sync_all_preferences_to_graph("Simon")

    assert result["entity"] == "Simon"
    assert result["total_prefs"] == 2
    assert result["synced"] == 2
    rows = _edge_rows(mk)
    assert any(
        r["from_id"] == "simon"
        and r["relation"] == "food"
        and r["to_id"] == "kimchi"
        and r["valid_from"] == "2024-01-01"
        and r["valid_until"] == "2025-01-01"
        for r in rows
    )
    assert any(
        r["from_id"] == "simon"
        and r["relation"] == "food"
        and r["to_id"] == "ramen"
        and r["valid_from"] == "2025-01-01"
        and r["valid_until"] is None
        for r in rows
    )


def test_sync_all_preferences_to_graph_can_exclude_closed_preferences(mk: MemKraft) -> None:
    mk.track("Simon")
    mk.pref_set("Simon", "food", "kimchi", category="food", valid_from="2024-01-01")
    mk.pref_set("Simon", "food", "ramen", category="food", valid_from="2025-01-01")

    result = mk.sync_all_preferences_to_graph("Simon", include_closed=False)

    assert result["total_prefs"] == 1
    assert result["synced"] == 1
    rows = _edge_rows(mk)
    assert any(r["from_id"] == "simon" and r["relation"] == "food" and r["to_id"] == "ramen" for r in rows)
    assert not any(r["from_id"] == "simon" and r["relation"] == "food" and r["to_id"] == "kimchi" for r in rows)


def test_korean_preference_api_slug_is_not_clobbered_by_graph_sync_mixin(mk: MemKraft) -> None:
    mk.track("김서준", entity_type="person")
    mk.pref_set("김서준", "food", "김치찌개", category="food")

    prefs = mk.pref_get("김서준", key="food")
    assert len(prefs) == 1
    assert prefs[0]["value"] == "김치찌개"
